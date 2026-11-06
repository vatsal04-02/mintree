"""notify(): add per-user notifications inside the caller's transaction. Recipients are resolved from permission codes (who can act)
or explicit user ids; the actor is never notified of their own action."""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.notifications.models import UserNotification


def users_with_permission(db: Session, code: str) -> list[int]:
    rows = db.execute(text("""SELECT DISTINCT u.id FROM users u JOIN user_roles ur ON ur.user_id = u.id
                                JOIN role_permissions rp ON rp.role_id = ur.role_id JOIN permissions p ON p.id = rp.permission_id
                               WHERE p.code = :c AND u.is_active ORDER BY u.id"""), {"c": code})
    return [r[0] for r in rows]


def notify(db: Session, *, kind: str, title: str, body: str | None = None, link: str | None = None, priority: str = "NORMAL",
           user_ids: list[int] | None = None, permission: str | None = None, exclude_user_id: int | None = None) -> int:
    ids = set(user_ids or [])
    if permission:
        ids.update(users_with_permission(db, permission))
    ids.discard(exclude_user_id)
    if ids and priority not in ("HIGH", "CRITICAL"):                  # urgent alerts cannot be muted
        muted = db.execute(text("SELECT user_id FROM notification_preferences WHERE kind = :k AND channel = 'IN_APP' AND NOT enabled AND user_id = ANY(:u)"), {"k": kind, "u": sorted(ids)})
        ids.difference_update(r[0] for r in muted)
    for uid in sorted(ids):
        db.add(UserNotification(user_id=uid, kind=kind, title=title, body=body, link=link, priority=priority))
    return len(ids)
