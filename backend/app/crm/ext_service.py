"""CRM workflows: lead conversion (match-or-create the master customer), opportunities with a guarded stage machine, activities."""
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core import outbox
from app.core.clock import business_today
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.crm.ext_models import STAGE_PROBABILITY, CrmActivity, Opportunity, OpportunityStageHistory
from app.crm.models import Lead
from app.masters.models import Customer
from app.sales.models import SalesQuotation

NEXT = {"PROSPECTING": {"QUALIFIED", "PROPOSAL", "LOST"}, "QUALIFIED": {"PROPOSAL", "LOST"}, "PROPOSAL": {"NEGOTIATION", "WON", "LOST"},
        "NEGOTIATION": {"WON", "LOST", "PROPOSAL"}, "WON": set(), "LOST": set()}
ORDER = ["PROSPECTING", "QUALIFIED", "PROPOSAL", "NEGOTIATION"]


# ------------------------------------------------------------------ lead -> customer
def convert_lead(db: Session, cu: CurrentUser, *, lead_id: int, customer_id: int | None, customer_type: str, create_if_no_match: bool = True) -> dict:
    """Qualified lead -> the ONE master customer record. Existing customer (explicit id, or same email/phone) is linked, never duplicated."""
    lead = db.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise NotFound("Lead")
    if lead.status != "QUALIFIED":
        raise BusinessConflict("lead_not_qualified", f"Only a qualified lead can be converted (status {lead.status})")
    matched, cust = False, None
    if customer_id:
        cust = db.get(Customer, customer_id)
        if not cust or not cust.is_active:
            raise Unprocessable("Customer not found or inactive")
        matched = True
    else:
        conds = []
        if lead.email:
            conds.append(func.lower(Customer.email) == lead.email.lower())
        if lead.phone:
            conds.append(Customer.phone == lead.phone)
        if conds:
            from sqlalchemy import or_
            found = db.scalars(select(Customer).where(or_(*conds), Customer.is_active.is_(True)).order_by(Customer.id)).all()
            if len(found) > 1:
                raise BusinessConflict("ambiguous_customer_match", "More than one existing customer matches this lead; choose one explicitly", candidates=[c.id for c in found])
            if found:
                cust, matched = found[0], True
    if cust is None:
        if not create_if_no_match:
            raise BusinessConflict("no_customer_match", "No existing customer matches this lead")
        cust = Customer(code=f"CUS-L{lead.id:06d}", name=lead.company or lead.name, customer_type=customer_type, contact_name=lead.name, phone=lead.phone, email=lead.email)
        db.add(cust)
        db.flush()
        audit.record(db, user=cu, action="create", entity="customer", entity_id=cust.id, after={"code": cust.code, "from_lead": lead.id})
    lead.status, lead.converted_customer_id = "WON", cust.id
    for o in db.scalars(select(Opportunity).where(Opportunity.lead_id == lead.id, Opportunity.customer_id.is_(None))):
        o.customer_id = cust.id                                         # open opportunities of this lead now point at the master customer
    audit.record(db, user=cu, action="convert", entity="lead", entity_id=lead.id, before={"status": "QUALIFIED"}, after={"status": "WON", "customer_id": cust.id, "matched_existing": matched})
    outbox.add(db, event_type="crm.lead.converted", aggregate_type="lead", aggregate_id=lead.id, payload={"lead_id": lead.id, "customer_id": cust.id, "matched_existing": matched, "actor_id": cu.id})
    return {"lead_id": lead.id, "status": "WON", "customer_id": cust.id, "customer_code": cust.code, "matched_existing": matched}


# ------------------------------------------------------------------ opportunities
def _history(db: Session, o: Opportunity, frm: str | None, to: str, note: str | None, uid: int) -> None:
    db.add(OpportunityStageHistory(opportunity_id=o.id, from_stage=frm, to_stage=to, note=note, changed_by=uid))


def create_opportunity(db: Session, cu: CurrentUser, *, name: str, lead_id: int | None, customer_id: int | None, expected_value: Decimal, expected_close, owner_id: int | None) -> dict:
    if not lead_id and not customer_id:
        raise Unprocessable("An opportunity needs a lead or a customer")
    if lead_id:
        lead = db.get(Lead, lead_id)
        if not lead:
            raise NotFound("Lead")
        if lead.status not in ("QUALIFIED", "WON"):
            raise BusinessConflict("lead_not_qualified", "Opportunities are raised from qualified leads (or existing customers)")
        if lead.converted_customer_id and not customer_id:
            customer_id = lead.converted_customer_id
    if customer_id and not db.get(Customer, customer_id):
        raise Unprocessable("Customer not found")
    if expected_close and expected_close < business_today():
        raise Unprocessable("Expected close date is in the past")
    n = db.execute(text("SELECT nextval('opportunity_seq')")).scalar_one()
    o = Opportunity(number=f"OPP/{business_today():%y}/{n:06d}", name=name, lead_id=lead_id, customer_id=customer_id, expected_value=expected_value,
                    expected_close=expected_close, owner_id=owner_id or cu.id, created_by=cu.id, probability=STAGE_PROBABILITY["PROSPECTING"])
    db.add(o)
    db.flush()
    _history(db, o, None, "PROSPECTING", "created", cu.id)
    audit.record(db, user=cu, action="create", entity="opportunity", entity_id=o.id, after={"number": o.number, "value": str(expected_value)})
    return opp_dict(o)


def _get(db: Session, oid: int) -> Opportunity:
    o = db.scalar(select(Opportunity).where(Opportunity.id == oid).with_for_update())
    if not o:
        raise NotFound("Opportunity")
    return o


def attach_quotation(db: Session, cu: CurrentUser, *, opportunity_id: int, quotation_id: int, expected_version: int) -> dict:
    o = _get(db, opportunity_id)
    if o.version != expected_version:
        raise BusinessConflict("version_conflict", "The opportunity was changed by someone else. Reload and try again.")
    if o.stage in ("WON", "LOST"):
        raise BusinessConflict("opportunity_closed", f"Opportunity is {o.stage}")
    if not o.customer_id:
        raise BusinessConflict("customer_required", "Convert the lead to a customer before attaching a quotation")
    q = db.scalar(select(SalesQuotation).where(SalesQuotation.id == quotation_id).with_for_update())
    if not q:
        raise NotFound("Quotation")
    if q.customer_id != o.customer_id:
        raise BusinessConflict("customer_mismatch", "The quotation belongs to a different customer")
    if db.scalar(select(Opportunity.id).where(Opportunity.quotation_id == q.id, Opportunity.id != o.id)):
        raise BusinessConflict("quotation_already_linked", "That quotation already backs another opportunity")
    o.quotation_id = q.id
    o.expected_value = q.total
    o.version += 1
    if o.stage in ("PROSPECTING", "QUALIFIED"):
        _history(db, o, o.stage, "PROPOSAL", f"quotation {q.number} attached", cu.id)
        o.stage, o.probability = "PROPOSAL", STAGE_PROBABILITY["PROPOSAL"]
    audit.record(db, user=cu, action="attach_quotation", entity="opportunity", entity_id=o.id, after={"quotation": q.number, "value": str(q.total)})
    return opp_dict(o)


def move_stage(db: Session, cu: CurrentUser, *, opportunity_id: int, stage: str, expected_version: int, note: str | None, lost_reason: str | None) -> dict:
    o = _get(db, opportunity_id)
    if o.version != expected_version:
        raise BusinessConflict("version_conflict", "The opportunity was changed by someone else. Reload and try again.")
    if stage not in NEXT[o.stage]:
        raise BusinessConflict("invalid_transition", f"An opportunity cannot move from {o.stage} to {stage}")
    if stage == "LOST" and not lost_reason:
        raise Unprocessable("A lost reason is required")
    if stage == "WON":
        if not o.customer_id:
            raise BusinessConflict("customer_required", "A won opportunity must belong to a customer")
        q = db.get(SalesQuotation, o.quotation_id) if o.quotation_id else None
        if not q or q.status not in ("ACCEPTED", "CONVERTED"):
            raise BusinessConflict("quotation_not_accepted", "An opportunity is won only when its quotation has been accepted by the customer")
    before = o.stage
    o.stage, o.probability, o.version = stage, STAGE_PROBABILITY[stage], o.version + 1
    if stage == "LOST":
        o.lost_reason = lost_reason
    if stage in ("WON", "LOST"):
        o.closed_at = func.now()
    _history(db, o, before, stage, note or lost_reason, cu.id)
    db.flush()
    db.refresh(o)
    audit.record(db, user=cu, action="stage", entity="opportunity", entity_id=o.id, before={"stage": before}, after={"stage": stage}, reason=lost_reason or note)
    if stage in ("WON", "LOST"):
        outbox.add(db, event_type=f"crm.opportunity.{stage.lower()}", aggregate_type="opportunity", aggregate_id=o.id, payload={"number": o.number, "value": str(o.expected_value), "actor_id": cu.id})
    return opp_dict(o)


def log_activity(db: Session, cu: CurrentUser, data: dict) -> dict:
    if not any(data.get(k) for k in ("lead_id", "opportunity_id", "customer_id")):
        raise Unprocessable("An activity must reference a lead, an opportunity or a customer")
    for key, model in (("lead_id", Lead), ("opportunity_id", Opportunity), ("customer_id", Customer)):
        if data.get(key) and not db.get(model, data[key]):
            raise Unprocessable(f"{key} does not exist")
    a = CrmActivity(created_by=cu.id, **{k: v for k, v in data.items() if v is not None})
    db.add(a)
    db.flush()
    db.refresh(a)
    return activity_dict(a)


def pipeline_summary(db: Session) -> dict:
    rows = db.execute(text("""SELECT stage, count(*) n, COALESCE(sum(expected_value),0) value, COALESCE(sum(expected_value * probability / 100.0),0)::numeric(18,2) weighted
                                FROM opportunities GROUP BY stage""")).mappings().all()
    by = {r["stage"]: {"count": r["n"], "value": str(r["value"]), "weighted": str(r["weighted"])} for r in rows}
    won, lost = by.get("WON", {}).get("count", 0), by.get("LOST", {}).get("count", 0)
    lost_reasons = [dict(r) for r in db.execute(text("SELECT lost_reason AS reason, count(*) n FROM opportunities WHERE stage='LOST' GROUP BY 1 ORDER BY 2 DESC")).mappings()]
    return {"by_stage": by, "win_rate_pct": round(100 * won / (won + lost), 1) if won + lost else None, "lost_reasons": lost_reasons,
            "open_weighted_value": str(sum((Decimal(by[s]["weighted"]) for s in ORDER if s in by), Decimal(0)))}


def opp_dict(o: Opportunity) -> dict:
    return {"id": o.id, "number": o.number, "name": o.name, "lead_id": o.lead_id, "customer_id": o.customer_id, "stage": o.stage, "probability": o.probability,
            "expected_value": str(o.expected_value), "expected_close": o.expected_close, "owner_id": o.owner_id, "quotation_id": o.quotation_id,
            "lost_reason": o.lost_reason, "version": o.version}


def activity_dict(a: CrmActivity) -> dict:
    return {"id": a.id, "kind": a.kind, "subject": a.subject, "notes": a.notes, "occurred_at": a.occurred_at, "duration_min": a.duration_min,
            "lead_id": a.lead_id, "opportunity_id": a.opportunity_id, "customer_id": a.customer_id, "created_by": a.created_by}
