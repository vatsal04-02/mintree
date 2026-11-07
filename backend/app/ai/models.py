"""AI layer storage. The model never writes business data: the only rows the layer creates are these (conversations, extractions, proposals,
usage, briefings). Everything a human later accepts goes through the normal permissioned, idempotent, audited endpoints."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AiConversation(Base):
    __tablename__ = "ai_conversations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_ai_conversations_user", "user_id", "id"),)


class AiMessage(Base):
    __tablename__ = "ai_messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("ai_conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list | None] = mapped_column(JSONB)          # [{label, href}] built from tool results, never from model prose
    unverified: Mapped[list | None] = mapped_column(JSONB)         # figures in the answer that appear in no tool result
    provider: Mapped[str | None] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(60))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("role IN ('user','assistant')", name="role_valid"), Index("ix_ai_messages_conv", "conversation_id", "id"))


class AiToolCall(Base):
    """Every tool the model asked for, including refused ones: the audit trail of what the assistant looked at, as whom."""

    __tablename__ = "ai_tool_calls"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("ai_messages.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    tool: Mapped[str] = mapped_column(String(60))
    arguments: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10))
    result_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("status IN ('OK','DENIED','ERROR','UNKNOWN')", name="status_valid"), Index("ix_ai_tool_calls_user", "user_id", "id"))


class AiUsage(Base):
    __tablename__ = "ai_usage"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)       # business date
    tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    __table_args__ = (CheckConstraint("tokens >= 0", name="tokens_nonneg"),)


class AiProposal(Base):
    """A deterministic suggestion (reorder, follow-up). It changes nothing until a person with the right permission accepts it."""

    __tablename__ = "ai_proposals"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    dedupe_key: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    rationale: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    required_permission: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10), default="PROPOSED", server_default="PROPOSED")
    result: Mapped[dict | None] = mapped_column(JSONB)             # what accepting produced (e.g. the draft PR id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('PROPOSED','ACCEPTED','REJECTED','EXPIRED')", name="status_valid"),
        Index("uq_ai_proposals_open", "kind", "dedupe_key", unique=True, postgresql_where=text("status = 'PROPOSED'")),
    )


class AiExtraction(Base):
    """A document read by the model into structured fields, matched against masters and arithmetic-checked by code.
    It is a draft for a person to correct; nothing is posted from here."""

    __tablename__ = "ai_extractions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    filename: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12), default="EXTRACTED", server_default="EXTRACTED")
    fields: Mapped[dict] = mapped_column(JSONB)                    # what the model read
    matched: Mapped[dict] = mapped_column(JSONB)                   # supplier / PO / lines resolved by code
    flags: Mapped[list] = mapped_column(JSONB)                     # [{level, code, message}] from code, never from the model
    draft_payload: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(60))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_edits: Mapped[dict | None] = mapped_column(JSONB)       # the person's corrections, kept as accuracy evidence
    __table_args__ = (
        CheckConstraint("kind IN ('INVOICE','DELIVERY_NOTE')", name="kind_valid"),
        CheckConstraint("status IN ('EXTRACTED','ACCEPTED','DISCARDED')", name="status_valid"),
        Index("ix_ai_extractions_user", "created_by", "id"),
    )


class AiBriefing(Base):
    __tablename__ = "ai_briefings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    day: Mapped[date] = mapped_column(Date)
    content: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("uq_ai_briefings_user_day", "user_id", "day", unique=True),)
