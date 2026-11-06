"""Branding & marketing: campaigns, content calendar with approval by a different user, and publishing records.
Publishing is MANUAL: nothing is posted to Meta; the team records where/when they published. Each item can be published once (unique)."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.core.uow import run_tx
from app.branding.models import Campaign, ContentItem, PublishingRecord

router = APIRouter(prefix="/branding", tags=["branding"])
DB = Depends(get_db, scope="function")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CampaignIn(_In):
    name: str = Field(min_length=2, max_length=160)
    brand_id: int | None = None
    budget: Decimal | None = Field(default=None, ge=0)
    start_date: date | None = None
    end_date: date | None = None


class ContentIn(_In):
    campaign_id: int | None = None
    channel: Literal["INSTAGRAM", "FACEBOOK", "WEBSITE", "EMAIL", "OTHER"]
    title: str = Field(min_length=2, max_length=200)
    caption: str | None = None
    scheduled_for: datetime | None = None


class RejectIn(_In):
    reason: str = Field(min_length=3)


class PublishIn(_In):
    external_url: str | None = Field(default=None, max_length=300)


def campaign_dict(c: Campaign) -> dict:
    return {"id": c.id, "name": c.name, "brand_id": c.brand_id, "budget": c.budget, "start_date": c.start_date, "end_date": c.end_date, "status": c.status}


def content_dict(c: ContentItem) -> dict:
    return {"id": c.id, "campaign_id": c.campaign_id, "channel": c.channel, "title": c.title, "caption": c.caption, "scheduled_for": c.scheduled_for,
            "status": c.status, "created_by": c.created_by, "approved_by": c.approved_by, "rejection_reason": c.rejection_reason}


def _flush(d: Session, msg: str):
    try:
        d.flush()
    except IntegrityError as ex:
        raise Unprocessable(msg) from ex


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.campaign:create"))):
    def work(d: Session):
        c = Campaign(**body.model_dump(), created_by=cu.id)
        d.add(c)
        _flush(d, "Invalid campaign (check brand and dates)")
        audit.record(d, user=cu, action="create", entity="campaign", entity_id=c.id)
        return campaign_dict(c)
    return run_tx(db, work, name="branding.campaign.create")


@router.get("/campaigns")
def campaigns(db: Session = DB, _: CurrentUser = Depends(require("branding.campaign:read"))):
    return [campaign_dict(c) for c in db.scalars(select(Campaign).order_by(Campaign.id.desc()))]


@router.post("/content", status_code=201)
def create_content(body: ContentIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.content:create"))):
    def work(d: Session):
        c = ContentItem(**body.model_dump(), created_by=cu.id)
        d.add(c)
        _flush(d, "Invalid content item (check campaign)")
        audit.record(d, user=cu, action="create", entity="content", entity_id=c.id)
        return content_dict(c)
    return run_tx(db, work, name="branding.content.create")


@router.get("/content")
def content(status: str | None = None, db: Session = DB, _: CurrentUser = Depends(require("branding.content:read"))):
    q = select(ContentItem).order_by(ContentItem.scheduled_for.nulls_last(), ContentItem.id)
    if status:
        q = q.where(ContentItem.status == status)
    return [content_dict(c) for c in db.scalars(q)]


def _item(d: Session, cid: int) -> ContentItem:
    c = d.scalar(select(ContentItem).where(ContentItem.id == cid).with_for_update())
    if not c:
        raise NotFound("Content item")
    return c


@router.post("/content/{cid}/submit")
def submit(cid: int, db: Session = DB, cu: CurrentUser = Depends(require("branding.content:create"))):
    def work(d: Session):
        c = _item(d, cid)
        if c.status not in ("DRAFT", "REJECTED"):
            raise BusinessConflict("invalid_status", f"Cannot submit content in status {c.status}")
        c.status, c.rejection_reason = "IN_REVIEW", None
        audit.record(d, user=cu, action="submit", entity="content", entity_id=c.id)
        return content_dict(c)
    return run_tx(db, work, name="branding.content.submit")


@router.post("/content/{cid}/approve")
def approve(cid: int, db: Session = DB, cu: CurrentUser = Depends(require("branding.content:approve"))):
    def work(d: Session):
        c = _item(d, cid)
        if c.status != "IN_REVIEW":
            raise BusinessConflict("invalid_status", "Only content in review can be approved")
        if c.created_by == cu.id:
            raise BusinessConflict("segregation_of_duties", "Content cannot be approved by its author")
        c.status, c.approved_by = "APPROVED", cu.id
        audit.record(d, user=cu, action="approve", entity="content", entity_id=c.id)
        return content_dict(c)
    return run_tx(db, work, name="branding.content.approve")


@router.post("/content/{cid}/reject")
def reject(cid: int, body: RejectIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.content:approve"))):
    def work(d: Session):
        c = _item(d, cid)
        if c.status != "IN_REVIEW":
            raise BusinessConflict("invalid_status", "Only content in review can be rejected")
        c.status, c.rejection_reason = "REJECTED", body.reason
        audit.record(d, user=cu, action="reject", entity="content", entity_id=c.id, reason=body.reason)
        return content_dict(c)
    return run_tx(db, work, name="branding.content.reject")


@router.post("/content/{cid}/publish")
def publish(cid: int, body: PublishIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.content:publish"))):
    """Records that approved content was published manually. No call to Meta is made."""
    def work(d: Session):
        c = _item(d, cid)
        if c.status != "APPROVED":
            raise BusinessConflict("not_approved", "Only approved content can be published")
        d.add(PublishingRecord(content_id=c.id, mode="MANUAL", external_url=body.external_url, published_by=cu.id))
        c.status = "PUBLISHED"
        _flush(d, "Already published")
        audit.record(d, user=cu, action="publish", entity="content", entity_id=c.id, after={"mode": "MANUAL"})
        return content_dict(c)
    return run_tx(db, work, name="branding.content.publish")
