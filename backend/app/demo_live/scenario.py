"""The scripted order-to-cash demonstration: a new customer order arrives and is followed all the way to cash, one visible step at a time.

Every step calls the real API as a seeded demo user (see actions.py), so each one creates real records, ledger entries, stock movements and activity.
The presenter can start, pause, resume, step manually, change speed and reset. State lives in `demo_scenario` (one row). A step that cannot run (for
example a stock shortage) stops the scenario with the plain reason; the presenter retries it or resets. Activity is labelled "Demo"."""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from app.core.db import SessionLocal

log = logging.getLogger("mintree.demo_live.scenario")

STEPS: list[tuple[str, str]] = [
    ("A new customer order arrives", "s_order"),
    ("The order appears on the Sales & Dispatch dashboard", "s_dashboard"),
    ("Inventory is checked and a production order is planned", "s_inventory"),
    ("A purchase request is raised for missing materials", "s_pr"),
    ("The request is approved and a purchase order is issued", "s_po"),
    ("Materials are received", "s_receive"),
    ("Incoming materials pass quality inspection", "s_incoming_qc"),
    ("Production starts: materials are issued", "s_produce_start"),
    ("Production output is recorded", "s_produce_done"),
    ("The finished batch is tested in QC", "s_fg_qc"),
    ("The batch is released to available stock", "s_release"),
    ("The order is allocated and packed; its invoice is drafted, validated and posted (the goods ship)", "s_dispatch"),
    ("Delivery is confirmed and the invoice PDF is available", "s_deliver"),
    ("The customer pays and the payment is allocated", "s_payment"),
    ("The management dashboard reflects the completed transaction", "s_verify"),
]
STEP_DELAY_S = 5.0


def get(db) -> dict:
    r = db.execute(text("SELECT status, step, speed, ctx, log, next_at, started_at FROM demo_scenario WHERE id = 1")).mappings().first()
    return dict(r) if r else {"status": "idle", "step": 0, "speed": 1, "ctx": {}, "log": [], "next_at": None, "started_at": None}


def public(db) -> dict:
    s = get(db)
    return {"status": s["status"], "step": s["step"], "total": len(STEPS), "speed": s["speed"], "log": s["log"],
            "steps": [t for t, _ in STEPS], "started_at": s["started_at"].isoformat() if s["started_at"] else None}


def _set(db, **cols) -> None:
    import json
    sets, params = [], {}
    for k, v in cols.items():
        if k in ("ctx", "log"):
            sets.append(f"{k} = CAST(:{k} AS json)")
            params[k] = json.dumps(v, default=str)
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    db.execute(text(f"UPDATE demo_scenario SET {', '.join(sets)}, updated_at = now() WHERE id = 1"), params)


def control(db, action: str, speed: int | None = None) -> dict:
    s = get(db)
    if action == "start":
        if s["status"] in ("running", "paused"):
            raise ValueError("The scenario is already running; reset it first")
        _set(db, status="running", step=0, ctx={}, log=[], started_at=datetime.now(timezone.utc), next_at=datetime.now(timezone.utc))
    elif action == "pause" and s["status"] == "running":
        _set(db, status="paused")
    elif action == "resume" and s["status"] == "paused":
        _set(db, status="running", next_at=datetime.now(timezone.utc))
    elif action == "speed":
        _set(db, speed=speed or 1)
    elif action == "reset":
        _set(db, status="idle", step=0, ctx={}, log=[], next_at=None, started_at=None)
    elif action == "retry" and s["status"] == "failed":
        _set(db, status="running", next_at=datetime.now(timezone.utc))
    elif action == "step" and s["status"] in ("paused", "running", "failed"):
        _set(db, status="running", next_at=datetime.now(timezone.utc), ctx={**s["ctx"], "_once": True})
    elif action in ("pause", "resume", "retry", "step"):
        raise ValueError(f"Cannot {action} a scenario that is {s['status']}")
    return public(db)


class Scenario:
    """Mixed into `Live`. `scenario_tick()` is called by the engine loop about once a second."""

    def scenario_active(self) -> bool:
        with SessionLocal() as db:
            return get(db)["status"] in ("running", "paused")

    def scenario_tick(self) -> None:
        with SessionLocal() as db:
            s = get(db)
            if s["status"] != "running" or (s["next_at"] and s["next_at"] > datetime.now(timezone.utc)):
                return
            idx, ctx = s["step"], dict(s["ctx"] or {})
        title, fn = STEPS[idx]
        once = bool(ctx.pop("_once", False))
        self.source = "demo-control"
        t0 = time.time()
        try:
            msg = getattr(self, fn)(ctx)
            ok = True
        except Exception as e:                                                           # noqa: BLE001 - a step failure stops the scenario with a plain reason
            msg, ok = str(e)[:300], False
            log.warning("scenario step %s failed: %s", idx + 1, e)
        finally:
            self.source = "demo-live"
        entry = {"n": idx + 1, "title": title, "ok": ok, "skipped": bool(ctx.pop("_skipped", False)) if ok else False, "message": msg, "seconds": round(time.time() - t0, 1),
                 "at": datetime.now(timezone.utc).isoformat()}
        with SessionLocal() as db:
            cur = get(db)
            if cur["status"] == "idle":                                                  # reset while the step ran: leave the cleared state alone
                return
            logs = [*cur["log"], entry]
            if not ok:
                _set(db, status="failed", log=logs, ctx=ctx)
            else:
                last = idx + 1 >= len(STEPS)
                delay = 0.0 if last else STEP_DELAY_S / max(1, int(cur["speed"]))
                nxt = datetime.fromtimestamp(time.time() + delay, tz=timezone.utc)
                cols = {"step": idx + 1, "log": logs, "ctx": ctx, "next_at": nxt}
                if last:
                    cols["status"] = "done"
                elif once:
                    cols["status"] = "paused"                                    # a manual "next step" runs one step and stays paused
                _set(db, **cols)                                                 # status is otherwise left alone: a pause pressed during this step must not be overwritten
            db.commit()

    # ------------------------------------------------------------------ the steps (ctx carries ids between them; every value in it is JSON-safe)
    def _avail(self, item_id: int) -> Decimal:
        """Free stock in the finished-goods warehouse, which is where the order is fulfilled from."""
        return self.q("""SELECT COALESCE(sum(b.qty_physical - b.qty_reserved - b.qty_blocked), 0) AS a FROM stock_balances b JOIN warehouses w ON w.id = b.warehouse_id
                          WHERE b.item_id = :i AND w.code = 'FG-WH'""", i=item_id)[0]["a"]

    SCENARIO_ITEM = "FG-DEMO-GIFTSET"

    def ensure_product(self) -> dict:
        """The scenario sells one dedicated demo product, created once through the real master-data, quality and BOM screens' APIs. Stock of the seeded catalogue is
        deliberately huge (25,000 orders of history), so a product with a small, honest stock position is what lets the story be true: order > stock -> make it ->
        a material is short -> buy it. Its bill of materials needs more of one low-stock raw material than is on hand, so each run really has to purchase."""
        row = self.q("""SELECT i.id, i.item_code, b.id AS bom_id, b.output_qty FROM items i JOIN boms b ON b.output_item_id = i.id AND b.status = 'ACTIVE' WHERE i.item_code = :c LIMIT 1""", c=self.SCENARIO_ITEM)
        if row:
            return row[0]
        tmpl = self.q("SELECT category_id, uom_id, hsn_code, gst_rate, shelf_life_days FROM items WHERE kind = 'FG' AND is_active ORDER BY id LIMIT 1")[0]
        item = self.q("SELECT id FROM items WHERE item_code = :c", c=self.SCENARIO_ITEM)
        if item:
            item_id = item[0]["id"]
        else:
            item_id = self.call("admin", "post", "/api/masters/items", json={
                "item_code": self.SCENARIO_ITEM, "name": "Mintree Gift Set (Demo)", "kind": "FG", "category_id": tmpl["category_id"], "uom_id": tmpl["uom_id"], "hsn_code": tmpl["hsn_code"],
                "gst_rate": str(tmpl["gst_rate"] if tmpl["gst_rate"] is not None else 18), "shelf_life_days": tmpl["shelf_life_days"] or 730, "is_batch_tracked": True})["id"]
        if not self.q("SELECT 1 FROM quality_specs WHERE item_id = :i AND stage = 'FINISHED' AND status = 'ACTIVE'", i=item_id):
            self.call("qa", "post", "/api/quality/specs", json={"item_id": item_id, "stage": "FINISHED", "tests": [
                {"test_name": "Appearance", "is_pass_fail": True, "method": "Visual"}, {"test_name": "pH", "unit": "", "min_value": "4.5", "max_value": "6.5", "method": "pH meter"}]})
        low = self.q("""SELECT i.id, COALESCE(sum(b.qty_physical - b.qty_reserved - b.qty_blocked), 0) AS av FROM items i LEFT JOIN stock_balances b ON b.item_id = i.id
                         WHERE i.item_code = 'RM-GLYCERIN' GROUP BY i.id""")[0]
        water = self.q("SELECT id FROM items WHERE item_code = 'RM-DIWATER'")[0]["id"]
        need = int(low["av"] * Decimal("1.3")) + 10                               # more than is on hand: the first batch must buy glycerin
        bom = self.call("pm", "post", "/api/production/boms/manual", json={"output_item_id": item_id, "output_qty": "100", "lines": [{"item_id": low["id"], "qty": str(need)}, {"item_id": water, "qty": "40"}]})
        self.call("pm2", "post", f"/api/production/boms/{bom['id']}/approve")
        return {"id": item_id, "item_code": self.SCENARIO_ITEM, "bom_id": bom["id"], "output_qty": Decimal(100)}

    def s_order(self, ctx: dict) -> str:
        it = self.ensure_product()
        av = max(Decimal(0), self._avail(it["id"]))
        cust = self.q("SELECT id, name FROM customers WHERE customer_type = 'B2B' AND is_active AND COALESCE(billing_address, '') <> '' ORDER BY (gstin IS NOT NULL) DESC, credit_limit DESC NULLS FIRST, random() LIMIT 1")[0]
        qty = int(av) + 24                                       # always more than is on hand, so the next steps genuinely have to make some
        wh = self.q("SELECT id FROM warehouses WHERE code = 'FG-WH'")[0]["id"]
        o = self.call("disp", "post", "/api/dispatch/orders", headers=self.key("sc-so"), json={
            "customer_id": cust["id"], "warehouse_id": wh, "channel": "B2B", "external_ref": self._order_ref("SCN"),
            "lines": [{"item_id": it["id"], "qty": str(qty), "unit_price": "899.00"}]})
        o = self.call("disp", "post", f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": o["version"]})
        ctx.update(order_id=o["id"], order_no=o["number"], item_id=it["id"], item_code=it["item_code"], bom_id=it["bom_id"], batch=str(it["output_qty"]), qty=qty, on_hand=str(av), customer=cust["name"])
        return f"{o['number']}: {qty} x {it['item_code']} for {cust['name']} (only {int(av)} on hand)"

    def s_dashboard(self, ctx: dict) -> str:
        o = self.call("disp", "get", f"/api/dispatch/orders/{ctx['order_id']}")
        open_n = self.q("SELECT count(*) AS n FROM sales_orders WHERE status IN ('CONFIRMED','ALLOCATED','PACKED')")[0]["n"]
        dash = self.call("disp", "get", "/api/dispatch/dashboard")
        if o["status"] != "CONFIRMED" or dash is None:
            raise RuntimeError(f"expected the order to be confirmed and visible, found {o['status']}")
        return f"{ctx['order_no']} is {o['status']} and counted among {open_n} open orders on the dashboard"

    def s_inventory(self, ctx: dict) -> str:
        av = self._avail(ctx["item_id"])
        short = Decimal(ctx["qty"]) - av
        if short <= 0:
            ctx["_skipped"] = True
            ctx["prod_id"] = None
            return f"{av} on hand covers the order; no production needed"
        size = Decimal(ctx["batch"])
        planned = size * math.ceil(short / size)
        wh = self.q("SELECT id FROM warehouses WHERE code = 'RM-WH'")[0]["id"]
        p = self.call("pm", "post", "/api/production/orders", json={"bom_id": ctx["bom_id"], "planned_qty": str(planned), "warehouse_id": wh, "planned_date": time.strftime("%Y-%m-%d")})
        av_rm = self.call("pm", "get", f"/api/production/orders/{p['id']}/availability")
        lines = av_rm.get("lines") or av_rm.get("materials") or []
        ctx.update(prod_id=p["id"], prod_no=p["number"], planned=str(planned), shortages=[{"item_id": ln["item_id"], "short": ln["short"]} for ln in lines if Decimal(ln["short"]) > 0])
        return f"short by {short} units: production order {p['number']} planned for {planned}; {len(ctx['shortages'])} material(s) short"

    def s_pr(self, ctx: dict) -> str:
        if not ctx.get("shortages"):
            ctx["_skipped"] = True
            return "all materials are in stock: no purchase needed"
        items = [{"item_id": s["item_id"], "qty": str((Decimal(s["short"]) * Decimal("1.05")).quantize(Decimal("1"), rounding="ROUND_UP"))} for s in ctx["shortages"]]
        pr = self.call("buyer", "post", "/api/procurement/purchase-requests", json={"items": items, "required_by": time.strftime("%Y-%m-%d", time.localtime(time.time() + 5 * 86400)), "notes": f"Demo scenario: materials for {ctx['prod_no']}"})
        pr = self.call("buyer", "post", f"/api/procurement/purchase-requests/{pr['id']}/submit", json={"expected_version": pr["version"]})
        ctx.update(pr_id=pr["id"], pr_no=pr["number"])
        return f"purchase request {pr['number']} for {len(items)} material(s) submitted"

    def s_po(self, ctx: dict) -> str:
        if not ctx.get("pr_id"):
            ctx["_skipped"] = True
            return "no purchase request, so no purchase order"
        pr = self.q("SELECT version FROM purchase_requests WHERE id = :i", i=ctx["pr_id"])[0]
        self.call("admin", "post", f"/api/procurement/purchase-requests/{ctx['pr_id']}/approve", json={"expected_version": pr["version"]})
        lines = self.q("SELECT item_id, qty FROM purchase_request_items WHERE pr_id = :p ORDER BY line_no", p=ctx["pr_id"])
        sup = self.q("SELECT id FROM suppliers WHERE approval_status = 'APPROVED' AND is_active ORDER BY random() LIMIT 1")[0]["id"]
        items = []
        for ln in lines:
            last = self.q("SELECT unit_price FROM purchase_order_items WHERE item_id = :i ORDER BY id DESC LIMIT 1", i=ln["item_id"])
            items.append({"item_id": ln["item_id"], "qty": str(ln["qty"]), "unit_price": str(last[0]["unit_price"]) if last else "100.00"})
        po = self.call("buyer", "post", "/api/procurement/purchase-orders", json={"supplier_id": sup, "pr_id": ctx["pr_id"], "expected_date": time.strftime("%Y-%m-%d", time.localtime(time.time() + 3 * 86400)),
                                                                                    "notes": f"Demo scenario for {ctx['prod_no']}", "items": items})
        po = self.call("buyer", "post", f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]})
        po = self.call("admin", "post", f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]})
        for second in ("mgmt", "acct", "pm"):                                             # a large order needs a second, different approver
            if po.get("status") == "APPROVED":
                break
            try:
                po = self.call(second, "post", f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]})
            except RuntimeError:
                continue
        if po.get("status") != "APPROVED":
            raise RuntimeError(f"purchase order {po['number']} is still waiting for a second approver")
        ctx.update(po_id=po["id"], po_no=po["number"])
        return f"request approved; purchase order {po['number']} issued and approved"

    def s_receive(self, ctx: dict) -> str:
        if not ctx.get("po_id"):
            ctx["_skipped"] = True
            return "nothing was ordered, so nothing to receive"
        ctx["t_receive"] = str(self.q("SELECT now() AS t")[0]["t"])
        out = self.receipt(ctx["po_id"])
        return f"{out}: stock and the supplier history are updated; incoming QC inspections opened"

    def s_incoming_qc(self, ctx: dict) -> str:
        if not ctx.get("po_id"):
            ctx["_skipped"] = True
            return "no incoming materials to inspect"
        rows = self.q("""SELECT i.id, i.number, b.item_id FROM inspections i JOIN inventory_batches b ON b.id = i.batch_id
                          WHERE i.status <> 'DECIDED' AND b.created_at >= CAST(:t AS timestamptz) ORDER BY i.id""", t=ctx["t_receive"])
        if not rows:
            return "the received materials need no inspection (no specification applies)"
        for r in rows:
            self._record_results(r)
            self.call("qa", "post", f"/api/quality/inspections/{r['id']}/decide", headers=self.key("sc-in"), json={"decision": "RELEASE"})
        return f"{len(rows)} incoming inspection(s) passed; the batches are released and now count as available"

    def s_produce_start(self, ctx: dict) -> str:
        if not ctx.get("prod_id"):
            ctx["_skipped"] = True
            return "no production needed"
        o = self.call("pm", "get", f"/api/production/orders/{ctx['prod_id']}")
        self.call("pm", "post", f"/api/production/orders/{ctx['prod_id']}/release", headers=self.key("sc-rel"), json={"expected_version": o["version"]})
        self.call("pm", "post", f"/api/production/orders/{ctx['prod_id']}/issue", headers=self.key("sc-iss"))
        return f"{ctx['prod_no']} released; materials reserved and issued (raw-material stock falls)"

    def s_produce_done(self, ctx: dict) -> str:
        if not ctx.get("prod_id"):
            ctx["_skipped"] = True
            return "no production needed"
        last = ""
        for _ in range(20):
            last = self.production(oid=ctx["prod_id"])
            if "completed;" in last:
                break
        else:
            raise RuntimeError("production did not complete: " + last)
        r = self.q("SELECT output_batch_id, actual_qty FROM production_orders WHERE id = :o", o=ctx["prod_id"])[0]
        ctx.update(fg_batch=r["output_batch_id"], produced=str(r["actual_qty"]))
        return f"{ctx['prod_no']} completed: {r['actual_qty']} produced into a new finished-goods batch (held in QC)"

    def s_fg_qc(self, ctx: dict) -> str:
        if not ctx.get("fg_batch"):
            ctx["_skipped"] = True
            return "no new batch to test"
        ins = self.q("SELECT id, number FROM inspections WHERE batch_id = :b AND status <> 'DECIDED' ORDER BY id DESC LIMIT 1", b=ctx["fg_batch"])
        if not ins:
            raise RuntimeError("the finished batch has no open inspection")
        n = self._record_results({"id": ins[0]["id"], "item_id": ctx["item_id"]})
        ctx["insp_id"] = ins[0]["id"]
        return f"{n} test results recorded on {ins[0]['number']}; all within specification"

    def s_release(self, ctx: dict) -> str:
        if not ctx.get("insp_id"):
            ctx["_skipped"] = True
            return "no batch to release"
        self.call("qa", "post", f"/api/quality/inspections/{ctx['insp_id']}/decide", headers=self.key("sc-rl"), json={"decision": "RELEASE"})
        src = self.q("SELECT warehouse_id, qty_physical - qty_reserved - qty_blocked AS av FROM stock_balances WHERE batch_id = :b AND qty_physical > 0 ORDER BY id LIMIT 1", b=ctx["fg_batch"])[0]
        fg_wh = self.q("SELECT id FROM warehouses WHERE code = 'FG-WH'")[0]["id"]
        if src["warehouse_id"] != fg_wh and src["av"] > 0:                                # finished goods are moved from the production floor to the finished-goods warehouse
            self.call("stores", "post", "/api/inventory/transfers", headers=self.key("sc-tr"), json={
                "item_id": ctx["item_id"], "batch_id": ctx["fg_batch"], "from_warehouse_id": src["warehouse_id"], "to_warehouse_id": fg_wh, "qty": str(src["av"]),
                "source_id": int(time.time() * 1000) % 2_000_000_000})
            return f"batch released by a second person (segregation of duties) and {src['av']} units moved to the finished-goods warehouse: available stock"
        return f"batch released by a second person (segregation of duties); {ctx['produced']} units are now available stock"

    def s_dispatch(self, ctx: dict) -> str:
        self.draft_invoice(ctx["order_id"])                                    # review the invoice first: a draft posts nothing
        out = self.post_invoice()                                              # validate, then post: the goods ship and the GST invoice is issued
        inv = self.q("SELECT id, number, total, shipment_id FROM sales_invoices WHERE so_id = :o ORDER BY id DESC LIMIT 1", o=ctx["order_id"])[0]
        ctx.update(invoice_id=inv["id"], invoice_no=inv["number"], invoice_total=str(inv["total"]), shipment_id=inv["shipment_id"])
        return out

    def s_deliver(self, ctx: dict) -> str:
        s = self.q("SELECT awb FROM shipments WHERE id = :s", s=ctx["shipment_id"])[0]
        self._courier_event(s["awb"], "DELIVERED")
        st = self.q("SELECT status FROM shipments WHERE id = :s", s=ctx["shipment_id"])[0]["status"]
        if st != "DELIVERED":
            raise RuntimeError(f"shipment is {st}, expected DELIVERED")
        r = self.u("acct").get(f"/api/dispatch/invoices/{ctx['invoice_id']}/pdf")
        if r.status_code != 200 or "pdf" not in r.headers.get("content-type", ""):
            raise RuntimeError(f"invoice PDF not available ({r.status_code})")
        return f"shipment {s['awb']} delivered; invoice {ctx['invoice_no']} PDF is available ({len(r.content) // 1024 or 1} KB)"

    def s_payment(self, ctx: dict) -> str:
        inv = self.q("SELECT customer_id, total - amount_paid - credited_amount AS due FROM sales_invoices WHERE id = :i", i=ctx["invoice_id"])[0]
        if inv["due"] <= 0:
            return f"invoice {ctx['invoice_no']} is already paid"
        self.call("acct", "post", "/api/accounts/payments", headers=self.key("sc-pay"), json={
            "direction": "RECEIVED", "party_id": inv["customer_id"], "amount": str(inv["due"]), "payment_date": time.strftime("%Y-%m-%d"), "method": "BANK_TRANSFER",
            "bank_account_code": "1100", "reference": f"demo-scenario {ctx['order_no']}", "allocations": [{"invoice_id": ctx["invoice_id"], "amount": str(inv["due"])}]})
        ctx["paid"] = str(inv["due"])
        return f"payment of ₹{inv['due']} received and allocated to {ctx['invoice_no']}; outstanding balance is now zero"

    def s_verify(self, ctx: dict) -> str:
        inv = self.q("SELECT status, total, amount_paid FROM sales_invoices WHERE id = :i", i=ctx["invoice_id"])[0]
        o = self.q("SELECT status FROM sales_orders WHERE id = :o", o=ctx["order_id"])[0]["status"]
        bal = self.q("SELECT COALESCE(sum(debit),0) AS d, COALESCE(sum(credit),0) AS c FROM journal_lines")[0]
        if inv["status"] != "PAID" or inv["amount_paid"] != inv["total"]:
            raise RuntimeError(f"invoice is {inv['status']}, expected PAID")
        if bal["d"] != bal["c"]:
            raise RuntimeError("the ledger is out of balance")
        summary = self.call("admin", "get", "/api/analytics/management-summary")
        self.emit("kpi.invalidate", {"scope": ["all"]})
        return (f"order {ctx['order_no']} is {o}, invoice {ctx['invoice_no']} is {inv['status']} (₹{inv['total']}), the ledger balances and the Overview "
                f"shows receivables of ₹{summary['sales']['receivable_outstanding']}")
