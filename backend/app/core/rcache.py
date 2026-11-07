"""Response cache for read-mostly endpoints (reference data lists, dashboards).

What it is NOT: a source of truth. Stock allocation, QC release, postings and every other write read the database; this only short-cuts *reads* of master data and
analytics. Safety properties:
  * the permission check (`require(...)`) always runs first; the key also carries a signature of the caller's permission set, because `read_transform`s hide fields per role;
  * invalidation is by version: every ORM commit touching a cached table bumps that table's Redis version (after_commit), and entries remember the version they were built under;
    raw-SQL writes do not bump it, so every entry also expires after `ttl_s` (default 60 s for reference data);
  * Redis unavailable -> no caching at all (bypass), never a stale answer;
  * bounded memory (LRU, MAX_ENTRIES / MAX_BYTES) and a single-flight lock per key (no stampede: concurrent misses wait for the first computation);
  * dashboards (ttl 15 s, not version-checked) say so in the response: `meta.cached`, `meta.generated_at`, `meta.max_age_s`.
Metrics: mintree_response_cache_total{result=hit|miss|bypass}."""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict

from prometheus_client import Counter
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core import authz_cache

log = logging.getLogger("mintree.rcache")
RESULT = Counter("mintree_response_cache_total", "Response cache lookups", ["ns", "result"])
MAX_ENTRIES = 2000
MAX_BYTES = 64 * 1024 * 1024
_lock = threading.Lock()
_entries: "OrderedDict[str, tuple[str, float, bytes]]" = OrderedDict()
_bytes = 0
_flights: dict[str, threading.Lock] = {}
CACHED_TABLES: dict[str, str] = {}       # table name -> namespace (registered by the routers that opt in)


def _version(ns: str) -> str | None:
    """Current version of a namespace; None if Redis is unavailable (caller bypasses)."""
    if authz_cache.current_epoch() is None:          # also trips the shared 1 s Redis back-off
        return None
    try:
        return authz_cache._client().get(f"mintree:rcv:{ns}") or "0"
    except Exception:
        return None


def bump(ns: str) -> None:
    try:
        authz_cache._client().incr(f"mintree:rcv:{ns}")
    except Exception:
        log.error("could not bump response-cache version for %s; relying on the TTL", ns)
    with _lock:
        for k in [k for k in _entries if k.startswith(ns + "|")]:
            _drop(k)


def perm_sig(perms) -> str:
    return hashlib.blake2b(",".join(sorted(perms)).encode(), digest_size=8).hexdigest()


def _drop(key: str) -> None:
    global _bytes
    e = _entries.pop(key, None)
    if e:
        _bytes -= len(e[2])


def lookup(ns: str, key: str, ttl_s: float, check_version: bool = True) -> tuple[bytes | None, str | None]:
    """-> (body or None, version). version None means 'do not cache this response' (Redis down)."""
    ver = _version(ns) if check_version else "ttl"
    if ver is None:
        RESULT.labels(ns, "bypass").inc()
        return None, None
    full = f"{ns}|{key}"
    e = _entries.get(full)
    if e and e[0] == ver and time.monotonic() - e[1] < ttl_s:
        with _lock:
            if full in _entries:
                _entries.move_to_end(full)
        RESULT.labels(ns, "hit").inc()
        return e[2], ver
    RESULT.labels(ns, "miss").inc()
    return None, ver


def store(ns: str, key: str, ver: str, body: bytes) -> None:
    global _bytes
    full = f"{ns}|{key}"
    with _lock:
        _drop(full)
        _entries[full] = (ver, time.monotonic(), body)
        _bytes += len(body)
        while len(_entries) > MAX_ENTRIES or _bytes > MAX_BYTES:
            _drop(next(iter(_entries)))


def single_flight(ns: str, key: str):
    """Context manager: concurrent misses for one key serialise, so the second finds the first's result (checked by the caller after entering)."""
    full = f"{ns}|{key}"
    with _lock:
        lk = _flights.setdefault(full, threading.Lock())
    return _Flight(full, lk)


class _Flight:
    def __init__(self, full: str, lk: threading.Lock):
        self.full, self.lk = full, lk

    def __enter__(self):
        self.lk.acquire()
        return self

    def __exit__(self, *exc):
        self.lk.release()
        with _lock:
            if not self.lk.locked():
                _flights.pop(self.full, None)


def clear() -> None:
    global _bytes
    with _lock:
        _entries.clear()
        _bytes = 0


# ---- invalidation: note which cached tables a flush touched, bump their versions only after the commit succeeds
@event.listens_for(Session, "after_flush")
def _flag(session: Session, _ctx) -> None:
    if not CACHED_TABLES:
        return
    touched = session.info.setdefault("rc_dirty", set())
    for obj in (*session.new, *session.dirty, *session.deleted):
        ns = CACHED_TABLES.get(getattr(obj, "__tablename__", ""))
        if ns:
            touched.add(ns)


@event.listens_for(Session, "after_commit")
def _bump(session: Session) -> None:
    for ns in session.info.pop("rc_dirty", ()):
        bump(ns)


@event.listens_for(Session, "after_rollback")
def _clear(session: Session) -> None:
    session.info.pop("rc_dirty", None)
