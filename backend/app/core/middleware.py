"""Pure-ASGI middleware (no BaseHTTPMiddleware: it adds a task + queue per request and breaks contextvars)."""
import asyncio
import json
import time
import uuid

import logging

from app.core import observability as obs
from app.core.config import get_settings
from app.core.context import client_ip_var, request_id_var, stats_var, RequestStats

log = logging.getLogger("mintree.perf")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
]


class RequestContextMiddleware:
    """Request/correlation ID, client IP and security headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hdrs = dict(scope["headers"])
        rid = (hdrs.get(b"x-request-id", b"").decode("latin1")[:64]) or uuid.uuid4().hex
        request_id_var.set(rid)
        client_ip_var.set(scope["client"][0] if scope.get("client") else None)
        scope.setdefault("state", {})["request_id"] = rid
        is_api = scope["path"].startswith("/api")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                h = list(message.get("headers", []))
                names = {k.lower() for k, _ in h}
                h.append((b"x-request-id", rid.encode()))
                h.extend(x for x in SECURITY_HEADERS if x[0] not in names)
                if is_api and b"cache-control" not in names:
                    h.append((b"cache-control", b"no-store"))
                message = {**message, "headers": h}
            await send(message)

        await self.app(scope, receive, send_wrapper)


class AdmissionControl:
    """Bound the number of requests in flight to the DB connection capacity of this process.

    Why per *request* (not per thread): a sync request takes a connection in one thread hop, gives the thread
    back, and needs another hop to finish. Under load the freed threads are taken by new requests that block on
    pool checkout while every connection is held by requests waiting for a thread -> deadlock until the pool
    timeout. Admitting at most `slots` (= pool capacity) requests, *before* any thread or connection is used,
    makes that state unreachable. Excess requests wait here (no thread, no connection) and are shed with 503
    after `timeout_s`.

    Invariant this relies on: a request never holds more than one DB connection at a time.
    """

    def __init__(self, app, slots: int, timeout_s: float, exempt_prefixes: tuple[str, ...] = ()):
        self.app = app
        self.sem = asyncio.Semaphore(slots)
        self.slots = slots
        self.timeout_s = timeout_s
        self.exempt = exempt_prefixes
        self.waiting = 0
        self.in_flight = 0
        self.shed = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(self.exempt):
            return await self.app(scope, receive, send)
        t0 = time.perf_counter()
        self.waiting += 1
        obs.ADMISSION_WAITING.set(self.waiting)
        try:
            await asyncio.wait_for(self.sem.acquire(), self.timeout_s)
        except asyncio.TimeoutError:
            self.shed += 1
            obs.ADMISSION_SHED.inc()
            obs.ADMISSION_WAIT.observe(time.perf_counter() - t0)
            return await self._reject(send, scope)
        finally:
            self.waiting -= 1
            obs.ADMISSION_WAITING.set(self.waiting)
        waited = time.perf_counter() - t0
        obs.ADMISSION_WAIT.observe(waited)
        st = stats_var.get()
        if st is not None:
            st.admission_wait_s = waited
        self.in_flight += 1
        obs.HTTP_IN_FLIGHT.inc()
        released = False

        def release():
            nonlocal released
            if not released:
                released = True
                self.in_flight -= 1
                obs.HTTP_IN_FLIGHT.dec()
                self.sem.release()

        async def send_wrapper(message):
            # The DB session is closed before the response starts (dependencies use scope="function"),
            # so the slot can be freed as soon as headers go out -- slow clients never hold a slot.
            if message["type"] == "http.response.start":
                release()
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            release()

    async def _reject(self, send, scope):
        body = json.dumps({"detail": "Server is busy, retry shortly",
                           "request_id": scope.get("state", {}).get("request_id")}).encode()
        await send({"type": "http.response.start", "status": 503,
                    "headers": [(b"content-type", b"application/json"), (b"retry-after", b"1"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


_NUM = __import__("re").compile(r"/\d+(?=/|$)")


def _normalize(path: str) -> str:
    return _NUM.sub("/{id}", path)


class MetricsMiddleware:
    """Per-request metrics + slow-request log with a DB breakdown. Sits outside AdmissionControl so the latency it
    records is what the client experiences (including time queued for a slot)."""

    def __init__(self, app):
        self.app = app
        self.slow_s = get_settings().slow_request_ms / 1000

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith("/api/metrics"):
            return await self.app(scope, receive, send)
        st = RequestStats(route=_normalize(scope["path"]))
        stats_var.set(st)
        t0 = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            dt = time.perf_counter() - t0
            # Only matched routes get their own label (bounded cardinality); numeric IDs collapse to {id}.
            route = _normalize(scope["path"]) if scope.get("route") is not None else (
                "admission" if status["code"] == 503 else "unmatched")
            st.route = route
            code = status["code"]
            obs.HTTP_REQUESTS.labels(scope["method"], route, str(code)).inc()
            obs.HTTP_LATENCY.labels(scope["method"], route, f"{code // 100}xx").observe(dt)
            if dt >= self.slow_s or code >= 500:
                log.warning("slow request" if code < 500 else "server error", extra={"fields": {
                    "method": scope["method"], "route": route, "status": code, "total_ms": round(dt * 1000, 1),
                    "admission_wait_ms": round(st.admission_wait_s * 1000, 1), "pool_wait_ms": round(st.pool_wait_s * 1000, 1),
                    "db_ms": round(st.db_time_s * 1000, 1), "queries": st.n_queries,
                    "slowest_queries": [(round(x * 1000, 1), q) for x, q in st.slowest]}})
