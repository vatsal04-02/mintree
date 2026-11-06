"""R&D (immutable approved formulations, handover rules) and Quality (server-side pass/fail, release control, deviations/CAPA)."""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from scripts.check_integrity import run_checks
from tests.conftest import mk_master
from tests.test_inventory_procurement import N, po_to_approved, receive
from app.core.clock import business_today  # noqa: E402


def integrity(db):
    bad = [c for c in run_checks(db.connection())["checks"] if not c["ok"]]
    assert not bad, bad


def mk_items(n_rm=2):
    """Committed: one BULK product item and n RM ingredient items."""
    from sqlalchemy.orm import Session as S
    from app.core.db import engine
    from app.masters.models import Item, UnitOfMeasure
    from sqlalchemy import select
    tag = uuid.uuid4().hex[:6].upper()
    with S(engine) as s:
        u = s.scalar(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG")).id
        prod = Item(item_code=f"BLK-{tag}", name="Bulk serum", kind="BULK", uom_id=u)
        rms = [Item(item_code=f"RMI-{tag}-{i}", name=f"Ingredient {i}", kind="RM", uom_id=u) for i in range(n_rm)]
        s.add_all([prod, *rms])
        s.commit()
        return prod.id, [r.id for r in rms]


@pytest.fixture
def rnd(login, admin):
    return {"dev": login("rnd_manager"), "approver": login("rnd_manager"), "qm": login("quality_manager"), "admin": admin}


def formulation(client, prod, rms, code=None, qtys=("60", "40"), size="100"):
    return client.post("/api/rnd/formulations", json={"code": code or f"F-{uuid.uuid4().hex[:6]}", "name": "Vit C serum", "product_item_id": prod, "batch_size": size,
                                                      "ingredients": [{"item_id": i, "qty": q} for i, q in zip(rms, qtys)]})


def pass_trial(client, vid):
    t = client.post(f"/api/rnd/versions/{vid}/trials", json={"quantity": "1"}).json()
    return client.post(f"/api/rnd/trials/{t['id']}/conclude", json={"status": "PASSED", "observations": "stable, good texture"})


def test_formulation_lifecycle_handover_rules_and_immutability(rnd, db):
    prod, rms = mk_items()
    f = formulation(rnd["dev"], prod, rms)
    assert f.status_code == 201, f.text
    fid, v1 = f.json()["id"], f.json()["versions"][0]
    assert [N(i["percentage"]) for i in v1["ingredients"]] == [60, 40]
    assert rnd["dev"].post(f"/api/rnd/versions/{v1['id']}/submit").json()["detail"]["code"] == "no_passed_trial"     # handover rule
    assert pass_trial(rnd["dev"], v1["id"]).status_code == 200
    assert rnd["dev"].post(f"/api/rnd/versions/{v1['id']}/submit").status_code == 200
    assert rnd["dev"].post(f"/api/rnd/versions/{v1['id']}/approve").json()["detail"]["code"] == "self_approval_not_allowed"
    ok = rnd["approver"].post(f"/api/rnd/versions/{v1['id']}/approve")
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    # immutable: API refuses ...
    assert rnd["dev"].patch(f"/api/rnd/versions/{v1['id']}", json={"batch_size": "200"}).json()["detail"]["code"] == "version_not_editable"
    # ... and so does the DATABASE, even for raw SQL
    for sql in (f"UPDATE formulation_versions SET batch_size = 999 WHERE id = {v1['id']}", f"DELETE FROM formulation_versions WHERE id = {v1['id']}",
                f"UPDATE formulation_ingredients SET qty = 1 WHERE version_id = {v1['id']}", f"DELETE FROM formulation_ingredients WHERE version_id = {v1['id']}",
                f"INSERT INTO formulation_ingredients(version_id, line_no, item_id, qty, percentage) VALUES ({v1['id']}, 9, {rms[0]}, 1, 1)"):
        with pytest.raises(DBAPIError, match="immutable|cannot be deleted"):
            with db.begin_nested():
                db.execute(text(sql))
    # new version: copy, edit, trial, approve -> v1 becomes OBSOLETE; only one current version
    v2 = rnd["dev"].post(f"/api/rnd/formulations/{fid}/versions", json={"from_version_no": 1, "revision_note": "more vitamin C"})
    assert v2.status_code == 201 and v2.json()["status"] == "DRAFT" and v2.json()["version_no"] == 2
    assert rnd["dev"].post(f"/api/rnd/formulations/{fid}/versions", json={"from_version_no": 1, "revision_note": "again"}).json()["detail"]["code"] == "open_version_exists"
    assert rnd["dev"].patch(f"/api/rnd/versions/{v2.json()['id']}", json={"ingredients": [{"item_id": rms[0], "qty": "70"}, {"item_id": rms[1], "qty": "30"}]}).status_code == 200
    pass_trial(rnd["dev"], v2.json()["id"])
    rnd["dev"].post(f"/api/rnd/versions/{v2.json()['id']}/submit")
    assert rnd["approver"].post(f"/api/rnd/versions/{v2.json()['id']}/approve").status_code == 200
    vs = {v["version_no"]: v["status"] for v in rnd["dev"].get(f"/api/rnd/formulations/{fid}").json()["versions"]}
    assert vs == {1: "OBSOLETE", 2: "APPROVED"}
    assert [N(i["qty"]) for i in rnd["dev"].get(f"/api/rnd/formulations/{fid}").json()["versions"][0]["ingredients"]] == [60, 40], "history keeps the original content"


def test_formulation_validation_and_permissions(rnd, login):
    prod, rms = mk_items()
    bad = formulation(rnd["dev"], prod, rms, qtys=("60", "30"))                               # 90 % of batch
    v = bad.json()["versions"][0]["id"]
    pass_trial(rnd["dev"], v)
    assert rnd["dev"].post(f"/api/rnd/versions/{v}/submit").json()["detail"]["code"] == "percentages_not_100"
    assert formulation(rnd["dev"], rms[0], rms[1:]).status_code == 422                          # product must be BULK/FG
    assert formulation(rnd["dev"], prod, [rms[0], rms[0]]).status_code == 422                   # duplicate ingredient
    assert login("production_operator").get("/api/rnd/formulations").status_code == 403
    assert login("qc_inspector").post("/api/rnd/formulations", json={}).status_code == 403
    code = f"DUP-{uuid.uuid4().hex[:5]}"
    assert formulation(rnd["dev"], prod, rms, code=code).status_code == 201
    assert formulation(rnd["dev"], prod, rms, code=code).json()["detail"]["code"] == "duplicate_code"


@pytest.fixture
def qa(login):
    return {"buyer": login("purchase_manager"), "stores": login("stores_manager"), "inspector": login("qc_inspector"), "inspector2": login("qc_inspector"),
            "qm": login("quality_manager"), "qm2": login("quality_manager")}


def incoming_setup(qa, admin):
    m = mk_master("QC")
    spec = qa["qm"].post("/api/quality/specs", json={"item_id": m["item_id"], "stage": "INCOMING", "tests": [
        {"test_name": "Assay", "unit": "%", "min_value": "98.0", "max_value": "101.0"},
        {"test_name": "Appearance", "is_pass_fail": True},
        {"test_name": "Odour", "is_pass_fail": True, "mandatory": False}]})
    assert spec.status_code == 201, spec.text
    po = po_to_approved(qa["buyer"], admin, m, qty="100")
    gr = receive(qa["stores"], po, m["wh1"], "100", f"DN-{uuid.uuid4().hex[:6]}", f"key-qc-{uuid.uuid4().hex[:12]}").json()
    batch = gr["lines"][0]["batch_id"]
    insp = qa["qm"].get("/api/quality/inspections", params={"batch_id": batch}).json()["items"]
    assert len(insp) == 1 and insp[0]["status"] == "PENDING", "goods receipt auto-creates the INCOMING inspection"
    return m, batch, insp[0]["id"]


def test_inspection_server_side_judgement_and_release_controls(qa, admin, db):
    m, batch, iid = incoming_setup(qa, admin)
    key = lambda: {"Idempotency-Key": f"key-dec-{uuid.uuid4().hex[:12]}"}
    r = qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "97.2"})
    assert r.status_code == 201 and r.json()["passed"] is False, "judged by the server from the spec limits"
    assert qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "99.5", "passed": False}).json()["passed"] is True  # client cannot override; retest supersedes
    assert qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Nope", "value": "1"}).status_code == 422
    assert qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Appearance"}).status_code == 422     # pass/fail needs 'passed'
    # cannot release with a mandatory test missing
    assert qa["qm"].post(f"/api/quality/inspections/{iid}/decide", headers=key(), json={"decision": "RELEASE"}).json()["detail"]["code"] == "mandatory_tests_missing"
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Appearance", "passed": True})
    # segregation: a tester who also holds decide rights cannot release; inspectors lack the permission anyway
    assert qa["inspector"].post(f"/api/quality/inspections/{iid}/decide", headers=key(), json={"decision": "RELEASE"}).status_code == 403
    # the direct inventory QC endpoint cannot bypass a governed batch
    assert qa["qm"].post(f"/api/inventory/batches/{batch}/qc-decision", headers=key(), json={"decision": "RELEASED"}).json()["detail"]["code"] == "inspection_required"
    assert N(qa["stores"].get("/api/inventory/availability", params={"item_id": m["item_id"]}).json()["total"]["available"]) == 0
    ok = qa["qm"].post(f"/api/quality/inspections/{iid}/decide", headers=key(), json={"decision": "RELEASE"})
    assert ok.status_code == 200 and ok.json()["decision"] == "RELEASE"
    assert N(qa["stores"].get("/api/inventory/availability", params={"item_id": m["item_id"]}).json()["total"]["available"]) == 100
    assert qa["qm"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "99"}).json()["detail"]["code"] == "inspection_decided"
    assert qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "99"}).json()["detail"]["code"] == "inspection_decided"
    # append-only results: the failing first reading is still in the table
    rows = db.execute(text("SELECT passed FROM inspection_results WHERE inspection_id=:i AND test_name='Assay' ORDER BY id"), {"i": iid}).all()
    assert [r[0] for r in rows] == [False, True]
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(text("UPDATE inspection_results SET passed = true"))
    integrity(db)


def test_failed_test_blocks_release_and_reject_quarantines_forever(qa, admin, db):
    m, batch, iid = incoming_setup(qa, admin)
    key = lambda: {"Idempotency-Key": f"key-dec-{uuid.uuid4().hex[:12]}"}
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "90"})
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Appearance", "passed": True})
    r = qa["qm"].post(f"/api/quality/inspections/{iid}/decide", headers=key(), json={"decision": "RELEASE"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "failed_tests"
    assert qa["qm"].post(f"/api/quality/inspections/{iid}/decide", headers=key(), json={"decision": "REJECT"}).status_code == 422   # reason required
    assert qa["qm"].post(f"/api/quality/inspections/{iid}/decide", headers=key(), json={"decision": "REJECT", "reason": "assay out of limits"}).status_code == 200
    batch_status = qa["qm"].get(f"/api/inventory/batches/{batch}").json()["batch"]["qc_status"]
    assert batch_status == "REJECTED"
    assert N(qa["stores"].get("/api/inventory/availability", params={"item_id": m["item_id"]}).json()["total"]["available"]) == 0
    integrity(db)


def test_critical_deviation_holds_batch_and_closure_requires_verified_capa(qa, admin, db, login):
    m, batch, iid = incoming_setup(qa, admin)
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "99"})
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Appearance", "passed": True})
    qa["qm"].post(f"/api/quality/inspections/{iid}/decide", headers={"Idempotency-Key": "key-dev-rel-" + uuid.uuid4().hex[:8]}, json={"decision": "RELEASE"})
    assert N(qa["stores"].get("/api/inventory/availability", params={"item_id": m["item_id"]}).json()["total"]["available"]) == 100
    d = qa["inspector"].post("/api/quality/deviations", json={"title": "Foreign particles", "description": "seen in drum 3", "classification": "CRITICAL", "batch_id": batch})
    assert d.status_code == 201 and d.json()["batch_held"] is True
    assert qa["qm"].get(f"/api/inventory/batches/{batch}").json()["batch"]["qc_status"] == "HOLD"
    assert N(qa["stores"].get("/api/inventory/availability", params={"item_id": m["item_id"]}).json()["total"]["available"]) == 0, "QC hold blocks the stock"
    did = d.json()["id"]
    assert qa["qm"].post(f"/api/quality/deviations/{did}/close").json()["detail"]["code"] == "root_cause_required"
    qa["qm"].post(f"/api/quality/deviations/{did}/investigate", json={"root_cause": "supplier packaging damage"})
    assert qa["qm"].post(f"/api/quality/deviations/{did}/close").json()["detail"]["code"] == "capa_required"
    owner = login("quality_manager")
    capa = qa["qm"].post(f"/api/quality/deviations/{did}/capa", json={"kind": "CORRECTIVE", "description": "re-inspect supplier", "responsible_id": owner.user.id,
                                                                    "due_date": str(business_today() + timedelta(days=7))})
    cid = capa.json()["actions"][0]["id"]
    assert qa["qm"].post(f"/api/quality/deviations/{did}/close").json()["detail"]["code"] == "capa_not_verified"
    assert qa["qm2"].post(f"/api/quality/capa/{cid}/complete").json()["detail"]["code"] == "not_responsible"
    assert owner.post(f"/api/quality/capa/{cid}/complete").json()["status"] == "DONE"
    assert owner.post(f"/api/quality/capa/{cid}/verify").json()["detail"]["code"] == "segregation_of_duties"
    assert qa["qm2"].post(f"/api/quality/capa/{cid}/verify").json()["status"] == "VERIFIED"
    assert qa["inspector"].post(f"/api/quality/deviations/{did}/close").status_code == 403
    assert qa["qm2"].post(f"/api/quality/deviations/{did}/close").json()["status"] == "CLOSED"
    integrity(db)
