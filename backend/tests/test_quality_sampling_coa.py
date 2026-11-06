"""Sampling plans (exact sample-size maths, wired into inspections) and Certificates of Analysis (frozen snapshots of released inspections)."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.quality.ext_models import SamplingPlan
from app.quality.ext_service import sample_size
from scripts.check_integrity import run_checks
from tests.conftest import mk_master
from tests.test_inventory_procurement import N, po_to_approved, receive
from tests.test_rnd_quality import qa  # noqa: F401

key = lambda: {"Idempotency-Key": f"key-q-{uuid.uuid4().hex[:12]}"}


def plan(method, **kw):
    return SamplingPlan(code="X", name="x", method=method, min_samples=kw.pop("min_samples", 1), **kw)


@pytest.mark.parametrize("p,lot,expected", [
    (plan("ALL"), "25", 25),
    (plan("FIXED", fixed_qty=5), "3", 3),                                  # never more than the lot
    (plan("FIXED", fixed_qty=5), "100", 5),
    (plan("PERCENT", percent=Decimal("10")), "25", 3),                    # ceil(2.5)
    (plan("PERCENT", percent=Decimal("10")), "20", 2),                    # exact: no float drift
    (plan("PERCENT", percent=Decimal("0.5"), min_samples=3), "100", 3),   # min applies
    (plan("PERCENT", percent=Decimal("50"), max_samples=7), "100", 7),    # max applies
    (plan("SQRT_PLUS_ONE"), "100", 11),
    (plan("SQRT_PLUS_ONE"), "101", 12),                                    # ceil(10.05) + 1
    (plan("SQRT_PLUS_ONE"), "1", 1),
    (plan("ALL"), "0", 0),
    (plan("PERCENT", percent=Decimal("10")), "24.2", 3),                  # fractional lot rounds up to whole units first (25 units)
])
def test_sample_size_maths(p, lot, expected):
    assert sample_size(p, Decimal(lot)) == expected


def test_plan_api_validation_and_calculation(qa, admin):
    ok = qa["qm"].post("/api/quality/sampling-plans", json={"code": f"sq-{uuid.uuid4().hex[:5]}", "name": "Root n + 1", "method": "SQRT_PLUS_ONE"})
    assert ok.status_code == 201, ok.text
    pid = ok.json()["id"]
    assert qa["qm"].get(f"/api/quality/sampling-plans/{pid}/calculate", params={"lot_qty": "250"}).json()["sample_size"] == 17
    assert qa["qm"].get(f"/api/quality/sampling-plans/{pid}/calculate", params={"lot_qty": "0"}).status_code == 422
    for bad in ({"method": "FIXED"}, {"method": "PERCENT"}, {"method": "PERCENT", "percent": "0"}, {"method": "PERCENT", "percent": "101"},
                {"method": "ALL", "min_samples": 5, "max_samples": 2}, {"method": "BOGUS"}, {"method": "FIXED", "fixed_qty": 0}):
        assert qa["qm"].post("/api/quality/sampling-plans", json={"code": f"b-{uuid.uuid4().hex[:5]}", "name": "bad", **bad}).status_code == 422, bad
    dup = {"code": "DUP-PLAN", "name": "d", "method": "ALL"}
    assert qa["qm"].post("/api/quality/sampling-plans", json=dup).status_code == 201
    assert qa["qm"].post("/api/quality/sampling-plans", json=dup).status_code == 409
    assert qa["inspector"].post("/api/quality/sampling-plans", json={"code": "NOPE", "name": "n", "method": "ALL"}).status_code == 403


def released_inspection(qa, admin, with_plan=True):
    m = mk_master("COA")
    pid = None
    if with_plan:
        pid = qa["qm"].post("/api/quality/sampling-plans", json={"code": f"sp-{uuid.uuid4().hex[:6]}", "name": "Root n+1", "method": "SQRT_PLUS_ONE"}).json()["id"]
    spec = qa["qm"].post("/api/quality/specs", json={"item_id": m["item_id"], "stage": "INCOMING", "sampling_plan_id": pid, "tests": [
        {"test_name": "Assay", "unit": "%", "min_value": "98.0", "max_value": "101.0", "method": "HPLC"}, {"test_name": "Appearance", "is_pass_fail": True}]})
    assert spec.status_code == 201, spec.text
    po = po_to_approved(qa["buyer"], admin, m, qty="100")
    gr = receive(qa["stores"], po, m["wh1"], "100", f"DN-{uuid.uuid4().hex[:6]}", f"key-coa-{uuid.uuid4().hex[:12]}").json()
    batch = gr["lines"][0]["batch_id"]
    insp = qa["qm"].get("/api/quality/inspections", params={"batch_id": batch}).json()["items"][0]
    return m, batch, insp["id"], spec.json()


def test_inspection_records_lot_and_sample_size_from_the_spec_plan(qa, admin, db):
    m, batch, iid, spec = released_inspection(qa, admin)
    row = db.execute(text("SELECT lot_qty, sample_size FROM inspections WHERE id=:i"), {"i": iid}).one()
    assert (row[0], row[1]) == (N("100"), 11)
    assert qa["qm"].get(f"/api/quality/inspections/{iid}").json()["sample_size"] == 11
    m2, b2, i2, _ = released_inspection(qa, admin, with_plan=False)
    assert db.scalar(text("SELECT sample_size FROM inspections WHERE id=:i"), {"i": i2}) is None            # no plan = nothing recorded, nothing invented
    assert qa["qm"].post("/api/quality/specs", json={"item_id": m["item_id"], "stage": "FINISHED", "sampling_plan_id": 99999999,
                                                    "tests": [{"test_name": "pH", "min_value": "4", "max_value": "6"}]}).status_code == 422


def test_coa_only_for_released_inspections_and_is_a_frozen_snapshot(qa, admin, db):
    m, batch, iid, spec = released_inspection(qa, admin)
    early = qa["qm"].post(f"/api/quality/inspections/{iid}/coa")
    assert early.status_code == 409 and early.json()["detail"]["code"] == "not_released"
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "97.0"})          # fails first
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "99.2"})          # retest passes
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Appearance", "passed": True})
    assert qa["qm"].post(f"/api/quality/inspections/{iid}/decide", headers=key(), json={"decision": "RELEASE"}).status_code == 200
    assert qa["inspector"].post(f"/api/quality/inspections/{iid}/coa").status_code == 403                                   # tester cannot certify
    r = qa["qm"].post(f"/api/quality/inspections/{iid}/coa")
    assert r.status_code == 201, r.text
    c = r.json()
    snap = c["snapshot"]
    assert c["number"].startswith("COA/") and snap["decision"] == "RELEASE" and snap["sample_size"] == 11 and snap["spec_version"] == 1
    assay = next(t for t in snap["tests"] if t["test"] == "Assay")
    assert (assay["value"], assay["passed"], assay["min"], assay["max"], assay["method"]) == ("99.2000", True, "98.0000", "101.0000", "HPLC")   # the retest value, not the failed first reading
    assert qa["qm"].post(f"/api/quality/inspections/{iid}/coa").json()["detail"]["code"] == "coa_exists"
    assert qa["qm"].get(f"/api/quality/coa/{c['id']}").json()["snapshot"] == snap
    assert [x["id"] for x in qa["qm"].get("/api/quality/coa", params={"batch_id": batch}).json()["items"]] == [c["id"]]
    # DB-level: content is frozen, the row can't be deleted; only revocation updates it
    for sql in ("UPDATE certificates_of_analysis SET snapshot = '{}'::jsonb", "UPDATE certificates_of_analysis SET number = 'X'", "DELETE FROM certificates_of_analysis"):
        with pytest.raises(DBAPIError, match="append-only"):
            with db.begin_nested():
                db.execute(text(sql))
    assert qa["qm"].post(f"/api/quality/coa/{c['id']}/revoke", json={"reason": "x"}).status_code == 422
    rv = qa["qm"].post(f"/api/quality/coa/{c['id']}/revoke", json={"reason": "Wrong assay method recorded"})
    assert rv.status_code == 200 and rv.json()["status"] == "REVOKED"
    assert qa["qm"].post(f"/api/quality/coa/{c['id']}/revoke", json={"reason": "again please"}).status_code == 409
    with pytest.raises(DBAPIError, match="final"):
        with db.begin_nested():
            db.execute(text("UPDATE certificates_of_analysis SET revoke_reason = 'edited'"))
    bad = [x for x in run_checks(db.connection())["checks"] if not x["ok"]]
    assert not bad, bad


def test_no_certificate_for_a_rejected_batch(qa, admin):
    m, batch, iid, _ = released_inspection(qa, admin)
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "90"})
    assert qa["qm"].post(f"/api/quality/inspections/{iid}/decide", headers=key(), json={"decision": "REJECT", "reason": "assay out of limits"}).status_code == 200
    r = qa["qm"].post(f"/api/quality/inspections/{iid}/coa")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_released"
    assert qa["qm"].post("/api/quality/inspections/999999/coa").status_code == 404
