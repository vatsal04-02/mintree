from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Campaign(Base):
    __tablename__ = "marketing_campaigns"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"))
    budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="PLANNED", server_default="PLANNED")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (CheckConstraint("status IN ('PLANNED','RUNNING','DONE')", name="status_valid"),
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("channel IN ('INSTAGRAM','FACEBOOK','WEBSITE','EMAIL','OTHER')", name="channel_valid"),
                      CheckConstraint("status IN ('DRAFT','IN_REVIEW','APPROVED','PUBLISHED','REJECTED')", name="status_valid"),
                      CheckConstraint("status NOT IN ('APPROVED','PUBLISHED') OR approved_by IS NOT NULL", name="approval_recorded"))


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
