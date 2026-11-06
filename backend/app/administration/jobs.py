"""Failure inspection for the outbox and background jobs (the dead-letter inspection mechanism)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound, Unprocessable
from app.core.platform_models import JobFailure, OutboxEvent

router = APIRouter(prefix="/admin", tags=["administration"])


@router.get("/outbox")
def outbox_summary(db: Session = Depends(get_db, scope="function"), _: CurrentUser = Depends(require("admin.jobs:read")),
                   limit: int = Query(50, ge=1, le=200)):
    counts = dict(db.execute(select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)).all())
    oldest = db.execute(text("SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(created_at)),0) FROM outbox_events "
                             "WHERE status IN ('pending','publishing')")).scalar()
    dead = db.scalars(select(OutboxEvent).where(OutboxEvent.status == "dead").order_by(OutboxEvent.id.desc()).limit(limit)).all()
    return {"counts": counts, "oldest_unpublished_age_s": float(oldest or 0),
            "dead": [{"id": e.id, "event_id": str(e.event_id), "event_type": e.event_type, "aggregate": f"{e.aggregate_type}:{e.aggregate_id}",
                      "attempts": e.attempts, "last_error": e.last_error, "request_id": e.request_id,
                      "created_at": e.created_at} for e in dead]}


@router.post("/outbox/{outbox_id}/requeue", status_code=204)
def requeue(outbox_id: int, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(require("admin.jobs:update"))):
    e = db.get(OutboxEvent, outbox_id)
    if not e:
        raise NotFound("Outbox event")
    if e.status != "dead":
        raise Unprocessable("Only dead events can be requeued")
    e.status, e.attempts, e.next_attempt_at = "pending", 0, func.now()
    audit.record(db, user=cu.user, action="outbox_requeue", entity="outbox_event", entity_id=e.id,
                 before={"status": "dead", "last_error": e.last_error})


@router.get("/job-failures")
def job_failures(include_resolved: bool = False, limit: int = Query(50, ge=1, le=200),
                 db: Session = Depends(get_db, scope="function"), _: CurrentUser = Depends(require("admin.jobs:read"))):
    q = select(JobFailure).order_by(JobFailure.id.desc()).limit(limit)
    if not include_resolved:
        q = q.where(JobFailure.resolved_at.is_(None))
    return [{"id": f.id, "task": f.task_name, "event_id": str(f.event_id) if f.event_id else None, "attempts": f.attempts,
             "error": f.error, "request_id": f.request_id, "failed_at": f.failed_at, "resolved_at": f.resolved_at}
            for f in db.scalars(q)]


@router.post("/job-failures/{failure_id}/resolve", status_code=204)
def resolve_failure(failure_id: int, db: Session = Depends(get_db, scope="function"),
                    cu: CurrentUser = Depends(require("admin.jobs:update"))):
    f = db.get(JobFailure, failure_id)
    if not f:
        raise NotFound("Job failure")
    f.resolved_at = func.now()
    audit.record(db, user=cu.user, action="job_failure_resolved", entity="job_failure", entity_id=f.id)
