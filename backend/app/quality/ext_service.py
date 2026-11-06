from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core import outbox
from app.core.clock import business_today
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.quality.ext_models import CertificateOfAnalysis, SamplingPlan
from app.quality.models import Inspection, QualitySpec
from app.quality.service import latest_results


def _ceil(v: Decimal) -> int:
    return int(v.to_integral_value(rounding=ROUND_CEILING))


def sample_size(plan: SamplingPlan, lot_qty: Decimal) -> int:
    """Units to test from a lot of `lot_qty` units. Exact Decimal maths (no floats); bounded by the lot itself and the plan's min/max."""
    lot = _ceil(Decimal(lot_qty))
    if lot <= 0:
        return 0
    if plan.method == "ALL":
        n = lot
    elif plan.method == "FIXED":
        n = plan.fixed_qty
    elif plan.method == "PERCENT":
        n = _ceil(Decimal(lot) * plan.percent / 100)
    else:                                                        # SQRT_PLUS_ONE: the classic "root n + 1" for incoming lots
        n = _ceil(Decimal(lot).sqrt()) + 1
    n = max(n, plan.min_samples)
    if plan.max_samples:
        n = min(n, plan.max_samples)
    return min(n, lot)


def batch_lot_qty(db: Session, batch_id: int) -> Decimal:
    return db.scalar(text("SELECT COALESCE(sum(qty_physical),0) FROM stock_balances WHERE batch_id = :b"), {"b": batch_id}) or Decimal(0)


def apply_sampling(db: Session, ins: Inspection, spec: QualitySpec) -> None:
    """Called when an inspection is created: record the lot size and the number of units the spec's sampling plan requires."""
    ins.lot_qty = batch_lot_qty(db, ins.batch_id)
    plan = db.get(SamplingPlan, spec.sampling_plan_id) if spec.sampling_plan_id else None
    ins.sample_size = sample_size(plan, ins.lot_qty) if plan and plan.is_active else None


# ------------------------------------------------------------------ Certificate of Analysis
def issue_coa(db: Session, cu: CurrentUser, inspection_id: int) -> dict:
    ins = db.scalar(select(Inspection).where(Inspection.id == inspection_id).with_for_update())
    if not ins:
        raise NotFound("Inspection")
    if ins.status != "DECIDED" or ins.decision != "RELEASE":
        raise BusinessConflict("not_released", "A certificate of analysis is issued only for an inspection that was decided RELEASE")
    if db.scalar(select(CertificateOfAnalysis.id).where(CertificateOfAnalysis.inspection_id == ins.id)):
        raise BusinessConflict("coa_exists", "A certificate has already been issued for this inspection")
    spec = db.get(QualitySpec, ins.spec_id)
    res = latest_results(db, ins.id)
    item = db.execute(text("SELECT i.id, i.item_code, i.name FROM items i JOIN inventory_batches b ON b.item_id = i.id WHERE b.id = :b"), {"b": ins.batch_id}).mappings().one()
    batch = db.execute(text("SELECT batch_no, supplier_lot_no, mfg_date, expiry_date FROM inventory_batches WHERE id = :b"), {"b": ins.batch_id}).mappings().one()
    tests = []
    for t in spec.tests:
        r = res.get(t.test_name)
        tests.append({"test": t.test_name, "method": t.method, "unit": t.unit, "min": None if t.min_value is None else str(t.min_value), "max": None if t.max_value is None else str(t.max_value),
                      "pass_fail": t.is_pass_fail, "mandatory": t.mandatory, "value": None if not r or r.value is None else str(r.value), "passed": None if not r else r.passed})
    snap = {"inspection": ins.number, "stage": ins.stage, "spec_version": spec.version_no, "item": dict(item), "batch": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in batch.items()},
            "lot_qty": None if ins.lot_qty is None else str(ins.lot_qty), "sample_size": ins.sample_size, "decision": ins.decision, "decided_at": ins.decided_at.isoformat(), "tests": tests}
    n = db.execute(text("SELECT nextval('coa_number_seq')")).scalar_one()
    c = CertificateOfAnalysis(number=f"COA/{business_today():%y}/{n:06d}", inspection_id=ins.id, batch_id=ins.batch_id, item_id=item["id"], snapshot=snap, issued_by=cu.id)
    db.add(c)
    db.flush()
    db.refresh(c)
    audit.record(db, user=cu, action="issue", entity="certificate_of_analysis", entity_id=c.id, after={"number": c.number, "batch_id": c.batch_id})
    outbox.add(db, event_type="quality.coa.issued", aggregate_type="certificate_of_analysis", aggregate_id=c.id, payload={"number": c.number, "batch_id": c.batch_id})
    return coa_dict(c)


def revoke_coa(db: Session, cu: CurrentUser, coa_id: int, reason: str) -> dict:
    c = db.scalar(select(CertificateOfAnalysis).where(CertificateOfAnalysis.id == coa_id).with_for_update())
    if not c:
        raise NotFound("Certificate")
    if c.status == "REVOKED":
        raise BusinessConflict("already_revoked", "This certificate is already revoked")
    if not reason or len(reason.strip()) < 5:
        raise Unprocessable("A revocation reason is required")
    c.status, c.revoked_by, c.revoked_at, c.revoke_reason = "REVOKED", cu.id, text("now()"), reason.strip()
    db.flush()
    db.refresh(c)
    audit.record(db, user=cu, action="revoke", entity="certificate_of_analysis", entity_id=c.id, reason=reason)
    return coa_dict(c)


def coa_dict(c: CertificateOfAnalysis) -> dict:
    return {"id": c.id, "number": c.number, "inspection_id": c.inspection_id, "batch_id": c.batch_id, "status": c.status, "issued_by": c.issued_by, "issued_at": c.issued_at,
            "revoke_reason": c.revoke_reason, "snapshot": c.snapshot}
