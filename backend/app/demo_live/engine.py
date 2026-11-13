"""The Live Mode loop. Run it as its own process:  python -m app.demo_live   (docker: a `live` service; see docs/DEMO_RUNBOOK.md)

Every action goes through the real API as a seeded demo user. A failed action is logged and skipped; it never stops the loop, and the global kill
switch (the `enabled` / `paused` columns, or stopping the process) takes effect within one second."""
from __future__ import annotations

import logging
import random
import time
from decimal import Decimal

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.demo_live import guard, store
from app.demo_live.actions import Actions
from app.demo_live.scenario import Scenario

log = logging.getLogger("mintree.demo_live")

QUALITY_ALERT_MIN_GAP_S = 600                        # rate limit for automatic out-of-limit readings; triggers bypass it
WEIGHTS = [("order", 40), ("payment", 25), ("receipt", 15), ("production", 20)]


def status_label() -> str:
    """For /api/ready: 'off' when the flag is off (healthy), else whether the loop has beaten recently."""
    if guard.refusal() is not None:
        return "off"
    with SessionLocal() as db:
        hb = db.execute(text("SELECT EXTRACT(EPOCH FROM now() - heartbeat_at) FROM demo_live_state WHERE id = 1")).scalar()
    if hb is None:
        raise RuntimeError("Live Mode is enabled but its worker has never started")
    if hb > 15:
        raise RuntimeError(f"Live Mode worker last beat {int(hb)}s ago")
    return "running"


class Live(Actions, Scenario):
    def __init__(self) -> None:
        from fastapi.testclient import TestClient
        from app.main import app
        self._TestClient, self._app = TestClient, app
        self.clients: dict[str, object] = {}
        self.rng = random.Random(get_settings().demo_live_seed)
        self.n = 0
        self.counters: dict[str, int] = {}
        self.last_alert = 0.0
        self.source = "demo-live"                              # "demo-control" while a presenter-requested action runs; labels the resulting activity "Demo"
        with SessionLocal() as db:
            self.counters = dict(store.get_state(db)["counters"] or {})
        self.n = int(self.counters.get("total", 0))

    # ---- plumbing --------------------------------------------------------------------------------------------------------------------
    def u(self, key: str):
        c = self.clients.get(key)
        if c is None:
            s = get_settings()
            c = self._TestClient(self._app)
            r = c.post("/api/auth/login", json={"email": f"{s.demo_live_user_prefix}{key}@example.com", "password": s.demo_live_password})
            if r.status_code != 200:
                raise RuntimeError(f"login as {key} failed: {r.status_code}")
            c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            self.clients[key] = c
        return c

    def key(self, label: str) -> dict:
        """Idempotency-Key: unique per action and per run, so a retried HTTP call replays instead of duplicating."""
        return {"Idempotency-Key": f"demo-live-{self.n}-{label}-{int(time.time())}"}

    def call(self, user: str, method: str, path: str, ok=(200, 201), **kw):
        self.u(user).headers["X-Mintree-Source"] = self.source
        r = getattr(self.u(user), method)(path, **kw)
        if r.status_code == 401:                               # session expired: log in again once
            self.clients.pop(user, None)
            r = getattr(self.u(user), method)(path, **kw)
        if r.status_code not in ok:
            raise RuntimeError(f"{method.upper()} {path} -> {r.status_code} {r.text[:200]}")
        return r.json() if r.content else None

    def q(self, sql: str, **p):
        with SessionLocal() as db:
            return db.execute(text(sql), p).mappings().all()

    def emit(self, type_: str, payload: dict, permission: str | None = None) -> None:
        with SessionLocal() as db:
            store.emit(db, type_, {**payload, "source": "demo-live"}, permission)
            db.commit()

    def done(self, kind: str) -> None:
        self.n += 1
        self.counters[kind] = self.counters.get(kind, 0) + 1
        self.counters["total"] = self.n

    # ---- actions: each returns a short description, or raises to be skipped --------------------------------------------------------
    def order(self, big: bool = False) -> str:
        accounts = self.call("disp", "get", "/api/ecommerce/accounts")
        accounts = accounts["items"] if isinstance(accounts, dict) else accounts
        acct = self.rng.choice(accounts)
        plat = str(acct.get("platform") or acct.get("code") or acct.get("name"))
        from scripts.demo.catalogue import CATALOGUE, GST
        code, _, price, _ = self.rng.choice(CATALOGUE)
        qty = self.rng.choice([30, 40, 60]) if big else self.rng.choice([1, 1, 2, 2, 3])
        oid = f"LIVE-{plat[:3]}-{int(time.time())}-{self.n}"
        names = [("Aarav", "Sharma"), ("Diya", "Iyer"), ("Kabir", "Khan"), ("Anaya", "Patel"), ("Ira", "Nair"), ("Myra", "Reddy")]
        fn, ln = self.rng.choice(names)
        body = {"order_id": oid, "order_date": time.strftime("%Y-%m-%d"), "customer_name": f"{fn} {ln}, {self.rng.choice(['Pune', 'Delhi', 'Kochi', 'Jaipur', 'Mysuru'])}",
                "items": [{"sku": f"MT-{plat[:3]}-{code[3:]}", "qty": str(qty), "unit_price": str((Decimal(price) / GST).quantize(Decimal('0.01')))}]}
        r = self.call("sales", "post", f"/api/ecommerce/accounts/{acct['id']}/orders", json={"orders": [body]})
        self.emit("order.created", {"external_id": oid, "platform": plat, "item": code, "qty": qty}, "ecommerce.order:read")
        self.emit("kpi.invalidate", {"scope": ["overview", "orders"]})
        return f"{plat.title()} order {oid} imported ({r.get('created', 0)} created, {r.get('duplicate', 0)} duplicate, {r.get('exception', 0)} exception)"

    def payment(self, big: bool = False) -> str:
        rows = self.q("""SELECT id, customer_id, total - amount_paid - credited_amount AS due FROM sales_invoices
                          WHERE status IN ('OPEN','PARTIAL') AND total - amount_paid - credited_amount > 0 ORDER BY """ + ("due DESC" if big else "random()") + " LIMIT 1")
        if not rows:
            raise RuntimeError("no open invoice to pay")
        inv = rows[0]
        amount = inv["due"] if big else (inv["due"] * Decimal(self.rng.choice(["0.5", "1", "1"]))).quantize(Decimal("0.01"))
        self.call("acct", "post", "/api/accounts/payments", headers=self.key("pay"), json={
            "direction": "RECEIVED", "party_id": inv["customer_id"], "amount": str(amount), "payment_date": time.strftime("%Y-%m-%d"),
            "method": self.rng.choice(["UPI", "BANK_TRANSFER"]), "bank_account_code": "1100", "reference": "demo-live", "allocations": [{"invoice_id": inv["id"], "amount": str(amount)}]})
        self.emit("payment.received", {"invoice_id": inv["id"], "amount": str(amount), "big": big}, "accounts.payment:read")
        self.emit("kpi.invalidate", {"scope": ["overview", "accounts"]})
        return f"payment {amount} on invoice {inv['id']}"

    def receipt(self, pid: int | None = None) -> str:
        if pid is None:
            po = self.q("""SELECT p.id FROM purchase_orders p WHERE p.status IN ('APPROVED','PARTIALLY_RECEIVED')
                            AND EXISTS (SELECT 1 FROM purchase_order_items i WHERE i.po_id = p.id AND i.qty_received < i.qty_ordered) ORDER BY random() LIMIT 1""")
            if not po:
                return self._raise_po()
            pid = po[0]["id"]
        lines = self.q("SELECT id, qty_ordered - qty_received AS open FROM purchase_order_items WHERE po_id = :p AND qty_received < qty_ordered ORDER BY id", p=pid)
        wh = self.q("SELECT id FROM warehouses ORDER BY id LIMIT 1")[0]["id"]
        d = time.strftime("%Y-%m-%d")
        end = time.strftime("%Y-%m-%d", time.localtime(time.time() + 500 * 86400))
        r = self.call("stores", "post", "/api/procurement/goods-receipts", headers=self.key("grn"), json={
            "po_id": pid, "warehouse_id": wh, "delivery_note_no": f"DL-{self.n}-{int(time.time())}",
            "lines": [{"po_item_id": ln["id"], "qty": str(ln["open"]), "supplier_lot_no": f"LIVE-{self.n}-{i}", "mfg_date": d, "expiry_date": end} for i, ln in enumerate(lines, 1)]})
        self.emit("stock.changed", {"po_id": pid, "receipt": r.get("number") if isinstance(r, dict) else None}, "inventory.stock:read")
        self.emit("kpi.invalidate", {"scope": ["overview", "inventory", "purchase"]})
        return f"receipt against PO {pid}"

    def _raise_po(self) -> str:
        """No PO is waiting: re-order a past one (same supplier and lines) through the real create, submit, approve flow so the next cycle can receive it."""
        src = self.q("SELECT id, supplier_id FROM purchase_orders WHERE status IN ('RECEIVED','COMPLETED') ORDER BY random() LIMIT 1")
        if not src:
            raise RuntimeError("no purchase order history to re-order from")
        lines = self.q("SELECT item_id, qty_ordered, unit_price FROM purchase_order_items WHERE po_id = :p ORDER BY line_no", p=src[0]["id"])
        po = self.call("buyer", "post", "/api/procurement/purchase-orders", json={
            "supplier_id": src[0]["supplier_id"], "expected_date": time.strftime("%Y-%m-%d", time.localtime(time.time() + 5 * 86400)), "notes": "demo-live reorder",
            "items": [{"item_id": ln["item_id"], "qty": str(ln["qty_ordered"]), "unit_price": str(ln["unit_price"])} for ln in lines]})
        po = self.call("buyer", "post", f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]})
        self.call("admin", "post", f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]})
        self.emit("kpi.invalidate", {"scope": ["overview", "purchase"]})
        return f"purchase order {po['number']} raised and approved"

    def production(self, newest: bool = False, oid: int | None = None) -> str:
        """Advance one batch by one step: release -> issue materials -> start stage -> complete stage ... -> complete order (mixing, filling, QC hold, released)."""
        rows = [{"id": oid}] if oid else self.q("SELECT id FROM production_orders WHERE status IN ('PLANNED','RELEASED','IN_PROGRESS') ORDER BY (status = 'IN_PROGRESS') DESC, id " + ("DESC" if newest else "") + " LIMIT 1")
        if not rows:
            raise RuntimeError("no production order to advance")
        oid = rows[0]["id"]
        o = self.call("pm", "get", f"/api/production/orders/{oid}")
        if o["status"] == "PLANNED":
            self.call("pm", "post", f"/api/production/orders/{oid}/release", headers=self.key("rel"), json={"expected_version": o["version"]})
            step = "released"
        elif o["status"] == "RELEASED" and not any(m["issued"] != "0.000" and Decimal(m["issued"]) > 0 for m in o["materials"]):
            self.call("pm", "post", f"/api/production/orders/{oid}/issue", headers=self.key("iss"))
            step = "materials issued"
        else:
            running = [s for s in o["stages"] if s["status"] == "IN_PROGRESS"]
            waiting = [s for s in o["stages"] if s["status"] in ("PENDING", "PLANNED", "NOT_STARTED")]
            if running:
                s = running[0]
                try:
                    self.call("op", "post", f"/api/production/orders/{oid}/stages/{s['seq']}/complete", json={})
                except RuntimeError as e:
                    if "process_check_failed" not in str(e):
                        raise
                    for prm in self.q("SELECT DISTINCT parameter FROM process_checks WHERE order_id = :o AND stage_seq = :s AND NOT passed", o=oid, s=s["seq"]) or [{"parameter": "Batch temperature (°C)"}]:
                        self.call("qi", "post", f"/api/production/orders/{oid}/stages/{s['seq']}/checks", json={      # the failing reading is re-checked, as the plant would, and now passes
                            "parameter": prm["parameter"], "value": "75", "min_value": "70", "max_value": "80", "observation": "Re-check after corrective action: within limits"})
                    self.call("op", "post", f"/api/production/orders/{oid}/stages/{s['seq']}/complete", json={})
                step = f"{s['name']} completed"
            elif waiting:
                s = waiting[0]
                self.call("op", "post", f"/api/production/orders/{oid}/stages/{s['seq']}/start", json={})
                step = f"{s['name']} started"
            else:
                qty = (Decimal(o["planned_qty"]) * Decimal("0.98")).quantize(Decimal("0.001"))
                self.call("pm", "post", f"/api/production/orders/{oid}/complete", headers=self.key("done"), json={"expected_version": o["version"], "actual_qty": str(qty), "wastage": []})
                step = "completed; finished goods in QC hold"
        self.emit("batch.stage", {"order_id": oid, "number": o["number"], "step": step}, "production.order:read")
        self.emit("kpi.invalidate", {"scope": ["overview", "production"]})
        return f"{o['number']}: {step}"

    def quality_alert(self) -> str:
        """One out-of-limit in-process reading (deterministic values, rate-limited): the real service raises a HIGH notification."""
        rows = self.q("""SELECT o.id, s.seq, o.number FROM production_orders o JOIN production_stages s ON s.order_id = o.id
                          WHERE o.status = 'IN_PROGRESS' AND s.status = 'IN_PROGRESS' ORDER BY o.id DESC LIMIT 1""")
        if not rows:
            raise RuntimeError("no stage in progress to take a reading on")
        r = rows[0]
        self.call("qi", "post", f"/api/production/orders/{r['id']}/stages/{r['seq']}/checks", json={
            "parameter": "Batch temperature (°C)", "value": "93.5", "min_value": "70", "max_value": "80",
            "observation": "Reading above the upper limit; holding the batch for review", "corrective_action": "Cooling applied; batch held for QA review"})
        self.last_alert = time.time()
        self.emit("quality.alert", {"order": r["number"], "parameter": "Batch temperature", "value": 93.5, "limit": "70-80"}, "production.order:read")
        self.emit("kpi.invalidate", {"scope": ["overview", "quality"]})
        return f"out-of-limit reading on {r['number']}"

    def low_stock(self) -> str:
        return self.order(big=True)

    # ---- the loop --------------------------------------------------------------------------------------------------------------------
    def run_trigger(self, kind: str) -> str:
        if kind in Actions.KINDS:
            return self.run_control(kind)
        return {"order": self.order, "quality_alert": self.quality_alert, "big_payment": lambda: self.payment(big=True), "low_stock": self.low_stock}[kind]()

    def pick(self) -> str:
        kinds, w = zip(*WEIGHTS)
        return self.rng.choices(kinds, w)[0]

    def step(self) -> str | None:
        s = get_settings()
        with SessionLocal() as db:
            trig = store.pop_trigger(db)
            db.commit()
        if trig:
            tid, kind = trig
            self.source = "demo-control"
            try:
                out = self.run_trigger(kind)
            except Exception as e:                       # noqa: BLE001
                out = f"failed: {e}"
            finally:
                self.source = "demo-live"
            with SessionLocal() as db:
                store.finish_trigger(db, tid, out)
                db.commit()
            self.done("trigger")
            return out
        if self.counters.get("total", 0) >= s.demo_live_max_created:
            return None                                   # volume cap reached: stay quiet until a reset
        kind = self.pick()
        if kind == "production" and time.time() - self.last_alert > QUALITY_ALERT_MIN_GAP_S and self.rng.random() < 0.15:
            kind = "quality_alert"
        out = getattr(self, kind)()
        self.done(kind)
        return out


def main() -> None:
    from app.core.logging import configure_logging
    configure_logging()
    guard.assert_safe()
    s = get_settings()
    live = Live()
    with SessionLocal() as db:
        store.set_state(db, enabled=True)
        db.commit()
    log.info("Demo Live Mode started (db=%s)", s.database_url.rsplit("/", 1)[-1])
    next_at = time.time() + 5
    while True:
        with SessionLocal() as db:
            st = store.get_state(db)
            if int((st["counters"] or {}).get("total", 0)) < live.n:          # the database was reset to the golden snapshot: start counting again, log in again
                live.counters, live.n, live.clients = {}, 0, {}
            store.beat(db, live.counters, acted=False)
            db.commit()
        try:
            live.scenario_tick()                                              # the scripted scenario runs whether or not the automatic mode is on
        except Exception as e:                                                # noqa: BLE001
            log.warning("scenario tick failed: %s", e)
        if not st["enabled"] or live.scenario_active():                       # while a scenario runs it is the only thing creating activity
            time.sleep(1)
            continue
        # A queued trigger runs at once, even while paused: the presenter asked for it explicitly.
        if not _has_trigger() and (st["paused"] or time.time() < next_at):
            time.sleep(1)
            continue
        try:
            out = live.step()
            if out:
                log.info("live: %s", out)
                with SessionLocal() as db:
                    store.beat(db, live.counters, acted=True)
                    db.commit()
        except Exception as e:                           # noqa: BLE001 - a bad action must never stop the show
            log.warning("live action skipped: %s", e)
        speed = max(1, int(st["speed"]))
        next_at = time.time() + live.rng.uniform(s.demo_live_interval_min_s, s.demo_live_interval_max_s) / speed


def _has_trigger() -> bool:
    with SessionLocal() as db:
        return bool(db.execute(text("SELECT 1 FROM demo_live_triggers WHERE done_at IS NULL LIMIT 1")).first())
