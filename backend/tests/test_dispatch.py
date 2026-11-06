"""Dispatch / order-to-cash: allocation, atomic dispatch posting (stock + COGS + GST invoice + shipment), courier booking and webhooks, returns."""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.dispatch import courier
from scripts.check_integrity import run_checks
from tests.conftest import mk_master
from tests.test_inventory_procurement import N
from tests.test_production import k, plant, stock_rm  # noqa: F401  (fixtures/helpers)


def integrity(db):
    bad = [c for c in run_checks(db.connection())["checks"] if not c["ok"]]
    assert not bad, bad


@pytest.fixture
def shop(plant, login):
    return {**plant, "disp": login("dispatch_manager"), "sales": login("sales_operator"), "acct": login("accounts_manager")}


def make_customer(credit_limit=None):
    from sqlalchemy.orm import Session as S
    from app.core.db import engine
    from app.masters.models import Customer
    with S(engine) as s:
        c = Customer(code=f"C-{uuid.uuid4().hex[:7].upper()}", name="Glow Retail", customer_type="B2B", credit_limit=credit_limit, state="MH", shipping_address="12 MG Road, Pune")
        s.add(c)
        s.commit()
        return c.id


def make_fg(plant, qty=100, rm_price=10, release=True):
    """Finished goods produced through the REAL chain (PO -> GR -> QC -> BOM -> order -> issue -> stages -> completion), so valuation + GL are genuine."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.core.db import engine
    from app.masters.models import Item, UnitOfMeasure
    m = mk_master("FGX")
    stock_rm(plant, m, qty, rm_price)
    with S(engine) as s:
        u = s.scalar(select(UnitOfMeasure).where(UnitOfMeasure.code == "PCS")).id
        fg = Item(item_code=f"FG-{uuid.uuid4().hex[:6].upper()}", name="Vitamin C Serum 30ml", kind="FG", uom_id=u, gst_rate=18, shelf_life_days=730)
        s.add(fg)
        s.commit()
        fg_id = fg.id
    bom = plant["pm"].post("/api/production/boms/manual", json={"output_item_id": fg_id, "output_qty": str(qty), "lines": [{"item_id": m["item_id"], "qty": str(qty)}]}).json()
    plant["pm2"].post(f"/api/production/boms/{bom['id']}/approve")
    o = plant["pm"].post("/api/production/orders", json={"bom_id": bom["id"], "planned_qty": str(qty), "warehouse_id": m["wh1"]}).json()
    plant["pm"].post(f"/api/production/orders/{o['id']}/release", headers=k(), json={"expected_version": 1})
    plant["pm"].post(f"/api/production/orders/{o['id']}/issue", headers=k())
    for seq in (1, 2, 3):
        plant["op"].post(f"/api/production/orders/{o['id']}/stages/{seq}/start", json={})
        plant["op"].post(f"/api/production/orders/{o['id']}/stages/{seq}/complete", json={})
    done = plant["pm"].post(f"/api/production/orders/{o['id']}/complete", headers=k(), json={"expected_version": 3, "actual_qty": str(qty)}).json()
    if release:
        assert plant["qm"].post(f"/api/inventory/batches/{done['output_batch_id']}/qc-decision", headers=k(), json={"decision": "RELEASED"}).status_code == 200
    return {"fg": fg_id, "wh": m["wh1"], "batch": done["output_batch_id"], "unit_cost": N(done["unit_cost"])}


def new_so(shop, f, cust, qty="30", price="25", ref=None, channel="B2B"):
    r = shop["disp"].post("/api/dispatch/orders", json={"customer_id": cust, "warehouse_id": f["wh"], "channel": channel, "external_ref": ref,
                                                        "lines": [{"item_id": f["fg"], "qty": qty, "unit_price": price}]})
    assert r.status_code == 201, r.text
    return r.json()


def advance(shop, o, to="PACKED"):
    v = o["version"]
    assert shop["disp"].post(f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": v}).status_code == 200
    r = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/allocate", headers=k(), json={"expected_version": v + 1})
    assert r.status_code == 200, r.text
    if to == "ALLOCATED":
        return r.json()
    p = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/pack", json={"expected_version": v + 2, "cartons": 2, "weight_kg": "3.5"})
    assert p.status_code == 200, p.text
    return p.json()


def sign(body: dict, secret="dev-courier-secret"):
    raw = json.dumps(body).encode()
    return raw, hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def hook(client, awb, event_id, etype, when="2026-11-06T10:00:00+00:00", secret="dev-courier-secret"):
    raw, sig = sign({"event_id": event_id, "awb": awb, "event_type": etype, "occurred_at": when}, secret)
    return client.post("/api/dispatch/webhooks/courier/fake", content=raw, headers={"X-Signature": sig, "Content-Type": "application/json"})


def test_order_to_cash_end_to_end(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    assert f["unit_cost"] == N("10.0000")
    o = new_so(shop, f, cust)                                         # 30 x 25 = 750 + 18% GST 135 = 885
    assert (N(o["subtotal"]), N(o["tax_total"]), N(o["total"])) == (750, 135, 885)
    packed = advance(shop, o)
    assert packed["status"] == "PACKED"
    d = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": packed["version"]})
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["status"] == "DISPATCHED" and body["invoice"]["number"].startswith("SI/") and N(body["invoice"]["total"]) == 885
    t = shop["acct"].get("/api/accounts/reports/trial-balance").json()
    bal = {a["code"]: N(a["balance"]) for a in t["accounts"]}
    assert t["balanced"] and bal["1200"] >= 885 and bal["4000"] <= -750 and bal["2200"] <= -135
    assert N(shop["stores"].get("/api/inventory/availability", params={"item_id": f["fg"]}).json()["total"]["physical"]) == 70
    # shipment + courier booking OUTSIDE a transaction; booking is idempotent
    sh = shop["disp"].get(f"/api/dispatch/shipments/{body['shipment_id']}").json()
    assert sh["status"] == "PENDING_BOOKING" and sh["lines"][0]["batch_id"] == f["batch"] and N(sh["lines"][0]["unit_cost"]) == N("10.0000")
    assert shop["disp"].post(f"/api/dispatch/shipments/{sh['id']}/book").json()["result"] == "booked"
    assert shop["disp"].post(f"/api/dispatch/shipments/{sh['id']}/book").json()["result"] == "skipped"
    sh = shop["disp"].get(f"/api/dispatch/shipments/{sh['id']}").json()
    assert sh["status"] == "BOOKED" and sh["awb"].startswith("FAKE")
    # webhooks: signed, idempotent, monotonic
    assert hook(shop["disp"], sh["awb"], "e1", "PICKED_UP", secret="wrong").status_code == 401
    assert hook(shop["disp"], sh["awb"], "e1", "PICKED_UP").json()["result"] == "processed"
    assert hook(shop["disp"], sh["awb"], "e1", "PICKED_UP").json()["result"] == "duplicate"
    assert hook(shop["disp"], sh["awb"], "e3", "IN_TRANSIT").json()["shipment_status"] == "IN_TRANSIT"
    assert hook(shop["disp"], sh["awb"], "e2", "PICKED_UP").json()["shipment_status"] == "IN_TRANSIT", "a late older event never moves the shipment backwards"
    assert hook(shop["disp"], sh["awb"], "e4", "DELIVERED").json()["shipment_status"] == "DELIVERED"
    assert hook(shop["disp"], "UNKNOWN", "e5", "DELIVERED").json()["result"] == "ignored"
    assert shop["disp"].get(f"/api/dispatch/orders/{o['id']}").json()["status"] == "DELIVERED"
    assert db.execute(text("SELECT count(*) FROM courier_events WHERE shipment_id=:s AND event_type='PICKED_UP'"), {"s": sh["id"]}).scalar() == 2   # e1 + late e2 recorded, e1 once
    # customer receipt settles the invoice
    pay = shop["acct"].post("/api/accounts/payments", headers=k(), json={"direction": "RECEIVED", "party_id": cust, "amount": "885", "payment_date": str(datetime.now().date()),
                                                                         "method": "BANK_TRANSFER", "bank_account_code": "1100", "allocations": [{"invoice_id": body["invoice"]["id"], "amount": "885"}]})
    assert pay.status_code == 201, pay.text
    assert shop["disp"].get(f"/api/dispatch/invoices/{body['invoice']['id']}").json()["status"] == "PAID"
    integrity(db)


def test_dispatch_is_idempotent_and_cannot_repeat(shop, db):
    f, cust = make_fg(shop, 50, 8), make_customer()
    o = new_so(shop, f, cust, qty="10")
    packed = advance(shop, o)
    key = k("disp")
    a = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=key, json={"expected_version": packed["version"]})
    b = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=key, json={"expected_version": packed["version"]})
    assert a.json() == b.json() and b.headers["idempotent-replayed"] == "true"
    assert shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": packed["version"] + 1}).json()["detail"]["code"] == "invalid_status"
    assert db.execute(text("SELECT count(*) FROM sales_invoices WHERE so_id=:o"), {"o": o["id"]}).scalar() == 1
    assert N(shop["stores"].get("/api/inventory/availability", params={"item_id": f["fg"]}).json()["total"]["physical"]) == 40
    integrity(db)


def test_allocation_rules_credit_limit_and_duplicate_marketplace_order(shop, db):
    f = make_fg(shop, 20, 10)
    cust = make_customer(credit_limit=1000)
    big = new_so(shop, f, cust, qty="50", price="30")                          # 50 x 30 x 1.18 = 1770 > 1000
    r = shop["disp"].post(f"/api/dispatch/orders/{big['id']}/confirm", json={"expected_version": 1})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "credit_limit_exceeded"
    cust2 = make_customer()
    o = new_so(shop, f, cust2, qty="50")                                       # only 20 in stock
    shop["disp"].post(f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": 1})
    a = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/allocate", headers=k(), json={"expected_version": 2})
    assert a.status_code == 409 and a.json()["detail"]["code"] == "insufficient_stock" and a.json()["detail"]["shortages"][0]["item_id"] == f["fg"]
    assert N(shop["stores"].get("/api/inventory/availability", params={"item_id": f["fg"]}).json()["total"]["reserved"]) == 0
    f2 = make_fg(shop, 20, 10, release=False)                                  # quarantined FG is not sellable
    o2 = new_so(shop, f2, cust2, qty="5")
    shop["disp"].post(f"/api/dispatch/orders/{o2['id']}/confirm", json={"expected_version": 1})
    assert shop["disp"].post(f"/api/dispatch/orders/{o2['id']}/allocate", headers=k(), json={"expected_version": 2}).json()["detail"]["code"] == "insufficient_stock"
    ref = f"AMZ-{uuid.uuid4().hex[:8]}"
    new_so(shop, f, cust2, qty="1", ref=ref, channel="MARKETPLACE")
    dup = shop["disp"].post("/api/dispatch/orders", json={"customer_id": cust2, "warehouse_id": f["wh"], "channel": "MARKETPLACE", "external_ref": ref,
                                                         "lines": [{"item_id": f["fg"], "qty": "1", "unit_price": "10"}]})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "duplicate_order"
    cancel = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/cancel", headers=k(), json={"reason": "customer changed mind"})
    assert cancel.json()["status"] == "CANCELLED"
    integrity(db)


def test_courier_booking_failure_is_recorded_retried_and_never_double_books(shop, db, monkeypatch):
    f, cust = make_fg(shop, 20, 10), make_customer()
    o = new_so(shop, f, cust, qty="5")
    d = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": advance(shop, o)["version"]}).json()
    sid = d["shipment_id"]

    class Flaky:
        name = "fake"
        calls = 0
        def book(self, **kw):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise ConnectionError("courier API down")
            return courier.Booking(awb=f"FAKE{int(kw['client_ref']):010d}", tracking_url="https://t.example/x")

    monkeypatch.setitem(courier._ADAPTERS, "fake", Flaky())
    with pytest.raises(ConnectionError):
        from contextlib import nullcontext
        from app.dispatch.service import book_shipment
        book_shipment(sid, lambda: nullcontext(db))
    s = shop["disp"].get(f"/api/dispatch/shipments/{sid}").json()
    assert s["status"] == "PENDING_BOOKING" and "ConnectionError" in s["last_error"] and s["awb"] is None
    assert shop["disp"].post(f"/api/dispatch/shipments/{sid}/book").json()["result"] == "booked"
    assert shop["disp"].post(f"/api/dispatch/shipments/{sid}/book").json()["result"] == "skipped"
    assert Flaky.calls == 2, "no extra courier call once booked"
    assert shop["disp"].get(f"/api/dispatch/shipments/{sid}").json()["attempts"] == 2


def test_returns_restock_and_reject_with_credit_notes(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    o = new_so(shop, f, cust, qty="20", price="25")                            # 500 + 90 tax = 590
    d = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": advance(shop, o)["version"]}).json()
    inv_id = d["invoice"]["id"]
    line = shop["disp"].get(f"/api/dispatch/shipments/{d['shipment_id']}").json()["lines"][0]
    bad = shop["disp"].post(f"/api/dispatch/returns?order_id={o['id']}", headers=k(), json={"reason": "damaged in transit", "lines": [{"shipment_line_id": line["id"], "qty": "21"}]})
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "return_exceeds_shipped"
    r1 = shop["disp"].post(f"/api/dispatch/returns?order_id={o['id']}", headers=k(), json={"reason": "damaged in transit", "lines": [{"shipment_line_id": line["id"], "qty": "5"}]}).json()
    assert shop["disp"].post(f"/api/dispatch/returns/{r1['id']}/decide", headers=k(), json={"decision": "RESTOCK"}).json()["detail"]["code"] == "invalid_status"   # not received yet
    assert shop["disp"].post(f"/api/dispatch/returns/{r1['id']}/receive", headers=k()).status_code == 200
    t = shop["stores"].get("/api/inventory/availability", params={"item_id": f["fg"]}).json()["total"]
    assert (N(t["physical"]), N(t["blocked"])) == (85, 5), "returned goods are physical but blocked until Quality decides"
    ok = shop["disp"].post(f"/api/dispatch/returns/{r1['id']}/decide", headers=k(), json={"decision": "RESTOCK"})
    assert ok.status_code == 200 and ok.json()["credit_note"]["number"].startswith("CN/") and N(ok.json()["credit_note"]["total"]) == N("147.50")   # 5 x 25 x 1.18
    t = shop["stores"].get("/api/inventory/availability", params={"item_id": f["fg"]}).json()["total"]
    assert (N(t["physical"]), N(t["blocked"]), N(t["available"])) == (85, 0, 85)
    assert N(shop["disp"].get(f"/api/dispatch/invoices/{inv_id}").json()["credited_amount"]) == N("147.50")
    r2 = shop["disp"].post(f"/api/dispatch/returns?order_id={o['id']}", headers=k(), json={"reason": "wrong shade", "lines": [{"shipment_line_id": line["id"], "qty": "3"}]}).json()
    shop["disp"].post(f"/api/dispatch/returns/{r2['id']}/receive", headers=k())
    rej = shop["disp"].post(f"/api/dispatch/returns/{r2['id']}/decide", headers=k(), json={"decision": "REJECT"})
    assert rej.json()["credit_note"]["number"] != ok.json()["credit_note"]["number"]
    t = shop["stores"].get("/api/inventory/availability", params={"item_id": f["fg"]}).json()["total"]
    assert (N(t["physical"]), N(t["blocked"]), N(t["available"])) == (88, 3, 85), "rejected goods stay blocked and never become sellable"
    pay = shop["acct"].post("/api/accounts/payments", headers=k(), json={"direction": "RECEIVED", "party_id": cust, "amount": "400", "payment_date": str(datetime.now().date()), "method": "UPI",
                                                                         "bank_account_code": "1100", "allocations": [{"invoice_id": inv_id, "amount": "400"}]})
    assert pay.json()["detail"]["code"] == "overpayment"    # 590 - 147.5 - 88.5 = 354 outstanding
    integrity(db)


def test_permissions(shop):
    assert shop["op"].get("/api/dispatch/orders").status_code == 403
    assert shop["sales"].post("/api/dispatch/orders/1/dispatch", headers=k(), json={"expected_version": 1}).status_code == 403
    assert shop["stores"].post("/api/dispatch/orders", json={}).status_code == 403
    assert shop["sales"].get("/api/dispatch/orders").status_code == 200
