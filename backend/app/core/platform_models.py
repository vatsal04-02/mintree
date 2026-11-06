"""Platform tables: idempotency, transactional outbox, consumer de-duplication, job failures, notifications."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class IdempotencyKey(Base):
    """One row per (scope, key). Inserted in the SAME transaction as the business effect, so a committed row
    proves the effect committed and an aborted transaction leaves no trace. The unique index makes concurrent
    duplicates serialise on the database, not in application code."""

    __tablename__ = "idempotency_keys"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope: Mapped[str] = mapped_column(String(80))          # e.g. "inventory.goods_receipt.post"
    key: Mapped[str] = mapped_column(String(128))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    request_hash: Mapped[str] = mapped_column(String(64))   # sha256 of the canonical request payload
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now() + interval '7 days'"))
    __table_args__ = (UniqueConstraint("scope", "key"), Index("ix_idempotency_keys_expires_at", "expires_at"))


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(100))
    aggregate_type: Mapped[str] = mapped_column(String(60))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(64))     # correlation ID of the originating request
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(80))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("status IN ('pending','publishing','published','dead')", name="status_valid"),
        # The publisher only ever scans unpublished rows; published history stays out of the hot index.
        Index("ix_outbox_unpublished", "next_attempt_at", "id", postgresql_where=text("status IN ('pending','publishing')")),
        Index("ix_outbox_dead", "id", postgresql_where=text("status = 'dead'")),
        Index("ix_outbox_aggregate", "aggregate_type", "aggregate_id"),
    )


class ProcessedEvent(Base):
    """Consumer-side de-duplication: PK (consumer, event_id). Written in the same transaction as the consumer's
    effects, so at-least-once delivery yields exactly-once effects."""

    __tablename__ = "processed_events"
    consumer: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobFailure(Base):
    """Permanently failed background work (retries exhausted): the dead-letter inspection table."""

    __tablename__ = "job_failures"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_name: Mapped[str] = mapped_column(String(120))
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    task_id: Mapped[str | None] = mapped_column(String(80))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(64))
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_job_failures_open", "failed_at", postgresql_where=text("resolved_at IS NULL")),)


class Notification(Base):
    """First consumer effect (Notifications module). unique(event_id, kind) is the final duplicate guard."""

    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[dict | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("event_id", "kind"),)


class WebhookEvent(Base):
    """Inbound external events (courier, marketplace, payment). unique(source, external_event_id) is the final duplicate guard:
    a re-delivered webhook is recognised and acknowledged without any second business effect."""

    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    external_event_id: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12), default="RECEIVED", server_default="RECEIVED")   # RECEIVED | PROCESSED | IGNORED | FAILED
    error: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("source", "external_event_id", name="uq_webhook_events_ext"),)
