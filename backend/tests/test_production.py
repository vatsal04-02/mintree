"""Production: R&D -> BOM -> order -> reserve -> issue -> stages -> completion, costing, genealogy, and the rules that guard it."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from scripts.check_integrity import run_checks
from tests.conftest import mk_master
from tests.test_inventory_procurement import N, po_to_approved, receive
from tests.test_rnd_quality import formulation, pass_trial


def integrity(db):
    bad = [c for c in run_checks(db.connection())["checks"] if not c["ok"]]
    assert not bad, bad


def k(p="k"):
    return {"Idempotency-Key": f"{p}-{uuid.uuid4().hex[:14]}"}


def extra_rm(m):
    from sqlalchemy.orm import Session as S
    from sqlalchemy import select
    from app.core.db import engine
    from app.masters.models import Item, UnitOfMeasure
    with S(engine) as s:
        u = s.scalar(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG")).id
        i = Item(item_code=f"RM2-{uuid.uuid4().hex[:6].upper()}", name="Second RM", kind="RM", uom_id=u, shelf_life_days=365)
        bulk = Item(item_code=f"BLK-{uuid.uuid4().hex[:6].upper()}", name="Bulk output", kind="BULK", uom_id=u, shelf_life_days=180)
        s.add_all([i, bulk])
        s.commit()
        return i.id, bulk.id


@pytest.fixture
def plant(login, admin):
    return {"dev": login("rnd_manager"), "approver": login("rnd_manager"), "pm": login("production_manager"), "pm2": login("production_manager"),
            "op": login("production_operator"), "qm": login("quality_manager"), "buyer": login("purchase_manager"), "stores": login("stores_manager"), "admin": admin}


def stock_rm(plant, m, qty, price):
    po = po_to_approved(plant["buyer"], plant["admin"], m, qty=str(qty), price=str(price))
    gr = receive(plant["stores"], po, m["wh1"], str(qty), f"DN-{uuid.uuid4().hex[:8]}", f"key-{uuid.uuid4().hex[:12]}").json()
    assert plant["qm"].post(f"/api/inventory/batches/{gr['lines'][0]['batch_id']}/qc-decision", headers=k(), json={"decision": "RELEASED"}).status_code == 200
    return gr["lines"][0]["batch_id"]


def world(plant, qty1=100, qty2=100):
    """Two RMs with valued, released stock (RM1 @10, RM2 @20) and an approved formulation (60/40 per 100 batch) -> ACTIVE BOM."""
    m1 = mk_master("PR")
    rm2, bulk = extra_rm(m1)
    m2 = {**m1, "item_id": rm2}
    b1, b2 = stock_rm(plant, m1, qty1, 10), stock_rm(plant, m2, qty2, 20)
    f = formulation(plant["dev"], bulk, [m1["item_id"], rm2], qtys=("60", "40"), size="100").json()
    v = f["versions"][0]["id"]
    pass_trial(plant["dev"], v)
    plant["dev"].post(f"/api/rnd/versions/{v}/submit")
    assert plant["approver"].post(f"/api/rnd/versions/{v}/approve").status_code == 200
    bom = plant["pm"].post("/api/production/boms", json={"formulation_version_id": v})
    assert bom.status_code == 201, bom.text
    return {"m1": m1, "rm2": rm2, "bulk": bulk, "b1": b1, "b2": b2, "v": v, "fid": f["id"], "bom": bom.json()}


def new_order(plant, w, qty="100"):
    r = plant["pm"].post("/api/production/orders", json={"bom_id": w["bom"]["id"], "planned_qty": qty, "warehouse_id": w["m1"]["wh1"]})
    assert r.status_code == 201, r.text
    return r.json()


def run_stages(plant, oid, n=3):
    for seq in range(1, n + 1):
        assert plant["op"].post(f"/api/production/orders/{oid}/stages/{seq}/start", json={}).status_code == 200
        assert plant["op"].post(f"/api/production/orders/{oid}/stages/{seq}/complete", json={"parameters": {"temp_c": 72}}).status_code == 200


def test_rnd_to_production_end_to_end_costing_and_genealogy(plant, db):
    w = world(plant)
    assert w["bom"]["status"] == "ACTIVE" and w["bom"]["formulation_version_id"] == w["v"]
    o = new_order(plant, w)
    assert [N(m["required"]) for m in o["materials"]] == [60, 40]
    assert o["formulation_version_id"] == w["v"], "the order pins the exact approved formulation version"
    assert plant["pm"].get(f"/api/production/orders/{o['id']}/availability").json()["ok"] is True
    assert plant["op"].post(f"/api/production/orders/{o['id']}/release", headers=k(), json={"expected_version": 1}).status_code == 403   # operator cannot release
    rel = plant["pm"].post(f"/api/production/orders/{o['id']}/release", headers=k(), json={"expected_version": 1})
    assert rel.status_code == 200 and rel.json()["status"] == "RELEASED"
    assert N(plant["stores"].get("/api/inventory/availability", params={"item_id": w["m1"]["item_id"]}).json()["total"]["reserved"]) == 60
    iss = plant["pm"].post(f"/api/production/orders/{o['id']}/issue", headers=k())
    assert iss.status_code == 200, iss.text
    assert N(iss.json()["input_cost"]) == N("1400.00") and N(iss.json()["wip_cost"]) == N("1400.00")         # 60 x 10 + 40 x 20
    assert plant["pm"].post(f"/api/production/orders/{o['id']}/issue", headers=k()).json()["detail"]["code"] == "invalid_status"
    # cannot complete before stages are done
    early = plant["pm"].post(f"/api/production/orders/{o['id']}/complete", headers=k(), json={"expected_version": 3, "actual_qty": "96", "wastage": [{"category": "SPILLAGE", "qty": "4", "reason": "transfer losses"}]})
    assert early.json()["detail"]["code"] == "stages_open"
    assert plant["op"].post(f"/api/production/orders/{o['id']}/stages/2/start", json={}).json()["detail"]["code"] == "previous_stage_open"
    run_stages(plant, o["id"])
    low = plant["pm"].post(f"/api/production/orders/{o['id']}/complete", headers=k(), json={"expected_version": 3, "actual_qty": "90"})
    assert low.json()["detail"]["code"] == "wastage_reason_required"
    over = plant["pm"].post(f"/api/production/orders/{o['id']}/complete", headers=k(), json={"expected_version": 3, "actual_qty": "111"})
    assert over.json()["detail"]["code"] == "output_exceeds_plan"
    done = plant["pm"].post(f"/api/production/orders/{o['id']}/complete", headers=k(), json={"expected_version": 3, "actual_qty": "96",
                            "wastage": [{"category": "SPILLAGE", "qty": "4", "reason": "transfer losses", "department": "Production"}]})
    assert done.status_code == 200, done.text
    d = done.json()
    assert d["status"] == "COMPLETED" and N(d["yield_pct"]) == N("96.00") and N(d["unit_cost"]) == N("14.5833") and N(d["wip_cost"]) == 0   # 1400 / 96
    # output: quarantined bulk batch, valued at the absorbed cost; raw materials consumed
    t = plant["stores"].get("/api/inventory/availability", params={"item_id": w["bulk"]}).json()["total"]
    assert (N(t["physical"]), N(t["blocked"]), N(t["available"])) == (96, 96, 0)
    assert db.execute(text("SELECT qty, value FROM item_valuation WHERE item_id=:i"), {"i": w["bulk"]}).one() == (96, N("1400.00"))
    assert N(plant["stores"].get("/api/inventory/availability", params={"item_id": w["m1"]["item_id"]}).json()["total"]["physical"]) == 40
    # genealogy back to supplier lots and forward from a raw-material lot
    g = plant["pm"].get(f"/api/production/batches/{d['output_batch_id']}/genealogy").json()
    assert g["production_order"]["number"] == o["number"] and g["production_order"]["formulation_version_id"] == w["v"]
    assert sorted(i["batch_id"] if "batch_id" in i else i["id"] for i in g["inputs"]) == sorted([w["b1"], w["b2"]])
    assert all(i["goods_receipt"]["po_number"].startswith("PO/") and N(i["consumed_qty"]) in (60, 40) for i in g["inputs"])
    wu = plant["pm"].get(f"/api/production/batches/{w['b1']}/where-used").json()
    assert wu["used_in"][0]["production_order"] == o["number"] and wu["used_in"][0]["output_batch_id"] == d["output_batch_id"]
    integrity(db)


def test_shortage_is_reported_and_leaves_no_reservations(plant, db):
    w = world(plant, qty1=100, qty2=30)                       # RM2 can cover 30 of the 40 needed
    o = new_order(plant, w)
    av = plant["pm"].get(f"/api/production/orders/{o['id']}/availability").json()
    assert av["ok"] is False and [N(l["short"]) for l in av["lines"]] == [0, 10]
    r = plant["pm"].post(f"/api/production/orders/{o['id']}/release", headers=k(), json={"expected_version": 1})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "material_shortage" and r.json()["detail"]["shortages"][0]["item_id"] == w["rm2"]
    assert db.execute(text("SELECT count(*) FROM stock_reservations WHERE reference_type='production_order' AND reference_id=:o"), {"o": str(o["id"])}).scalar() == 0
    assert N(plant["stores"].get("/api/inventory/availability", params={"item_id": w["m1"]["item_id"]}).json()["total"]["reserved"]) == 0, "RM1 reservation rolled back with the failure"
    integrity(db)


def test_cannot_start_without_approved_specs_or_with_a_superseded_formula(plant, db):
    w = world(plant)
    m_item = w["bulk"]
    bom = plant["pm"].post("/api/production/boms/manual", json={"output_item_id": m_item, "output_qty": "10", "lines": [{"item_id": w["m1"]["item_id"], "qty": "5"}]})
    assert bom.status_code == 201 and bom.json()["status"] == "DRAFT"
    r = plant["pm"].post("/api/production/orders", json={"bom_id": bom.json()["id"], "planned_qty": "10", "warehouse_id": w["m1"]["wh1"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "bom_not_approved"
    assert plant["pm"].post(f"/api/production/boms/{bom.json()['id']}/approve").json()["detail"]["code"] == "self_approval_not_allowed"
    # new formulation version approved -> the BOM built from the old one is superseded
    v2 = plant["dev"].post(f"/api/rnd/formulations/{w['fid']}/versions", json={"from_version_no": 1, "revision_note": "tweak"}).json()
    pass_trial(plant["dev"], v2["id"]); plant["dev"].post(f"/api/rnd/versions/{v2['id']}/submit")
    plant["approver"].post(f"/api/rnd/versions/{v2['id']}/approve")
    r = plant["pm"].post("/api/production/orders", json={"bom_id": w["bom"]["id"], "planned_qty": "100", "warehouse_id": w["m1"]["wh1"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "formula_superseded"
    new_bom = plant["pm"].post("/api/production/boms", json={"formulation_version_id": v2["id"]}).json()
    assert new_bom["version_no"] == 3 and new_bom["status"] == "ACTIVE"   # v2 was the draft manual BOM
    assert plant["pm"].get(f"/api/production/boms/{w['bom']['id']}").json()["status"] == "OBSOLETE"
    # an unapproved formulation cannot become a BOM
    f3 = formulation(plant["dev"], w["bulk"], [w["m1"]["item_id"], w["rm2"]]).json()
    assert plant["pm"].post("/api/production/boms", json={"formulation_version_id": f3["versions"][0]["id"]}).json()["detail"]["code"] == "formulation_not_approved"


def test_cancel_before_issue_releases_reservations_and_after_issue_is_refused(plant, db):
    w = world(plant)
    o = new_order(plant, w)
    plant["pm"].post(f"/api/production/orders/{o['id']}/release", headers=k(), json={"expected_version": 1})
    assert N(plant["stores"].get("/api/inventory/availability", params={"item_id": w["m1"]["item_id"]}).json()["total"]["reserved"]) == 60
    c = plant["pm"].post(f"/api/production/orders/{o['id']}/cancel", headers=k(), json={"reason": "demand dropped"})
    assert c.status_code == 200 and c.json()["status"] == "CANCELLED"
    assert N(plant["stores"].get("/api/inventory/availability", params={"item_id": w["m1"]["item_id"]}).json()["total"]["reserved"]) == 0
    o2 = new_order(plant, w)
    plant["pm"].post(f"/api/production/orders/{o2['id']}/release", headers=k(), json={"expected_version": 1})
    plant["pm"].post(f"/api/production/orders/{o2['id']}/issue", headers=k())
    assert plant["pm"].post(f"/api/production/orders/{o2['id']}/cancel", headers=k(), json={"reason": "too late now"}).json()["detail"]["code"] == "materials_issued"
    assert plant["pm"].post(f"/api/production/orders/{o2['id']}/release", headers=k(), json={"expected_version": 1}).status_code == 409
    integrity(db)


def test_replay_and_permissions(plant, db):
    w = world(plant)
    o = new_order(plant, w)
    key = k("rel")
    a = plant["pm"].post(f"/api/production/orders/{o['id']}/release", headers=key, json={"expected_version": 1})
    b = plant["pm"].post(f"/api/production/orders/{o['id']}/release", headers=key, json={"expected_version": 1})
    assert a.json() == b.json() and b.headers["idempotent-replayed"] == "true"
    assert N(plant["stores"].get("/api/inventory/availability", params={"item_id": w["m1"]["item_id"]}).json()["total"]["reserved"]) == 60, "replay must not reserve twice"
    assert plant["op"].post("/api/production/boms", json={"formulation_version_id": w["v"]}).status_code == 403
    assert plant["stores"].get("/api/production/orders").status_code == 403
    assert plant["op"].post(f"/api/production/orders/{o['id']}/issue", headers=k()).status_code == 403
    integrity(db)
