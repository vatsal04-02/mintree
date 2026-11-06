import logging
import time
from datetime import datetime, timezone

from celery import Task
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import observability as obs
from app.core.context import request_id_var
from app.core.db import SessionLocal
from app.core.platform_models import JobFailure, ProcessedEvent
from app.core.uow import run_tx
from app.workers import consumers as C
from app.workers.celery_app import celery

log = logging.getLogger("mintree.worker")


class ConsumerTask(Task):
    """Records permanent failure (retries exhausted) into job_failures for inspection."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        obs.WORKER_TASKS.labels(self.name, "dead").inc()
        try:
            with SessionLocal() as db:
                db.add(JobFailure(task_name=args[0] if args else self.name, event_id=args[1] if len(args) > 1 else None,
                                  task_id=task_id, attempts=self.request.retries + 1, error=f"{type(exc).__name__}: {exc}"[:2000],
                                  payload=args[3] if len(args) > 3 else None, request_id=args[4] if len(args) > 4 else None))
                db.commit()
        except Exception:
            log.exception("could not record job failure for %s", task_id)


@celery.task(bind=True, base=ConsumerTask, name="mintree.consume", max_retries=5, acks_late=True,
             autoretry_for=(Exception,), retry_backoff=2, retry_backoff_max=60, retry_jitter=True)
def consume(self, consumer_name: str, event_id: str, event_type: str, payload: dict, request_id: str | None = None,
            published_at: float | None = None):
    """Idempotent consumer. args are JSON-serialisable by design."""
    request_id_var.set(request_id)
    c = C.REGISTRY[consumer_name]
    t0 = time.perf_counter()
    if published_at and self.request.retries == 0:
        obs.WORKER_QUEUE_WAIT.labels(c.queue).observe(max(0.0, time.time() - published_at))
    if self.request.retries:
        obs.WORKER_TASKS.labels(consumer_name, "retry").inc()

    def work(db: Session) -> str:
        # De-dup marker and effects in one transaction: exactly-once effects over at-least-once delivery.
        fresh = db.execute(insert(ProcessedEvent).values(consumer=consumer_name, event_id=event_id)
                           .on_conflict_do_nothing().returning(ProcessedEvent.event_id)).scalar()
        if fresh is None:
            return "duplicate"
        c.handler(db, C.Event(event_id, event_type, payload, request_id))
        return "processed"

    with SessionLocal() as db:
        outcome = run_tx(db, work, name=f"consume.{consumer_name}")
    obs.WORKER_TASKS.labels(consumer_name, outcome).inc()
    obs.WORKER_DURATION.labels(consumer_name).observe(time.perf_counter() - t0)
    return outcome
