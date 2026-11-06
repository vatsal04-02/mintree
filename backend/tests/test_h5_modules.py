"""Maintenance, e-commerce, CRM, branding and analytics."""
import hashlib
import hmac
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import mk_master
from tests.test_dispatch import advance, integrity, make_customer, make_fg, new_so, shop  # noqa: F401
from tests.test_inventory_procurement import N
from tests.test_production import k, plant, stock_rm  # noqa: F401
from app.core.clock import business_today  # noqa: E402


def eq(c, **kw):
    r = c.post("/api/maintenance/equipment", json={"asset_id": f"EQ-{uuid.uuid4().hex[:6].upper()}", "name": "Homogenizer 500L", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def running_order(plant, qty=50):
    """A production order that is IN_PROGRESS (materials issued) so stages can be started."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.core.db import engine
    from app.masters.models import Item, UnitOfMeasure
    m = mk_master("EQP")
    stock_rm(plant, m, qty, 10)
    with S(engine) as s:
        fg = Item(item_code=f"FG-{uuid.uuid4().hex[:6].upper()}", name="Toner", kind="FG", uom_id=s.scalar(select(UnitOfMeasure).where(UnitOfMeasure.code == "PCS")).id, gst_rate=18)
        s.add(fg)
        s.commit()
        fg_id = fg.id
    bom = plant["pm"].post("/api/production/boms/manual", json={"output_item_id": fg_id, "output_qty": str(qty), "lines": [{"item_id": m["item_id"], "qty": str(qty)}]}).json()
    plant["pm2"].post(f"/api/production/boms/{bom['id']}/approve")
    o = plant["pm"].post("/api/production/orders", json={"bom_id": bom["id"], "planned_qty": str(qty), "warehouse_id": m["wh1"]}).json()
    plant["pm"].post(f"/api/production/orders/{o['id']}/release", headers=k(), json={"expected_version": 1})
    assert plant["pm"].post(f"/api/production/orders/{o['id']}/issue", headers=k()).status_code == 200
    return o


# ------------------------------------------------------------------ maintenance
def test_breakdown_blocks_production_until_verified_repair(plant, login, db):
    mm, mm2, tech = login("maintenance_manager"), login("maintenance_manager"), login("maintenance_manager")
    o = running_order(plant)
    e = eq(mm)
    start = lambda: plant["op"].post(f"/api/production/orders/{o['id']}/stages/1/start", json={"equipment_id": e["id"]})
    wo = mm.post("/api/maintenance/breakdowns", json={"equipment_id": e["id"], "description": "Seal leaking", "priority": "HIGH"}).json()
    r = start()
    assert r.status_code == 409 and "equipment_unavailable" in json.dumps(r.json())
    assert mm.post(f"/api/maintenance/work-orders/{wo['id']}/assign", json={"assignee_id": tech.user.id}).status_code == 200
    assert mm.post(f"/api/maintenance/work-orders/{wo['id']}/start").status_code == 409, "only the assignee can start it"
    assert tech.post(f"/api/maintenance/work-orders/{wo['id']}/start").status_code == 200
    assert tech.post(f"/api/maintenance/work-orders/{wo['id']}/complete", json={}).status_code == 409, "a breakdown needs root cause and repair notes"
    assert tech.post(f"/api/maintenance/work-orders/{wo['id']}/complete", json={"root_cause": "Worn seal", "repair_notes": "Replaced"}).status_code == 200
    assert tech.post(f"/api/maintenance/work-orders/{wo['id']}/verify").status_code == 409, "technician cannot verify own work"
    assert start().status_code == 409, "still not operational before verification"
    assert mm2.post(f"/api/maintenance/work-orders/{wo['id']}/verify").status_code == 200
    assert start().status_code == 200
    dt = mm.get(f"/api/maintenance/equipment/{e['id']}/downtime").json()
    assert len(dt["records"]) == 1 and dt["records"][0]["ended_at"] is not None


def test_calibration_gate(plant, login):
    mm = login("maintenance_manager")
    o = running_order(plant)
    e = eq(mm, calibration_required=True)
    start = lambda: plant["op"].post(f"/api/production/orders/{o['id']}/stages/1/start", json={"equipment_id": e["id"]})
    assert start().status_code == 409, "no calibration on record"
    today = business_today()
    assert mm.post(f"/api/maintenance/equipment/{e['id']}/calibrations", json={"calibrated_on": str(today - timedelta(days=400)), "due_date": str(today - timedelta(days=35)), "result": "PASS"}).status_code == 201
    assert start().status_code == 409, "calibration expired"
    r = mm.post(f"/api/maintenance/equipment/{e['id']}/calibrations", json={"calibrated_on": str(today), "due_date": str(today + timedelta(days=180)), "result": "FAIL"})
    assert r.json()["status"] == "UNDER_MAINTENANCE"


def test_preventive_generator_is_idempotent(login, db):
    mm = login("maintenance_manager")
    e = eq(mm)
    first = business_today() - timedelta(days=1)
    assert mm.post("/api/maintenance/schedules", json={"equipment_id": e["id"], "title": "Quarterly service", "frequency_days": 90, "first_due": str(first)}).status_code == 201
    a = mm.post("/api/maintenance/schedules/generate").json()
    b = mm.post("/api/maintenance/schedules/generate").json()
    assert a["created"] == 1 and b["created"] == 0
    n = db.execute(text("SELECT count(*) FROM maintenance_work_orders WHERE equipment_id = :e AND kind = 'PREVENTIVE'"), {"e": e["id"]}).scalar()
    assert n == 1
    assert db.execute(text("SELECT next_due FROM maintenance_schedules WHERE equipment_id = :e"), {"e": e["id"]}).scalar() == first + timedelta(days=90)
    # even a forced duplicate is impossible at the database level
    with pytest.raises(Exception):
        db.execute(text("INSERT INTO maintenance_work_orders(number, equipment_id, kind, schedule_id, due_date, description, raised_by) "
                        "SELECT 'DUP', equipment_id, 'PREVENTIVE', schedule_id, due_date, 'x', raised_by FROM maintenance_work_orders WHERE equipment_id = :e"), {"e": e["id"]})
        db.flush()


def test_maintenance_requires_permissions(login):
    assert login("sales_operator").post("/api/maintenance/equipment", json={"asset_id": "ZZ", "name": "x"}).status_code == 403
    assert login("management").get("/api/maintenance/equipment").status_code == 200


# ------------------------------------------------------------------ e-commerce
@pytest.fixture
def market(login):
    m = mk_master("MKT")
    ops = login("sales_operator")
    mgr = login("dispatch_manager")
    acct = mgr.post("/api/ecommerce/accounts", json={"platform": "AMAZON", "name": "Amazon IN", "seller_id": f"S{uuid.uuid4().hex[:8]}", "customer_id": make_customer(), "warehouse_id": m["wh1"]})
    assert acct.status_code == 201, acct.text
    return {"m": m, "ops": ops, "mgr": mgr, "acct": acct.json()}


def fg_item():
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.core.db import engine
    from app.masters.models import Item, UnitOfMeasure
    with S(engine) as s:
        fg = Item(item_code=f"FG-{uuid.uuid4().hex[:6].upper()}", name="Face Wash", kind="FG", uom_id=s.scalar(select(UnitOfMeasure).where(UnitOfMeasure.code == "PCS")).id, gst_rate=18)
        s.add(fg)
        s.commit()
        return fg.id


def order(oid, sku, qty="2", price="500"):
    return {"order_id": oid, "order_date": str(business_today()), "customer_name": "A Buyer", "shipping_address": "1 Marine Drive, Mumbai",
            "items": [{"sku": sku, "qty": qty, "unit_price": price}]}


def test_ingestion_is_idempotent_maps_packs_and_queues_exceptions(market, db):
    a, mgr = market["acct"], market["mgr"]
    item = fg_item()
    assert mgr.put(f"/api/ecommerce/accounts/{a['id']}/mappings", json={"platform_sku": "FW-PACK3", "item_id": item, "pack_multiplier": "3", "safety_stock": "5"}).status_code == 200
    body = {"orders": [order("AMZ-1", "FW-PACK3", "2", "900"), order("AMZ-2", "UNKNOWN-SKU")]}
    r1 = mgr.post(f"/api/ecommerce/accounts/{a['id']}/orders", json=body).json()
    assert r1 == {"created": 1, "exception": 1, "duplicate": 0}
    r2 = mgr.post(f"/api/ecommerce/accounts/{a['id']}/orders", json=body).json()
    assert r2["duplicate"] == 2 and r2["created"] == 0, "re-import must not create anything"
    so = db.execute(text("SELECT id, status, channel, external_ref FROM sales_orders WHERE external_ref = 'AMAZON:AMZ-1'")).mappings().all()
    assert len(so) == 1 and so[0]["status"] == "DRAFT" and so[0]["channel"] == "MARKETPLACE"
    line = db.execute(text("SELECT qty, unit_price FROM sales_order_items WHERE so_id = :s"), {"s": so[0]["id"]}).mappings().one()
    assert N(line["qty"]) == 6 and N(line["unit_price"]) == 300, "2 packs of 3 at 900 = 6 units at 300 (value preserved)"
    exc = mgr.get("/api/ecommerce/external-orders", params={"status": "EXCEPTION"}).json()["items"]
    assert len(exc) == 1 and "UNKNOWN-SKU" in exc[0]["exception_reason"]
    assert mgr.post(f"/api/ecommerce/external-orders/{exc[0]['id']}/retry").json()["status"] == "EXCEPTION", "still unmapped"
    mgr.put(f"/api/ecommerce/accounts/{a['id']}/mappings", json={"platform_sku": "UNKNOWN-SKU", "item_id": item})
    assert mgr.post(f"/api/ecommerce/external-orders/{exc[0]['id']}/retry").json()["status"] == "SO_CREATED"
    assert mgr.post(f"/api/ecommerce/external-orders/{exc[0]['id']}/retry").status_code == 409
    assert db.execute(text("SELECT count(*) FROM sales_orders WHERE channel = 'MARKETPLACE' AND external_ref LIKE 'AMAZON:AMZ-%'")).scalar() == 2


def test_csv_import_groups_lines_and_rejects_bad_files(market, db):
    a, mgr = market["acct"], market["mgr"]
    item = fg_item()
    mgr.put(f"/api/ecommerce/accounts/{a['id']}/mappings", json={"platform_sku": "SKU1", "item_id": item})
    mgr.put(f"/api/ecommerce/accounts/{a['id']}/mappings", json={"platform_sku": "SKU2", "item_id": fg_item()})
    csv_ = "order_id,order_date,customer_name,shipping_address,sku,qty,unit_price\nC-1,2026-11-01,Riya,Pune,SKU1,1,100\nC-1,2026-11-01,Riya,Pune,SKU2,2,50\nC-2,2026-11-02,Aman,Delhi,SKU1,3,100\n"
    up = lambda text_: mgr.post(f"/api/ecommerce/accounts/{a['id']}/orders/import-csv", files={"file": ("o.csv", text_, "text/csv")})
    assert up(csv_).json() == {"created": 2, "exception": 0, "duplicate": 0}
    assert up(csv_).json()["duplicate"] == 2
    assert up("order_id,sku,qty,unit_price\nX,SKU1,-1,10\n").status_code == 422
    assert up("garbage").status_code == 422


def test_order_webhook_signature_and_redelivery(market, db):
    a = market["acct"]
    mgr = market["mgr"]
    mgr.put(f"/api/ecommerce/accounts/{a['id']}/mappings", json={"platform_sku": "W1", "item_id": fg_item()})
    ev = {"event_id": f"ev-{uuid.uuid4().hex[:8]}", "order": order(f"WH-{uuid.uuid4().hex[:6]}", "W1")}
    raw = json.dumps(ev).encode()
    sig = hmac.new(b"dev-marketplace-secret", raw, hashlib.sha256).hexdigest()
    url = f"/api/ecommerce/accounts/{a['id']}/webhook"
    anon = market["ops"]
    assert anon.post(url, content=raw, headers={"X-Signature": "bad"}).status_code == 401
    assert anon.post(url, content=b"{}", headers={"X-Signature": hmac.new(b"dev-marketplace-secret", b"{}", hashlib.sha256).hexdigest()}).status_code == 422
    assert anon.post(url, content=raw, headers={"X-Signature": sig}).json()["created"] == 1
    assert anon.post(url, content=raw, headers={"X-Signature": sig}).json()["result"] == "duplicate"
    assert db.execute(text("SELECT count(*) FROM external_orders WHERE external_order_id = :o"), {"o": ev["order"]["order_id"]}).scalar() == 1


def test_inventory_sync_preview_uses_available_stock(market, shop, db):
    f = make_fg(shop, 100, 10)
    mgr = market["mgr"]
    a = mgr.post("/api/ecommerce/accounts", json={"platform": "FLIPKART", "name": "Flipkart", "seller_id": f"F{uuid.uuid4().hex[:6]}", "customer_id": make_customer(), "warehouse_id": f["wh"]}).json()
    mgr.put(f"/api/ecommerce/accounts/{a['id']}/mappings", json={"platform_sku": "FW-3PK", "item_id": f["fg"], "pack_multiplier": "3", "safety_stock": "10"})
    p = mgr.get(f"/api/ecommerce/accounts/{a['id']}/inventory-sync-preview").json()
    assert p["mode"] == "PREVIEW_ONLY"
    row = p["items"][0]
    assert N(row["available"]) == 100 and row["quantity_to_list"] == 30, "(100 - 10 safety) / 3 packs = 30"
    # reserve 30 by allocating a sales order: available falls, preview follows
    cust = make_customer()
    o = new_so(shop, f, cust, qty="30")
    advance(shop, o, "ALLOCATED")
    row = mgr.get(f"/api/ecommerce/accounts/{a['id']}/inventory-sync-preview").json()["items"][0]
    assert N(row["available"]) == 70 and row["quantity_to_list"] == 20


def test_settlement_reconciliation(market):
    a, mgr = market["acct"], market["mgr"]
    body = {"reference": f"SET-{uuid.uuid4().hex[:6]}", "period_start": "2026-11-01", "period_end": "2026-11-07", "gross_sales": "10000", "commission": "1500", "shipping_fees": "400", "ad_fees": "250", "returns_refunds": "350", "other_deductions": "0"}
    s = mgr.post(f"/api/ecommerce/accounts/{a['id']}/settlements", json=body)
    assert s.status_code == 201 and N(s.json()["net_settlement"]) == 7500
    assert mgr.post(f"/api/ecommerce/accounts/{a['id']}/settlements", json=body).status_code == 409
    r = mgr.post(f"/api/ecommerce/settlements/{s.json()['id']}/received", json={"received_amount": "7400"}).json()
    assert N(r["unreconciled_difference"]) == -100 and r["reconciled"] is False
    assert mgr.post(f"/api/ecommerce/settlements/{s.json()['id']}/received", json={"received_amount": "7500"}).json()["reconciled"] is True


def test_ecommerce_permissions(login, market):
    assert login("production_operator").get("/api/ecommerce/accounts").status_code == 403
    assert login("management").post("/api/ecommerce/accounts", json={}).status_code in (403, 422)


# ------------------------------------------------------------------ CRM + branding
def test_lead_and_complaint_workflows(login, shop):
    c = login("sales_operator")
    l = c.post("/api/crm/leads", json={"name": "Spa Chain", "email": "buyer@spa.example", "source": "Trade fair"}).json()
    assert c.post(f"/api/crm/leads/{l['id']}/status", json={"status": "WON"}).status_code == 409, "must be qualified first"
    for s in ("CONTACTED", "QUALIFIED", "WON"):
        assert c.post(f"/api/crm/leads/{l['id']}/status", json={"status": s}).status_code == 200
    assert c.post(f"/api/crm/leads/{l['id']}/status", json={"status": "LOST"}).status_code == 409
    f = make_fg(shop, 50, 10)
    cu = make_customer()
    cm = c.post("/api/crm/complaints", json={"customer_id": cu, "batch_id": f["batch"], "description": "Serum separated in the bottle"})
    assert cm.status_code == 201, cm.text
    assert c.post(f"/api/crm/complaints/{cm.json()['id']}/update", json={"status": "RESOLVED"}).status_code == 409
    assert c.post(f"/api/crm/complaints/{cm.json()['id']}/update", json={"status": "RESOLVED", "resolution": "Replaced"}).json()["status"] == "RESOLVED"
    assert c.post("/api/crm/complaints", json={"customer_id": 99999999, "description": "unknown customer"}).status_code == 422


def test_content_approval_and_single_publish(login, db):
    mk, ap = login("marketing_manager"), login("marketing_manager")
    camp = mk.post("/api/branding/campaigns", json={"name": "Diwali Glow", "budget": "50000", "start_date": "2026-11-01", "end_date": "2026-11-15"})
    assert camp.status_code == 201, camp.text
    assert mk.post("/api/branding/campaigns", json={"name": "Bad dates", "start_date": "2026-11-10", "end_date": "2026-11-01"}).status_code == 422
    ci = mk.post("/api/branding/content", json={"campaign_id": camp.json()["id"], "channel": "INSTAGRAM", "title": "Reel: serum routine", "caption": "Glow up"}).json()
    assert mk.post(f"/api/branding/content/{ci['id']}/approve").status_code == 409, "not submitted yet"
    assert mk.post(f"/api/branding/content/{ci['id']}/submit").status_code == 200
    assert mk.post(f"/api/branding/content/{ci['id']}/approve").status_code == 409, "author cannot approve own content"
    assert mk.post(f"/api/branding/content/{ci['id']}/publish", json={}).status_code == 409, "unapproved content cannot be published"
    assert ap.post(f"/api/branding/content/{ci['id']}/reject", json={"reason": "Fix the caption"}).json()["status"] == "REJECTED"
    assert mk.post(f"/api/branding/content/{ci['id']}/submit").status_code == 200
    assert ap.post(f"/api/branding/content/{ci['id']}/approve").json()["approved_by"] == ap.user.id
    assert mk.post(f"/api/branding/content/{ci['id']}/publish", json={"external_url": "https://instagram.com/p/abc"}).json()["status"] == "PUBLISHED"
    assert mk.post(f"/api/branding/content/{ci['id']}/publish", json={}).status_code == 409
    assert db.execute(text("SELECT mode FROM publishing_records WHERE content_id = :c"), {"c": ci["id"]}).scalar() == "MANUAL"
    # the database also refuses an approved item without an approver
    with pytest.raises(Exception):
        db.execute(text("UPDATE content_calendar SET approved_by = NULL WHERE id = :c"), {"c": ci["id"]})
        db.flush()


# ------------------------------------------------------------------ analytics
def test_management_summary_is_computed_from_real_records(shop, login, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    before = login("management").get("/api/analytics/management-summary").json()
    o = new_so(shop, f, cust)
    p = advance(shop, o)
    assert shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": p["version"]}).status_code == 200
    mg = login("management")
    after = mg.get("/api/analytics/management-summary").json()
    assert N(after["sales"]["revenue_ex_tax"]) - N(before["sales"]["revenue_ex_tax"]) == 750
    assert N(after["sales"]["receivable_outstanding"]) - N(before["sales"]["receivable_outstanding"]) == 885
    assert N(after["sales"]["cogs"]) - N(before["sales"]["cogs"]) == 300, "30 units at moving-average cost 10"
    assert N(after["finance"]["inventory_value"]) - N(before["finance"]["inventory_value"]) == -300
    # cross-check against the ledger directly
    gl = db.execute(text("SELECT COALESCE(sum(l.debit - l.credit),0) FROM journal_lines l JOIN accounts a ON a.id = l.account_id WHERE a.code = '5000'")).scalar()
    assert N(after["sales"]["cogs"]) == N(gl)
    assert login("qc_inspector").get("/api/analytics/management-summary").status_code == 403
    integrity(db)
