"""PRD CRM + Dispatch gaps: contacts, customer pricing, follow-ups, quotations -> orders, pipeline, pick/packing lists, dashboard, manifest, reverse pickup, invoice PDF."""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from tests.test_dispatch import advance, make_customer, make_fg, shop  # noqa: F401
from tests.test_inventory_procurement import N
from tests.test_production import k, plant  # noqa: F401


@pytest.fixture
def crm(shop, login):
    return {**shop, "s1": login("sales_operator"), "s2": login("sales_operator"), "mgr": login("dispatch_manager"), "mk": login("marketing_manager")}


def test_contacts_have_one_primary(crm, db):
    cust = make_customer()
    a = crm["s1"].post(f"/api/sales/customers/{cust}/contacts", json={"name": "Anita", "is_primary": True, "email": "a@x.example"})
    b = crm["s1"].post(f"/api/sales/customers/{cust}/contacts", json={"name": "Bharat", "is_primary": True})
    assert a.status_code == b.status_code == 201
    rows = crm["s1"].get(f"/api/sales/customers/{cust}/contacts").json()
    assert [(r["name"], r["is_primary"]) for r in rows] == [("Anita", False), ("Bharat", True)]
    assert crm["s1"].post(f"/api/sales/customers/{cust}/contacts", json={"name": "x", "email": "not-an-email"}).status_code == 422


def test_customer_pricing_windows_and_quotation_to_order(crm, db):
    f, cust = make_fg(crm, 100, 10), make_customer()
    t = date.today()
    price = lambda **kw: crm["mgr"].post("/api/sales/prices", json={"customer_id": cust, "item_id": f["fg"], **kw})
    assert price(price="22.50", valid_from=str(t - timedelta(days=10)), valid_to=str(t + timedelta(days=30))).status_code == 201
    over = price(price="20", valid_from=str(t), valid_to=str(t + timedelta(days=5)))
    assert over.status_code == 409 and over.json()["detail"]["code"] == "price_window_overlap"
    assert price(price="24", valid_from=str(t + timedelta(days=31))).status_code == 201, "adjacent window is fine"
    assert crm["s1"].post("/api/sales/prices", json={"customer_id": cust, "item_id": f["fg"], "price": "1", "valid_from": str(t)}).status_code == 403
    assert crm["s1"].get("/api/sales/prices/resolve", params={"customer_id": cust, "item_id": f["fg"]}).json()["price"] == "22.50"
    q = crm["s1"].post("/api/sales/quotations", json={"customer_id": cust, "valid_until": str(t + timedelta(days=7)), "lines": [{"item_id": f["fg"], "qty": "10"}]})
    assert q.status_code == 201, q.text
    q = q.json()
    assert q["items"][0]["price_source"] == "CUSTOMER_LIST" and N(q["subtotal"]) == 225 and N(q["total"]) == N("265.50")
    other = make_customer()
    assert crm["s1"].post("/api/sales/quotations", json={"customer_id": other, "valid_until": str(t + timedelta(days=7)), "lines": [{"item_id": f["fg"], "qty": "1"}]}).status_code == 422, "no price for that customer"
    manual = crm["s1"].post("/api/sales/quotations", json={"customer_id": other, "valid_until": str(t + timedelta(days=7)), "lines": [{"item_id": f["fg"], "qty": "1", "unit_price": "30"}]}).json()
    assert manual["items"][0]["price_source"] == "MANUAL"
    act = lambda name, **kw: crm["s1"].post(f"/api/sales/quotations/{q['id']}/{name}", json=kw)
    assert act("accept", expected_version=1).status_code == 409, "must be sent first"
    sent = act("send", expected_version=1).json()
    assert act("send", expected_version=1).status_code == 409
    acc = act("accept", expected_version=sent["version"]).json()
    assert acc["status"] == "ACCEPTED"
    conv = act("convert", expected_version=acc["version"], warehouse_id=f["wh"])
    assert conv.status_code == 200 and conv.json()["status"] == "CONVERTED"
    so = crm["s1"].get(f"/api/dispatch/orders/{conv.json()['sales_order_id']}").json()
    assert N(so["total"]) == N("265.50") and so["status"] == "DRAFT"
    assert act("convert", expected_version=conv.json()["version"], warehouse_id=f["wh"]).status_code == 409, "converted once"


def test_expired_quotation_cannot_be_accepted(crm, db):
    f, cust = make_fg(crm, 20, 10), make_customer()
    q = crm["s1"].post("/api/sales/quotations", json={"customer_id": cust, "valid_until": str(date.today() + timedelta(days=1)), "lines": [{"item_id": f["fg"], "qty": "1", "unit_price": "10"}]}).json()
    sent = crm["s1"].post(f"/api/sales/quotations/{q['id']}/send", json={"expected_version": 1}).json()
    db.execute(text("UPDATE sales_quotations SET valid_until = current_date - 1 WHERE id = :i"), {"i": q["id"]})
    r = crm["s1"].post(f"/api/sales/quotations/{q['id']}/accept", json={"expected_version": sent["version"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "quotation_expired"


def test_followups_and_pipeline(crm, db):
    lead = crm["s1"].post("/api/crm/leads", json={"name": "Salon Chain"}).json()
    f = crm["s1"].post("/api/sales/followups", json={"subject": "Call about samples", "due_date": str(date.today() - timedelta(days=1)), "lead_id": lead["id"], "assigned_to": crm["s2"].user.id})
    assert f.status_code == 201, f.text
    assert crm["s1"].post("/api/sales/followups", json={"subject": "bad", "due_date": str(date.today()), "assigned_to": crm["s2"].user.id}).status_code == 422, "needs a lead or a customer"
    mine = crm["s2"].get("/api/sales/followups").json()["items"]
    assert any(x["id"] == f.json()["id"] and x["overdue"] for x in mine)
    assert any(n["kind"] == "followup.assigned" for n in crm["s2"].get("/api/notifications").json()["items"])
    assert crm["s2"].post(f"/api/sales/followups/{f.json()['id']}/close", json={}).status_code == 409, "outcome required"
    done = crm["s2"].post(f"/api/sales/followups/{f.json()['id']}/close", json={"outcome": "Sent samples"})
    assert done.json()["status"] == "DONE"
    assert crm["s2"].post(f"/api/sales/followups/{f.json()['id']}/close", json={"outcome": "again"}).status_code == 409
    p = crm["s1"].get("/api/sales/pipeline").json()
    assert p["leads_by_status"].get("NEW", 0) >= 1 and "overdue_followups" in p
    assert crm["mk"].get("/api/sales/pipeline").status_code == 403


def test_pick_list_packing_list_dashboard_charge_manifest_and_pdf(crm, db):
    f, cust = make_fg(crm, 100, 10), make_customer()
    dash0 = crm["disp"].get("/api/dispatch/dashboard").json()
    o = crm["disp"].post("/api/dispatch/orders", json={"customer_id": cust, "warehouse_id": f["wh"], "channel": "B2B", "lines": [{"item_id": f["fg"], "qty": "20", "unit_price": "25"}]}).json()
    assert crm["disp"].get(f"/api/dispatch/orders/{o['id']}/pick-list").status_code == 409, "not allocated yet"
    alloc = advance(crm, o, "ALLOCATED")
    pl = crm["disp"].get(f"/api/dispatch/orders/{o['id']}/pick-list").json()
    assert N(pl["total_qty"]) == 20 and pl["lines"][0]["batch_no"] and N(pl["lines"][0]["qty"]) == 20
    assert crm["disp"].get("/api/dispatch/dashboard").json()["ready_to_pack"] == dash0["ready_to_pack"] + 1
    packed = crm["disp"].post(f"/api/dispatch/orders/{o['id']}/pack", json={"expected_version": alloc["version"], "cartons": 3, "weight_kg": "7.5"}).json()
    pk = crm["disp"].get(f"/api/dispatch/orders/{o['id']}/packing-list").json()
    assert pk["cartons"] == 3 and N(pk["weight_kg"]) == N("7.5") and N(pk["lines"][0]["qty"]) == 20
    d = crm["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": packed["version"]}).json()
    sid, inv = d["shipment_id"], d["invoice"]
    assert crm["disp"].post(f"/api/dispatch/shipments/{sid}/book").json()["result"] == "booked"
    assert crm["disp"].post(f"/api/dispatch/shipments/{sid}/shipping-charge", json={"amount": "85.50"}).json()["shipping_charge"] == "85.50"
    assert crm["s1"].post(f"/api/dispatch/shipments/{sid}/shipping-charge", json={"amount": "1"}).status_code == 403
    m = crm["disp"].post("/api/dispatch/manifests", json={"courier": "fake"})
    assert m.status_code == 201, m.text
    mf = crm["disp"].get(f"/api/dispatch/manifests/{m.json()['manifest_no']}").json()
    assert any(s["shipment_id"] == sid and s["awb"].startswith("FAKE") for s in mf["shipments"]) and mf["total_cartons"] >= 3
    again = crm["disp"].post("/api/dispatch/manifests", json={"courier": "fake"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "nothing_to_manifest"
    dash = crm["disp"].get("/api/dispatch/dashboard").json()
    assert dash["dispatched_today"] == dash0["dispatched_today"] + 1 and N(dash["shipping_charges_recorded"]) - N(dash0["shipping_charges_recorded"]) == N("85.50")
    pdf = crm["disp"].get(f"/api/dispatch/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:5] == b"%PDF-" and len(pdf.content) > 1500
    assert crm["s1"].get(f"/api/dispatch/invoices/{inv['id']}/pdf").status_code == 403


def test_reverse_pickup_is_idempotent(crm, db):
    f, cust = make_fg(crm, 50, 10), make_customer()
    o = crm["disp"].post("/api/dispatch/orders", json={"customer_id": cust, "warehouse_id": f["wh"], "channel": "B2B", "lines": [{"item_id": f["fg"], "qty": "10", "unit_price": "25"}]}).json()
    p = advance(crm, o)
    d = crm["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": p["version"]}).json()
    line = crm["disp"].get(f"/api/dispatch/shipments/{d['shipment_id']}").json()["lines"][0]
    r = crm["disp"].post(f"/api/dispatch/returns?order_id={o['id']}", headers=k(), json={"reason": "wrong shade", "lines": [{"shipment_line_id": line["id"], "qty": "2"}]}).json()
    assert crm["disp"].post(f"/api/dispatch/returns/{r['id']}/reverse-pickup").json()["result"] == "requested"
    assert crm["disp"].post(f"/api/dispatch/returns/{r['id']}/reverse-pickup").json()["result"] == "skipped"
    assert db.execute(text("SELECT reverse_awb FROM sales_returns WHERE id = :i"), {"i": r["id"]}).scalar().startswith("FAKER")
    crm["disp"].post(f"/api/dispatch/returns/{r['id']}/receive", headers=k())
    assert crm["disp"].post(f"/api/dispatch/returns/{r['id']}/reverse-pickup").status_code == 409, "too late once received"
