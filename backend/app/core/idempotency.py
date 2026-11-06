"""Idempotency for business-posting APIs.

Protocol (all inside the business transaction, see `idempotent`):
  1. INSERT (scope, key, request_hash) ... ON CONFLICT DO NOTHING.  A concurrent duplicate blocks on the unique
     index until the first transaction commits or aborts -- PostgreSQL serialises it for us.
  2. Inserted  -> perform the business effect, then store the response on the same row, commit once.
     Conflict   -> the first request committed: same payload => replay its stored response (no new effect);
                   different payload => 409 `idempotency_key_reuse`.
  3. If the first transaction aborted, its row never existed, so the duplicate simply proceeds.
The database unique constraints on the business tables remain the final guard (a bug here cannot double-post).
"""
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.context import request_id_var
from app.core.platform_models import IdempotencyKey

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{7,127}$")


class IdempotencyConflict(HTTPException):
    def __init__(self):
        super().__init__(409, {"code": "idempotency_key_reuse",
                               "message": "This Idempotency-Key was already used with a different request payload."})


def require_idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str:
    """FastAPI dependency: posting endpoints must carry a client-generated key (8-128 chars)."""
    if not idempotency_key or not KEY_RE.match(idempotency_key):
        raise HTTPException(400, "Idempotency-Key header is required (8-128 chars: letters, digits, _ . : -)")
    return idempotency_key


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass
class Posted:
    status: int
    body: dict
    replayed: bool = False


def idempotent(db: Session, *, scope: str, key: str, actor_id: int | None, payload: Any,
               effect: Callable[[], tuple[int, dict, str | None, str | None]]) -> Posted:
    """Run `effect()` at most once per (scope, key). `effect` returns (status, body, resource_type, resource_id).
    Must be called inside `run_tx` so the key row commits atomically with the business effect."""
    h = canonical_hash(payload)
    inserted = db.execute(
        insert(IdempotencyKey).values(scope=scope, key=key, actor_id=actor_id, request_hash=h,
                                      request_id=request_id_var.get())
        .on_conflict_do_nothing(index_elements=["scope", "key"]).returning(IdempotencyKey.id)).scalar()
    if inserted is None:
        existing = db.scalar(select(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key))
        if existing is None:  # lost a race with an aborted twin between INSERT and SELECT: caller's retry handles it
            raise HTTPException(409, "Concurrent request with the same Idempotency-Key; retry")
        if existing.request_hash != h:
            raise IdempotencyConflict()
        if existing.response_body is None:
            raise HTTPException(409, "Original request with this Idempotency-Key has not completed; retry")
        return Posted(existing.response_status or 200, existing.response_body, replayed=True)
    status, body, rtype, rid = effect()
    row = db.execute(select(IdempotencyKey).where(IdempotencyKey.id == inserted)).scalar_one()
    row.response_status, row.response_body, row.resource_type, row.resource_id = status, body, rtype, rid
    return Posted(status, body)
