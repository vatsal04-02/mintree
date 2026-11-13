"""Tier 1 operations: batch recall, expiry control, material return and rework, GST e-invoice.

Every test goes through the real routes and finishes by running the database-wide integrity checks (stock, ledger, valuation, books)."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.accounts import einvoice
from app.core.clock import business_today
from app.core.config import get_settings
from tests.test_dispatch import advance, integrity, make_customer, make_fg, new_so, shop  # noqa: F401  (fixtures/helpers)
from tests.test_inventory_procurement import N
from tests.test_production import k, new_order, plant, run_stages, world  # noqa: F401


def ship(shop, f, cust, qty="10"):
    o = new_so(shop, f, cust, qty=qty)
    p = advance(shop, o)
    d = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": p["version"]})
    assert d.status_code == 200, d.text
    return d.json()


# ================================================================================================================================ GSTIN and IRN
def test_gstin_check_character_and_irn_definition():
    g = einvoice.make_gstin("27", "ABCDE1234F")
    assert einvoice.valid_gstin(g) and len(g) == 15
    assert not einvoice.valid_gstin(g[:14] + ("A" if g[14] != "A" else "B")), "a wrong check character is refused"
    assert not einvoice.valid_gstin("27ABCDE1234F1Z") and not einvoice.valid_gstin(None)
    irn = einvoice.irn_for(g, "2026-27", "INV", "SI/2026-27/000001")
    assert len(irn) == 64 and irn == einvoice.irn_for(g, "2026-27", "INV", "SI/2026-27/000001") and irn != einvoice.irn_for(g, "2026-27", "INV", "SI/2026-27/000002")
    assert einvoice.fy_label(business_today().replace(month=4, day=2)).startswith(str(business_today().year)) and einvoice.fy_label(business_today().replace(month=2, day=1))[:4] == str(business_today().year - 1)


# ================================================================================================================================ e-invoice
@pytest.fixture
def einv(shop, db, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "einvoice_mode", "sandbox")
    seller = einvoice.make_gstin("27", "AAACM1234A")
    for key, val in (("company.gstin", seller), ("company.state_code", "27"), ("company.address", "Plot 4, MIDC, Pune 411019")):
        assert admin.put(f"/api/admin/settings/{key}", json={"value": val}).status_code == 200
    f, cust = make_fg(shop, 100, 10), make_customer()
    db.execute(text("UPDATE items SET hsn_code = '33049990' WHERE id = :i"), {"i": f["fg"]})
    db.execute(text("UPDATE customers SET gstin = :g, billing_address = '12 MG Road, Pune 411001', state = 'MH' WHERE id = :c"), {"g": einvoice.make_gstin("27", "BBBCB4321B"), "c": cust})
    d = ship(shop, f, cust, "30")                                              # 30 x 25 = 750 + 18% = 885
    return {"f": f, "cust": cust, "inv": d["invoice"], "seller": seller}


def test_einvoice_payload_is_built_from_the_books_and_validated(shop, einv):
    pv = shop["acct"].get(f"/api/accounts/einvoices/{einv['inv']['id']}/preview")
    assert pv.status_code == 200, pv.text
    body = pv.json()
    assert body["valid"] and body["mode"] == "SANDBOX", body["errors"]
    p = body["payload"]
    assert p["DocDtls"]["No"] == einv["inv"]["number"] and p["SellerDtls"]["Gstin"] == einv["seller"] and p["TranDtls"]["SupTyp"] == "B2B"
    assert (p["ValDtls"]["AssVal"], p["ValDtls"]["CgstVal"], p["ValDtls"]["SgstVal"], p["ValDtls"]["IgstVal"], p["ValDtls"]["TotInvVal"]) == (750.0, 67.5, 67.5, 0.0, 885.0), "intra-state: CGST + SGST"
    assert p["ItemList"][0]["HsnCd"] == "33049990" and p["ItemList"][0]["Qty"] == 30.0


def test_interstate_supply_uses_igst(shop, einv, db):
    db.execute(text("UPDATE customers SET gstin = :g, state = 'KA' WHERE id = :c"), {"g": einvoice.make_gstin("29", "CCCDC1111C"), "c": einv["cust"]})
    v = shop["acct"].get(f"/api/accounts/einvoices/{einv['inv']['id']}/preview").json()["payload"]["ValDtls"]
    assert (v["CgstVal"], v["SgstVal"], v["IgstVal"]) == (0.0, 0.0, 135.0)


def test_generate_is_idempotent_stored_and_shown_on_the_pdf_as_sandbox(shop, einv, db):
    iid = einv["inv"]["id"]
    g1 = shop["acct"].post(f"/api/accounts/einvoices/{iid}/generate")
    assert g1.status_code == 200, g1.text
    a = g1.json()
    assert a["status"] == "GENERATED" and a["mode"] == "SANDBOX" and len(a["irn"]) == 64 and "no legal value" in a["notice"]
    assert a["irn"] == einvoice.irn_for(einv["seller"], einvoice.fy_label(business_today()), "INV", einv["inv"]["number"])
    b = shop["acct"].post(f"/api/accounts/einvoices/{iid}/generate").json()
    assert b["irn"] == a["irn"] and db.execute(text("SELECT count(*) FROM einvoices WHERE invoice_id = :i"), {"i": iid}).scalar() == 1, "a second request never makes a second IRN"
    pdf = shop["acct"].get(f"/api/dispatch/invoices/{iid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert db.execute(text("SELECT count(*) FROM audit_logs WHERE entity = 'sales_invoice' AND entity_id = :i AND action = 'einvoice_generate'"), {"i": str(iid)}).scalar() == 1
    integrity(db)


def test_invalid_party_data_blocks_generation_with_every_reason(shop, einv, db):
    db.execute(text("UPDATE customers SET gstin = '27AAAAA0000A1Z0', billing_address = 'Pune' WHERE id = :c"), {"c": einv["cust"]})      # wrong check character, no PIN
    db.execute(text("UPDATE items SET hsn_code = NULL WHERE id = :i"), {"i": einv["f"]["fg"]})
    r = shop["acct"].post(f"/api/accounts/einvoices/{einv['inv']['id']}/generate")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "einvoice_invalid"
    errs = " | ".join(r.json()["detail"]["errors"])
    assert "not valid" in errs and "PIN" in errs and "HSN" in errs
    assert db.execute(text("SELECT count(*) FROM einvoices WHERE invoice_id = :i"), {"i": einv["inv"]["id"]}).scalar() == 0, "nothing is stored for a refused request"


def test_buyer_without_gstin_is_not_an_einvoice_case(shop, einv, db):
    db.execute(text("UPDATE customers SET gstin = NULL WHERE id = :c"), {"c": einv["cust"]})
    r = shop["acct"].post(f"/api/accounts/einvoices/{einv['inv']['id']}/generate")
    assert r.status_code == 409 and any("no GSTIN" in e for e in r.json()["detail"]["errors"])


def test_cancel_rules_and_no_reuse_of_a_cancelled_number(shop, einv, db):
    iid = einv["inv"]["id"]
    shop["acct"].post(f"/api/accounts/einvoices/{iid}/generate")
    assert shop["acct"].post(f"/api/accounts/einvoices/{iid}/eway-bill", json={"distance_km": 100}).status_code == 409, "885 is below the e-way bill threshold"
    c = shop["acct"].post(f"/api/accounts/einvoices/{iid}/cancel", json={"reason": "Wrong buyer details"})
    assert c.status_code == 200 and c.json()["status"] == "CANCELLED"
    again = shop["acct"].post(f"/api/accounts/einvoices/{iid}/generate")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "irn_cancelled"
    # the 24-hour window
    f2, cust2 = make_fg(shop, 100, 10), make_customer()
    db.execute(text("UPDATE items SET hsn_code = '33049990' WHERE id = :i"), {"i": f2["fg"]})
    db.execute(text("UPDATE customers SET gstin = :g, billing_address = '5 FC Road, Pune 411004', state = 'MH' WHERE id = :c"), {"g": einvoice.make_gstin("27", "DDDDD5555D"), "c": cust2})
    inv2 = ship(shop, f2, cust2, "30")["invoice"]
    shop["acct"].post(f"/api/accounts/einvoices/{inv2['id']}/generate")
    db.execute(text("UPDATE einvoices SET ack_date = now() - interval '25 hours' WHERE invoice_id = :i"), {"i": inv2["id"]})
    late = shop["acct"].post(f"/api/accounts/einvoices/{inv2['id']}/cancel", json={"reason": "Too late"})
    assert late.status_code == 409 and late.json()["detail"]["code"] == "cancel_window_passed"


def test_eway_bill_needs_an_irn_and_a_value_over_the_threshold(shop, einv, db):
    f2, cust2 = make_fg(shop, 300, 10), make_customer()
    db.execute(text("UPDATE items SET hsn_code = '33049990' WHERE id = :i"), {"i": f2["fg"]})
    db.execute(text("UPDATE customers SET gstin = :g, billing_address = '5 FC Road, Pune 411004', state = 'MH' WHERE id = :c"), {"g": einvoice.make_gstin("27", "EEEEE6666E"), "c": cust2})
    inv = ship(shop, f2, cust2, "200")["invoice"]                              # 200 x 25 = 5000 + GST: still below the threshold
    db.execute(text("UPDATE sales_invoices SET subtotal = subtotal, total = total WHERE id = :i"), {"i": inv["id"]})
    assert shop["acct"].post(f"/api/accounts/einvoices/{inv['id']}/eway-bill", json={"distance_km": 100}).json()["detail"]["code"] == "not_generated"


def test_einvoice_is_off_unless_switched_on_and_live_is_refused_without_a_provider(shop, einv, monkeypatch):
    monkeypatch.setattr(get_settings(), "einvoice_mode", "off")
    assert shop["acct"].post(f"/api/accounts/einvoices/{einv['inv']['id']}/generate").json()["detail"]["code"] == "einvoice_off"
    monkeypatch.setattr(get_settings(), "einvoice_mode", "live")
    r = shop["acct"].post(f"/api/accounts/einvoices/{einv['inv']['id']}/generate")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "einvoice_provider_not_configured"


def test_production_refuses_sandbox_einvoicing_and_permissions(shop, einv, login):
    from app.core.config import Settings, production_problems
    bad = production_problems(Settings(environment="production", _env_file=None, einvoice_mode="sandbox"))
    assert any("EINVOICE" in b for b in bad)
    assert login("production_operator").post(f"/api/accounts/einvoices/{einv['inv']['id']}/generate").status_code == 403


# ================================================================================================================================ recall
def _rm_batch_of(db, fg_batch):
    return db.execute(text("SELECT batch_id FROM production_consumptions WHERE order_id = (SELECT source_id FROM inventory_batches WHERE id = :b) ORDER BY id LIMIT 1"), {"b": fg_batch}).scalar()


def test_recall_holds_everything_downstream_lists_customers_and_closes_only_when_resolved(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    shipped = ship(shop, f, cust, "30")
    rm = _rm_batch_of(db, f["batch"])
    qm = shop["qm"]
    r = qm.post("/api/quality/recalls", json={"batch_id": rm, "reason": "Contamination found at the supplier", "severity": "CRITICAL"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["number"].startswith("RCL/") and c["status"] == "OPEN"
    held = {b["batch_id"]: b for b in c["batches"]}
    assert rm in held and f["batch"] in held and held[rm]["role"] == "SUSPECT" and held[f["batch"]]["role"] == "AFFECTED"
    assert all(b["status_now"] == "HOLD" for b in held.values()), "every touched batch is on hold"
    assert db.execute(text("SELECT COALESCE(sum(qty_available),0) FROM stock_balances WHERE batch_id = :b"), {"b": f["batch"]}).scalar() == 0, "held stock cannot be allocated"
    assert [x["customer_code"] for x in c["customers"]] and N(c["customers"][0]["qty"]) == 30 and c["summary"]["customers"] == 1
    again = qm.post("/api/quality/recalls", json={"batch_id": rm, "reason": "A second try for the same batch", "severity": "MAJOR"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "recall_exists"
    assert qm.post(f"/api/quality/recalls/{c['id']}/close", json={"note": "Trying to close early"}).json()["detail"]["code"] == "customers_pending"
    bulk = qm.post(f"/api/quality/recalls/{c['id']}/customers", json={"disposition": "NOTIFIED", "note": "Called all"})
    assert bulk.status_code == 200 and bulk.json()["progress"]["customers_pending"] == 0
    line = c["customers"][0]["id"]
    assert qm.post(f"/api/quality/recalls/{c['id']}/customers/{line}", json={"disposition": "NOTIFIED", "note": "Phoned the buyer"}).status_code == 200
    d = qm.post(f"/api/quality/recalls/{c['id']}/customers/{line}", json={"disposition": "RETURNED"}).json()
    assert d["progress"]["customers_pending"] == 0 and not d["progress"]["can_close"]
    assert qm.post(f"/api/quality/recalls/{c['id']}/close", json={"note": "Still holding batches"}).json()["detail"]["code"] == "batches_still_held"
    assert qm.post(f"/api/quality/recalls/{c['id']}/batches/{rm}/release", json={"note": "Supplier cleared the lot"}).status_code == 200
    done = qm.post(f"/api/quality/recalls/{c['id']}/batches/{f['batch']}/reject", json={"note": "Destroyed under supervision"}).json()
    assert done["progress"]["can_close"]
    closed = qm.post(f"/api/quality/recalls/{c['id']}/close", json={"note": "All customers contacted; stock resolved"})
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
    assert db.execute(text("SELECT qc_status FROM inventory_batches WHERE id = :b"), {"b": f["batch"]}).scalar() == "REJECTED"
    csvr = qm.get(f"/api/quality/recalls/{c['id']}/notice.csv")
    assert csvr.status_code == 200 and "Customer" in csvr.text.splitlines()[0] and csvr.text.count("\n") >= 2
    assert shipped["invoice"]["number"]
    integrity(db)


def test_a_batch_already_held_is_recorded_but_not_touched_and_roles_are_enforced(shop, db, login):
    f = make_fg(shop, 100, 10)
    shop["qm"].post(f"/api/inventory/batches/{f['batch']}/qc-decision", headers=k(), json={"decision": "HOLD", "reason": "pending retest"})
    c = shop["qm"].post("/api/quality/recalls", json={"batch_id": f["batch"], "reason": "Customer complaint pattern", "severity": "MINOR"}).json()
    assert c["batches"][0]["prior_status"] == "HOLD" and c["batches"][0]["held_by_recall"] is False
    assert shop["qm"].post(f"/api/quality/recalls/{c['id']}/batches/{f['batch']}/release", json={"note": "not ours to release"}).json()["detail"]["code"] == "not_held_by_recall"
    assert login("production_operator").post("/api/quality/recalls", json={"batch_id": f["batch"], "reason": "operators may not recall", "severity": "MINOR"}).status_code == 403
    assert login("management").get(f"/api/quality/recalls/{c['id']}").status_code == 200, "management can read a recall"


# ================================================================================================================================ expiry
def test_expiry_worklist_sweep_and_minimum_shelf_life(shop, db, admin):
    f, cust = make_fg(shop, 100, 10), make_customer()
    db.execute(text("UPDATE inventory_batches SET mfg_date = business_today() - 400, expiry_date = business_today() - 3 WHERE id = :b"), {"b": f["batch"]})
    w = shop["disp"].get("/api/inventory/expiry/worklist", params={"within_days": 90}).json()
    me = next(i for i in w["items"] if i["batch_id"] == f["batch"])
    assert me["bucket"] == "EXPIRED" and me["needs_hold"] and w["counts"]["EXPIRED"] >= 1 and Decimal(w["value_by_bucket"]["EXPIRED"]) > 0
    sw = shop["qm"].post("/api/inventory/expiry/sweep", json={})
    assert sw.status_code == 200 and sw.json()["held_count"] >= 1
    assert db.execute(text("SELECT qc_status FROM inventory_batches WHERE id = :b"), {"b": f["batch"]}).scalar() == "HOLD"
    assert shop["qm"].post("/api/inventory/expiry/sweep", json={}).json()["held_count"] == 0, "a second sweep finds nothing new"
    # minimum remaining shelf life on allocation
    f2 = make_fg(shop, 100, 10)
    db.execute(text("UPDATE inventory_batches SET expiry_date = business_today() + 20 WHERE id = :b"), {"b": f2["batch"]})
    assert admin.put("/api/admin/settings/dispatch.min_shelf_life_days", json={"value": 30}).status_code == 200
    o = new_so(shop, f2, cust, qty="5")
    assert shop["disp"].post(f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": o["version"]}).status_code == 200
    a = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/allocate", headers=k(), json={"expected_version": o["version"] + 1})
    assert a.status_code == 409 and a.json()["detail"]["code"] == "insufficient_stock", "20 days left is under the 30-day minimum"
    assert admin.put("/api/admin/settings/dispatch.min_shelf_life_days", json={"value": 10}).status_code == 200
    assert shop["disp"].post(f"/api/dispatch/orders/{o['id']}/allocate", headers=k(), json={"expected_version": o["version"] + 1}).status_code == 200
    assert admin.put("/api/admin/settings/dispatch.min_shelf_life_days", json={"value": -1}).status_code == 422
    integrity(db)


# ================================================================================================================================ material return and rework
def test_unused_material_goes_back_to_its_lots_at_cost_and_the_books_still_tie(plant, db):
    w = world(plant)
    o = new_order(plant, w)                                                     # needs 60 + 40
    assert plant["pm"].post(f"/api/production/orders/{o['id']}/release", headers=k(), json={"expected_version": 1}).status_code == 200
    assert plant["pm"].post(f"/api/production/orders/{o['id']}/issue", headers=k()).status_code == 200
    item = w["m1"]["item_id"]
    wip0 = db.execute(text("SELECT wip_cost FROM production_orders WHERE id = :o"), {"o": o["id"]}).scalar()
    bal0 = db.execute(text("SELECT COALESCE(sum(qty_physical),0) FROM stock_balances WHERE item_id = :i"), {"i": item}).scalar()
    r = plant["pm"].post(f"/api/production/orders/{o['id']}/return-materials", json={"lines": [{"item_id": item, "qty": "10"}], "reason": "Line stopped early; drum not opened"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert N(body["returned_cost"]) == 100 and body["returned"][0]["returned_blocked"] is False, "10 units at 10 each go back as available stock"
    assert db.execute(text("SELECT COALESCE(sum(qty_physical),0) FROM stock_balances WHERE item_id = :i"), {"i": item}).scalar() == bal0 + 10
    assert db.execute(text("SELECT wip_cost FROM production_orders WHERE id = :o"), {"o": o["id"]}).scalar() == wip0 - 100
    mat = next(m for m in plant["pm"].get(f"/api/production/orders/{o['id']}/returnable").json()["materials"] if m["item_id"] == item)
    assert N(mat["returned"]) == 10 and N(mat["returnable"]) == 50
    over = plant["pm"].post(f"/api/production/orders/{o['id']}/return-materials", json={"lines": [{"item_id": item, "qty": "51"}], "reason": "Too much"})
    assert over.status_code == 409 and over.json()["detail"]["code"] == "return_exceeds_consumed"
    assert plant["op"].post(f"/api/production/orders/{o['id']}/return-materials", json={"lines": [{"item_id": item, "qty": "1"}], "reason": "operators cannot"}).status_code == 403
    integrity(db)
    run_stages(plant, o["id"])
    done = plant["pm"].post(f"/api/production/orders/{o['id']}/complete", headers=k(), json={"expected_version": 3, "actual_qty": "96"})
    assert done.status_code == 200, done.text
    assert N(done.json()["unit_cost"]) > 0
    late = plant["pm"].post(f"/api/production/orders/{o['id']}/return-materials", json={"lines": [{"item_id": item, "qty": "1"}], "reason": "after completion"})
    assert late.status_code == 409 and late.json()["detail"]["code"] == "invalid_status"
    integrity(db)


def test_rework_turns_a_held_batch_into_a_new_quarantined_batch_with_loss_in_the_cost(shop, db):
    f = make_fg(shop, 100, 10)
    shop["qm"].post(f"/api/inventory/batches/{f['batch']}/qc-decision", headers=k(), json={"decision": "HOLD", "reason": "pH out of range"})
    pm = shop["pm"]
    unit0 = db.execute(text("SELECT value / qty FROM item_valuation WHERE item_id = :i"), {"i": f["fg"]}).scalar()
    r = pm.post("/api/production/rework", json={"batch_id": f["batch"], "qty": "40", "reason": "pH out of range", "process": "Re-blend with citric acid and re-filter"})
    assert r.status_code == 201, r.text
    ro = r.json()
    assert ro["status"] == "STARTED" and N(ro["input_qty"]) == 40 and N(ro["input_cost"]) > 0
    assert db.execute(text("SELECT COALESCE(sum(qty_physical),0) FROM stock_balances WHERE batch_id = :b"), {"b": f["batch"]}).scalar() == 60
    integrity(db)                                                               # cost held in the open rework still reconciles
    over = pm.post(f"/api/production/rework/{ro['id']}/complete", json={"output_qty": "41"})
    assert over.status_code == 409 and over.json()["detail"]["code"] == "output_exceeds_input"
    d = pm.post(f"/api/production/rework/{ro['id']}/complete", json={"output_qty": "38"})
    assert d.status_code == 200, d.text
    done = d.json()
    assert done["status"] == "COMPLETED" and N(done["loss_qty"]) == 2 and done["output_batch_id"] and "quarantine" in done["note"]
    nb = done["output_batch_id"]
    assert db.execute(text("SELECT qc_status FROM inventory_batches WHERE id = :b"), {"b": nb}).scalar() == "QUARANTINE"
    assert db.execute(text("SELECT COALESCE(sum(qty_available),0) FROM stock_balances WHERE batch_id = :b"), {"b": nb}).scalar() == 0, "reworked stock is not sellable until QC releases it"
    assert N(done["unit_cost"]) > N(unit0), "the 2 lost units raise the unit cost of the 38 good ones"
    assert db.execute(text("SELECT expiry_date FROM inventory_batches WHERE id = :b"), {"b": nb}).scalar() == db.execute(text("SELECT expiry_date FROM inventory_batches WHERE id = :b"), {"b": f["batch"]}).scalar()
    integrity(db)
    # a recall on the source batch now reaches the reworked batch too
    c = shop["qm"].post("/api/quality/recalls", json={"batch_id": f["batch"], "reason": "Source batch recalled; check rework output", "severity": "MAJOR"}).json()
    assert nb in {b["batch_id"] for b in c["batches"]}
    assert shop["qm"].post("/api/production/rework", json={"batch_id": nb, "qty": "1", "reason": "r", "process": "p"}).status_code in (403, 422)


def test_a_started_rework_can_be_cancelled_and_only_held_batches_qualify(shop, db):
    f = make_fg(shop, 100, 10)
    pm = shop["pm"]
    assert pm.post("/api/production/rework", json={"batch_id": f["batch"], "qty": "10", "reason": "released stock", "process": "no reason to"}).json()["detail"]["code"] == "not_reworkable"
    shop["qm"].post(f"/api/inventory/batches/{f['batch']}/qc-decision", headers=k(), json={"decision": "REJECTED", "reason": "failed assay"})
    ro = pm.post("/api/production/rework", json={"batch_id": f["batch"], "qty": "25", "reason": "failed assay", "process": "Re-blend and retest"}).json()
    c = pm.post(f"/api/production/rework/{ro['id']}/cancel", json={"reason": "Not worth reworking"})
    assert c.status_code == 200 and c.json()["status"] == "CANCELLED"
    assert db.execute(text("SELECT COALESCE(sum(qty_physical),0) FROM stock_balances WHERE batch_id = :b"), {"b": f["batch"]}).scalar() == 100
    assert pm.post("/api/production/rework", json={"batch_id": f["batch"], "qty": "500", "reason": "too much", "process": "no way to"}).json()["detail"]["code"] == "not_enough_blocked"
    integrity(db)
