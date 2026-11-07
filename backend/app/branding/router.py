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
    objective: Literal["AWARENESS", "TRAFFIC", "ENGAGEMENT", "LEADS", "SALES"] | None = None
    meta_ad_account_id: int | None = None
    budget: Decimal | None = Field(default=None, ge=0)
    start_date: date | None = None
    end_date: date | None = None


class ContentIn(_In):
    campaign_id: int | None = None
    channel: Literal["INSTAGRAM", "FACEBOOK", "WEBSITE", "EMAIL", "OTHER"]
    title: str = Field(min_length=2, max_length=200)
    caption: str | None = None
    scheduled_for: datetime | None = None
    meta_page_id: int | None = None


class RejectIn(_In):
    reason: str = Field(min_length=3)


class PublishIn(_In):
    external_url: str | None = Field(default=None, max_length=300)


def campaign_dict(c: Campaign) -> dict:
    return {"id": c.id, "name": c.name, "brand_id": c.brand_id, "budget": c.budget, "start_date": c.start_date, "end_date": c.end_date, "status": c.status,
            "objective": c.objective, "meta_ad_account_id": c.meta_ad_account_id}


def content_dict(c: ContentItem) -> dict:
    return {"id": c.id, "campaign_id": c.campaign_id, "channel": c.channel, "title": c.title, "caption": c.caption, "scheduled_for": c.scheduled_for,
            "status": c.status, "created_by": c.created_by, "approved_by": c.approved_by, "rejection_reason": c.rejection_reason, "meta_page_id": c.meta_page_id}


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


class ScheduleIn(_In):
    scheduled_for: datetime


@router.post("/content/{cid}/schedule")
def schedule(cid: int, body: ScheduleIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.content:publish"))):
    """Approved content gets a planned publishing time. Nothing is sent to Meta: publishing remains a recorded manual step (or, later, an API call after a verified connection)."""
    def work(d: Session):
        c = _item(d, cid)
        if c.status not in ("APPROVED", "SCHEDULED"):
            raise BusinessConflict("not_approved", "Only approved content can be scheduled")
        c.status, c.scheduled_for = "SCHEDULED", body.scheduled_for
        audit.record(d, user=cu, action="schedule", entity="content", entity_id=c.id, after={"scheduled_for": body.scheduled_for.isoformat()})
        return content_dict(c)
    return run_tx(db, work, name="branding.content.schedule")


@router.post("/content/{cid}/publish")
def publish(cid: int, body: PublishIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.content:publish"))):
    """Records that approved content was published manually. No call to Meta is made."""
    def work(d: Session):
        c = _item(d, cid)
        if c.status not in ("APPROVED", "SCHEDULED"):
            raise BusinessConflict("not_approved", "Only approved or scheduled content can be published")
        d.add(PublishingRecord(content_id=c.id, mode="MANUAL", external_url=body.external_url, published_by=cu.id))
        c.status = "PUBLISHED"
        _flush(d, "Already published")
        audit.record(d, user=cu, action="publish", entity="content", entity_id=c.id, after={"mode": "MANUAL"})
        return content_dict(c)
    return run_tx(db, work, name="branding.content.publish")


# ------------------------------------------------------------------ brand library, social accounts, campaign metrics, marketing analytics
from app.branding.models import BrandAsset, CampaignMetric, SocialAccount     # noqa: E402


class AssetIn(_In):
    brand_id: int
    kind: Literal["LOGO", "GUIDELINE", "PHOTO", "DOCUMENT", "COLOR", "OTHER"]
    name: str = Field(min_length=1, max_length=160)
    document_id: int | None = None
    value: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class SocialIn(_In):
    platform: Literal["FACEBOOK", "INSTAGRAM"]
    handle: str = Field(min_length=1, max_length=120)
    brand_id: int | None = None
    credentials_ref: str | None = Field(default=None, max_length=120, description="Reference to a secret-store entry; never the token itself")


class MetricIn(_In):
    campaign_id: int
    metric_date: date
    reach: int = Field(default=0, ge=0)
    impressions: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    engagements: int = Field(default=0, ge=0)
    results: int = Field(default=0, ge=0)
    spend: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=2)


@router.post("/assets", status_code=201)
def add_asset(body: AssetIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.asset:create"))):
    def work(d: Session):
        if body.kind == "COLOR" and not body.value:
            raise Unprocessable("A colour asset needs a hex value like #126B61")
        a = BrandAsset(**body.model_dump(), created_by=cu.id)
        d.add(a)
        _flush(d, "Invalid asset (check the brand, the document, and that files reference a Document Centre record)")
        audit.record(d, user=cu, action="create", entity="brand_asset", entity_id=a.id)
        return {"id": a.id, "brand_id": a.brand_id, "kind": a.kind, "name": a.name, "document_id": a.document_id, "value": a.value}
    return run_tx(db, work, name="branding.asset.create")


@router.get("/assets")
def assets(brand_id: int | None = None, kind: str | None = None, db: Session = DB, _: CurrentUser = Depends(require("branding.asset:read"))):
    q = select(BrandAsset).order_by(BrandAsset.brand_id, BrandAsset.kind, BrandAsset.name)
    if brand_id:
        q = q.where(BrandAsset.brand_id == brand_id)
    if kind:
        q = q.where(BrandAsset.kind == kind)
    return [{"id": a.id, "brand_id": a.brand_id, "kind": a.kind, "name": a.name, "document_id": a.document_id, "value": a.value} for a in db.scalars(q)]


@router.post("/social-accounts", status_code=201)
def add_social(body: SocialIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.social:manage"))):
    def work(d: Session):
        a = SocialAccount(**body.model_dump())
        d.add(a)
        try:
            d.flush()
        except IntegrityError as ex:
            raise BusinessConflict("duplicate_account", "That account is already registered") from ex
        audit.record(d, user=cu, action="create", entity="social_account", entity_id=a.id, after={"platform": a.platform, "handle": a.handle})
        return social_dict(a)
    return run_tx(db, work, name="branding.social.create")


def social_dict(a: SocialAccount) -> dict:
    return {"id": a.id, "platform": a.platform, "handle": a.handle, "brand_id": a.brand_id, "connection_mode": a.connection_mode, "status": a.status,
            "api_connection": "not connected: publishing is recorded manually until a Meta app with verified permissions is configured"}


@router.get("/social-accounts")
def social(db: Session = DB, _: CurrentUser = Depends(require("branding.social:read"))):
    return [social_dict(a) for a in db.scalars(select(SocialAccount).order_by(SocialAccount.id))]


@router.post("/metrics", status_code=201)
def add_metric(body: MetricIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.campaign:create"))):
    """Records one day of results for a campaign (manual entry / import). Entering the same day again is rejected: correct it via the audit-trailed update below."""
    def work(d: Session):
        if not d.get(Campaign, body.campaign_id):
            raise NotFound("Campaign")
        m = CampaignMetric(**body.model_dump(), recorded_by=cu.id)
        d.add(m)
        try:
            d.flush()
        except IntegrityError as ex:
            raise BusinessConflict("metric_exists", "Metrics for that campaign and day already exist") from ex
        return {"id": m.id, "campaign_id": m.campaign_id, "metric_date": m.metric_date}
    return run_tx(db, work, name="branding.metric.create")


@router.get("/analytics")
def analytics(start: date, end: date, db: Session = DB, _: CurrentUser = Depends(require("branding.campaign:read"))):
    from sqlalchemy import text
    rows = db.execute(text("""
        SELECT c.id, c.name, c.status, c.budget, sum(m.reach) reach, sum(m.impressions) impressions, sum(m.clicks) clicks, sum(m.engagements) engagements, sum(m.results) results, sum(m.spend) spend
          FROM marketing_campaigns c JOIN campaign_metrics m ON m.campaign_id = c.id WHERE m.metric_date BETWEEN :s AND :e GROUP BY c.id ORDER BY sum(m.spend) DESC"""), {"s": start, "e": end}).mappings().all()
    ratio = lambda a, b, nd=2: None if not b else round(float(a) / float(b), nd)
    camp = [{"campaign_id": r["id"], "name": r["name"], "status": r["status"], "budget": None if r["budget"] is None else str(r["budget"]), "spend": str(r["spend"]),
             "budget_used_pct": None if not r["budget"] else round(100 * float(r["spend"]) / float(r["budget"]), 1), "reach": r["reach"], "impressions": r["impressions"], "clicks": r["clicks"],
             "engagements": r["engagements"], "results": r["results"], "ctr_pct": None if not r["impressions"] else round(100 * r["clicks"] / r["impressions"], 2),
             "engagement_rate_pct": None if not r["reach"] else round(100 * r["engagements"] / r["reach"], 2), "cost_per_click": ratio(r["spend"], r["clicks"]),
             "cost_per_result": ratio(r["spend"], r["results"])} for r in rows]
    by_brand = [dict(r) | {"spend": str(r["spend"])} for r in db.execute(text("""SELECT COALESCE(b.name, 'Unassigned') AS brand, sum(m.spend) spend, sum(m.results) results, sum(m.reach) reach
                                                                               FROM campaign_metrics m JOIN marketing_campaigns c ON c.id = m.campaign_id LEFT JOIN brands b ON b.id = c.brand_id
                                                                              WHERE m.metric_date BETWEEN :s AND :e GROUP BY b.name ORDER BY 2 DESC"""), {"s": start, "e": end}).mappings()]
    content = [dict(r) for r in db.execute(text("""SELECT channel, status, count(*) AS n FROM content_calendar GROUP BY 1, 2 ORDER BY 1, 2""")).mappings()]
    return {"period": {"start": start, "end": end}, "campaigns": camp, "by_brand": by_brand, "content_pipeline": content,
            "total_spend": str(sum((Decimal(c["spend"]) for c in camp), Decimal(0))), "source": "manually entered / imported campaign metrics; no Meta API connection"}


# ------------------------------------------------------------------ campaign lifecycle and the Meta workspace (sandbox vs live)
from app.branding.meta_provider import GraphMetaProvider, MetaError, MetaNotConfigured, demo_provider   # noqa: E402
from app.branding.models import MetaConnection                                                       # noqa: E402
from app.core.config import get_settings                                                             # noqa: E402

CAMPAIGN_FLOW = {"PLANNED": {"RUNNING"}, "RUNNING": {"PAUSED", "DONE"}, "PAUSED": {"RUNNING", "DONE"}, "DONE": set()}


class CampaignStatusIn(_In):
    status: Literal["RUNNING", "PAUSED", "DONE"]


@router.post("/campaigns/{cid}/status")
def campaign_status(cid: int, body: CampaignStatusIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.campaign:create"))):
    def work(d: Session):
        c = d.scalar(select(Campaign).where(Campaign.id == cid).with_for_update())
        if not c:
            raise NotFound("Campaign")
        if body.status not in CAMPAIGN_FLOW[c.status]:
            raise BusinessConflict("invalid_transition", f"A {c.status.lower()} campaign cannot become {body.status.lower()}")
        before = c.status
        c.status = body.status
        audit.record(d, user=cu, action="status", entity="campaign", entity_id=c.id, before={"status": before}, after={"status": c.status})
        return campaign_dict(c)
    return run_tx(db, work, name="branding.campaign.status")


class ConnectionIn(_In):
    kind: Literal["BUSINESS", "PAGE", "INSTAGRAM", "AD_ACCOUNT"]
    name: str = Field(min_length=2, max_length=160)
    external_ref: str = Field(min_length=2, max_length=80)
    mode: Literal["DEMO", "LIVE"]
    parent_id: int | None = None


def connection_dict(m: MetaConnection) -> dict:
    return {"id": m.id, "kind": m.kind, "name": m.name, "external_ref": m.external_ref, "mode": m.mode, "status": m.status, "parent_id": m.parent_id, "verified_at": m.verified_at,
            "label": "Simulated sandbox record: not connected to Meta" if m.mode == "DEMO" else ("Connected and verified with the Meta Graph API" if m.status == "CONNECTED" else "Registered, not connected")}


@router.post("/meta/connections", status_code=201)
def add_connection(body: ConnectionIn, db: Session = DB, cu: CurrentUser = Depends(require("branding.social:manage"))):
    if body.mode == "DEMO" and get_settings().environment == "production":
        raise Unprocessable("Sandbox records cannot be created in a production environment")
    def work(d: Session):
        m = MetaConnection(kind=body.kind, name=body.name, external_ref=body.external_ref, mode=body.mode, parent_id=body.parent_id,
                           status="SANDBOX" if body.mode == "DEMO" else "NOT_CONNECTED", created_by=cu.id)
        d.add(m)
        try:
            d.flush()
        except IntegrityError as ex:
            raise BusinessConflict("duplicate_connection", "That Meta asset is already registered") from ex
        audit.record(d, user=cu, action="create", entity="meta_connection", entity_id=m.id, after={"kind": m.kind, "mode": m.mode})
        return connection_dict(m)
    return run_tx(db, work, name="branding.meta.connection.create")


@router.get("/meta/workspace")
def meta_workspace(db: Session = DB, _: CurrentUser = Depends(require("branding.social:read"))):
    rows = [connection_dict(m) for m in db.scalars(select(MetaConnection).order_by(MetaConnection.kind, MetaConnection.id))]
    return {"connections": rows, "live_connected": any(r["status"] == "CONNECTED" for r in rows), "has_sandbox_data": any(r["mode"] == "DEMO" for r in rows),
            "live_configured": bool(get_settings().meta_access_token),
            "notice": "Records marked DEMO are simulated. Nothing here is connected to Meta unless a connection shows 'Connected and verified'."}


@router.post("/meta/connections/{mid}/verify")
def verify_connection(mid: int, db: Session = DB, cu: CurrentUser = Depends(require("branding.social:manage"))):
    """Asks the Graph API whether the asset exists and the configured token can read it. Only then does the connection become CONNECTED."""
    m = db.get(MetaConnection, mid)
    if not m:
        raise NotFound("Meta connection")
    if m.mode == "DEMO":
        raise BusinessConflict("sandbox_record", "Sandbox records are simulated and cannot be verified against Meta")
    try:
        info = GraphMetaProvider().verify(m.kind, m.external_ref)
    except MetaNotConfigured as e:
        raise BusinessConflict("meta_not_configured", str(e)) from None
    except MetaError as e:
        raise BusinessConflict("meta_verification_failed", str(e)) from None

    def work(d: Session):
        row = d.scalar(select(MetaConnection).where(MetaConnection.id == mid).with_for_update())
        row.status, row.verified_by = "CONNECTED", cu.id
        row.verified_at = d.scalar(__import__("sqlalchemy").text("SELECT now()"))
        audit.record(d, user=cu, action="verify", entity="meta_connection", entity_id=row.id, after={"graph_id": info.get("id")})
        return connection_dict(row)
    return run_tx(db, work, name="branding.meta.connection.verify")


@router.post("/meta/sync-sandbox")
def sync_sandbox(db: Session = DB, cu: CurrentUser = Depends(require("branding.campaign:create"))):
    """Fills in SIMULATED daily metrics (source DEMO) for campaigns attached to a sandbox ad account, from the campaign start to today (or end). Idempotent: existing days are never touched."""
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from datetime import timedelta
    from app.core.clock import business_today
    prov = demo_provider()

    def work(d: Session):
        made = 0
        camps = d.execute(text("""SELECT c.id, c.start_date, c.end_date, c.budget, c.objective, c.status FROM marketing_campaigns c JOIN meta_connections m ON m.id = c.meta_ad_account_id
                                  WHERE m.mode = 'DEMO' AND c.status IN ('RUNNING','PAUSED','DONE') AND c.start_date IS NOT NULL""")).mappings().all()
        for c in camps:
            end = min(c["end_date"] or business_today(), business_today())
            days = max((end - c["start_date"]).days + 1, 1)
            daily = (c["budget"] or Decimal(0)) / days if c["budget"] else Decimal(500)
            for i in range(days):
                day = c["start_date"] + timedelta(days=i)
                if c["status"] == "PAUSED" and i >= days * 0.6:
                    break                                       # a paused campaign stops delivering part-way
                ins = prov.insights(f"campaign-{c['id']}", day, daily, c["objective"])
                r = d.execute(pg_insert(CampaignMetric).values(campaign_id=c["id"], metric_date=day, reach=ins.reach, impressions=ins.impressions, clicks=ins.clicks, engagements=ins.engagements,
                                                                results=ins.results, spend=ins.spend, source="DEMO", recorded_by=cu.id).on_conflict_do_nothing().returning(CampaignMetric.id)).first()
                made += 1 if r else 0
        audit.record(d, user=cu, action="sync_sandbox", entity="campaign_metric", after={"days_created": made, "campaigns": len(camps)})
        return {"days_created": made, "campaigns": len(camps), "simulated": True}
    return run_tx(db, work, name="branding.meta.sync_sandbox")
