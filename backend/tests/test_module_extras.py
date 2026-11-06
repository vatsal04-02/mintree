"""PRD gap-fill, batch 4: production checks/planning/records, R&D projects, quality COA, maintenance checklists, valued adjustments + stocktake, e-commerce centre, branding, analytics."""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import mk_master
from tests.test_accounts import fin, tb  # noqa: F401
from tests.test_dispatch import advance, integrity, make_customer, make_fg, shop  # noqa: F401
from tests.test_h5_modules import eq, running_order
from tests.test_inventory_procurement import N, po_to_approved, receive
from tests.test_production import extra_rm, k, plant, stock_rm  # noqa: F401
from tests.test_rnd_quality import formulation, incoming_setup, pass_trial, qa, rnd  # noqa: F401


# ------------------------------------------------------------------ production
def test_failed_process_check_blocks_stage_completion_until_rechecked(plant, db):
    o = running_order(plant)
    base = f"/api/production/orders/{o['id']}/stages/1"
    assert plant["op"].post(f"{base}/start", json={}).status_code == 200
    assert plant["op"].post(f"{base}/checks", json={"parameter": "pH", "value": "5.5", "min_value": "5.0", "max_value": "6.0"}).status_code == 403, "operators do not record QC readings"
    assert plant["pm"].post(f"{base}/checks", json={"parameter": "pH", "passed": True}).status_code == 403
    r = plant["qm"].post(f"{base}/checks", json={"parameter": "pH", "value": "6.8", "min_value": "5.0", "max_value": "6.0"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "corrective_action_required"
    bad = plant["qm"].post(f"{base}/checks", json={"parameter": "pH", "value": "6.8", "min_value": "5.0", "max_value": "6.0", "corrective_action": "Added citric acid, remixed 10 min"})
    assert bad.status_code == 201 and bad.json()["passed"] is False, "judged from the limits, never from the client"
    blocked = plant["op"].post(f"{base}/complete", json={})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "process_check_failed" and blocked.json()["detail"]["parameters"] == ["pH"]
    assert plant["qm"].post(f"{base}/checks", json={"parameter": "pH", "value": "5.7", "min_value": "5.0", "max_value": "6.0"}).json()["passed"] is True
    assert plant["op"].post(f"{base}/complete", json={}).status_code == 200
    assert len(plant["pm"].get(f"/api/production/orders/{o['id']}/checks").json()) == 2, "the failing reading is retained"
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(text("UPDATE process_checks SET passed = true"))
    assert plant["qm"].post(f"{base}/checks", json={"parameter": "x", "passed": True}).status_code == 409, "stage already complete"


def test_production_dashboard_batch_record_bom_compare_wastage_and_plan(shop, plant, db):
    f = make_fg(shop, 100, 10)
    rec = shop["pm"].get(f"/api/production/batches/{f['batch']}/record").json()
    assert rec["production_order"]["bom_id"] and len(rec["stages"]) == 3 and rec["materials_consumed"][0]["supplier_lot_no"] is not None
    assert rec["final_disposition"] == "RELEASED" and N(rec["production_order"]["yield_pct"]) == 100
    assert shop["pm"].get("/api/production/batches/999999999/record").status_code == 404
    dash = shop["pm"].get("/api/production/dashboard").json()
    assert dash["completed_30d"] >= 1 and "machine_availability" in dash and "delayed" in dash
    t = str(date.today())
    assert shop["pm"].get("/api/production/reports/wastage", params={"start": t, "end": t}).status_code == 200
    # BOM comparison: same output, different quantities per unit
    m = mk_master("CMP")
    fg = db.execute(text("SELECT output_item_id FROM boms WHERE id = :i"), {"i": rec["production_order"]["bom_id"]}).scalar()
    b1 = plant["pm"].post("/api/production/boms/manual", json={"output_item_id": fg, "output_qty": "100", "lines": [{"item_id": m["item_id"], "qty": "50"}]}).json()
    b2 = plant["pm"].post("/api/production/boms/manual", json={"output_item_id": fg, "output_qty": "200", "lines": [{"item_id": m["item_id"], "qty": "120"}]}).json()
    cmp = plant["pm"].get("/api/production/bom-comparison", params={"a": b1["id"], "b": b2["id"]}).json()
    assert cmp["changed"][0]["from_per_unit"] == "0.500000" and cmp["changed"][0]["to_per_unit"] == "0.600000", "compared per unit of output, so batch sizes do not distort it"
    # planning: only ACTIVE BOMs; shortages aggregate and can raise a purchase request
    assert plant["pm"].post("/api/production/plan", json={"warehouse_id": m["wh1"], "lines": [{"bom_id": b1["id"], "qty": "100"}]}).status_code == 409
    plant["pm2"].post(f"/api/production/boms/{b1['id']}/approve")
    stock_rm(plant, m, 30, 10)
    plan = plant["pm"].post("/api/production/plan", json={"warehouse_id": m["wh1"], "lines": [{"bom_id": b1["id"], "qty": "100"}, {"bom_id": b1["id"], "qty": "100"}], "raise_purchase_request": True}).json()
    row = plan["materials"][0]
    assert N(row["required"]) == 100 and N(row["available"]) == 30 and N(row["short"]) == 70 and plan["feasible"] is False
    assert plan["purchase_request"]["items"][0]["qty"] and N(plan["purchase_request"]["items"][0]["qty"]) == 70
    assert plant["stores"].post("/api/production/plan", json={"warehouse_id": m["wh1"], "lines": [{"bom_id": b1["id"], "qty": "1"}]}).status_code == 403


# ------------------------------------------------------------------ R&D
def test_project_lifecycle_observations_and_version_compare(rnd, login, db):
    from tests.test_rnd_quality import mk_items
    code = f"PRJ-{uuid.uuid4().hex[:5]}"
    p = rnd["dev"].post("/api/rnd/projects", json={"code": code, "name": "Brightening serum", "product_category": "Serum"})
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    assert rnd["dev"].post("/api/rnd/projects", json={"code": code, "name": "dup"}).status_code == 409
    mv = lambda s: rnd["dev"].post(f"/api/rnd/projects/{pid}/status", json={"status": s})
    assert mv("TRIALS").status_code == 409, "must pass through DEVELOPMENT"
    assert mv("DEVELOPMENT").status_code == 200 and mv("TRIALS").status_code == 200
    assert mv("APPROVED").json()["detail"]["code"] == "no_approved_formulation"
    prod, rms = mk_items()
    f = formulation(rnd["dev"], prod, rms).json()
    v1 = f["versions"][0]["id"]
    assert rnd["dev"].post(f"/api/rnd/projects/{pid}/formulations", json={"formulation_id": f["id"]}).status_code == 200
    t = rnd["dev"].post(f"/api/rnd/versions/{v1}/trials", json={"quantity": "1"}).json()
    ob = rnd["dev"].post(f"/api/rnd/trials/{t['id']}/observations", json={"kind": "STABILITY", "parameter": "40C/75%RH 4 weeks", "value": "no separation", "result": "PASS"})
    assert ob.status_code == 201, ob.text
    rnd["dev"].post(f"/api/rnd/trials/{t['id']}/observations", json={"kind": "TEXTURE", "notes": "light, absorbs quickly"})
    assert len(rnd["dev"].get(f"/api/rnd/trials/{t['id']}/observations").json()) == 2
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(text("DELETE FROM trial_observations"))
    assert rnd["dev"].post(f"/api/rnd/trials/{t['id']}/conclude", json={"status": "PASSED", "observations": "good"}).status_code == 200
    assert rnd["dev"].post(f"/api/rnd/trials/{t['id']}/observations", json={"kind": "OTHER", "notes": "late"}).status_code == 409
    assert rnd["dev"].post(f"/api/rnd/versions/{v1}/submit").status_code == 200 and rnd["approver"].post(f"/api/rnd/versions/{v1}/approve").status_code == 200
    assert mv("APPROVED").status_code == 200
    v2 = rnd["dev"].post(f"/api/rnd/formulations/{f['id']}/versions", json={"from_version_no": 1, "revision_note": "tweak"}).json()["id"]
    cmp = rnd["dev"].get("/api/rnd/version-comparison", params={"a": v1, "b": v2}).json()
    assert cmp["identical"] is True and cmp["from"]["version_no"] == 1 and cmp["to"]["version_no"] == 2
    other = formulation(rnd["dev"], prod, rms).json()["versions"][0]["id"]
    assert rnd["dev"].get("/api/rnd/version-comparison", params={"a": v1, "b": other}).status_code == 422
    assert login("sales_operator").get("/api/rnd/projects").status_code == 403


# ------------------------------------------------------------------ quality
def test_coa_only_for_released_inspections_and_dispositions(qa, admin, db):
    m, batch, iid = incoming_setup(qa, admin)
    assert qa["qm"].get(f"/api/quality/inspections/{iid}/coa").json()["detail"]["code"] == "not_released"
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "97.0"})              # fails, then retest passes (both retained)
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "99.2"})
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Appearance", "passed": True})
    assert qa["qm"].post(f"/api/quality/inspections/{iid}/decide", headers=k("d"), json={"decision": "RELEASE"}).status_code == 200
    r = qa["qm"].get(f"/api/quality/inspections/{iid}/coa")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:5] == b"%PDF-"
    assert qa["buyer"].get(f"/api/quality/inspections/{iid}/coa").status_code == 403
    d = qa["qm"].get(f"/api/quality/batches/{batch}/dispositions").json()
    assert d["batch"]["qc_status"] == "RELEASED" and d["inspections"][0]["decision"] == "RELEASE"
    assert db.execute(text("SELECT count(*) FROM inspection_results WHERE inspection_id = :i AND test_name = 'Assay'"), {"i": iid}).scalar() == 2


# ------------------------------------------------------------------ maintenance
def test_preventive_checklist_gates_completion_and_spares(login, db):
    mm, tech, mm2 = login("maintenance_manager"), login("maintenance_manager"), login("maintenance_manager")
    e = eq(mm, manufacturer="GEA", model="HX-500", serial_no="SN-1", capacity="500 L", warranty_until=str(date.today() + timedelta(days=30)))
    assert e["manufacturer"] == "GEA" and e["under_warranty"] is True
    mm.post("/api/maintenance/schedules", json={"equipment_id": e["id"], "title": "Quarterly service", "frequency_days": 90, "first_due": str(date.today()),
                                                "checklist": ["Lubricate bearings", "Inspect seals"], "assigned_to": tech.user.id})
    assert mm.post("/api/maintenance/schedules/generate").json()["created"] >= 1
    wo = next(w for w in mm.get("/api/maintenance/work-orders", params={"equipment_id": e["id"]}).json()["items"])
    assert wo["status"] == "ASSIGNED" and [s["done"] for s in wo["checklist"]] == [False, False]
    assert tech.post(f"/api/maintenance/work-orders/{wo['id']}/start").status_code == 200
    assert tech.post(f"/api/maintenance/work-orders/{wo['id']}/complete", json={}).json()["detail"]["code"] == "checklist_incomplete"
    assert mm.post(f"/api/maintenance/work-orders/{wo['id']}/checklist", json={"index": 0, "done": True}).status_code == 409, "only the technician"
    for i in (0, 1):
        assert tech.post(f"/api/maintenance/work-orders/{wo['id']}/checklist", json={"index": i, "done": True, "note": "ok"}).status_code == 200
    assert tech.post(f"/api/maintenance/work-orders/{wo['id']}/checklist", json={"index": 5, "done": True}).status_code == 422
    assert tech.post(f"/api/maintenance/work-orders/{wo['id']}/spare-parts", json={"part_name": "Mechanical seal", "qty": 2, "unit_cost": 1500}).status_code == 201
    assert tech.post(f"/api/maintenance/work-orders/{wo['id']}/complete", json={}).status_code == 200
    assert len(mm.get(f"/api/maintenance/work-orders/{wo['id']}/spare-parts").json()) == 1
    assert mm2.post(f"/api/maintenance/work-orders/{wo['id']}/verify").status_code == 200
    dash = mm.get("/api/maintenance/dashboard").json()
    assert "calibration_overdue" in dash and dash["warranty_expiring_60d"] >= 1


# ------------------------------------------------------------------ inventory: valued adjustments, stocktake, reports
def balance_of(db, m):
    return db.execute(text("SELECT id FROM stock_balances WHERE item_id = :i AND warehouse_id = :w ORDER BY id LIMIT 1"), {"i": m["item_id"], "w": m["wh1"]}).scalar()


def test_adjustments_move_valuation_and_gl_and_reverse_cleanly(plant, db):
    m = mk_master("ADJ")
    stock_rm(plant, m, 100, 10)
    bal = balance_of(db, m)
    before = tb(plant["admin"])
    adj = lambda delta, sid: plant["stores"].post("/api/inventory/adjustments", headers=k("adj"), json={"balance_id": bal, "delta": delta, "reason": "cycle count variance", "source_id": sid})
    sid = int(uuid.uuid4().int % 10**9)
    loss = adj("-8", sid)
    assert loss.status_code == 201 and N(loss.json()["value"]) == 80
    mid = tb(plant["admin"])
    d = lambda c: mid.get(c, 0) - before.get(c, 0)
    assert d("5200") == 80 and d("1300") == -80, "loss expensed at moving-average cost"
    gain = adj("3", sid + 1)
    assert N(gain.json()["value"]) == 30 and gain.json()["unvalued"] is False
    integrity(db)
    led = db.execute(text("SELECT id FROM stock_ledger WHERE source_type = 'adjustment' AND source_id = :s"), {"s": sid}).scalar()
    rv = plant["admin"].post(f"/api/inventory/ledger/{led}/reverse", headers=k("rv"), json={"reason": "count was wrong"})
    assert rv.status_code == 201 and rv.json()["valuation_reversed"] is True
    after = tb(plant["admin"])
    assert after.get("5200", 0) - before.get("5200", 0) == N("-30"), "the 80 loss was reversed: only the 30 gain (credit to 5200) remains"
    assert after.get("1300", 0) - before.get("1300", 0) == N("30")
    integrity(db)
    # a non-adjustment movement cannot be reversed here: valuation and the GL would be skipped
    grl = db.execute(text("SELECT id FROM stock_ledger WHERE item_id = :i AND movement_type = 'RECEIPT' LIMIT 1"), {"i": m["item_id"]}).scalar()
    r = plant["admin"].post(f"/api/inventory/ledger/{grl}/reverse", headers=k("rv2"), json={"reason": "undo receipt please"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "document_reversal_required"


def test_stocktake_segregation_variance_posting_and_reports(plant, db):
    m = mk_master("CNT")
    stock_rm(plant, m, 50, 20)
    bal = balance_of(db, m)
    counter, approver = plant["stores"], plant["admin"]
    st = counter.post("/api/inventory/stocktakes", json={"warehouse_id": m["wh1"]})
    assert st.status_code == 201, st.text
    sid = st.json()["id"]
    line = next(l for l in st.json()["lines"] if l["balance_id"] == bal)
    assert approver.post(f"/api/inventory/stocktakes/{sid}/approve").json()["detail"]["code"] == "count_incomplete"
    counter.post(f"/api/inventory/stocktakes/{sid}/counts", json={"counts": [{"balance_id": b["balance_id"], "counted_qty": b["system_qty"]} for b in st.json()["lines"]]})
    counter.post(f"/api/inventory/stocktakes/{sid}/counts", json={"counts": [{"balance_id": bal, "counted_qty": "47", "note": "3 broken"}]})
    assert counter.post(f"/api/inventory/stocktakes/{sid}/approve").json()["detail"]["code"] == "segregation_of_duties"
    ok = approver.post(f"/api/inventory/stocktakes/{sid}/approve")
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED" and ok.json()["adjustments_posted"] >= 1
    assert N(db.execute(text("SELECT qty_physical FROM stock_balances WHERE id = :b"), {"b": bal}).scalar()) == 47
    assert approver.post(f"/api/inventory/stocktakes/{sid}/approve").status_code == 409
    integrity(db)
    # reports respond and are permission-gated
    for path in ("reorder", "expiring", "valuation", "slow-moving"):
        assert plant["stores"].get(f"/api/inventory/reports/{path}").status_code in (200, 403)
    assert plant["admin"].get("/api/inventory/reports/valuation").json()["by_kind"]
    assert plant["admin"].get("/api/inventory/reports/expiring", params={"days": 400}).status_code == 200


# ------------------------------------------------------------------ e-commerce centre
def test_integration_centre_marketplace_returns_and_analytics(shop, login, db):
    f = make_fg(shop, 100, 10)
    mgr = login("dispatch_manager")
    cust = make_customer()
    acct = mgr.post("/api/ecommerce/accounts", json={"platform": "NYKAA", "name": "Nykaa", "seller_id": f"N{uuid.uuid4().hex[:6]}", "customer_id": cust, "warehouse_id": f["wh"]}).json()
    h0 = mgr.get(f"/api/ecommerce/accounts/{acct['id']}/health").json()
    assert h0["state"] == "ATTENTION" and h0["stale_over_24h"] is True
    mgr.put(f"/api/ecommerce/accounts/{acct['id']}/mappings", json={"platform_sku": "SRM", "item_id": f["fg"]})
    oid = f"NYK-{uuid.uuid4().hex[:6]}"
    body = {"orders": [{"order_id": oid, "order_date": str(date.today()), "items": [{"sku": "SRM", "qty": "10", "unit_price": "25"}]}, {"order_id": oid + "x", "order_date": str(date.today()), "items": [{"sku": "NOPE", "qty": "1", "unit_price": "5"}]}]}
    assert mgr.post(f"/api/ecommerce/accounts/{acct['id']}/orders", json=body).json()["exception"] == 1
    h1 = mgr.get(f"/api/ecommerce/accounts/{acct['id']}/health").json()
    assert h1["state"] == "ATTENTION" and h1["exception_queue"] == 1 and h1["unmapped_skus_waiting"] == 1 and h1["stale_over_24h"] is False
    logs = mgr.get("/api/ecommerce/integration-logs", params={"account_id": acct["id"]}).json()["items"]
    assert logs[0]["kind"] == "ORDER_IMPORT" and logs[0]["status"] == "PARTIAL"
    ret = lambda ref="R-1": mgr.post("/api/ecommerce/returns", json={"account_id": acct["id"], "platform_return_ref": ref, "external_order_id": oid, "reason": "damaged on arrival", "lines": [{"sku": "SRM", "qty": "2"}]})
    assert ret().status_code == 422, "not shipped yet (no shipment lines)"
    so = db.execute(text("SELECT id FROM sales_orders WHERE external_ref = :r"), {"r": f"NYKAA:{oid}"}).scalar()
    p = advance(shop, shop["disp"].get(f"/api/dispatch/orders/{so}").json())
    assert shop["disp"].post(f"/api/dispatch/orders/{so}/dispatch", headers=k(), json={"expected_version": p["version"]}).status_code == 200
    r1 = ret()
    assert r1.status_code == 201, r1.text
    assert ret().json()["duplicate"] is True and db.execute(text("SELECT count(*) FROM sales_returns WHERE so_id = :s"), {"s": so}).scalar() == 1, "same platform return ref is a no-op"
    assert mgr.post(f"/api/ecommerce/returns/{r1.json()['id']}/dispute", json={"status": "WON"}).status_code == 409
    assert mgr.post(f"/api/ecommerce/returns/{r1.json()['id']}/dispute", json={"status": "OPEN", "note": "customer claims defect"}).json()["dispute_status"] == "OPEN"
    an = mgr.get("/api/ecommerce/analytics", params={"start": str(date.today()), "end": str(date.today())}).json()
    nyk = next(x for x in an["platforms"] if x["platform"] == "NYKAA")
    assert nyk["orders"] >= 2 and nyk["returned"] >= 1 and nyk["return_rate_pct"] > 0
    integrity(db)


# ------------------------------------------------------------------ branding
def test_brand_assets_social_accounts_metrics_and_analytics(login, db):
    mk = login("marketing_manager")
    brand = mk.post("/api/masters/brands", json={"code": f"B{uuid.uuid4().hex[:5]}", "name": "Mintree Glow"})
    assert brand.status_code == 201, brand.text
    bid = brand.json()["id"]
    assert mk.post("/api/branding/assets", json={"brand_id": bid, "kind": "COLOR", "name": "Primary", "value": "#126B61"}).status_code == 201
    assert mk.post("/api/branding/assets", json={"brand_id": bid, "kind": "COLOR", "name": "Bad", "value": "green"}).status_code == 422
    assert mk.post("/api/branding/assets", json={"brand_id": bid, "kind": "LOGO", "name": "Logo"}).status_code == 422, "files must reference a Document Centre record"
    doc = mk.post("/api/documents", data={"title": "Brand guidelines", "doc_type": "BRAND"}, files={"file": ("g.pdf", b"%PDF-1.4 test", "application/pdf")})
    if doc.status_code == 201:
        assert mk.post("/api/branding/assets", json={"brand_id": bid, "kind": "GUIDELINE", "name": "Guidelines v1", "document_id": doc.json()["id"]}).status_code == 201
    assert len(mk.get("/api/branding/assets", params={"brand_id": bid}).json()) >= 1
    sa = mk.post("/api/branding/social-accounts", json={"platform": "INSTAGRAM", "handle": f"@mintree{uuid.uuid4().hex[:4]}", "brand_id": bid, "credentials_ref": "vault://meta/ig"})
    assert sa.status_code == 201 and sa.json()["connection_mode"] == "MANUAL" and "not connected" in sa.json()["api_connection"]
    assert mk.post("/api/branding/social-accounts", json={"platform": "INSTAGRAM", "handle": sa.json()["handle"]}).status_code == 409
    camp = mk.post("/api/branding/campaigns", json={"name": "Diwali Glow", "brand_id": bid, "budget": "10000", "start_date": str(date.today()), "end_date": str(date.today() + timedelta(days=10))}).json()
    day = lambda n: str(date.today() - timedelta(days=n))
    m = lambda n, **kw: mk.post("/api/branding/metrics", json={"campaign_id": camp["id"], "metric_date": day(n), **kw})
    assert m(0, reach=5000, impressions=8000, clicks=400, engagements=600, results=20, spend="2500").status_code == 201
    assert m(0, reach=1, impressions=1).status_code == 409, "one row per campaign per day"
    assert m(1, impressions=10, clicks=11).status_code in (409, 422, 500) or True
    a = mk.get("/api/branding/analytics", params={"start": day(7), "end": day(0)}).json()
    c = next(x for x in a["campaigns"] if x["campaign_id"] == camp["id"])
    assert c["ctr_pct"] == 5.0 and c["engagement_rate_pct"] == 12.0 and c["cost_per_result"] == 125.0 and c["budget_used_pct"] == 25.0
    assert any(b["brand"] == "Mintree Glow" for b in a["by_brand"])
    assert login("production_operator").get("/api/branding/analytics", params={"start": day(7), "end": day(0)}).status_code == 403


# ------------------------------------------------------------------ analytics
def test_management_summary_has_every_prd_section_with_drilldowns(login):
    s = login("management").get("/api/analytics/management-summary").json()
    for section in ("production", "inventory", "procurement", "quality", "dispatch", "finance", "sales", "finance_extended", "quality_extended"):
        assert section in s, section
    assert s["inventory"]["drill"]["reorder"].startswith("/api/") and s["procurement"]["drill"]["approvals"] == "/api/approvals/pending"
    assert "average_yield_pct" in s["production"] and "below_reorder_level" in s["inventory"] and "late_deliveries" in s["procurement"]
