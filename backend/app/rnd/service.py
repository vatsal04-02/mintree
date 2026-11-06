"""R&D: formulation versions with approval and immutable approved content (also enforced by DB triggers)."""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from psycopg import errors as pgerr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core import outbox
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.masters.models import Item
from app.rnd.models import Formulation, FormulationIngredient, FormulationVersion, LabTrial
from app.core.clock import business_today

P5 = Decimal("0.00001")


def _ingredients(db: Session, batch_size: Decimal, rows: list[dict]) -> list[FormulationIngredient]:
    if len({r["item_id"] for r in rows}) != len(rows):
        raise Unprocessable("An ingredient may appear only once per version")
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_({r["item_id"] for r in rows})))}
    out = []
    for n, r in enumerate(rows, 1):
        it = items.get(r["item_id"])
        if not it or not it.is_active or it.kind not in ("RM", "PM", "BULK"):
            raise Unprocessable(f"Ingredient item {r['item_id']} is not an active raw material / packaging / bulk item")
        q = Decimal(str(r["qty"]))
        out.append(FormulationIngredient(line_no=n, item_id=it.id, qty=q, percentage=(q / batch_size * 100).quantize(P5, rounding=ROUND_HALF_UP)))
    return out


def create_formulation(db: Session, cu: CurrentUser, *, code: str, name: str, product_item_id: int, batch_size, process_instructions: str | None,
                       ingredients: list[dict]) -> dict:
    prod = db.get(Item, product_item_id)
    if not prod or prod.kind not in ("BULK", "FG"):
        raise Unprocessable("A formulation makes a BULK or FG item")
    bs = Decimal(str(batch_size))
    f = Formulation(code=code.upper(), name=name, product_item_id=product_item_id, created_by=cu.id)
    v = FormulationVersion(version_no=1, batch_size=bs, process_instructions=process_instructions, created_by=cu.id, revision_note="Initial version")
    v.ingredients = _ingredients(db, bs, ingredients)
    f.versions = [v]
    db.add(f)
    try:
        db.flush()
    except IntegrityError as e:
        if isinstance(e.orig, pgerr.UniqueViolation):
            raise BusinessConflict("duplicate_code", "A formulation with this code already exists") from e
        raise
    audit.record(db, user=cu, action="create", entity="formulation", entity_id=f.id, after={"code": f.code})
    return formulation_dict(f)


def get_version(db: Session, version_id: int, lock: bool = False) -> FormulationVersion:
    q = select(FormulationVersion).where(FormulationVersion.id == version_id)
    v = db.scalar(q.with_for_update() if lock else q)
    if not v:
        raise NotFound("Formulation version")
    return v


def new_version(db: Session, cu: CurrentUser, formulation_id: int, from_version_no: int, revision_note: str) -> dict:
    f = db.scalar(select(Formulation).where(Formulation.id == formulation_id).with_for_update())     # serialises version creation
    if not f:
        raise NotFound("Formulation")
    src = next((v for v in f.versions if v.version_no == from_version_no), None)
    if not src:
        raise NotFound("Source version")
    if any(v.status in ("DRAFT", "SUBMITTED") for v in f.versions):
        raise BusinessConflict("open_version_exists", "Finish or reject the open version before creating another")
    nv = FormulationVersion(formulation_id=f.id, version_no=max(v.version_no for v in f.versions) + 1, batch_size=src.batch_size,
                            process_instructions=src.process_instructions, revision_note=revision_note, created_by=cu.id)
    nv.ingredients = [FormulationIngredient(line_no=i.line_no, item_id=i.item_id, qty=i.qty, percentage=i.percentage) for i in src.ingredients]
    db.add(nv)
    db.flush()
    audit.record(db, user=cu, action="new_version", entity="formulation", entity_id=f.id, after={"version_no": nv.version_no}, reason=revision_note)
    return version_dict(nv)


def update_draft(db: Session, cu: CurrentUser, version_id: int, *, batch_size, process_instructions, ingredients: list[dict] | None) -> dict:
    v = get_version(db, version_id, lock=True)
    if v.status != "DRAFT":
        raise BusinessConflict("version_not_editable", f"Only DRAFT versions can be edited (this one is {v.status}); create a new version")
    bs = Decimal(str(batch_size)) if batch_size is not None else v.batch_size
    v.batch_size = bs
    if process_instructions is not None:
        v.process_instructions = process_instructions
    if ingredients is not None:
        for i in list(v.ingredients):
            db.delete(i)
        db.flush()
        v.ingredients = _ingredients(db, bs, ingredients)
    db.flush()
    audit.record(db, user=cu, action="update", entity="formulation_version", entity_id=v.id)
    return version_dict(v)


def create_trial(db: Session, cu: CurrentUser, version_id: int, quantity) -> dict:
    v = get_version(db, version_id, lock=True)
    if v.status not in ("DRAFT", "SUBMITTED"):
        raise BusinessConflict("version_not_open", "Trials are run against open (draft/submitted) versions")
    n = (db.scalar(select(LabTrial.trial_no).where(LabTrial.version_id == v.id).order_by(LabTrial.trial_no.desc()).limit(1)) or 0) + 1
    t = LabTrial(version_id=v.id, trial_no=n, quantity=quantity, researcher_id=cu.id, status="IN_PROGRESS")
    db.add(t)
    db.flush()
    return trial_dict(t)


def conclude_trial(db: Session, cu: CurrentUser, trial_id: int, status: str, observations: str, stability_note: str | None) -> dict:
    t = db.scalar(select(LabTrial).where(LabTrial.id == trial_id).with_for_update())
    if not t:
        raise NotFound("Trial")
    if t.status in ("PASSED", "FAILED"):
        raise BusinessConflict("trial_concluded", "This trial has already been concluded")
    t.status, t.observations, t.stability_note, t.concluded_at = status, observations, stability_note, datetime.now(timezone.utc)
    audit.record(db, user=cu, action=f"trial_{status.lower()}", entity="lab_trial", entity_id=t.id, reason=observations[:200])
    return trial_dict(t)


def submit(db: Session, cu: CurrentUser, version_id: int) -> dict:
    v = get_version(db, version_id, lock=True)
    if v.status != "DRAFT":
        raise BusinessConflict("invalid_status", f"Cannot submit a version in status {v.status}")
    total = sum((i.percentage for i in v.ingredients), Decimal(0))
    if not v.ingredients or abs(total - 100) > Decimal("0.01"):
        raise BusinessConflict("percentages_not_100", f"Ingredient percentages add up to {total}, not 100", total=str(total))
    if not db.scalar(select(LabTrial.id).where(LabTrial.version_id == v.id, LabTrial.status == "PASSED").limit(1)):
        raise BusinessConflict("no_passed_trial", "A passed lab trial is required before a formulation can be submitted for approval")
    v.status = "SUBMITTED"
    audit.record(db, user=cu, action="submit", entity="formulation_version", entity_id=v.id)
    return version_dict(v)


def approve(db: Session, cu: CurrentUser, version_id: int) -> dict:
    v = get_version(db, version_id, lock=True)
    if v.status != "SUBMITTED":
        raise BusinessConflict("invalid_status", f"Cannot approve a version in status {v.status}")
    if v.created_by == cu.id:
        raise BusinessConflict("self_approval_not_allowed", "You cannot approve a formulation version you created")
    db.execute(update(FormulationVersion).where(FormulationVersion.formulation_id == v.formulation_id, FormulationVersion.status == "APPROVED")
               .values(status="OBSOLETE"))                       # previous current version steps down first (unique current index)
    v.status, v.approved_by, v.approved_at, v.effective_date = "APPROVED", cu.id, datetime.now(timezone.utc), business_today()
    db.flush()
    audit.record(db, user=cu, action="approve", entity="formulation_version", entity_id=v.id, after={"version_no": v.version_no})
    outbox.add(db, event_type="rnd.formulation.approved", aggregate_type="formulation_version", aggregate_id=v.id,
               payload={"version_id": v.id, "formulation_id": v.formulation_id, "version_no": v.version_no})
    return version_dict(v)


def reject(db: Session, cu: CurrentUser, version_id: int, reason: str) -> dict:
    v = get_version(db, version_id, lock=True)
    if v.status != "SUBMITTED":
        raise BusinessConflict("invalid_status", f"Cannot reject a version in status {v.status}")
    v.status, v.rejection_reason = "DRAFT", reason
    audit.record(db, user=cu, action="reject", entity="formulation_version", entity_id=v.id, reason=reason)
    return version_dict(v)


def version_dict(v: FormulationVersion) -> dict:
    return {"id": v.id, "formulation_id": v.formulation_id, "version_no": v.version_no, "status": v.status, "batch_size": str(v.batch_size),
            "revision_note": v.revision_note, "approved_by": v.approved_by, "effective_date": v.effective_date, "rejection_reason": v.rejection_reason,
            "ingredients": [{"line_no": i.line_no, "item_id": i.item_id, "qty": str(i.qty), "percentage": str(i.percentage)} for i in v.ingredients]}


def formulation_dict(f: Formulation) -> dict:
    return {"id": f.id, "code": f.code, "name": f.name, "product_item_id": f.product_item_id, "versions": [version_dict(v) for v in f.versions]}


def trial_dict(t: LabTrial) -> dict:
    return {"id": t.id, "version_id": t.version_id, "trial_no": t.trial_no, "status": t.status, "quantity": str(t.quantity), "observations": t.observations}
