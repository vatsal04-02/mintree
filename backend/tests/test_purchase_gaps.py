"""PRD Module 02 gaps: RFQ -> quotations -> comparison -> award; multi-level approval, delegation, notifications, settings, tracking."""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from tests.conftest import mk_master
from tests.test_inventory_procurement import N, po_to_approved, receive


@pytest.fixture
def buy(login, admin):
    return {"pm": login("purchase_manager"), "pm2": login("purchase_manager"), "admin": admin, "stores": login("stores_manager")}


def two_suppliers():
    a, b = mk_master("RFQ"), mk_master("RFQ")
    return a, b["supplier_id"]


def make_rfq(buy, a, s2, qty="100", send=True):
    r = buy["pm"].post("/api/procurement/rfqs", json={"items": [{"item_id": a["item_id"], "qty": qty}], "supplier_ids": [a["supplier_id"], s2]})
    assert r.status_code == 201, r.text
    rfq = r.json()
    if send:
        assert buy["pm"].post(f"/api/procurement/rfqs/{rfq['id']}/send").status_code == 200
    return rfq


def quote(buy, rfq, a, supplier_id, price, lead=7, freight="0", tax="18", valid=None):
    body = {"supplier_id": supplier_id, "lines": [{"item_id": a["item_id"], "unit_price": price, "tax_rate": tax}], "lead_time_days": lead, "freight": freight}
    if valid:
        body["valid_until"] = valid
    return buy["pm"].post(f"/api/procurement/rfqs/{rfq['id']}/quotations", json=body)


def test_rfq_compare_recommend_and_award_creates_po(buy):
    a, s2 = two_suppliers()
    rfq = make_rfq(buy, a, s2)
    assert quote(buy, rfq, a, a["supplier_id"], "10.00", lead=10, freight="50").status_code == 201        # 100*10*1.18 + 50 = 1230
    assert quote(buy, rfq, a, a["supplier_id"], "9.00").status_code == 409, "one quotation per supplier"
    assert quote(buy, rfq, a, 99999999, "9").status_code == 409, "uninvited supplier"
    assert quote(buy, rfq, a, s2, "10.50", lead=3).status_code == 201                                      # 100*10.5*1.18 = 1239
    c = buy["pm"].get(f"/api/procurement/rfqs/{rfq['id']}/comparison").json()
    totals = {q["supplier_id"]: N(q["landed_total"]) for q in c["quotations"]}
    assert totals == {a["supplier_id"]: N("1230.00"), s2: N("1239.00")}, "freight is part of the landed cost"
    rec = next(q for q in c["quotations"] if q["quotation_id"] == c["recommended_quotation_id"])
    assert rec["supplier_id"] == a["supplier_id"]
    other = next(q for q in c["quotations"] if q["supplier_id"] == s2)
    r = buy["pm"].post(f"/api/procurement/rfqs/{rfq['id']}/award", json={"quotation_id": other["quotation_id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "reason_required"
    ok = buy["pm"].post(f"/api/procurement/rfqs/{rfq['id']}/award", json={"quotation_id": rec["quotation_id"]})
    assert ok.status_code == 200, ok.text
    po = ok.json()["purchase_order"]
    assert po["status"] == "DRAFT" and po["supplier_id"] == a["supplier_id"] and N(po["total_amount"]) == N("1180.00") and po["items"][0]["unit_price"] == "10.00"
    assert buy["pm"].post(f"/api/procurement/rfqs/{rfq['id']}/award", json={"quotation_id": rec["quotation_id"]}).status_code == 409, "an RFQ is awarded once"
    assert quote(buy, rfq, a, s2, "1").status_code == 409


def test_incomplete_and_expired_quotations_are_not_recommended(buy):
    a, s2 = two_suppliers()
    extra = mk_master("RFQ")
    r = buy["pm"].post("/api/procurement/rfqs", json={"items": [{"item_id": a["item_id"], "qty": "10"}, {"item_id": extra["item_id"], "qty": "5"}], "supplier_ids": [a["supplier_id"], s2]}).json()
    buy["pm"].post(f"/api/procurement/rfqs/{r['id']}/send")
    # the cheap supplier prices only one line -> incomplete
    assert buy["pm"].post(f"/api/procurement/rfqs/{r['id']}/quotations", json={"supplier_id": a["supplier_id"], "lines": [{"item_id": a["item_id"], "unit_price": "1"}]}).status_code == 201
    assert buy["pm"].post(f"/api/procurement/rfqs/{r['id']}/quotations", json={"supplier_id": s2, "lines": [
        {"item_id": a["item_id"], "unit_price": "20"}, {"item_id": extra["item_id"], "unit_price": "20"}]}).status_code == 201
    c = buy["pm"].get(f"/api/procurement/rfqs/{r['id']}/comparison").json()
    assert c["recommended_quotation_id"] == next(q["quotation_id"] for q in c["quotations"] if q["supplier_id"] == s2)
    inc = next(q for q in c["quotations"] if q["supplier_id"] == a["supplier_id"])
    assert buy["pm"].post(f"/api/procurement/rfqs/{r['id']}/award", json={"quotation_id": inc["quotation_id"], "reason": "cheaper"}).status_code == 409
    # an already-expired quotation is rejected at entry
    assert quote(buy, {"id": r["id"]}, a, a["supplier_id"], "1", valid=str(date.today() - timedelta(days=1))).status_code in (409, 422)


def test_rfq_rules(buy, login):
    a, s2 = two_suppliers()
    rfq = make_rfq(buy, a, s2, send=False)
    assert quote(buy, rfq, a, a["supplier_id"], "10").status_code == 409, "not open until sent"
    assert buy["pm"].post(f"/api/procurement/rfqs/{rfq['id']}/cancel").json()["status"] == "CANCELLED"
    bad = buy["pm"].post("/api/procurement/rfqs", json={"items": [{"item_id": a["item_id"], "qty": "1"}], "supplier_ids": [987654321]})
    assert bad.status_code == 409
    assert login("production_operator").get("/api/procurement/rfqs").status_code == 403
    assert login("stores_manager").post("/api/procurement/rfqs", json={"items": [{"item_id": 1, "qty": "1"}], "supplier_ids": [1]}).status_code == 403


def test_two_level_approval_history_and_notifications(buy, db):
    m = mk_master("TWO")
    assert buy["admin"].put("/api/admin/settings/procurement.two_level_threshold", json={"value": 500}).status_code == 200
    po = buy["pm"].post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": "100", "unit_price": "10"}]}).json()   # 1180
    po = buy["pm"].post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}).json()
    inbox = buy["pm2"].get("/api/approvals/pending").json()
    assert any(i["type"] == "purchase_order" and i["id"] == po["id"] for i in inbox["items"])
    first = buy["pm2"].post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]})
    assert first.status_code == 200 and first.json()["status"] == "PENDING_APPROVAL" and first.json()["approvals_required"] == 2
    again = buy["pm2"].post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": first.json()["version"]})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_approved_by_you"
    assert not any(i["id"] == po["id"] and i["type"] == "purchase_order" for i in buy["pm2"].get("/api/approvals/pending").json()["items"]), "already approved by me: leaves my inbox"
    second = buy["admin"].post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": first.json()["version"]})
    assert second.status_code == 200 and second.json()["status"] == "APPROVED"
    hist = buy["admin"].get(f"/api/approvals/history/purchase_order/{po['id']}").json()
    assert [(h["level"], h["decision"]) for h in hist] == [(1, "APPROVED"), (2, "APPROVED")]
    note = buy["pm"].get("/api/notifications").json()
    assert note["unread"] >= 1 and any("approved" in n["kind"] for n in note["items"])
    nid = note["items"][0]["id"]
    assert buy["pm"].post(f"/api/notifications/{nid}/read").json()["read"] is True
    assert buy["pm"].get("/api/notifications", params={"unread_only": True}).json()["unread"] == note["unread"] - 1
    assert buy["admin"].put("/api/admin/settings/procurement.two_level_threshold", json={"value": None}).status_code == 200


def test_delegation_grants_and_revokes_authority(buy, login, db):
    m = mk_master("DEL")
    big = buy["pm"].post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": "100000", "unit_price": "10"}]}).json()   # 1.18M > 500k
    big = buy["pm"].post(f"/api/procurement/purchase-orders/{big['id']}/submit", json={"expected_version": big["version"]}).json()
    over = buy["pm2"].post(f"/api/procurement/purchase-orders/{big['id']}/approve", json={"expected_version": big["version"]})
    assert over.status_code == 409 and over.json()["detail"]["code"] == "approval_limit_exceeded"
    d = buy["admin"].post("/api/approvals/delegations", json={"doc_type": "purchase_order", "to_user_id": buy["pm2"].user.id, "valid_from": str(date.today()), "valid_to": str(date.today() + timedelta(days=3)), "reason": "cover"})
    assert d.status_code == 201, d.text
    assert buy["admin"].post("/api/approvals/delegations", json={"doc_type": "x", "to_user_id": buy["admin"].user.id, "valid_from": str(date.today()), "valid_to": str(date.today())}).status_code == 422
    ok = buy["pm2"].post(f"/api/procurement/purchase-orders/{big['id']}/approve", json={"expected_version": big["version"]})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    assert db.execute(text("SELECT acting_for_user_id FROM approval_records WHERE doc_id = :i"), {"i": big["id"]}).scalar() == buy["admin"].user.id, "the record shows whose authority was used"
    # revoked delegation no longer works
    big2 = buy["pm"].post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": "100000", "unit_price": "10"}]}).json()
    big2 = buy["pm"].post(f"/api/procurement/purchase-orders/{big2['id']}/submit", json={"expected_version": big2["version"]}).json()
    assert buy["admin"].post(f"/api/approvals/delegations/{d.json()['id']}/revoke").status_code == 200
    assert buy["pm2"].post(f"/api/procurement/purchase-orders/{big2['id']}/approve", json={"expected_version": big2["version"]}).status_code == 409


def test_settings_validation_and_permissions(buy, login):
    assert buy["admin"].put("/api/admin/settings/inventory.rotation_policy", json={"value": "LIFO"}).status_code == 422
    assert buy["admin"].put("/api/admin/settings/nope", json={"value": 1}).status_code == 404
    assert login("purchase_manager").get("/api/admin/settings").status_code == 403
    assert login("business_admin").put("/api/admin/settings/company.name", json={"value": "x"}).status_code == 403
    keys = {s["key"] for s in buy["admin"].get("/api/admin/settings").json()}
    assert "finance.fiscal_year_start_month" in keys


def test_delivery_tracking_and_supplier_performance(buy, db):
    m = mk_master("TRK")
    po = buy["pm"].post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "expected_date": str(date.today() - timedelta(days=3)),
                                                                  "items": [{"item_id": m["item_id"], "qty": "100", "unit_price": "10"}]}).json()
    po = buy["pm"].post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}).json()
    po = buy["admin"].post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}).json()
    t = buy["pm"].get("/api/procurement/delivery-tracking", params={"supplier_id": m["supplier_id"]}).json()
    assert t[0]["state"] == "LATE" and t[0]["days_late"] == 3
    assert receive(buy["stores"], po, m["wh1"], "40", f"DN-{uuid.uuid4().hex[:6]}", f"k-{uuid.uuid4().hex[:10]}").status_code == 201
    t = buy["pm"].get("/api/procurement/delivery-tracking", params={"supplier_id": m["supplier_id"]}).json()
    assert t[0]["percent_received"] == 40.0
    perf = next(p for p in buy["pm"].get("/api/procurement/supplier-performance").json() if p["supplier_id"] == m["supplier_id"])
    assert perf["fill_rate_pct"] == 40.0 and perf["on_time_pct"] == 0.0, "received 3 days after the promised date"
