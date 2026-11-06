from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ApprovalDelegation(Base):
    """While active, `to_user` may act with the approval authority of `from_user` for `doc_type` (e.g. holiday cover). Audited; self-approval rules still apply."""

    __tablename__ = "approval_delegations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(40))
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (CheckConstraint("valid_to >= valid_from", name="dates_ordered"), CheckConstraint("from_user_id <> to_user_id", name="not_self"),
                      Index("ix_approval_delegations_to", "to_user_id", "doc_type"))


class ApprovalRecord(Base):
    """Every approval/rejection decision at every level. unique(doc, user, decision) means one person cannot supply two levels of the same approval."""

    __tablename__ = "approval_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(40))
    doc_id: Mapped[int] = mapped_column(BigInteger)
    cycle: Mapped[int] = mapped_column(Integer, default=1, server_default="1")      # 1 + rejections so far: a resubmitted document starts a new cycle
    level: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    acting_for_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("decision IN ('APPROVED','REJECTED')", name="decision_valid"),
                      UniqueConstraint("doc_type", "doc_id", "cycle", "user_id", "decision", name="uq_approval_records_once"),
                      Index("ix_approval_records_doc", "doc_type", "doc_id"))


class CompanySetting(Base):
    __tablename__ = "company_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(String(200))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
