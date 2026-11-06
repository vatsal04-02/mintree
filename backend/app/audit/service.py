import datetime as dt
import decimal
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.core.context import client_ip_var, request_id_var

SENSITIVE = {"password_hash", "token_hash", "csrf_token", "bank_account_no"}


def _jsonable(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return str(v)
    return v


def snapshot(obj: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    """Column-level snapshot of a model instance, with secrets redacted."""
    exclude = exclude or set()
    out: dict[str, Any] = {}
    for col in inspect(obj).mapper.column_attrs:
        if col.key in exclude:
            continue
        out[col.key] = "***" if col.key in SENSITIVE else _jsonable(getattr(obj, col.key))
    return out


def record(
    db: Session,
    *,
    user: Any | None,
    action: str,
    entity: str,
    entity_id: Any = None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
    source: str = "api",
    user_email: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=getattr(user, "id", None),
        user_email=getattr(user, "email", None) or user_email,
        action=action,
        entity=entity,
        entity_id=None if entity_id is None else str(entity_id),
        before=before,
        after=after,
        reason=reason,
        source=source,
        request_id=request_id_var.get(),
        ip=client_ip_var.get(),
    )
    db.add(entry)
    return entry


def diff(before: dict, after: dict) -> tuple[dict, dict]:
    """Only the fields that changed, so audit rows stay readable."""
    keys = [k for k in after if before.get(k) != after.get(k) and k not in {"updated_at"}]
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}
