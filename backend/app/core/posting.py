"""Glue for business-posting endpoints: unit of work + idempotency, committing BEFORE the response is built."""
from collections.abc import Callable
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.idempotency import idempotent
from app.core.uow import run_tx


def idempotent_post(db: Session, cu: CurrentUser, *, scope: str, key: str, payload: Any,
                    effect: Callable[[Session], tuple[int, Any, str | None, Any]], isolation: str | None = None) -> JSONResponse:
    """`effect(db)` performs the business posting and returns (status, body, resource_type, resource_id)."""

    def work(d: Session):
        def eff():
            status, body, rtype, rid = effect(d)
            return status, jsonable_encoder(body), rtype, None if rid is None else str(rid)
        return idempotent(d, scope=scope, key=key, actor_id=cu.id, payload=payload, effect=eff)

    posted = run_tx(db, work, name=scope, isolation=isolation)   # commit happens inside run_tx
    return JSONResponse(posted.body, status_code=posted.status, headers={"Idempotent-Replayed": "true"} if posted.replayed else None)
