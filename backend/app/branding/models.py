from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class MetaConnection(Base):
    """A Meta Business asset the company works with: business account, Facebook page, Instagram professional account, ad account.
    mode DEMO = a clearly simulated sandbox record (status SANDBOX, never 'connected'); mode LIVE starts NOT_CONNECTED and becomes CONNECTED only after
    the Graph API has verified it (verified_at set). The CHECKs make a fabricated 'connected' state impossible."""

    __tablename__ = "meta_connections"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(160))
    external_ref: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(4), default="LIVE", server_default="LIVE")
    status: Mapped[str] = mapped_column(String(13), default="NOT_CONNECTED", server_default="NOT_CONNECTED")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("meta_connections.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("kind", "external_ref", name="uq_meta_connections_ref"),
                      CheckConstraint("kind IN ('BUSINESS','PAGE','INSTAGRAM','AD_ACCOUNT')", name="kind_valid"),
                      CheckConstraint("mode IN ('DEMO','LIVE')", name="mode_valid"),
                      CheckConstraint("status IN ('SANDBOX','NOT_CONNECTED','CONNECTED')", name="status_valid"),
                      CheckConstraint("(mode = 'DEMO' AND status = 'SANDBOX') OR (mode = 'LIVE' AND status IN ('NOT_CONNECTED','CONNECTED'))", name="mode_status_consistent"),
                      CheckConstraint("status <> 'CONNECTED' OR (mode = 'LIVE' AND verified_at IS NOT NULL)", name="connected_means_verified"))


class Campaign(Base):
    __tablename__ = "marketing_campaigns"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"))
    budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="PLANNED", server_default="PLANNED")
    objective: Mapped[str | None] = mapped_column(String(12))
    meta_ad_account_id: Mapped[int | None] = mapped_column(ForeignKey("meta_connections.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (CheckConstraint("status IN ('PLANNED','RUNNING','PAUSED','DONE')", name="status_valid"),
                      CheckConstraint("objective IS NULL OR objective IN ('AWARENESS','TRAFFIC','ENGAGEMENT','LEADS','SALES')", name="objective_valid"),
                      CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="dates_ordered"))


class ContentItem(Base):
    __tablename__ = "content_calendar"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("marketing_campaigns.id"))
    channel: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(200))
    caption: Mapped[str | None] = mapped_column(Text)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", server_default="DRAFT")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    meta_page_id: Mapped[int | None] = mapped_column(ForeignKey("meta_connections.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("channel IN ('INSTAGRAM','FACEBOOK','WEBSITE','EMAIL','OTHER')", name="channel_valid"),
                      CheckConstraint("status IN ('DRAFT','IN_REVIEW','APPROVED','SCHEDULED','PUBLISHED','REJECTED')", name="status_valid"),
                      CheckConstraint("status NOT IN ('APPROVED','SCHEDULED','PUBLISHED') OR approved_by IS NOT NULL", name="approval_recorded"),
                      CheckConstraint("status <> 'SCHEDULED' OR scheduled_for IS NOT NULL", name="scheduled_has_time"))


class PublishingRecord(Base):
    """One publishing record per content item (unique): publishing twice is impossible. `mode` is honest: MANUAL until a Meta
    API connection with verified publishing permissions exists (PRD §12.3)."""

    __tablename__ = "publishing_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_calendar.id"), unique=True)
    mode: Mapped[str] = mapped_column(String(8), default="MANUAL", server_default="MANUAL")
    external_url: Mapped[str | None] = mapped_column(String(300))
    published_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BrandAsset(Base):
    """Brand library entry. Files live in the Document Centre (`document_id`); colours are stored as hex values."""

    __tablename__ = "brand_assets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"))
    kind: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(160))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    value: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("kind IN ('LOGO','GUIDELINE','PHOTO','DOCUMENT','COLOR','OTHER')", name="kind_valid"),
                      CheckConstraint("kind <> 'COLOR' OR value ~ '^#[0-9A-Fa-f]{6}$'", name="color_hex"),
                      CheckConstraint("kind = 'COLOR' OR document_id IS NOT NULL", name="file_needs_document"))


class SocialAccount(Base):
    """A Facebook Page / Instagram professional account. `credentials_ref` points at a secret store; publishing stays MANUAL until a verified Meta connection exists."""

    __tablename__ = "social_accounts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    platform: Mapped[str] = mapped_column(String(10))
    handle: Mapped[str] = mapped_column(String(120))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"))
    connection_mode: Mapped[str] = mapped_column(String(8), default="MANUAL", server_default="MANUAL")
    credentials_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(8), default="ACTIVE", server_default="ACTIVE")
    __table_args__ = (UniqueConstraint("platform", "handle", name="uq_social_accounts_handle"),
                      CheckConstraint("platform IN ('FACEBOOK','INSTAGRAM')", name="platform_valid"),
                      CheckConstraint("connection_mode IN ('MANUAL','API')", name="mode_valid"), CheckConstraint("status IN ('ACTIVE','PAUSED')", name="status_valid"))


class CampaignMetric(Base):
    """Daily performance for a campaign, entered or imported (never invented). unique(campaign, date) makes re-imports idempotent."""

    __tablename__ = "campaign_metrics"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("marketing_campaigns.id"))
    metric_date: Mapped[date] = mapped_column(Date)
    reach: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    impressions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    clicks: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    engagements: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    results: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    spend: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, server_default="0")
    source: Mapped[str] = mapped_column(String(8), default="MANUAL", server_default="MANUAL")
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (UniqueConstraint("campaign_id", "metric_date", name="uq_campaign_metrics_day"),
                      CheckConstraint("reach >= 0 AND impressions >= 0 AND clicks >= 0 AND engagements >= 0 AND results >= 0 AND spend >= 0", name="non_negative"),
                      CheckConstraint("clicks <= impressions OR impressions = 0", name="clicks_within_impressions"))
