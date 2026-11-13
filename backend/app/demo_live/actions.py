"""Demo Control Centre actions. Every one calls the REAL HTTP API as a seeded demo user (the same routes, validation, permissions, idempotency and audit as a person
using the screens), so the resulting records, ledger entries and activity are genuine. Nothing here writes business tables directly.

Each method returns a short plain-English result, or raises: the engine reports the failure to the presenter and carries on.
Activity produced here is labelled "Demo" (the engine sends X-Mintree-Source; see core/activity.py)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.clock import business_today
from app.core.config import get_settings

PLATFORMS = ("AMAZON", "FLIPKART", "MYNTRA", "NYKAA")


def _today() -> str:
    return business_today().isoformat()


class Actions:
    """Mixed into `Live` (engine.py), which provides call / q / key / emit / u / rng / n."""

    # ------------------------------------------------------------------ helpers
    def _fg_with_stock(self, qty: int):
        """A finished good (and the warehouse holding it) the order can really be allocated from: free, QC-released, unexpired stock."""
        rows = self.q("""SELECT i.id, i.item_code, w.id AS wh, sum(b.qty_physical - b.qty_reserved - b.qty_blocked) AS av
                           FROM items i JOIN stock_balances b ON b.item_id = i.id JOIN warehouses w ON w.id = b.warehouse_id
                           LEFT JOIN inventory_batches ib ON ib.id = b.batch_id
                          WHERE i.kind = 'FG' AND i.is_active AND i.item_code <> 'FG-DEMO-GIFTSET'
                            AND (b.batch_id IS NULL OR (ib.qc_status = 'RELEASED' AND (ib.expiry_date IS NULL OR ib.expiry_date > business_today())))
                          GROUP BY i.id, i.item_code, w.id HAVING sum(b.qty_physical - b.qty_reserved - b.qty_blocked) >= :q ORDER BY random() LIMIT 1""", q=qty)
        if not rows:
            raise RuntimeError(f"no finished-goods item has {qty} units available to allocate; run a production batch and release it first")
        return rows[0]

    def _price(self, code: str) -> Decimal:
        from scripts.demo.catalogue import CATALOGUE, GST
        for c, _n, price, _x in CATALOGUE:
            if c == code:
                return (Decimal(price) / GST).quantize(Decimal("0.01"))
        return Decimal("500.00")

    def _order_ref(self, kind: str) -> str:
        return f"DEMO-{kind}-{int(time.time())}-{self.n}"

    # ------------------------------------------------------------------ sales
    def b2b_order(self) -> str:
        cust = self.q("SELECT id, name FROM customers WHERE customer_type = 'B2B' AND is_active AND COALESCE(billing_address, '') <> '' ORDER BY (gstin IS NOT NULL) DESC, random() LIMIT 1")
        if not cust:
            raise RuntimeError("no B2B customer exists")
        qty = self.rng.choice([12, 24, 36, 48])
        item = self._fg_with_stock(qty)
        o = self.call("disp", "post", "/api/dispatch/orders", headers=self.key("so"), json={
            "customer_id": cust[0]["id"], "warehouse_id": item["wh"], "channel": "B2B", "external_ref": self._order_ref("B2B"),
            "lines": [{"item_id": item["id"], "qty": str(qty), "unit_price": str(self._price(item["item_code"]))}]})
        o = self.call("disp", "post", f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": o["version"]})
        return f"B2B order {o['number']} for {cust[0]['name']} created and confirmed"

    def confirm_order(self) -> str:
        r = self.q("SELECT id, version, number FROM sales_orders WHERE status = 'DRAFT' AND created_at > now() - interval '3 days' ORDER BY id DESC LIMIT 1")
        if not r:
            raise RuntimeError("no draft order is waiting for confirmation")
        o = self.call("disp", "post", f"/api/dispatch/orders/{r[0]['id']}/confirm", json={"expected_version": r[0]["version"]})
        return f"order {o['number']} confirmed"

    def cancel_order(self) -> str:
        r = self.q("""SELECT id, number FROM sales_orders WHERE status IN ('DRAFT','CONFIRMED','ALLOCATED','PACKED')
                         AND (external_ref LIKE 'DEMO-%' OR external_ref LIKE 'LIVE-%') ORDER BY id DESC LIMIT 1""")
        if not r:
            raise RuntimeError("no demo order is open to cancel")
        self.call("disp", "post", f"/api/dispatch/orders/{r[0]['id']}/cancel", headers=self.key("cx"), json={"reason": "Demo: customer cancelled the order"})
        return f"order {r[0]['number']} cancelled; any reserved stock was released"

    # ------------------------------------------------------------------ procurement
    def purchase_request(self) -> str:
        low = self.call("admin", "get", "/api/analytics/management-summary").get("low_stock") or []
        code = low[0]["item_code"] if low else "RM-ALOE"
        iid = self.q("SELECT id FROM items WHERE item_code = :c", c=code)[0]["id"]
        pr = self.call("buyer", "post", "/api/procurement/purchase-requests", json={
            "items": [{"item_id": iid, "qty": "120"}], "required_by": (business_today() + timedelta(days=7)).isoformat(), "notes": "Demo: replenish low stock"})
        pr = self.call("buyer", "post", f"/api/procurement/purchase-requests/{pr['id']}/submit", json={"expected_version": pr["version"]})
        return f"purchase request {pr['number']} for {code} submitted for approval"

    def approve_pr(self) -> str:
        r = self.q("SELECT id, version, number FROM purchase_requests WHERE status = 'SUBMITTED' ORDER BY id DESC LIMIT 1")
        if not r:
            raise RuntimeError("no purchase request is waiting for approval; create one first")
        pr = self.call("admin", "post", f"/api/procurement/purchase-requests/{r[0]['id']}/approve", json={"expected_version": r[0]["version"]})
        return f"purchase request {pr['number']} approved"

    def issue_po(self) -> str:
        r = self.q("""SELECT pr.id, pr.number FROM purchase_requests pr WHERE pr.status = 'APPROVED'
                         AND NOT EXISTS (SELECT 1 FROM purchase_orders p WHERE p.pr_id = pr.id) ORDER BY pr.id DESC LIMIT 1""")
        if not r:
            return self._raise_po()
        lines = self.q("SELECT item_id, qty FROM purchase_request_items WHERE pr_id = :p ORDER BY line_no", p=r[0]["id"])
        sup = self.q("SELECT id FROM suppliers WHERE approval_status = 'APPROVED' AND is_active ORDER BY random() LIMIT 1")[0]["id"]
        items = []
        for ln in lines:
            last = self.q("SELECT unit_price FROM purchase_order_items WHERE item_id = :i ORDER BY id DESC LIMIT 1", i=ln["item_id"])
            items.append({"item_id": ln["item_id"], "qty": str(ln["qty"]), "unit_price": str(last[0]["unit_price"]) if last else "100.00"})
        po = self.call("buyer", "post", "/api/procurement/purchase-orders", json={
            "supplier_id": sup, "pr_id": r[0]["id"], "expected_date": (business_today() + timedelta(days=5)).isoformat(), "notes": "Demo: from approved request", "items": items})
        po = self.call("buyer", "post", f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]})
        po = self.call("admin", "post", f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]})
        return f"purchase order {po['number']} issued from {r[0]['number']}"

    def receive_materials(self) -> str:
        po = self.q("""SELECT p.id FROM purchase_orders p WHERE p.status IN ('APPROVED','PARTIALLY_RECEIVED')
                          AND EXISTS (SELECT 1 FROM purchase_order_items i WHERE i.po_id = p.id AND i.qty_received < i.qty_ordered) ORDER BY p.id DESC LIMIT 1""")
        return self.receipt(po[0]["id"] if po else None)

    def delayed_delivery(self) -> str:
        """A SIMULATED supplier message: it raises a clearly-labelled alert for buyers. No order, date or stock is changed."""
        po = self.q("""SELECT p.number, s.name FROM purchase_orders p JOIN suppliers s ON s.id = p.supplier_id
                        WHERE p.status IN ('APPROVED','PARTIALLY_RECEIVED') ORDER BY p.id DESC LIMIT 1""")
        if not po:
            raise RuntimeError("no open purchase order to report a delay on")
        from app.core.db import SessionLocal
        from app.notifications.service import notify
        with SessionLocal() as db:
            notify(db, kind="demo.supplier_delay", title=f"DEMO · {po[0]['name']} reports a 3-day delay on {po[0]['number']}", body="Simulated supplier message; no order or date was changed.",
                   permission="procurement.po:read", priority="HIGH")
            db.commit()
        return f"simulated delay alert raised for {po[0]['number']} (labelled DEMO)"

    # ------------------------------------------------------------------ production
    def start_production(self) -> str:
        wh = self.q("SELECT id FROM warehouses WHERE code = 'RM-WH'")[0]["id"]
        bom = self.q("""SELECT b.id, b.output_qty, i.item_code FROM boms b JOIN items i ON i.id = b.output_item_id
                          WHERE b.status = 'ACTIVE' AND i.kind = 'FG' AND i.item_code <> 'FG-DEMO-GIFTSET'
                            AND NOT EXISTS (SELECT 1 FROM bom_lines l WHERE l.bom_id = b.id AND l.qty > COALESCE(
                                  (SELECT sum(sb.qty_available) FROM stock_balances sb LEFT JOIN inventory_batches ib ON ib.id = sb.batch_id
                                    WHERE sb.item_id = l.item_id AND sb.warehouse_id = :wh AND (sb.batch_id IS NULL OR ib.qc_status = 'RELEASED')), 0))
                          ORDER BY random() LIMIT 1""", wh=wh)
        if not bom:
            raise RuntimeError("no bill of materials has all its materials in stock right now; receive materials first")
        o = self.call("pm", "post", "/api/production/orders", json={"bom_id": bom[0]["id"], "planned_qty": str(bom[0]["output_qty"]), "warehouse_id": wh, "planned_date": _today()})
        self.call("pm", "post", f"/api/production/orders/{o['id']}/release", headers=self.key("rel"), json={"expected_version": o["version"]})
        self.call("pm", "post", f"/api/production/orders/{o['id']}/issue", headers=self.key("iss"))
        return f"production order {o['number']} ({bom[0]['item_code']}) released and materials issued"

    def production_step(self) -> str:
        return self.production(newest=True)

    def complete_production(self) -> str:
        last = ""
        for _ in range(16):
            last = self.production(newest=True)
            if "completed;" in last:
                return last
        return last

    # ------------------------------------------------------------------ quality
    def _open_inspection(self):
        r = self.q("""SELECT i.id, i.number, i.batch_id, b.item_id FROM inspections i JOIN inventory_batches b ON b.id = i.batch_id
                       WHERE i.status <> 'DECIDED' ORDER BY (b.qc_status = 'QUARANTINE') DESC, i.id DESC LIMIT 1""")
        if not r:
            raise RuntimeError("no inspection is waiting; complete a production batch or receive materials first")
        return r[0]

    def _tests(self, item_id: int) -> list[dict]:
        specs = self.call("qa", "get", "/api/quality/specs", params={"item_id": item_id})
        specs = specs["items"] if isinstance(specs, dict) else specs
        return [t for s in specs for t in s.get("tests", [])]

    def _record_results(self, ins, *, fail_one: bool = False) -> int:
        tests = self._tests(ins["item_id"])
        for n, t in enumerate(tests):
            if t.get("pass_fail"):
                body = {"test_name": t["test_name"], "passed": not (fail_one and n == 0)}
            else:
                lo, hi = Decimal(t["min"] or "0"), Decimal(t["max"] or t["min"] or "0")
                body = {"test_name": t["test_name"], "value": str(hi + (hi - lo or Decimal(1)) if fail_one and n == 0 else (lo + hi) / 2)}
            self.call("qi", "post", f"/api/quality/inspections/{ins['id']}/results", json=body)
        return len(tests)

    def record_inspection(self) -> str:
        ins = self._open_inspection()
        n = self._record_results(ins)
        return f"{n} test results recorded on inspection {ins['number']} (decision still pending)"

    def pass_inspection(self) -> str:
        ins = self._open_inspection()
        self._record_results(ins)
        self.call("qa", "post", f"/api/quality/inspections/{ins['id']}/decide", headers=self.key("dec"), json={"decision": "RELEASE"})
        return f"inspection {ins['number']} passed; batch released to available stock"

    def fail_inspection(self) -> str:
        ins = self._open_inspection()
        self._record_results(ins, fail_one=True)
        self.call("qa", "post", f"/api/quality/inspections/{ins['id']}/decide", headers=self.key("dec"),
                  json={"decision": "HOLD", "reason": "Demo: a measured value is outside its limits; batch held for review"})
        return f"inspection {ins['number']} failed a test; batch held and removed from available stock"

    def release_held_batch(self) -> str:
        """A held batch is re-tested through a NEW inspection and released only if every mandatory test passes (and a different person releases it)."""
        b = self.q("""SELECT b.id, b.item_id, b.batch_no, COALESCE(i.stage, 'FG') AS stage FROM inventory_batches b
                        LEFT JOIN LATERAL (SELECT stage FROM inspections WHERE batch_id = b.id ORDER BY id DESC LIMIT 1) i ON true
                       WHERE b.qc_status = 'HOLD' ORDER BY b.id DESC LIMIT 1""")
        if not b:
            raise RuntimeError("no batch is on hold; fail an inspection first")
        ins = self.call("qa", "post", "/api/quality/inspections", json={"batch_id": b[0]["id"], "stage": b[0]["stage"]})
        self._record_results({"id": ins["id"], "item_id": b[0]["item_id"]})
        self.call("qa", "post", f"/api/quality/inspections/{ins['id']}/decide", headers=self.key("dec"), json={"decision": "RELEASE"})
        return f"held batch {b[0]['batch_no']} re-tested and released after the required checks"

    # ------------------------------------------------------------------ accounts
    def credit_note(self) -> str:
        """A customer return is received and restocked; accepting it posts the credit note and reverses revenue (the existing returns workflow)."""
        r = self.q("""SELECT l.id AS line_id, s.so_id FROM shipment_lines l JOIN shipments s ON s.id = l.shipment_id JOIN sales_orders o ON o.id = s.so_id
                       WHERE o.status IN ('DISPATCHED','DELIVERED','PARTIALLY_DISPATCHED') AND l.qty >= 2
                         AND NOT EXISTS (SELECT 1 FROM sales_return_lines rl WHERE rl.shipment_line_id = l.id) ORDER BY l.id DESC LIMIT 1""")
        if not r:
            raise RuntimeError("no shipped order is available to return; dispatch an order first")
        ret = self.call("disp", "post", f"/api/dispatch/returns?order_id={r[0]['so_id']}", headers=self.key("ret"),
                        json={"reason": "Demo: customer returned two damaged units", "lines": [{"shipment_line_id": r[0]["line_id"], "qty": "1"}]})
        self.call("disp", "post", f"/api/dispatch/returns/{ret['id']}/receive", headers=self.key("rcv"))
        out = self.call("disp", "post", f"/api/dispatch/returns/{ret['id']}/decide", headers=self.key("dcd"), json={"decision": "RESTOCK"})
        cn = (out.get("credit_note") or {}).get("number", "")
        return f"return {ret['number']} accepted; credit note {cn} posted"

    # ------------------------------------------------------------------ dispatch
    def _order_to_dispatch(self):
        r = self.q("""SELECT id, number FROM sales_orders WHERE status IN ('DRAFT','CONFIRMED','ALLOCATED','PACKED')
                         AND created_at > now() - interval '3 days' ORDER BY COALESCE(external_ref LIKE 'DEMO-%' OR external_ref LIKE 'LIVE-%', false) DESC, id DESC LIMIT 1""")
        if not r:
            raise RuntimeError("no open order to dispatch; create an order first")
        return r[0]

    def _prepare(self, oid: int) -> dict:
        """Confirm, allocate and pack an open order (each through its own real endpoint); returns the packed order."""
        o = self.call("disp", "get", f"/api/dispatch/orders/{oid}")
        if o["status"] == "DRAFT":
            o = self.call("disp", "post", f"/api/dispatch/orders/{oid}/confirm", json={"expected_version": o["version"]})
        if o["status"] == "CONFIRMED":
            o = self.call("disp", "post", f"/api/dispatch/orders/{oid}/allocate", headers=self.key("alc"), json={"expected_version": o["version"]})
        if o["status"] == "ALLOCATED":
            o = self.call("disp", "post", f"/api/dispatch/orders/{oid}/pack", json={"expected_version": o["version"], "cartons": 1, "weight_kg": "1.200"})
        return o

    def dispatch_order(self, order_id: int | None = None) -> str:
        r = {"id": order_id} if order_id else self._order_to_dispatch()
        o = self._prepare(r["id"])
        d = self.call("disp", "post", f"/api/dispatch/orders/{r['id']}/dispatch", headers=self.key("dsp"), json={"expected_version": o["version"], "courier": "fake"})
        self.call("disp", "post", f"/api/dispatch/shipments/{d['shipment_id']}/book", ok=(200, 201))
        return f"order {d['number']} dispatched; invoice {d['invoice']['number']} posted; shipment booked with a simulated courier"

    def draft_invoice(self, order_id: int | None = None) -> str:
        """Create a DRAFT invoice for an order that is ready to ship. A draft is a preview: it posts nothing to the ledger and uses no invoice number."""
        oid = order_id or self._order_to_dispatch()["id"]
        o = self._prepare(oid)
        d = self.call("disp", "post", f"/api/dispatch/orders/{oid}/invoice-drafts", json={})
        return f"invoice draft {d['reference']} for {d['order_number']} (₹{d['total']}) created; nothing is posted until it is validated and posted"

    def post_invoice(self, draft_id: int | None = None) -> str:
        """Validate (if needed) and POST a draft: the goods ship and the gapless GST invoice is issued through the real dispatch posting."""
        r = self.q("SELECT id, status FROM invoice_drafts WHERE status IN ('DRAFT','VALIDATED') " + ("AND id = :i " if draft_id else "") + "ORDER BY id DESC LIMIT 1", i=draft_id)
        if not r:
            raise RuntimeError("no invoice draft is waiting; create one first")
        did = r[0]["id"]
        if r[0]["status"] == "DRAFT":
            self.call("disp", "post", f"/api/dispatch/invoice-drafts/{did}/validate", json={})
        p = self.call("disp", "post", f"/api/dispatch/invoice-drafts/{did}/post", headers=self.key("inv"), json={"courier": "fake"})
        self.call("disp", "post", f"/api/dispatch/shipments/{p['shipment_id']}/book", ok=(200, 201))
        return f"invoice {p['invoice']['number']} posted (₹{p['invoice']['total']}); order is {p['order_status'].replace('_', ' ').lower()}; shipment booked"

    def invoice_pdf(self) -> str:
        inv = self.q("SELECT id, number FROM sales_invoices ORDER BY id DESC LIMIT 1")
        if not inv:
            raise RuntimeError("no invoice exists yet")
        r = self.u("acct").get(f"/api/dispatch/invoices/{inv[0]['id']}/pdf")
        if r.status_code != 200 or r.content[:4] != b"%PDF":
            raise RuntimeError(f"invoice PDF not available ({r.status_code})")
        return f"PDF for invoice {inv[0]['number']} generated ({max(1, len(r.content) // 1024)} KB, Mintree brand layout); open it from Sales invoices"

    def _shipment(self, states: tuple[str, ...]):
        r = self.q("SELECT id, awb, status FROM shipments WHERE status = ANY(:s) AND awb IS NOT NULL ORDER BY id DESC LIMIT 1", s=list(states))
        if not r:
            raise RuntimeError("no suitable shipment; dispatch an order first")
        return r[0]

    def _courier_event(self, awb: str, etype: str) -> None:
        raw = json.dumps({"event_id": f"{awb}-{etype}-{uuid.uuid4().hex[:6]}", "awb": awb, "event_type": etype, "occurred_at": datetime.now(timezone.utc).isoformat()}).encode()
        sig = hmac.new(get_settings().courier_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        r = self.u("disp").post("/api/dispatch/webhooks/courier/fake", content=raw, headers={"X-Signature": sig, "Content-Type": "application/json", "X-Mintree-Source": self.source})
        if r.status_code not in (200, 201):
            raise RuntimeError(f"courier event {etype} -> {r.status_code} {r.text[:120]}")

    def tracking_update(self) -> str:
        s = self._shipment(("BOOKED", "PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY"))
        nxt = {"BOOKED": "PICKED_UP", "PICKED_UP": "IN_TRANSIT", "IN_TRANSIT": "OUT_FOR_DELIVERY", "OUT_FOR_DELIVERY": "OUT_FOR_DELIVERY"}[s["status"]]
        self._courier_event(s["awb"], nxt)
        return f"tracking update on {s['awb']}: {nxt.replace('_', ' ').title()}"

    def deliver(self) -> str:
        s = self._shipment(("BOOKED", "PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY"))
        self._courier_event(s["awb"], "DELIVERED")
        return f"shipment {s['awb']} delivered (delivery event recorded; the order is marked delivered)"

    def delivery_exception(self) -> str:
        s = self._shipment(("IN_TRANSIT", "OUT_FOR_DELIVERY"))
        self.call("disp", "post", f"/api/dispatch/shipments/{s['id']}/delivery-attempts", headers=self.key("att"),
                  json={"outcome": "FAILED", "reason": "Demo: recipient unavailable; courier will retry"})
        return f"delivery attempt failed on {s['awb']}; a high-priority alert was raised"

    # ------------------------------------------------------------------ marketplace
    def _account(self, platform: str | None):
        r = self.q("SELECT id, platform FROM marketplace_accounts WHERE status = 'ACTIVE' AND (CAST(:p AS text) IS NULL OR platform = :p) ORDER BY random() LIMIT 1", p=platform)
        if not r:
            raise RuntimeError(f"no active {platform or 'marketplace'} account")
        return r[0]

    def _market_order(self, acct, *, sku: str | None = None, order_id: str | None = None, qty: int = 2) -> tuple[str, dict]:
        if sku is None:
            m = self.q("""SELECT m.platform_sku, i.item_code FROM sku_mappings m JOIN items i ON i.id = m.item_id WHERE m.account_id = :a AND i.kind = 'FG' ORDER BY random() LIMIT 1""", a=acct["id"])
            if not m:
                raise RuntimeError("this account has no SKU mappings")
            sku, price = m[0]["platform_sku"], self._price(m[0]["item_code"])
        else:
            price = Decimal("499.00")
        oid = order_id or f"{acct['platform'][:3]}-DEMO-{int(time.time())}-{self.n}"
        fn, ln = self.rng.choice([("Aarav", "Sharma"), ("Diya", "Iyer"), ("Kabir", "Khan"), ("Anaya", "Patel"), ("Ira", "Nair")])
        return oid, {"order_id": oid, "order_date": _today(), "customer_name": f"{fn} {ln}, {self.rng.choice(['Pune', 'Delhi', 'Kochi', 'Jaipur'])}",
                     "shipping_address": "DEMO address, India", "items": [{"sku": sku, "qty": str(qty), "unit_price": str(price)}]}

    def _import(self, acct, body: dict) -> dict:
        return self.call("sales", "post", f"/api/ecommerce/accounts/{acct['id']}/orders", json={"orders": [body]})

    def marketplace(self, platform: str) -> str:
        acct = self._account(platform)
        oid, body = self._market_order(acct)
        r = self._import(acct, body)
        return f"{platform.title()} demo order {oid} imported ({r['created']} created, {r['duplicate']} duplicate, {r['exception']} exception)"

    def marketplace_sync(self) -> str:
        done = []
        for p in PLATFORMS:
            try:
                done.append(self.marketplace(p).split(" ")[0])
            except Exception:                                      # noqa: BLE001 - an account without mappings is skipped, not fatal
                continue
        if not done:
            raise RuntimeError("no marketplace account could be synchronised")
        return f"demo synchronisation imported one order each from {', '.join(done)}"

    def failed_import(self) -> str:
        acct = self._account(None)
        oid, body = self._market_order(acct, sku=f"UNMAPPED-DEMO-{self.n}")
        r = self._import(acct, body)
        return f"{acct['platform'].title()} order {oid} could not be mapped and waits in the exception queue ({r['exception']} exception)"

    def retry_import(self) -> str:
        e = self.q("""SELECT e.id, e.account_id, i.platform_sku FROM external_orders e JOIN external_order_items i ON i.external_order_pk = e.id
                       WHERE e.status = 'EXCEPTION' AND e.external_order_id LIKE '%-DEMO-%' ORDER BY e.id DESC LIMIT 1""")
        if not e:
            raise RuntimeError("no failed demo import to retry; run 'Failed import' first")
        fg = self.q("SELECT id FROM items WHERE kind = 'FG' AND is_active ORDER BY random() LIMIT 1")[0]["id"]
        self.call("disp", "put", f"/api/ecommerce/accounts/{e[0]['account_id']}/mappings", json={"platform_sku": e[0]["platform_sku"], "item_id": fg})
        r = self.call("disp", "post", f"/api/ecommerce/external-orders/{e[0]['id']}/retry")
        return f"retry after mapping the SKU: order status is now {(r or {}).get('status', 'updated')}"

    def duplicate_import(self) -> str:
        e = self.q("""SELECT e.account_id, e.payload FROM external_orders e WHERE e.status = 'SO_CREATED' AND e.external_order_id LIKE '%-DEMO-%' ORDER BY e.id DESC LIMIT 1""")
        if not e:
            raise RuntimeError("no imported demo order to repeat; import one first")
        acct, p = {"id": e[0]["account_id"]}, e[0]["payload"]
        body = {"order_id": p["order_id"], "order_date": p.get("order_date"), "customer_name": p.get("customer_name"), "shipping_address": p.get("shipping_address"),
                "items": [{"sku": i["sku"], "qty": str(i["qty"]), "unit_price": str(i["unit_price"])} for i in p["items"]]}
        r = self._import(acct, body)
        return f"the same marketplace order was delivered again: {r['duplicate']} duplicate ignored, {r['created']} new order created"

    # ------------------------------------------------------------------ tier 1: recall, expiry, rework, return, e-invoice
    def start_recall(self) -> str:
        b = self.q("""SELECT b.id, b.batch_no FROM inventory_batches b
                       WHERE b.qc_status = 'RELEASED' AND EXISTS (SELECT 1 FROM shipment_lines sl WHERE sl.batch_id = b.id)
                         AND NOT EXISTS (SELECT 1 FROM recall_batches rb JOIN recall_cases c ON c.id = rb.case_id WHERE rb.batch_id = b.id AND c.status = 'OPEN')
                       ORDER BY (SELECT count(DISTINCT sh.so_id) FROM shipment_lines sl2 JOIN shipments sh ON sh.id = sl2.shipment_id WHERE sl2.batch_id = b.id) ASC, b.id DESC LIMIT 1""")      # a small, followable recall for the demo
        if not b:
            raise RuntimeError("no released batch that has been shipped; dispatch an order first")
        c = self.call("qa", "post", "/api/quality/recalls", json={"batch_id": b[0]["id"], "reason": "Demo: a customer reported an off smell on this batch", "severity": "MAJOR"})
        return f"recall {c['number']} opened on {b[0]['batch_no']}: {c['summary']['batches']} batch(es) held, {c['summary']['customers']} customer(s) to contact"

    def resolve_recall(self) -> str:
        r = self.q("SELECT id, number FROM recall_cases WHERE status = 'OPEN' ORDER BY id DESC LIMIT 1")
        if not r:
            raise RuntimeError("no open recall; start one first")
        cid = r[0]["id"]
        d = self.call("qa", "get", f"/api/quality/recalls/{cid}")
        if d["customers"]:
            self.call("qa", "post", f"/api/quality/recalls/{cid}/customers", json={"disposition": "NOTIFIED", "note": "Demo: customers called"})
            self.call("qa", "post", f"/api/quality/recalls/{cid}/customers", json={"disposition": "CLEARED", "note": "Demo: customers confirmed no stock left", "only_pending": False})
        for b in d["batches"]:
            if b["held_by_recall"] and b["status_now"] == "HOLD":
                self.call("qa", "post", f"/api/quality/recalls/{cid}/batches/{b['batch_id']}/release", json={"note": "Demo: retest passed; released"})
        out = self.call("qa", "post", f"/api/quality/recalls/{cid}/close", json={"note": "Demo: all customers contacted and batches resolved"})
        return f"recall {out['number']} resolved and closed: batches released, customers cleared"

    def expiry_sweep(self) -> str:
        r = self.call("qa", "post", "/api/inventory/expiry/sweep", json={})
        return f"expiry sweep: {r['held_count']} expired batch(es) held; {r['expiring_within_30_days']} expiring within 30 days"

    def rework_start(self) -> str:
        b = self.q("""SELECT b.id, b.batch_no, sum(s.qty_blocked) AS blocked FROM inventory_batches b JOIN stock_balances s ON s.batch_id = b.id JOIN items i ON i.id = b.item_id
                       WHERE b.qc_status IN ('HOLD','REJECTED') AND i.kind IN ('FG','BULK') AND s.qty_blocked > 0
                         AND NOT EXISTS (SELECT 1 FROM rework_orders r WHERE r.source_batch_id = b.id AND r.status = 'STARTED')
                       GROUP BY b.id ORDER BY b.id DESC LIMIT 1""")
        if not b:
            raise RuntimeError("no held or rejected batch with stock; fail an inspection first")
        qty = min(Decimal(b[0]["blocked"]), Decimal("10"))
        r = self.call("pm", "post", "/api/production/rework", json={"batch_id": b[0]["id"], "qty": str(qty), "reason": "Demo: pH out of range", "process": "Re-blend with citric acid and re-filter"})
        return f"rework {r['number']} started on {b[0]['batch_no']}: {r['input_qty']} units taken out of the held stock"

    def rework_complete(self) -> str:
        r = self.q("SELECT id, input_qty FROM rework_orders WHERE status = 'STARTED' ORDER BY id DESC LIMIT 1")
        if not r:
            raise RuntimeError("no rework in progress; start one first")
        out = (Decimal(r[0]["input_qty"]) - Decimal(1)).quantize(Decimal("0.001"))
        d = self.call("pm", "post", f"/api/production/rework/{r[0]['id']}/complete", json={"output_qty": str(max(out, Decimal("0.001")))})
        return f"rework {d['number']} completed: {d['output_qty']} good units in new batch {d['output_batch_no']}, waiting in QC; loss {d['loss_qty']}"

    def return_material(self) -> str:
        orders = self.call("pm", "get", "/api/production/orders", params={"status": "IN_PROGRESS", "page_size": 20})["items"]
        for o in orders:
            mats = self.call("pm", "get", f"/api/production/orders/{o['id']}/returnable")["materials"]
            m = next((x for x in mats if Decimal(x["returnable"]) > 0), None)
            if m:
                qty = (Decimal(m["returnable"]) / 4).quantize(Decimal("0.001"))
                if qty <= 0:
                    continue
                r = self.call("pm", "post", f"/api/production/orders/{o['id']}/return-materials", json={"lines": [{"item_id": m["item_id"], "qty": str(qty)}], "reason": "Demo: line stopped early, drum not opened"})
                return f"{qty} of {m['item_code']} returned to stock from {o['number']} at cost (₹{r['returned_cost']})"
        raise RuntimeError("no production order in progress with material to return; start production first")

    def einvoice(self) -> str:
        r = self.q("""SELECT i.id, i.number FROM sales_invoices i JOIN customers c ON c.id = i.customer_id LEFT JOIN einvoices e ON e.invoice_id = i.id
                       WHERE e.id IS NULL AND c.gstin IS NOT NULL AND c.gstin <> '' ORDER BY i.id DESC LIMIT 1""")
        if not r:
            raise RuntimeError("no invoice to a GST-registered customer without an e-invoice; dispatch an order to one first")
        e = self.call("acct", "post", f"/api/accounts/einvoices/{r[0]['id']}/generate")
        return f"e-invoice for {r[0]['number']}: IRN {e['irn'][:16]}… ({e['mode']}: not registered with any government portal)"

    # ------------------------------------------------------------------ registry
    KINDS = {
        "b2b_order": "b2b_order", "confirm_order": "confirm_order", "cancel_order": "cancel_order",
        "purchase_request": "purchase_request", "approve_pr": "approve_pr", "issue_po": "issue_po", "receive_materials": "receive_materials", "delayed_delivery": "delayed_delivery",
        "start_production": "start_production", "production_step": "production_step", "complete_production": "complete_production",
        "record_inspection": "record_inspection", "pass_inspection": "pass_inspection", "fail_inspection": "fail_inspection", "release_held_batch": "release_held_batch",
        "payment": "payment", "credit_note": "credit_note", "start_recall": "start_recall", "resolve_recall": "resolve_recall", "expiry_sweep": "expiry_sweep", "rework_start": "rework_start", "rework_complete": "rework_complete", "return_material": "return_material", "einvoice": "einvoice", "draft_invoice": "draft_invoice", "post_invoice": "post_invoice", "invoice_pdf": "invoice_pdf", "dispatch_order": "dispatch_order", "tracking_update": "tracking_update", "deliver": "deliver", "delivery_exception": "delivery_exception",
        "market_amazon": "AMAZON", "market_flipkart": "FLIPKART", "market_myntra": "MYNTRA", "market_nykaa": "NYKAA",
        "market_sync": "marketplace_sync", "failed_import": "failed_import", "retry_import": "retry_import", "duplicate_import": "duplicate_import",
    }

    def run_control(self, kind: str) -> str:
        target = self.KINDS[kind]
        if kind.startswith("market_") and kind != "market_sync":
            return self.marketplace(target)
        return getattr(self, target)()
