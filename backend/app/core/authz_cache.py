"""Per-process role/permission cache validated by a Redis *authorization epoch*.

Why: authentication cost 4 DB round-trips per request (session, user, roles, role->permissions) and was measured at
~34 % of server CPU (docs/BACKEND_ARCHITECTURE.md §2.3). Sessions/users stay database-authoritative (one joined query,
so logout / deactivation are immediate). Only the *derived* (role codes, permission codes) set is cached.

Correctness contract (permission changes must take effect immediately, on every replica):
  * every cache entry is tagged with the epoch it was built under;
  * any commit that changes roles / permissions / role assignments / user activation bumps the epoch (after_commit hook);
  * every request reads the epoch (one Redis GET) and ignores entries from older epochs;
  * if Redis is unreachable the cache is bypassed entirely (fail-safe: always read the database) -- Redis is only ever a
    coordination aid, never the source of truth.
Residual risk (documented): a commit followed by a crash before the epoch bump; mitigated by a short entry TTL (30 s).
"""
import logging
import time

from prometheus_client import Counter
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import get_settings

log = logging.getLogger("mintree.authz")
EPOCH_KEY = "mintree:authz_epoch"
TTL_S = 30.0
MAX_ENTRIES = 5000

CACHE = Counter("mintree_authz_cache_total", "Permission cache lookups", ["result"])  # hit | miss | stale | bypass

_entries: dict[int, tuple[str, float, frozenset[str], frozenset[str]]] = {}
_redis = None
_redis_down_until = 0.0


def _client():
    global _redis
    if _redis is None:
        import redis
        _redis = redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.05, socket_connect_timeout=0.05, decode_responses=True)
    return _redis


MEMO_S = 0.02            # a process re-reads a Redis version at most every 20 ms: other replicas see a change within that window (the writing process sees it at once)
_memo: dict[str, tuple[str, float]] = {}


def memo_get(key: str) -> str | None:
    """Redis GET with a 20 ms per-process memo (None => Redis unavailable). Used for the authz epoch and the response-cache versions: at 1,000 requests/s/process
    this turns 1,000-3,000 Redis round trips per second into 50 per key, and was ~29 % of busy CPU in the profile."""
    global _redis_down_until
    now = time.monotonic()
    m = _memo.get(key)
    if m is not None and now - m[1] < MEMO_S:
        return m[0]
    if now < _redis_down_until:
        return None
    try:
        v = _client().get(key) or "0"
    except Exception:
        _redis_down_until = now + 1.0                # don't hammer a dead Redis; retry in 1 s
        _memo.pop(key, None)
        log.warning("Redis unavailable; bypassing caches")
        return None
    _memo[key] = (v, now)
    return v


def forget(key: str) -> None:
    _memo.pop(key, None)


def current_epoch() -> str | None:
    """None => Redis unavailable (caller must bypass the cache)."""
    return memo_get(EPOCH_KEY)


def bump_epoch() -> None:
    try:
        _client().incr(EPOCH_KEY)
    except Exception:
        log.error("could not bump authz epoch; relying on TTL (%ss) and bypass", TTL_S)
    forget(EPOCH_KEY)
    _entries.clear()   # this process sees the change immediately regardless


def load_from_db(db: Session, user_id: int) -> tuple[frozenset[str], frozenset[str]]:
    from app.administration.models import Permission, Role, role_permissions, user_roles
    roles = frozenset(db.scalars(select(Role.code).join(user_roles, user_roles.c.role_id == Role.id).where(user_roles.c.user_id == user_id)))
    perms = frozenset(db.scalars(select(Permission.code).join(role_permissions, role_permissions.c.permission_id == Permission.id)
                                 .join(user_roles, user_roles.c.role_id == role_permissions.c.role_id).where(user_roles.c.user_id == user_id)))
    return roles, perms


def get(db: Session, user_id: int) -> tuple[frozenset[str], frozenset[str]]:
    epoch = current_epoch()
    if epoch is None:
        CACHE.labels("bypass").inc()
        return load_from_db(db, user_id)
    hit = _entries.get(user_id)
    if hit and hit[0] == epoch and time.monotonic() - hit[1] < TTL_S:
        CACHE.labels("hit").inc()
        return hit[2], hit[3]
    CACHE.labels("stale" if hit else "miss").inc()
    roles, perms = load_from_db(db, user_id)
    if len(_entries) >= MAX_ENTRIES:
        _entries.clear()
    _entries[user_id] = (epoch, time.monotonic(), roles, perms)
    return roles, perms


# ---- invalidation: flag the session when authorization-relevant rows change, bump the epoch only AFTER commit ----
_WATCHED = None


def _watched():
    global _WATCHED
    if _WATCHED is None:
        from app.administration.models import Role, User
        _WATCHED = (Role, User)
    return _WATCHED


@event.listens_for(Session, "after_flush")
def _flag(session: Session, _ctx) -> None:
    watched = _watched()
    from sqlalchemy import inspect as _inspect
    from app.administration.models import SessionRecord
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, watched):
            session.info["authz_dirty"] = True
            return
        # a session row matters here only when it is revoked / its expiry moves (the per-process session cache honours the same epoch); `last_seen_at` touches must not bump it
        if isinstance(obj, SessionRecord) and obj not in session.new:
            h = _inspect(obj).attrs
            if h.revoked_at.history.has_changes() or h.expires_at.history.has_changes():
                session.info["authz_dirty"] = True
                return
    # association-table-only changes (role_permissions / user_roles) mark their owner dirty via the relationship,
    # which is covered above; raw SQL touching these tables must call bump_epoch() explicitly.


@event.listens_for(Session, "after_commit")
def _bump(session: Session) -> None:
    if session.info.pop("authz_dirty", False):
        bump_epoch()


@event.listens_for(Session, "after_rollback")
def _clear(session: Session) -> None:
    session.info.pop("authz_dirty", None)
