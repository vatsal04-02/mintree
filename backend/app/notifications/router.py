from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, current_user
from app.core.errors import NotFound
from app.core.uow import run_tx
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.notifications.models import NotificationPreference as Pref
from app.notifications.models import UserNotification as N

router = APIRouter(prefix="/notifications", tags=["notifications"])
DB = Depends(get_db, scope="function")


def _d(n: N) -> dict:
    return {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "priority": n.priority, "created_at": n.created_at, "read": n.read_at is not None}


@router.get("")
def mine(unread_only: bool = False, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB, cu: CurrentUser = Depends(current_user)):
    q = select(N).where(N.user_id == cu.id)
    if unread_only:
        q = q.where(N.read_at.is_(None))
    unread = db.scalar(select(func.count()).select_from(N).where(N.user_id == cu.id, N.read_at.is_(None)))
    rows = db.scalars(q.order_by(N.id.desc()).limit(page_size).offset((page - 1) * page_size))
    return {"unread": unread, "items": [_d(n) for n in rows], "page": page}


@router.post("/{nid}/read")
def read(nid: int, db: Session = DB, cu: CurrentUser = Depends(current_user)):
    def work(d: Session):
        r = d.execute(update(N).where(N.id == nid, N.user_id == cu.id).values(read_at=func.coalesce(N.read_at, func.now())).returning(N.id)).first()
        if not r:
            raise NotFound("Notification")
        return {"id": nid, "read": True}
    return run_tx(db, work, name="notifications.read")


@router.post("/read-all")
def read_all(db: Session = DB, cu: CurrentUser = Depends(current_user)):
    return run_tx(db, lambda d: {"marked": d.execute(update(N).where(N.user_id == cu.id, N.read_at.is_(None)).values(read_at=func.now())).rowcount}, name="notifications.read_all")


class PrefIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$", max_length=60)      # e.g. po.submitted
    channel: Literal["IN_APP"] = "IN_APP"
    enabled: bool


@router.get("/preferences")
def preferences(db: Session = DB, cu: CurrentUser = Depends(current_user)):
    rows = db.scalars(select(Pref).where(Pref.user_id == cu.id).order_by(Pref.kind))
    seen = [r[0] for r in db.execute(select(N.kind).where(N.user_id == cu.id).distinct().order_by(N.kind))]
    return {"muted_or_set": [{"kind": p.kind, "channel": p.channel, "enabled": p.enabled} for p in rows], "kinds_received": seen,
            "note": "Only in-app notifications exist; HIGH and CRITICAL priority alerts are always delivered."}


@router.put("/preferences")
def set_preference(body: PrefIn, db: Session = DB, cu: CurrentUser = Depends(current_user)):
    def work(d: Session):
        d.execute(pg_insert(Pref).values(user_id=cu.id, kind=body.kind, channel=body.channel, enabled=body.enabled)
                  .on_conflict_do_update(index_elements=["user_id", "kind", "channel"], set_={"enabled": body.enabled, "updated_at": func.now()}))
        return {"kind": body.kind, "channel": body.channel, "enabled": body.enabled}
    return run_tx(db, work, name="notifications.preference")
