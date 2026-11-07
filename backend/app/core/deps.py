import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from prometheus_client import Counter
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session, make_transient_to_detached, noload

from app.administration.models import SessionRecord, User
from app.core.config import get_settings
from app.core import authz_cache
from app.core.db import get_db
from app.core.errors import Forbidden
from app.core.security import hash_token

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

# ---- per-process session cache --------------------------------------------------------------------------------------------------------------
# Saves the session+user query on every request. Safe because it is validated against the same Redis authorization epoch as the permission cache:
# logout, deactivation, password reset, role/permission changes all bump the epoch (see authz_cache.py), so every process drops its entries at once;
# expiry is re-checked on every request; entries also die after SESSION_TTL_S. Without Redis (epoch unavailable) the cache is bypassed entirely.
# The cached copy never contains the password hash; routes that need it use `current_user_allow_pw_change`, which always reads the database.
SESSION_TTL_S = 30.0
MAX_SESSIONS = 5000
_SESS: dict[str, tuple[str, float, dict, dict, frozenset, frozenset]] = {}
SESSION_CACHE = Counter("mintree_session_cache_total", "Session cache lookups", ["result"])   # hit | miss | stale | bypass


def _cols(obj, exclude: tuple[str, ...] = ()) -> dict:
    return {a.key: getattr(obj, a.key) for a in sa_inspect(obj).mapper.column_attrs if a.key not in exclude}


def _attach(db: Session, model, cols: dict):
    obj = model(**cols)
    make_transient_to_detached(obj)
    return db.merge(obj, load=False)                  # attached and clean, with no SQL


@dataclass
class CurrentUser:
    user: User
    session: SessionRecord
    permissions: set[str] = field(default_factory=set)
    role_codes: set[str] = field(default_factory=set)

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
    th = hash_token(token)
    epoch = None if allow_password_change else authz_cache.current_epoch()
    hit = _SESS.get(th) if epoch is not None else None
    if hit and hit[0] == epoch and time.monotonic() - hit[1] < SESSION_TTL_S and hit[2]["expires_at"] > now:
        SESSION_CACHE.labels("hit").inc()
        sess, user = _attach(db, SessionRecord, hit[2]), _attach(db, User, hit[3])
        roles, perms = hit[4], hit[5]
    else:
        SESSION_CACHE.labels("bypass" if epoch is None else ("stale" if hit else "miss")).inc()
        # ONE round-trip for session + user + department (was 4 queries incl. roles and permissions).
        row = db.execute(
            select(SessionRecord, User).join(User, User.id == SessionRecord.user_id)
            .where(SessionRecord.token_hash == th).options(noload(User.roles), noload(User.department))
        ).first()
        if not row or row[0].revoked_at or row[0].expires_at <= now:
            _SESS.pop(th, None)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
        sess, user = row
        if not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")
        roles, perms = authz_cache.get(db, user.id)   # cached per process, validated by a Redis epoch (see authz_cache.py)
        if epoch is not None:
            if len(_SESS) >= MAX_SESSIONS:
                _SESS.clear()
            _SESS[th] = (epoch, time.monotonic(), _cols(sess), _cols(user, ("password_hash",)), roles, perms)
    if not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")

    # CSRF: every state-changing request must echo the per-session token.
    if request.method in UNSAFE and request.headers.get("x-csrf-token") != sess.csrf_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")

    if user.must_change_password and not allow_password_change:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password change required")

    if now - sess.last_seen_at > timedelta(minutes=1):
        sess.last_seen_at = now
        if th in _SESS:                                  # keep the cached copy in step so the touch is not repeated on every request
            _SESS[th][2]["last_seen_at"] = now
    return CurrentUser(user=user, session=sess, permissions=set(perms), role_codes=set(roles))


def current_user(request: Request, db: Session = Depends(get_db, scope="function")) -> CurrentUser:
    cu = _load(request, db, allow_password_change=False)
    request.state.current_user = cu
    return cu


def current_user_allow_pw_change(request: Request, db: Session = Depends(get_db, scope="function")) -> CurrentUser:
    return _load(request, db, allow_password_change=True)


def require(*codes: str):
    """Backend permission gate. All listed codes are required."""

    def dep(cu: CurrentUser = Depends(current_user)) -> CurrentUser:
        missing = [c for c in codes if c not in cu.permissions]
        if missing:
            raise Forbidden(f"Missing permission: {', '.join(missing)}")
        return cu

    return dep
