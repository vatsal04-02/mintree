"""Transactional outbox.

Write side  : `add()` inserts the event in the caller's business transaction (atomic with the change).
Publish side: app.workers.outbox_publisher claims committed rows (FOR UPDATE SKIP LOCKED), publishes them OUTSIDE any
              DB transaction with broker confirms, then marks them published. A crash between publish and mark means
              the event is published again after the lease expires: delivery is at-least-once, consumers de-duplicate.
Ordering    : events are published in id order per publisher but NOT guaranteed across publishers/consumers.
              Consumers must be order-independent (carry versions in the payload where order matters).
"""
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.context import request_id_var
from app.core.platform_models import OutboxEvent


def add(db: Session, *, event_type: str, aggregate_type: str, aggregate_id: Any, payload: dict) -> uuid.UUID:
    ev = OutboxEvent(event_id=uuid.uuid4(), event_type=event_type, aggregate_type=aggregate_type,
                     aggregate_id=str(aggregate_id), payload=payload, request_id=request_id_var.get())
    db.add(ev)
    return ev.event_id


CLAIM_SQL = text("""
WITH c AS (
    SELECT id FROM outbox_events
    WHERE (status = 'pending' AND next_attempt_at <= now())
       OR (status = 'publishing' AND locked_at < now() - make_interval(secs => :lease))
    ORDER BY id
    LIMIT :n
    FOR UPDATE SKIP LOCKED
)
UPDATE outbox_events o
   SET status = 'publishing', attempts = o.attempts + 1, locked_at = now(), locked_by = :me
  FROM c WHERE o.id = c.id
RETURNING o.id, o.event_id, o.event_type, o.aggregate_type, o.aggregate_id, o.payload, o.request_id,
          o.attempts, o.created_at
""")

MARK_PUBLISHED_SQL = text("""
UPDATE outbox_events SET status = 'published', published_at = now(), locked_at = NULL, locked_by = NULL, last_error = NULL
 WHERE id = ANY(:ids) AND status = 'publishing' AND locked_by = :me
""")

MARK_FAILED_SQL = text("""
UPDATE outbox_events
   SET status = CASE WHEN attempts >= :max THEN 'dead' ELSE 'pending' END,
       next_attempt_at = now() + make_interval(secs => LEAST(300, power(2, attempts)::int)),
       last_error = :err, locked_at = NULL, locked_by = NULL
 WHERE id = :id AND status = 'publishing' AND locked_by = :me
""")

PURGE_SQL = text("DELETE FROM outbox_events WHERE status = 'published' AND published_at < now() - make_interval(days => :d)")
