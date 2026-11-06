"""Partial dispatch: one order, several shipments, one invoice per shipment; delivery attempts, proof of delivery, packages, close-short, returns per shipment."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_dispatch import advance, hook, integrity, make_customer, make_fg, new_so, shop  # noqa: F401
from tests.test_inventory_procurement import N
from tests.test_production import k, plant  # noqa: F401


def ship(shop, o, qty=None, version=None, lines=None, **kw):
    body = {"expected_version": version or o["version"], **kw}
    if qty is not None:
        body["lines"] = [{"so_item_id": o["items"][0]["id"], "qty": str(qty)}]
    if lines is not None:
        body["lines"] = lines
    return shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json=body)


def book(shop, sid):
    assert shop["disp"].post(f"/api/dispatch/shipments/{sid}/book").json()["result"] == "booked"
    return shop["disp"].get(f"/api/dispatch/shipments/{sid}").json()


@pytest.fixture
def two_shipments(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    o = new_so(shop, f, cust, qty="30", price="25")                    # 750 + 135 GST = 885
    packed = advance(shop, o)
    a = ship(shop, o, qty=12, version=packed["version"])
    assert a.status_code == 200, a.text
    return {"f": f, "cust": cust, "o": o, "a": a.json(), "db": db}


def test_two_shipments_two_invoices_sum_to_the_order(shop, two_shipments, db):
    f, o, a = two_shipments["f"], two_shipments["o"], two_shipments["a"]
    assert a["status"] == "PARTIALLY_DISPATCHED" and a["shipment_no"] == 1
    assert (N(a["invoice"]["total"])) == N("354.00")                                 # 12 x 25 = 300 + 18% = 54
    res = db.execute(text("SELECT status, qty FROM stock_reservations WHERE reference_type='sales_order' AND reference_id=:r"), {"r": str(o["id"])}).all()
    assert [(r[0], r[1]) for r in res if r[0] == "ACTIVE"] == [("ACTIVE", N("18"))]   # the rest stays reserved
    assert N(shop["stores"].get("/api/inventory/availability", params={"item_id": f["fg"]}).json()["total"]["physical"]) == 88
    b = ship(shop, o, version=a["version"])                                          # no lines = everything still unshipped
    assert b.status_code == 200, b.text
    b = b.json()
    assert b["status"] == "DISPATCHED" and b["shipment_no"] == 2 and N(b["invoice"]["total"]) == N("531.00")   # 18 x 25 = 450 + 81
    assert db.scalar(text("SELECT sum(total) FROM sales_invoices WHERE so_id=:o"), {"o": o["id"]}) == N("885.00")
    assert db.scalar(text("SELECT count(*) FROM shipments WHERE so_id=:o"), {"o": o["id"]}) == 2
    assert db.scalar(text("SELECT count(*) FROM stock_reservations WHERE reference_type='sales_order' AND reference_id=:r AND status='ACTIVE'"), {"r": str(o["id"])}) == 0
    assert N(shop["stores"].get("/api/inventory/availability", params={"item_id": f["fg"]}).json()["total"]["physical"]) == 70
    led = db.execute(text("SELECT source_type, sum(-qty_delta) FROM stock_ledger WHERE movement_type='DISPATCH' AND source_id IN (SELECT id FROM shipments WHERE so_id=:o) GROUP BY 1"), {"o": o["id"]}).all()
    assert [(r[0], r[1]) for r in led] == [("shipment", N("30"))]
    listing = shop["disp"].get(f"/api/dispatch/orders/{o['id']}/shipments").json()
    assert [(s["shipment_no"], N(s["qty"])) for s in listing["shipments"]] == [(1, 12), (2, 18)]
    assert shop["acct"].get("/api/accounts/reports/trial-balance").json()["balanced"] is True
    integrity(db)


def test_invoices_sum_exactly_when_rounding_would_drift(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    o = new_so(shop, f, cust, qty="3", price="33.33")
    assert (N(o["subtotal"]), N(o["tax_total"])) == (N("99.99"), N("18.00"))
    v = advance(shop, o)["version"]
    totals = []
    for _ in range(3):
        r = ship(shop, o, qty=1, version=v).json()
        v = r["version"]
        totals.append(N(r["invoice"]["total"]))
    assert totals[:2] == [N("39.33"), N("39.33")] and sum(totals) == N("117.99")   # last shipment takes the exact remainder: 39.33
    assert db.scalar(text("SELECT sum(subtotal) FROM sales_invoices WHERE so_id=:o"), {"o": o["id"]}) == N("99.99")
    assert db.scalar(text("SELECT sum(tax_total) FROM sales_invoices WHERE so_id=:o"), {"o": o["id"]}) == N("18.00")
    integrity(db)


def test_partial_dispatch_rules(shop, two_shipments, db):
    o, a = two_shipments["o"], two_shipments["a"]
    line = o["items"][0]["id"]
    over = ship(shop, o, qty=19, version=a["version"])
    assert over.status_code == 409 and over.json()["detail"]["code"] == "exceeds_remaining" and over.json()["detail"]["remaining"] == "18.000"
    assert ship(shop, o, qty=0, version=a["version"]).status_code == 422
    assert ship(shop, o, lines=[{"so_item_id": 99999999, "qty": "1"}], version=a["version"]).status_code == 422
    assert ship(shop, o, lines=[{"so_item_id": line, "qty": "1"}, {"so_item_id": line, "qty": "1"}], version=a["version"]).status_code == 422
    assert ship(shop, o, qty=1, version=a["version"] - 1).status_code == 409                   # stale version
    assert db.scalar(text("SELECT count(*) FROM shipments WHERE so_id=:o"), {"o": o["id"]}) == 1   # failures left nothing behind
    cancel = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/cancel", headers=k(), json={"reason": "customer changed mind"})
    assert cancel.status_code == 409 and cancel.json()["detail"]["code"] == "cannot_cancel"
    final = ship(shop, o, version=a["version"]).json()
    assert ship(shop, o, version=final["version"]).status_code == 409                          # nothing left / already DISPATCHED
    integrity(db)


def test_idempotent_replay_of_a_partial_dispatch(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    o = new_so(shop, f, cust, qty="10", price="10")
    v = advance(shop, o)["version"]
    h = k("idem")
    body = {"expected_version": v, "lines": [{"so_item_id": o["items"][0]["id"], "qty": "4"}]}
    r1 = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=h, json=body)
    r2 = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=h, json=body)
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    assert db.scalar(text("SELECT count(*) FROM shipments WHERE so_id=:o"), {"o": o["id"]}) == 1
    assert N(shop["stores"].get("/api/inventory/availability", params={"item_id": f["fg"]}).json()["total"]["physical"]) == 96


def test_order_is_delivered_only_when_every_shipment_is(shop, two_shipments, db):
    o, a = two_shipments["o"], two_shipments["a"]
    s1 = book(shop, a["shipment_id"])
    assert hook(shop["disp"], s1["awb"], "p1", "DELIVERED").json()["shipment_status"] == "DELIVERED"
    assert shop["disp"].get(f"/api/dispatch/orders/{o['id']}").json()["status"] == "PARTIALLY_DISPATCHED"      # more still to ship
    b = ship(shop, o, version=a["version"]).json()
    assert shop["disp"].get(f"/api/dispatch/orders/{o['id']}").json()["status"] == "DISPATCHED"                  # fully shipped, last one not delivered
    s2 = book(shop, b["shipment_id"])
    assert hook(shop["disp"], s2["awb"], "p2", "IN_TRANSIT").json()["shipment_status"] == "IN_TRANSIT"
    assert shop["disp"].get(f"/api/dispatch/orders/{o['id']}").json()["status"] == "DISPATCHED"
    assert hook(shop["disp"], s2["awb"], "p3", "DELIVERED").json()["shipment_status"] == "DELIVERED"
    assert shop["disp"].get(f"/api/dispatch/orders/{o['id']}").json()["status"] == "DELIVERED"
    att = db.execute(text("SELECT source, outcome FROM delivery_attempts WHERE shipment_id=:s"), {"s": a["shipment_id"]}).all()
    assert [(r[0], r[1]) for r in att] == [("COURIER", "DELIVERED")]                                               # webhook recorded the attempt
    integrity(db)


def test_close_short_releases_stock_and_completes_the_order(shop, two_shipments, db):
    f, o, a = two_shipments["f"], two_shipments["o"], two_shipments["a"]
    assert shop["disp"].post(f"/api/dispatch/orders/{o['id']}/close-short", headers=k(), json={"reason": "no reason"}).status_code == 200
    got = shop["disp"].get(f"/api/dispatch/orders/{o['id']}").json()
    assert got["status"] == "DISPATCHED"
    t = shop["stores"].get("/api/inventory/availability", params={"item_id": f["fg"]}).json()["total"]
    assert (N(t["physical"]), N(t["reserved"]), N(t["available"])) == (88, 0, 88)                                  # the 18 reserved units are free again
    assert db.scalar(text("SELECT short_close_reason FROM sales_orders WHERE id=:o"), {"o": o["id"]}) == "no reason"
    assert db.scalar(text("SELECT sum(total) FROM sales_invoices WHERE so_id=:o"), {"o": o["id"]}) == N("354.00")    # only what shipped is billed
    again = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/close-short", headers=k(), json={"reason": "again"})
    assert again.status_code == 409
    integrity(db)


def test_return_covers_one_shipment_and_credits_that_invoice(shop, two_shipments, db):
    o, a = two_shipments["o"], two_shipments["a"]
    b = ship(shop, o, version=a["version"]).json()
    lines = shop["disp"].get(f"/api/dispatch/orders/{o['id']}/shipment-lines").json()
    by_ship = {r["shipment_line_id"]: r for r in lines}
    sl = db.execute(text("SELECT sl.id, sl.shipment_id FROM shipment_lines sl JOIN shipments s ON s.id = sl.shipment_id WHERE s.so_id=:o ORDER BY s.shipment_no"), {"o": o["id"]}).all()
    assert len(sl) == 2 and len(by_ship) == 2
    mixed = shop["disp"].post("/api/dispatch/returns", headers=k("rt"), params={"order_id": o["id"]}, json={"reason": "mixed", "lines": [{"shipment_line_id": sl[0][0], "qty": "1"}, {"shipment_line_id": sl[1][0], "qty": "1"}]})
    assert mixed.status_code == 422 and "one shipment only" in mixed.text, mixed.text
    r = shop["disp"].post("/api/dispatch/returns", headers=k("rt"), params={"order_id": o["id"]}, json={"reason": "damaged in transit", "lines": [{"shipment_line_id": sl[1][0], "qty": "2"}]})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert shop["disp"].post(f"/api/dispatch/returns/{rid}/receive", headers=k("rc")).status_code == 200
    d = shop["disp"].post(f"/api/dispatch/returns/{rid}/decide", headers=k("dc"), json={"decision": "RESTOCK"})
    assert d.status_code == 200, d.text
    inv2 = db.execute(text("SELECT i.credited_amount FROM sales_invoices i JOIN shipments s ON s.id = i.shipment_id WHERE s.so_id=:o ORDER BY s.shipment_no"), {"o": o["id"]}).all()
    assert [x[0] for x in inv2] == [N("0.00"), N("59.00")]                                                         # 2 x 25 + 18% credited on shipment 2's invoice only
    integrity(db)


# ------------------------------------------------------------------ delivery attempts, proof of delivery, packages
def in_transit(shop, a):
    s = book(shop, a["shipment_id"])
    assert hook(shop["disp"], s["awb"], f"t-{uuid.uuid4().hex[:6]}", "IN_TRANSIT").json()["shipment_status"] == "IN_TRANSIT"
    return s


def attempt(shop, sid, **body):
    return shop["disp"].post(f"/api/dispatch/shipments/{sid}/delivery-attempts", headers=k("att"), json=body)


def test_failed_then_successful_delivery_with_proof(shop, two_shipments, db):
    a = two_shipments["a"]
    sid = a["shipment_id"]
    assert attempt(shop, sid, outcome="DELIVERED", received_by="Security desk").status_code == 409                # still PENDING_BOOKING: not on the road
    in_transit(shop, a)
    assert attempt(shop, sid, outcome="FAILED").status_code == 422                                                  # a failure needs a reason
    assert attempt(shop, sid, outcome="DELIVERED").status_code == 422                                               # delivery needs a recipient
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert attempt(shop, sid, outcome="FAILED", reason="x", attempted_at=future).status_code == 422
    assert attempt(shop, sid, outcome="FAILED", reason="Premises closed").json()["shipment_status"] == "FAILED_DELIVERY"
    ok = attempt(shop, sid, outcome="DELIVERED", received_by="R. Mehta", remarks="Signed slip")
    assert ok.status_code == 201, ok.text
    assert ok.json()["attempt_no"] == 2 and ok.json()["shipment_status"] == "DELIVERED" and ok.json()["proof_of_delivery"]["received_by"] == "R. Mehta"
    assert attempt(shop, sid, outcome="DELIVERED", received_by="again").status_code == 409                         # already delivered
    d = shop["disp"].get(f"/api/dispatch/shipments/{sid}/detail").json()
    assert [(x["attempt_no"], x["outcome"]) for x in d["delivery_attempts"]] == [(1, "FAILED"), (2, "DELIVERED")] and d["proof_of_delivery"]["received_by"] == "R. Mehta"
    assert db.scalar(text("SELECT count(*) FROM user_notifications WHERE kind='dispatch.delivery_failed' AND title LIKE :t"), {"t": f"%{d['awb'] or sid}%"}) >= 1     # the failure reached the people who can act
    assert db.scalar(text("SELECT count(*) FROM proofs_of_delivery WHERE shipment_id=:s"), {"s": sid}) == 1
    integrity(db)


def test_proof_added_after_courier_marks_delivered(shop, two_shipments, db):
    a = two_shipments["a"]
    s = book(shop, a["shipment_id"])
    r = shop["disp"].post(f"/api/dispatch/shipments/{a['shipment_id']}/proof-of-delivery", json={"received_by": "Front desk"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_delivered"
    assert hook(shop["disp"], s["awb"], "d1", "DELIVERED").json()["shipment_status"] == "DELIVERED"
    assert shop["disp"].post(f"/api/dispatch/shipments/{a['shipment_id']}/proof-of-delivery", json={"received_by": "Doc ghost", "document_id": 99999999}).status_code == 422
    assert shop["disp"].post(f"/api/dispatch/shipments/{a['shipment_id']}/proof-of-delivery", json={"received_by": "Front desk"}).status_code == 201
    assert shop["disp"].post(f"/api/dispatch/shipments/{a['shipment_id']}/proof-of-delivery", json={"received_by": "Twice"}).status_code == 409
    assert shop["disp"].post(f"/api/dispatch/shipments/{a['shipment_id']}/proof-of-delivery", json={"received_by": "x"}).status_code == 422


def test_packages_per_shipment(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    o = new_so(shop, f, cust, qty="20", price="10")
    v = advance(shop, o)["version"]
    r = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={
        "expected_version": v, "lines": [{"so_item_id": o["items"][0]["id"], "qty": "12"}],
        "packages": [{"weight_kg": "2.5", "length_cm": "30", "width_cm": "20", "height_cm": "15", "contents": "Serums x 6"}, {"weight_kg": "2.4", "contents": "Serums x 6"}]})
    assert r.status_code == 200, r.text
    sid = r.json()["shipment_id"]
    d = shop["disp"].get(f"/api/dispatch/shipments/{sid}/detail").json()
    assert [p["package_no"] for p in d["packages"]] == [1, 2] and d["packages"][0]["dimensions_cm"] == ["30.0", "20.0", "15.0"]
    assert shop["disp"].put(f"/api/dispatch/shipments/{sid}/packages", json={"packages": [{"weight_kg": "0"}]}).status_code == 422
    assert shop["disp"].put(f"/api/dispatch/shipments/{sid}/packages", json={"packages": []}).status_code == 422
    new = shop["disp"].put(f"/api/dispatch/shipments/{sid}/packages", json={"packages": [{"weight_kg": "5", "contents": "Re-packed"}]}).json()
    assert len(new["packages"]) == 1
    s = book(shop, sid)
    assert hook(shop["disp"], s["awb"], "pk", "DELIVERED").json()["shipment_status"] == "DELIVERED"
    assert shop["disp"].put(f"/api/dispatch/shipments/{sid}/packages", json={"packages": [{"weight_kg": "1"}]}).status_code == 409   # closed


def test_dispatch_permissions(shop, two_shipments, login, anon):
    a = two_shipments["a"]
    assert login("production_operator").post(f"/api/dispatch/shipments/{a['shipment_id']}/delivery-attempts", headers=k(), json={"outcome": "FAILED", "reason": "x"}).status_code == 403
    assert login("management").get(f"/api/dispatch/shipments/{a['shipment_id']}/detail").status_code == 200
    assert login("management").put(f"/api/dispatch/shipments/{a['shipment_id']}/packages", json={"packages": [{"weight_kg": "1"}]}).status_code == 403
    assert anon.get(f"/api/dispatch/shipments/{a['shipment_id']}/detail").status_code == 401
    assert login("sales_operator").post(f"/api/dispatch/orders/{two_shipments['o']['id']}/close-short", headers=k(), json={"reason": "no"}).status_code == 403


def test_concurrent_shipments_of_one_order_are_serialised(live_users, live_server):
    """8 simultaneous partial dispatches with the same order version: the row lock + version check lets exactly one through; totals stay exact."""
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.orm import Session as S
    from app.core.db import engine
    from tests.test_dispatch_concurrency import live_fg, key
    f = live_fg(live_users, 100)
    d1 = live_users("dispatch_manager")[0]
    ds = live_users("dispatch_manager", n=8)
    cust = make_customer()
    o = d1.post("/api/dispatch/orders", json={"customer_id": cust, "warehouse_id": f["wh"], "lines": [{"item_id": f["fg"], "qty": "20", "unit_price": "10"}]}).json()
    assert d1.post(f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": o["version"]}).status_code == 200
    assert d1.post(f"/api/dispatch/orders/{o['id']}/allocate", key=key("al"), json={"expected_version": o["version"] + 1}).status_code == 200
    p = d1.post(f"/api/dispatch/orders/{o['id']}/pack", json={"expected_version": o["version"] + 2, "cartons": 1, "weight_kg": "1"}).json()
    line = o["items"][0]["id"]
    body = {"expected_version": p["version"], "lines": [{"so_item_id": line, "qty": "6"}]}
    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(lambda c: c.post(f"/api/dispatch/orders/{o['id']}/dispatch", key=key("sh"), json=body).status_code, ds))
    assert codes.count(200) == 1 and all(c in (200, 409) for c in codes), codes
    with S(engine) as s:
        assert s.scalar(text("SELECT qty_shipped FROM sales_order_items WHERE id=:i"), {"i": line}) == N("6")
        assert s.scalar(text("SELECT count(*) FROM shipments WHERE so_id=:o"), {"o": o["id"]}) == 1
        assert s.scalar(text("SELECT count(*) FROM sales_invoices WHERE so_id=:o"), {"o": o["id"]}) == 1
        assert s.scalar(text("SELECT sum(qty_reserved) FROM stock_balances WHERE item_id=:i"), {"i": f["fg"]}) == N("14")
        bad = [c for c in __import__("scripts.check_integrity", fromlist=["x"]).run_checks(s.connection())["checks"] if not c["ok"]]
        assert not bad, bad
