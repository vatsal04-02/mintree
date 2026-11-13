"""Readiness checks for /api/ready (liveness stays /api/health).

A check is `required` (failure -> HTTP 503: the instance must not take traffic) or optional (failure -> status "degraded", HTTP 200: the app has a
documented fallback, e.g. in-process rate limiting when Redis is down). Every check is time-boxed so /ready can never hang."""
from __future__ import annotations

import shutil
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

from sqlalchemy import text

from app.core.config import get_settings

_TIMEOUT_S = 2.0


def _db() -> str:
    from app.core.db import SessionLocal
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return "ok"


def _migrations() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.core.db import SessionLocal
    cfg = Config("alembic.ini")
    heads = set(ScriptDirectory.from_config(cfg).get_heads())
    with SessionLocal() as db:
        current = {r[0] for r in db.execute(text("SELECT version_num FROM alembic_version"))}
    if current != heads:
        raise RuntimeError(f"database at {sorted(current)}, code expects {sorted(heads)}: run `alembic upgrade head`")
    return "current"


def _redis() -> str:
    import redis
    redis.Redis.from_url(get_settings().redis_url, socket_timeout=1, socket_connect_timeout=1).ping()
    return "ok"


def _broker() -> str:
    import socket
    from urllib.parse import urlparse
    u = urlparse(get_settings().broker_url)
    with socket.create_connection((u.hostname or "127.0.0.1", u.port or 5672), timeout=1):
        pass
    return "reachable"


def _outbox() -> str:
    """Queue depth: events not yet published. A growing backlog means the publisher/worker is not running."""
    from app.core.db import SessionLocal
    with SessionLocal() as db:
        n = db.execute(text("SELECT count(*) FROM outbox_events WHERE status IN ('pending','publishing') AND created_at < now() - interval '5 minutes'")).scalar() or 0
    if n:
        raise RuntimeError(f"{n} outbox events older than 5 minutes are unpublished")
    return "drained"


def _disk() -> str:
    root = get_settings().storage_root
    free = shutil.disk_usage(root if shutil.os.path.isdir(root) else ".").free
    if free < 500 * 1024 * 1024:
        raise RuntimeError(f"only {free // (1024 * 1024)} MB free")
    return f"{free // (1024 * 1024 * 1024)} GB free"


def _scheduler() -> str:
    """Demo Live Mode scheduler: reports 'off' when the feature flag is off (that is healthy); otherwise whether its loop is alive."""
    from app.demo_live import engine as live
    return live.status_label()


# name, function, required
CHECKS = [("database", _db, True), ("migrations", _migrations, True), ("disk", _disk, True),
          ("cache", _redis, False), ("queue", _broker, False), ("outbox", _outbox, False), ("scheduler", _scheduler, False)]


def run_checks() -> tuple[bool, dict]:
    out: dict = {}
    ok = True
    degraded = False
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        futs = {name: (pool.submit(fn), required, time.perf_counter()) for name, fn, required in CHECKS}
        for name, (fut, required, t0) in futs.items():
            try:
                detail = fut.result(timeout=_TIMEOUT_S)
                out[name] = {"ok": True, "detail": detail, "ms": round((time.perf_counter() - t0) * 1000)}
            except FutTimeout:
                out[name] = {"ok": False, "required": required, "detail": f"timed out after {_TIMEOUT_S}s"}
            except Exception as e:  # noqa: BLE001 - a readiness probe must report, never raise
                out[name] = {"ok": False, "required": required, "detail": str(e)[:200]}
            if not out[name]["ok"]:
                ok, degraded = (ok and not required), True
    return ok, {"status": "ready" if not degraded else ("degraded" if ok else "not_ready"), "checks": out}
