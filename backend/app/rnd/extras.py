"""R&D extras: development projects, structured trial observations, formula version comparison."""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.rnd.models import DevelopmentProject, Formulation, FormulationVersion, LabTrial, TrialObservation

FLOW = {"CONCEPT": {"DEVELOPMENT", "CANCELLED"}, "DEVELOPMENT": {"TRIALS", "CANCELLED"}, "TRIALS": {"DEVELOPMENT", "APPROVED", "CANCELLED"}, "APPROVED": {"LAUNCHED"}, "LAUNCHED": set(), "CANCELLED": set()}


def create_project(db: Session, cu: CurrentUser, data: dict) -> dict:
    p = DevelopmentProject(**data, created_by=cu.id)
    db.add(p)
    db.flush()
    audit.record(db, user=cu, action="create", entity="development_project", entity_id=p.id)
    return project_dict(p)


def move_project(db: Session, cu: CurrentUser, pid: int, status: str) -> dict:
    p = db.scalar(select(DevelopmentProject).where(DevelopmentProject.id == pid).with_for_update())
    if not p:
        raise NotFound("Project")
    if status not in FLOW[p.status]:
        raise BusinessConflict("invalid_transition", f"A project cannot move from {p.status} to {status}")
    if status == "APPROVED" and not db.scalar(select(FormulationVersion.id).join(Formulation, Formulation.id == FormulationVersion.formulation_id)
                                              .where(Formulation.project_id == pid, FormulationVersion.status == "APPROVED").limit(1)):
        raise BusinessConflict("no_approved_formulation", "A project can be approved only once one of its formulations has an approved version")
    audit.record(db, user=cu, action="status", entity="development_project", entity_id=p.id, before={"status": p.status}, after={"status": status})
    p.status = status
    return project_dict(p)


def link_formulation(db: Session, cu: CurrentUser, pid: int, formulation_id: int) -> dict:
    if not db.get(DevelopmentProject, pid):
        raise NotFound("Project")
    f = db.get(Formulation, formulation_id)
    if not f:
        raise NotFound("Formulation")
    f.project_id = pid
    audit.record(db, user=cu, action="link", entity="development_project", entity_id=pid, after={"formulation_id": formulation_id})
    return {"project_id": pid, "formulation_id": formulation_id}


def project_dict(p: DevelopmentProject) -> dict:
    return {"id": p.id, "code": p.code, "name": p.name, "concept": p.concept, "product_category": p.product_category, "status": p.status,
            "lead_researcher_id": p.lead_researcher_id, "target_launch_date": p.target_launch_date}


def add_observation(db: Session, cu: CurrentUser, trial_id: int, data: dict) -> dict:
    t = db.get(LabTrial, trial_id)
    if not t:
        raise NotFound("Trial")
    if t.status in ("PASSED", "FAILED"):
        raise BusinessConflict("trial_concluded", "Observations cannot be added to a concluded trial")
    o = TrialObservation(trial_id=trial_id, observed_by=cu.id, **data)
    db.add(o)
    db.flush()
    return {"id": o.id, "trial_id": trial_id, "kind": o.kind, "result": o.result}


def observations(db: Session, trial_id: int) -> list[dict]:
    return [{"id": o.id, "kind": o.kind, "parameter": o.parameter, "value": o.value, "result": o.result, "notes": o.notes, "observed_by": o.observed_by, "observed_at": o.observed_at}
            for o in db.scalars(select(TrialObservation).where(TrialObservation.trial_id == trial_id).order_by(TrialObservation.id))]


def compare_versions(db: Session, a_id: int, b_id: int) -> dict:
    """Ingredient-level diff of two versions of the SAME formulation: added / removed / changed (percentage and quantity at each batch size)."""
    va, vb = db.get(FormulationVersion, a_id), db.get(FormulationVersion, b_id)
    if not va or not vb:
        raise NotFound("Formulation version")
    if va.formulation_id != vb.formulation_id:
        raise Unprocessable("Versions must belong to the same formulation")
    ia, ib = {i.item_id: i for i in va.ingredients}, {i.item_id: i for i in vb.ingredients}
    added = [{"item_id": k, "percentage": str(v.percentage)} for k, v in ib.items() if k not in ia]
    removed = [{"item_id": k, "percentage": str(v.percentage)} for k, v in ia.items() if k not in ib]
    changed = [{"item_id": k, "from_percentage": str(ia[k].percentage), "to_percentage": str(ib[k].percentage), "delta_percentage": str(ib[k].percentage - ia[k].percentage)}
               for k in ia if k in ib and ia[k].percentage != ib[k].percentage]
    return {"from": {"id": va.id, "version_no": va.version_no, "status": va.status}, "to": {"id": vb.id, "version_no": vb.version_no, "status": vb.status},
            "batch_size_changed": va.batch_size != vb.batch_size, "from_batch_size": str(va.batch_size), "to_batch_size": str(vb.batch_size),
            "process_instructions_changed": (va.process_instructions or "") != (vb.process_instructions or ""), "added": added, "removed": removed, "changed": changed,
            "identical": not (added or removed or changed or va.batch_size != vb.batch_size)}


def formula_cost(db, version_id: int) -> dict:
    """Material cost of one batch of a formulation version, computed by code in Decimal from what the company actually holds/pays:
    1) the item's current weighted-average stock cost (item_valuation value / qty) when it has stock;
    2) otherwise the unit price on the item's most recent purchase-order line;
    3) otherwise the ingredient is reported as 'unpriced' and the total is marked incomplete (it is never guessed or set to zero silently).
    Cost per unit of output = total / batch size. This is a standard-costing aid for R&D, not a posting."""
    from decimal import Decimal
    from sqlalchemy import text
    from app.core.errors import NotFound
    v = db.execute(text("SELECT fv.id, fv.version_no, fv.status, fv.batch_size, f.name FROM formulation_versions fv JOIN formulations f ON f.id = fv.formulation_id WHERE fv.id = :i"), {"i": version_id}).mappings().first()
    if not v:
        raise NotFound("Formulation version")
    lines = db.execute(text("""SELECT fi.line_no, it.id AS item_id, it.item_code, it.name, u.code AS uom, fi.qty, fi.percentage,
                                      (SELECT CASE WHEN val.qty > 0 THEN val.value / val.qty END FROM item_valuation val WHERE val.item_id = it.id) AS avg_cost,
                                      (SELECT poi.unit_price FROM purchase_order_items poi JOIN purchase_orders po ON po.id = poi.po_id WHERE poi.item_id = it.id AND po.status NOT IN ('DRAFT','CANCELLED')
                                        ORDER BY po.id DESC LIMIT 1) AS last_po_price
                                 FROM formulation_ingredients fi JOIN items it ON it.id = fi.item_id JOIN units_of_measure u ON u.id = it.uom_id WHERE fi.version_id = :i ORDER BY fi.line_no"""), {"i": version_id}).mappings().all()
    out, total, complete = [], Decimal(0), True
    for l in lines:
        unit, src = (Decimal(l["avg_cost"]), "average stock cost") if l["avg_cost"] is not None else ((Decimal(l["last_po_price"]), "last purchase price") if l["last_po_price"] is not None else (None, "unpriced"))
        amount = None if unit is None else (Decimal(l["qty"]) * unit).quantize(Decimal("0.01"))
        if amount is None:
            complete = False
        else:
            total += amount
        out.append({"line_no": l["line_no"], "item_code": l["item_code"], "name": l["name"], "uom": l["uom"], "qty": str(l["qty"]), "percentage": None if l["percentage"] is None else str(l["percentage"]),
                    "unit_cost": None if unit is None else str(unit.quantize(Decimal("0.0001"))), "cost_source": src, "amount": None if amount is None else str(amount)})
    batch = Decimal(v["batch_size"]) if v["batch_size"] else None
    return {"version_id": v["id"], "formulation": v["name"], "version_no": v["version_no"], "status": v["status"], "batch_size": None if batch is None else str(batch), "lines": out,
            "material_cost": str(total), "complete": complete, "cost_per_unit": None if (not batch or not complete) else str((total / batch).quantize(Decimal("0.0001"))),
            "note": "Material cost only (no labour, overhead or wastage). Cost per unit is shown only when every ingredient is priced."}
