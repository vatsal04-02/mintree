"""Quality: specifications, inspections (server-side pass/fail), release decisions that drive batch QC status, deviations & CAPA."""
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.context import source_var
from app.core import activity
from app.audit import service as audit
from app.core import outbox
from app.core.context import request_id_var
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.inventory import service as inv
from app.quality.models import CapaAction, Deviation, Inspection, InspectionResult, QualitySpec, QualitySpecTest
from app.core.clock import business_today

DECISION_TO_QC = {"RELEASE": "RELEASED", "REJECT": "REJECTED", "HOLD": "HOLD"}


def create_spec(db: Session, cu: CurrentUser, *, item_id: int, stage: str, tests: list[dict], sampling_plan_id: int | None = None) -> dict:
    db.execute(text("SELECT id FROM items WHERE id = :i FOR NO KEY UPDATE"), {"i": item_id})        # serialise spec versioning per item
    cur = db.scalar(select(QualitySpec).where(QualitySpec.item_id == item_id, QualitySpec.stage == stage, QualitySpec.status == "ACTIVE"))
    ver = 1
    if cur:
        cur.status = "OBSOLETE"
        ver = cur.version_no + 1
        db.flush()
    if sampling_plan_id:
        from app.quality.ext_models import SamplingPlan
        plan = db.get(SamplingPlan, sampling_plan_id)
        if not plan or not plan.is_active:
            raise Unprocessable("Sampling plan not found or inactive")
    spec = QualitySpec(item_id=item_id, stage=stage, version_no=ver, created_by=cu.id, sampling_plan_id=sampling_plan_id)
    spec.tests = [QualitySpecTest(**t) for t in tests]
    db.add(spec)
    db.flush()
    audit.record(db, user=cu, action="create", entity="quality_spec", entity_id=spec.id, after={"item_id": item_id, "stage": stage, "version": ver})
    return spec_dict(spec)


def active_spec(db: Session, item_id: int, stage: str) -> QualitySpec | None:
    return db.scalar(select(QualitySpec).where(QualitySpec.item_id == item_id, QualitySpec.stage == stage, QualitySpec.status == "ACTIVE"))


def _new_inspection(db: Session, batch_id: int, item_id: int, stage: str, spec: QualitySpec) -> Inspection:
    n = db.execute(text("SELECT nextval('inspection_number_seq')")).scalar_one()
    ins = Inspection(number=f"QC/{business_today():%y}/{n:06d}", stage=stage, batch_id=batch_id, spec_id=spec.id, request_id=request_id_var.get())
    db.add(ins)
    db.flush()
    activity.emit(db, "quality.inspection.created", entity="inspection", entity_id=ins.id, number=ins.number, stage=stage)
    from app.quality.ext_service import apply_sampling
    apply_sampling(db, ins, spec)
    return ins


def create_inspection(db: Session, cu: CurrentUser, batch_id: int, stage: str) -> dict:
    b = db.execute(text("SELECT id, item_id, qc_status FROM inventory_batches WHERE id = :i"), {"i": batch_id}).mappings().first()
    if not b:
        raise NotFound("Batch")
    spec = active_spec(db, b["item_id"], stage)
    if not spec:
        raise BusinessConflict("no_active_spec", f"No active {stage} specification exists for this item")
    if db.scalar(select(Inspection.id).where(Inspection.batch_id == batch_id, Inspection.stage == stage, Inspection.status != "DECIDED")):
        raise BusinessConflict("inspection_open", "An inspection for this batch and stage is already open")
    ins = _new_inspection(db, batch_id, b["item_id"], stage, spec)
    audit.record(db, user=cu, action="create", entity="inspection", entity_id=ins.id, after={"number": ins.number, "batch_id": batch_id})
    return inspection_dict(db, ins)


def auto_inspection(db: Session, batch_id: int, item_id: int, stage: str) -> int | None:
    """Called by goods-receipt (INCOMING) and production completion (FINISHED): an inspection is created automatically when a spec exists."""
    spec = active_spec(db, item_id, stage)
    return _new_inspection(db, batch_id, item_id, stage, spec).id if spec else None


def auto_incoming(db: Session, batch_id: int, item_id: int) -> int | None:
    return auto_inspection(db, batch_id, item_id, "INCOMING")


def record_result(db: Session, cu: CurrentUser, inspection_id: int, *, test_name: str, value, passed: bool | None, observation: str | None) -> dict:
    ins = db.scalar(select(Inspection).where(Inspection.id == inspection_id).with_for_update())
    if not ins:
        raise NotFound("Inspection")
    if ins.status == "DECIDED":
        raise BusinessConflict("inspection_decided", "This inspection has been decided; results can no longer be added")
    test = db.scalar(select(QualitySpecTest).where(QualitySpecTest.spec_id == ins.spec_id, QualitySpecTest.test_name == test_name))
    if not test:
        raise Unprocessable(f"'{test_name}' is not part of this inspection's specification")
    if test.is_pass_fail:
        if passed is None:
            raise Unprocessable("This is a pass/fail test: 'passed' is required")
        ok, val = bool(passed), None
    else:
        if value is None:
            raise Unprocessable("A numeric value is required for this test")
        val = Decimal(str(value))
        ok = (test.min_value is None or val >= test.min_value) and (test.max_value is None or val <= test.max_value)   # server decides
    r = InspectionResult(inspection_id=ins.id, test_name=test_name, value=val, passed=ok, observation=observation, tested_by=cu.id)
    db.add(r)
    ins.status = "IN_PROGRESS"
    db.flush()
    audit.record(db, user=cu, action="result", entity="inspection", entity_id=ins.id, after={"test": test_name, "value": None if val is None else str(val), "passed": ok})
    return {"inspection_id": ins.id, "test_name": test_name, "value": None if val is None else str(val), "passed": ok}


def latest_results(db: Session, inspection_id: int) -> dict[str, InspectionResult]:
    out: dict[str, InspectionResult] = {}
    for r in db.scalars(select(InspectionResult).where(InspectionResult.inspection_id == inspection_id).order_by(InspectionResult.id)):
        out[r.test_name] = r          # a retest supersedes, but earlier rows remain in the table
    return out


def decide(db: Session, cu: CurrentUser, inspection_id: int, decision: str, reason: str | None) -> dict:
    ins = db.scalar(select(Inspection).where(Inspection.id == inspection_id).with_for_update())
    if not ins:
        raise NotFound("Inspection")
    if ins.status == "DECIDED":
        raise BusinessConflict("inspection_decided", "This inspection has already been decided")
    if decision != "RELEASE" and not reason:
        raise Unprocessable("A reason is required to reject or hold a batch")
    res = latest_results(db, ins.id)
    if decision == "RELEASE":
        spec = db.get(QualitySpec, ins.spec_id)
        missing = [t.test_name for t in spec.tests if t.mandatory and t.test_name not in res]
        failed = [n for n, r in res.items() if not r.passed]
        if missing:
            raise BusinessConflict("mandatory_tests_missing", "Mandatory tests have no result", tests=missing)
        if failed:
            raise BusinessConflict("failed_tests", "Cannot release a batch with failing tests", tests=failed)
        if any(r.tested_by == cu.id for r in res.values()):
            raise BusinessConflict("segregation_of_duties", "The person who tested cannot also release the batch")
    qc = inv.set_qc_status(db, batch_id=ins.batch_id, decision=DECISION_TO_QC[decision], reason=reason or "Inspection passed", actor_id=cu.id)
    ins.status, ins.decision, ins.decision_reason, ins.decided_by, ins.decided_at = "DECIDED", decision, reason, cu.id, datetime.now(timezone.utc)
    audit.record(db, user=cu, action=f"inspection_{decision.lower()}", entity="inspection", entity_id=ins.id,
                 before={"qc_status": qc["from"]}, after={"qc_status": qc["to"]}, reason=reason)
    outbox.add(db, event_type="quality.inspection.decided", aggregate_type="inspection", aggregate_id=ins.id,
               payload={"inspection_id": ins.id, "batch_id": ins.batch_id, "decision": decision, "stage": ins.stage, "number": ins.number, "actor_id": cu.id, **({"source": s} if (s := source_var.get()) else {})})
    return inspection_dict(db, ins)


# ------------------------------------------------------------------ deviations & CAPA
def raise_deviation(db: Session, cu: CurrentUser, *, title: str, description: str, classification: str, batch_id: int | None) -> dict:
    n = db.execute(text("SELECT nextval('deviation_number_seq')")).scalar_one()
    d = Deviation(number=f"DEV/{business_today():%y}/{n:05d}", title=title, description=description, classification=classification, batch_id=batch_id, raised_by=cu.id)
    db.add(d)
    db.flush()
    held = None
    if classification == "CRITICAL" and batch_id:      # a critical deviation blocks the affected stock immediately
        st = db.execute(text("SELECT qc_status FROM inventory_batches WHERE id = :b"), {"b": batch_id}).scalar()
        if st in ("QUARANTINE", "RELEASED"):
            held = inv.set_qc_status(db, batch_id=batch_id, decision="HOLD", reason=f"Critical deviation {d.number}", actor_id=cu.id)
    audit.record(db, user=cu, action="raise", entity="deviation", entity_id=d.id, after={"number": d.number, "class": classification, "batch_held": bool(held)})
    return deviation_dict(d) | {"batch_held": bool(held)}


def investigate(db: Session, cu: CurrentUser, dev_id: int, root_cause: str) -> dict:
    d = _dev(db, dev_id)
    if d.status == "CLOSED":
        raise BusinessConflict("deviation_closed", "Deviation is closed")
    d.root_cause, d.status = root_cause, "INVESTIGATION"
    audit.record(db, user=cu, action="investigate", entity="deviation", entity_id=d.id)
    return deviation_dict(d)


def _dev(db: Session, dev_id: int) -> Deviation:
    d = db.scalar(select(Deviation).where(Deviation.id == dev_id).with_for_update())
    if not d:
        raise NotFound("Deviation")
    return d


def add_capa(db: Session, cu: CurrentUser, dev_id: int, *, kind: str, description: str, responsible_id: int, due_date: date) -> dict:
    d = _dev(db, dev_id)
    if d.status == "CLOSED":
        raise BusinessConflict("deviation_closed", "Deviation is closed")
    a = CapaAction(kind=kind, description=description, responsible_id=responsible_id, due_date=due_date)
    d.actions.append(a)          # through the relationship so the returned view includes it
    db.flush()
    audit.record(db, user=cu, action="capa_add", entity="deviation", entity_id=d.id, after={"capa_id": a.id, "kind": kind})
    return deviation_dict(d)


def capa_step(db: Session, cu: CurrentUser, capa_id: int, action: str) -> dict:
    a = db.scalar(select(CapaAction).where(CapaAction.id == capa_id).with_for_update())
    if not a:
        raise NotFound("CAPA action")
    if action == "complete":
        if a.status != "OPEN":
            raise BusinessConflict("invalid_status", f"Cannot complete an action in status {a.status}")
        if a.responsible_id != cu.id:
            raise BusinessConflict("not_responsible", "Only the responsible person can complete this action")
        a.status, a.completed_at = "DONE", datetime.now(timezone.utc)
    else:   # verify
        if a.status != "DONE":
            raise BusinessConflict("invalid_status", "Only completed actions can be verified")
        if a.responsible_id == cu.id:
            raise BusinessConflict("segregation_of_duties", "The responsible person cannot verify their own action")
        a.status, a.verified_by = "VERIFIED", cu.id
    audit.record(db, user=cu, action=f"capa_{action}", entity="capa_action", entity_id=a.id)
    return {"id": a.id, "status": a.status}


def close_deviation(db: Session, cu: CurrentUser, dev_id: int) -> dict:
    d = _dev(db, dev_id)
    if d.status == "CLOSED":
        raise BusinessConflict("deviation_closed", "Deviation is already closed")
    if not d.root_cause:
        raise BusinessConflict("root_cause_required", "Record the root cause before closing")
    if d.classification != "MINOR" and not d.actions:
        raise BusinessConflict("capa_required", "Major and critical deviations need at least one CAPA action")
    open_ = [a.id for a in d.actions if a.status != "VERIFIED"]
    if open_:
        raise BusinessConflict("capa_not_verified", "All CAPA actions must be verified before closure", actions=open_)
    if d.raised_by == cu.id:
        raise BusinessConflict("segregation_of_duties", "The person who raised a deviation cannot close it")
    d.status, d.closed_by, d.closed_at = "CLOSED", cu.id, datetime.now(timezone.utc)
    audit.record(db, user=cu, action="close", entity="deviation", entity_id=d.id)
    return deviation_dict(d)


# ------------------------------------------------------------------ views
def spec_dict(s: QualitySpec) -> dict:
    return {"id": s.id, "item_id": s.item_id, "stage": s.stage, "version_no": s.version_no, "status": s.status, "sampling_plan_id": s.sampling_plan_id,
            "tests": [{"test_name": t.test_name, "min": None if t.min_value is None else str(t.min_value), "max": None if t.max_value is None else str(t.max_value),
                       "pass_fail": t.is_pass_fail, "mandatory": t.mandatory} for t in s.tests]}


def inspection_dict(db: Session, i: Inspection) -> dict:
    res = latest_results(db, i.id)
    return {"id": i.id, "number": i.number, "stage": i.stage, "batch_id": i.batch_id, "status": i.status, "decision": i.decision,
            "lot_qty": None if i.lot_qty is None else str(i.lot_qty), "sample_size": i.sample_size,
            "results": [{"test_name": n, "value": None if r.value is None else str(r.value), "passed": r.passed} for n, r in res.items()]}


def deviation_dict(d: Deviation) -> dict:
    return {"id": d.id, "number": d.number, "title": d.title, "classification": d.classification, "status": d.status, "batch_id": d.batch_id, "root_cause": d.root_cause,
            "actions": [{"id": a.id, "kind": a.kind, "status": a.status, "responsible_id": a.responsible_id, "due_date": a.due_date} for a in d.actions]}
