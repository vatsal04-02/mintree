from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.administration.models import SessionRecord, User
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import Forbidden
from app.core.security import hash_token

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class CurrentUser:
    user: User
    session: SessionRecord
    permissions: set[str] = field(default_factory=set)

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def email(self) -> str:
        return self.user.email

    def can(self, code: str) -> bool:
        return code in self.permissions


def _load(request: Request, db: Session, *, allow_password_change: bool) -> CurrentUser:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    now = datetime.now(timezone.utc)
    sess = db.scalar(select(SessionRecord).where(SessionRecord.token_hash == hash_token(token)))
    if not sess or sess.revoked_at or sess.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")

    # CSRF: every state-changing request must echo the per-session token.
    if request.method in UNSAFE and request.headers.get("x-csrf-token") != sess.csrf_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")

    if user.must_change_password and not allow_password_change:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password change required")

    if now - sess.last_seen_at > timedelta(minutes=1):
        sess.last_seen_at = now

    perms = {p.code for role in user.roles for p in role.permissions}
    return CurrentUser(user=user, session=sess, permissions=perms)


def current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    cu = _load(request, db, allow_password_change=False)
    request.state.current_user = cu
    return cu


def current_user_allow_pw_change(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    return _load(request, db, allow_password_change=True)


def require(*codes: str):
    """Backend permission gate. All listed codes are required."""

    def dep(cu: CurrentUser = Depends(current_user)) -> CurrentUser:
        missing = [c for c in codes if c not in cu.permissions]
        if missing:
            raise Forbidden(f"Missing permission: {', '.join(missing)}")
        return cu

    return dep
