from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crud import Resource, build_router
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound
from app.core.uow import run_tx
from app.crm import ext_service as svc
from app.crm.ext_models import CrmActivity, LeadSource, Opportunity, OpportunityStageHistory
from app.masters.schemas import Code, Name, _Audited, _Base

router = APIRouter(prefix="/crm", tags=["crm"])
DB = Depends(get_db, scope="function")
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class SourceIn(_Base):
    code: Code
    name: Name
    is_active: bool = True


class SourceUpdate(_Base):
    name: Name | None = None
    is_active: bool | None = None


class SourceOut(_Audited, SourceIn):
    pass


router.include_router(build_router(Resource("lead_source", LeadSource, "crm.lead_source", SourceIn, SourceUpdate, SourceOut, ["code", "name"], default_sort="code"),
                                   prefix="/lead-sources", tags=["crm"]))


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConvertIn(_In):
    customer_id: int | None = None
    customer_type: Literal["DISTRIBUTOR", "BRAND", "B2B", "MARKETPLACE", "DIRECT"] = "B2B"
    create_if_no_match: bool = True


class OppIn(_In):
    name: str = Field(min_length=2, max_length=160)
    lead_id: int | None = None
    customer_id: int | None = None
    expected_value: Money
    expected_close: date | None = None
    owner_id: int | None = None


class AttachIn(_In):
    quotation_id: int
    expected_version: int


class StageIn(_In):
    stage: Literal["QUALIFIED", "PROPOSAL", "NEGOTIATION", "WON", "LOST"]
    expected_version: int
    note: str | None = Field(None, max_length=500)
    lost_reason: str | None = Field(None, max_length=500)


class ActivityIn(_In):
    kind: Literal["CALL", "MEETING", "EMAIL", "NOTE", "VISIT"]
    subject: str = Field(min_length=2, max_length=200)
    notes: str | None = None
    duration_min: int | None = Field(None, ge=0, le=1440)
    lead_id: int | None = None
    opportunity_id: int | None = None
    customer_id: int | None = None


@router.post("/leads/{lid}/convert")
def convert(lid: int, body: ConvertIn, db: Session = DB, cu: CurrentUser = Depends(require("crm.lead:update"))):
    return run_tx(db, lambda d: svc.convert_lead(d, cu, lead_id=lid, customer_id=body.customer_id, customer_type=body.customer_type, create_if_no_match=body.create_if_no_match), name="crm.lead.convert")


@router.post("/opportunities", status_code=201)
def create_opp(body: OppIn, db: Session = DB, cu: CurrentUser = Depends(require("crm.opportunity:create"))):
    return run_tx(db, lambda d: svc.create_opportunity(d, cu, **body.model_dump()), name="crm.opportunity.create")


@router.get("/opportunities")
def list_opps(stage: str | None = None, customer_id: int | None = None, owner_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
              db: Session = DB, _: CurrentUser = Depends(require("crm.opportunity:read"))):
    q = select(Opportunity)
    for col, v in ((Opportunity.stage, stage), (Opportunity.customer_id, customer_id), (Opportunity.owner_id, owner_id)):
        if v is not None:
            q = q.where(col == v)
    rows = db.scalars(q.order_by(Opportunity.id.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    return {"items": [svc.opp_dict(o) for o in rows], "page": page, "page_size": page_size}


@router.get("/opportunities/{oid}")
def get_opp(oid: int, db: Session = DB, _: CurrentUser = Depends(require("crm.opportunity:read"))):
    o = db.get(Opportunity, oid)
    if not o:
        raise NotFound("Opportunity")
    hist = db.scalars(select(OpportunityStageHistory).where(OpportunityStageHistory.opportunity_id == oid).order_by(OpportunityStageHistory.id)).all()
    acts = db.scalars(select(CrmActivity).where(CrmActivity.opportunity_id == oid).order_by(CrmActivity.id.desc())).all()
    return {**svc.opp_dict(o), "history": [{"from": h.from_stage, "to": h.to_stage, "note": h.note, "changed_by": h.changed_by, "changed_at": h.changed_at} for h in hist],
            "activities": [svc.activity_dict(a) for a in acts]}


@router.post("/opportunities/{oid}/quotation")
def attach(oid: int, body: AttachIn, db: Session = DB, cu: CurrentUser = Depends(require("crm.opportunity:update"))):
    return run_tx(db, lambda d: svc.attach_quotation(d, cu, opportunity_id=oid, quotation_id=body.quotation_id, expected_version=body.expected_version), name="crm.opportunity.attach")


@router.post("/opportunities/{oid}/stage")
def stage(oid: int, body: StageIn, db: Session = DB, cu: CurrentUser = Depends(require("crm.opportunity:update"))):
    return run_tx(db, lambda d: svc.move_stage(d, cu, opportunity_id=oid, stage=body.stage, expected_version=body.expected_version, note=body.note, lost_reason=body.lost_reason), name="crm.opportunity.stage")


@router.get("/pipeline")
def pipeline(db: Session = DB, _: CurrentUser = Depends(require("crm.opportunity:read"))):
    return svc.pipeline_summary(db)


@router.post("/activities", status_code=201)
def log_activity(body: ActivityIn, db: Session = DB, cu: CurrentUser = Depends(require("crm.activity:create"))):
    return run_tx(db, lambda d: svc.log_activity(d, cu, body.model_dump()), name="crm.activity.create")


@router.get("/activities")
def activities(lead_id: int | None = None, customer_id: int | None = None, opportunity_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
               db: Session = DB, _: CurrentUser = Depends(require("crm.activity:read"))):
    q = select(CrmActivity)
    for col, v in ((CrmActivity.lead_id, lead_id), (CrmActivity.customer_id, customer_id), (CrmActivity.opportunity_id, opportunity_id)):
        if v is not None:
            q = q.where(col == v)
    return {"items": [svc.activity_dict(a) for a in db.scalars(q.order_by(CrmActivity.id.desc()).limit(page_size).offset((page - 1) * page_size))], "page": page}
