"""CRM depth: lead-source master, opportunities with stage history, and logged activities (calls, meetings, notes)."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.mixins import AuditFieldsMixin

STAGES = ("PROSPECTING", "QUALIFIED", "PROPOSAL", "NEGOTIATION", "WON", "LOST")
STAGE_PROBABILITY = {"PROSPECTING": 10, "QUALIFIED": 25, "PROPOSAL": 50, "NEGOTIATION": 75, "WON": 100, "LOST": 0}
ACTIVITY_KINDS = ("CALL", "MEETING", "EMAIL", "NOTE", "VISIT")


class LeadSource(AuditFieldsMixin, Base):
    __tablename__ = "lead_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    stage: Mapped[str] = mapped_column(String(12), default="PROSPECTING", server_default="PROSPECTING")
    expected_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    probability: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    expected_close: Mapped[date | None] = mapped_column(Date)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    quotation_id: Mapped[int | None] = mapped_column(ForeignKey("sales_quotations.id"), unique=True)   # a quotation backs at most one opportunity
    lost_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("stage IN ('PROSPECTING','QUALIFIED','PROPOSAL','NEGOTIATION','WON','LOST')", name="stage_valid"),
        CheckConstraint("lead_id IS NOT NULL OR customer_id IS NOT NULL", name="has_party"),
        CheckConstraint("expected_value >= 0 AND probability BETWEEN 0 AND 100", name="values_valid"),
        CheckConstraint("stage <> 'LOST' OR lost_reason IS NOT NULL", name="lost_needs_reason"),
        CheckConstraint("stage <> 'WON' OR (customer_id IS NOT NULL AND quotation_id IS NOT NULL)", name="won_needs_customer_and_quotation"),
        CheckConstraint("(stage IN ('WON','LOST')) = (closed_at IS NOT NULL)", name="closed_consistent"),
        Index("ix_opportunities_stage", "stage", "id"),
        Index("ix_opportunities_customer", "customer_id"),
    )


class OpportunityStageHistory(Base):
    __tablename__ = "opportunity_stage_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"))
    from_stage: Mapped[str | None] = mapped_column(String(12))
    to_stage: Mapped[str] = mapped_column(String(12))
    note: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_opp_stage_history_opp", "opportunity_id", "id"),)


class CrmActivity(Base):
    __tablename__ = "crm_activities"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    subject: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    duration_min: Mapped[int | None] = mapped_column(Integer)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        CheckConstraint("kind IN ('CALL','MEETING','EMAIL','NOTE','VISIT')", name="kind_valid"),
        CheckConstraint("lead_id IS NOT NULL OR opportunity_id IS NOT NULL OR customer_id IS NOT NULL", name="has_subject"),
        CheckConstraint("duration_min IS NULL OR duration_min >= 0", name="duration_valid"),
        Index("ix_crm_activities_lead", "lead_id"), Index("ix_crm_activities_opportunity", "opportunity_id"), Index("ix_crm_activities_customer", "customer_id"),
    )
