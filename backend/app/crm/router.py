"""CRM: leads and customer complaints. Complaints can reference a batch, which links them to production genealogy."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.core.uow import run_tx
from app.crm.ext_models import LeadSource
from app.crm.models import Complaint, Lead

router = APIRouter(prefix="/crm", tags=["crm"])
DB = Depends(get_db, scope="function")
LEAD_FLOW = {"NEW": {"CONTACTED", "LOST"}, "CONTACTED": {"QUALIFIED", "LOST"}, "QUALIFIED": {"WON", "LOST"}, "WON": set(), "LOST": set()}
COMPLAINT_FLOW = {"OPEN": {"INVESTIGATING", "RESOLVED"}, "INVESTIGATING": {"RESOLVED"}, "RESOLVED": set()}


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LeadIn(_In):
    name: str = Field(min_length=1, max_length=160)
    company: str | None = None
    phone: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    source: str | None = None
    lead_source_id: int | None = None
    owner_id: int | None = None
    notes: str | None = None


class StatusIn(_In):
    status: Literal["NEW", "CONTACTED", "QUALIFIED", "WON", "LOST"]
    reason: str | None = Field(default=None, max_length=500)


class ComplaintIn(_In):
    customer_id: int
    batch_id: int | None = None
    description: str = Field(min_length=5)


class ComplaintUpdate(_In):
    status: Literal["INVESTIGATING", "RESOLVED"]
    resolution: str | None = None


def lead_dict(l: Lead) -> dict:
    return {"id": l.id, "name": l.name, "company": l.company, "phone": l.phone, "email": l.email, "source": l.source, "lead_source_id": l.lead_source_id, "converted_customer_id": l.converted_customer_id, "lost_reason": l.lost_reason, "status": l.status, "owner_id": l.owner_id, "notes": l.notes}


def complaint_dict(c: Complaint) -> dict:
    return {"id": c.id, "customer_id": c.customer_id, "batch_id": c.batch_id, "description": c.description, "status": c.status, "resolution": c.resolution}


@router.post("/leads", status_code=201)
def create_lead(body: LeadIn, db: Session = DB, cu: CurrentUser = Depends(require("crm.lead:create"))):
    def work(d: Session):
        if body.lead_source_id and not d.get(LeadSource, body.lead_source_id):
            raise Unprocessable("Lead source does not exist")
        l = Lead(**body.model_dump())
        d.add(l)
        d.flush()
        audit.record(d, user=cu, action="create", entity="lead", entity_id=l.id)
        return lead_dict(l)
    return run_tx(db, work, name="crm.lead.create")


@router.get("/leads")
def leads(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB, _: CurrentUser = Depends(require("crm.lead:read"))):
    q = select(Lead).order_by(Lead.id.desc()).limit(page_size).offset((page - 1) * page_size)
    if status:
        q = q.where(Lead.status == status)
    return {"items": [lead_dict(l) for l in db.scalars(q)], "page": page}


@router.post("/leads/{lid}/status")
def lead_status(lid: int, body: StatusIn, db: Session = DB, cu: CurrentUser = Depends(require("crm.lead:update"))):
    def work(d: Session):
        l = d.scalar(select(Lead).where(Lead.id == lid).with_for_update())
        if not l:
            raise NotFound("Lead")
        if body.status not in LEAD_FLOW[l.status]:
            raise BusinessConflict("invalid_transition", f"A lead cannot move from {l.status} to {body.status}")
        if body.status == "WON":
            raise BusinessConflict("use_convert", "A lead is won by converting it to a customer: POST /crm/leads/{id}/convert")
        if body.status == "LOST":
            if not body.reason:
                raise Unprocessable("A lost reason is required")
            l.lost_reason = body.reason
        audit.record(d, user=cu, action="status", entity="lead", entity_id=l.id, before={"status": l.status}, after={"status": body.status}, reason=body.reason)
        l.status = body.status
        return lead_dict(l)
    return run_tx(db, work, name="crm.lead.status")


@router.post("/complaints", status_code=201)
def create_complaint(body: ComplaintIn, db: Session = DB, cu: CurrentUser = Depends(require("crm.complaint:create"))):
    def work(d: Session):
        c = Complaint(**body.model_dump())
        d.add(c)
        try:
            d.flush()
        except Exception as ex:
            raise Unprocessable("Customer or batch does not exist") from ex
        audit.record(d, user=cu, action="create", entity="complaint", entity_id=c.id)
        return complaint_dict(c)
    return run_tx(db, work, name="crm.complaint.create")


@router.get("/complaints")
def complaints(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB, _: CurrentUser = Depends(require("crm.complaint:read"))):
    q = select(Complaint).order_by(Complaint.id.desc()).limit(page_size).offset((page - 1) * page_size)
    if status:
        q = q.where(Complaint.status == status)
    return {"items": [complaint_dict(c) for c in db.scalars(q)], "page": page}


@router.post("/complaints/{cid}/update")
def complaint_update(cid: int, body: ComplaintUpdate, db: Session = DB, cu: CurrentUser = Depends(require("crm.complaint:update"))):
    def work(d: Session):
        c = d.scalar(select(Complaint).where(Complaint.id == cid).with_for_update())
        if not c:
            raise NotFound("Complaint")
        if body.status not in COMPLAINT_FLOW[c.status]:
            raise BusinessConflict("invalid_transition", f"A complaint cannot move from {c.status} to {body.status}")
        if body.status == "RESOLVED" and not body.resolution:
            raise BusinessConflict("resolution_required", "A resolution must be recorded to resolve a complaint")
        c.status, c.resolution = body.status, body.resolution or c.resolution
        audit.record(d, user=cu, action="status", entity="complaint", entity_id=c.id, after={"status": c.status})
        return complaint_dict(c)
    return run_tx(db, work, name="crm.complaint.update")
