"""Additive endpoints for Demo Live Mode and the real-time event stream.

GET  /api/events           SSE stream for any signed-in user: committed business events (from the outbox) and demo presentation events, each filtered to what
                           that user may see; also the user's own new notifications.
GET  /api/activity         recent committed business activity the caller may see (the Live activity timeline).
GET  /api/demo/status      small status for the "Demo mode" pill (any signed-in user)
POST /api/demo/controls    pause / resume / speed / stop / start  (demo administrator only)
POST /api/demo/trigger     queue a one-shot scenario (demo administrator only)
GET  /api/demo/metrics     live request metrics for the Demo controls drawer (demo administrator only)"""
from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import activity
from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.core.deps import CurrentUser, current_user
from app.core.errors import BusinessConflict, Forbidden
from app.demo_live import guard, store

router = APIRouter(tags=["demo"])
HEARTBEAT_S = 15
POLL_S = 1.0
MIN_GAP_S = {"kpi.invalidate": 2.0}              # event rate limiting: the UI never flickers (one invalidate per 2 s, latest scope wins)


def _demo_admin(cu: CurrentUser) -> CurrentUser:
    s = get_settings()
    if guard.refusal() is not None:
        raise Forbidden("Demo controls are not available in this environment")
    if cu.email != s.demo_admin_email:
        raise Forbidden("Demo controls are for the demo administrator")
    return cu


@router.get("/demo/status")
def status(db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(current_user)):
    try:
        why = guard.refusal()
    except Exception:                                         # noqa: BLE001 - the pill must never break a page
        why = "unavailable"
    if why:
        return {"available": False}                           # no reason: configuration details are not for every signed-in user
    st = store.get_state(db)
    alive = bool(st["heartbeat_at"]) and (db.execute(text("SELECT now() - heartbeat_at < interval '15 seconds' FROM demo_live_state WHERE id = 1")).scalar() or False)
    return {"available": True, "active": bool(st["enabled"] and alive), "paused": bool(st["paused"]), "speed": st["speed"], "worker_alive": alive,
            "counters": st["counters"], "last_action_at": st["last_action_at"], "controls": cu.email == get_settings().demo_admin_email}


class ControlIn(BaseModel):
    action: Literal["pause", "resume", "speed", "stop", "start"]
    speed: Literal[1, 3] | None = None


@router.post("/demo/controls")
def controls(body: ControlIn, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(current_user)):
    _demo_admin(cu)
    if body.action == "pause":
        store.set_state(db, paused=True)
    elif body.action == "resume":
        store.set_state(db, paused=False, enabled=True)
    elif body.action == "stop":
        store.set_state(db, enabled=False)
    elif body.action == "start":
        store.set_state(db, enabled=True, paused=False)
    elif body.action == "speed":
        store.set_state(db, speed=body.speed or 1)
    db.commit()
    return store.get_state(db)


LEGACY_TRIGGERS = ("order", "quality_alert", "big_payment", "low_stock")


class TriggerIn(BaseModel):
    kind: str

    @model_validator(mode="after")
    def _known(self):
        from app.demo_live.actions import Actions
        if self.kind not in LEGACY_TRIGGERS and self.kind not in Actions.KINDS:
            raise ValueError(f"unknown demo action {self.kind!r}")
        return self


@router.post("/demo/trigger", status_code=202)
def trigger(body: TriggerIn, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(current_user)):
    _demo_admin(cu)
    tid = store.add_trigger(db, body.kind, cu.id)
    db.commit()
    return {"id": tid, "kind": body.kind, "queued": True}


@router.get("/demo/triggers")
def recent_triggers(limit: int = 12, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(current_user)):
    """What the presenter asked for and what happened (the plain-English result of each queued action)."""
    _demo_admin(cu)
    rows = db.execute(text("SELECT id, kind, requested_at, done_at, result FROM demo_live_triggers ORDER BY id DESC LIMIT :n"), {"n": max(1, min(limit, 50))}).mappings()
    return {"items": [{"id": r["id"], "kind": r["kind"], "requested_at": r["requested_at"].isoformat(), "done_at": r["done_at"].isoformat() if r["done_at"] else None, "result": r["result"]} for r in rows]}


class ScenarioIn(BaseModel):
    action: Literal["start", "pause", "resume", "reset", "retry", "step", "speed"]
    speed: Literal[1, 3] | None = None


@router.get("/demo/scenario")
def scenario_status(db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(current_user)):
    _demo_admin(cu)
    from app.demo_live import scenario
    return scenario.public(db)


@router.post("/demo/scenario")
def scenario_control(body: ScenarioIn, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(current_user)):
    _demo_admin(cu)
    from app.demo_live import scenario
    try:
        out = scenario.control(db, body.action, body.speed)
    except ValueError as e:
        raise BusinessConflict("scenario_state", str(e)) from e
    db.commit()
    return out


@router.post("/demo/reset")
async def reset(db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(current_user)):
    """Restore the golden snapshot (seconds). The caller's session is preserved, so the page can simply reload."""
    _demo_admin(cu)
    db.close()                                    # this request must not hold a connection to the database that is about to be replaced
    from app.demo_live import ops
    return await run_in_threadpool(ops.reset)


@router.get("/demo/metrics")
def metrics(cu: CurrentUser = Depends(current_user)):
    _demo_admin(cu)
    from app.core import observability as obs
    return obs.live_snapshot()


# ---------------------------------------------------------------- the event stream
# Two sources feed one stream: (1) COMMITTED business events from the transactional outbox (every person's and every demo action alike) and
# (2) the demo engine's own presentation events. Both are filtered per viewer by permission. The client's Last-Event-ID is "<engine>:<outbox>".
GAP_TTL_S = 15.0                                   # an outbox id below the cursor that has not appeared yet is a transaction still in flight; wait for it this long
REPLAY_CAP = 400                                   # a client further behind than this just gets a full refresh instead of a replay


class Cursor:
    def __init__(self, ev: int, nt: int, ob: int) -> None:
        self.ev, self.nt, self.ob = ev, nt, ob
        self.gaps: dict[int, float] = {}           # outbox ids skipped over (rolled back, or committing late) -> first time we noticed

    @property
    def sse_id(self) -> str:
        return f"{self.ev}:{self.ob}"


def _poll(user_id: int, perms: set[str], cur: Cursor) -> list[dict]:
    out: list[dict] = []
    with SessionLocal() as db:
        for r in db.execute(text("SELECT id, type, permission, payload FROM demo_live_events WHERE id > :i ORDER BY id LIMIT 200"), {"i": cur.ev}).mappings():
            cur.ev = r["id"]
            if r["permission"] is None or r["permission"] in perms:
                out.append({"type": r["type"], "data": r["payload"]})
        for r in db.execute(text("SELECT id, kind, title, priority, link FROM user_notifications WHERE user_id = :u AND id > :i ORDER BY id LIMIT 50"), {"u": user_id, "i": cur.nt}).mappings():
            cur.nt = r["id"]
            out.append({"type": "notification.new", "data": {"id": r["id"], "kind": r["kind"], "title": r["title"], "priority": r["priority"], "link": r["link"]}})
        # Committed business events. Ids are handed out before commit, so a lower id can become visible after a higher one: ids skipped over are kept
        # as gaps and asked for again until they appear or expire. Nothing committed is missed; nothing is delivered twice.
        gaps = sorted(cur.gaps)
        rows = [dict(r) for r in db.execute(text("""SELECT id, event_id, event_type, aggregate_type, payload, created_at FROM outbox_events
                                                      WHERE id > :i OR id = ANY(:g) ORDER BY id LIMIT 300"""), {"i": cur.ob, "g": gaps}).mappings()]
        now = time.monotonic()
        seen = {r["id"] for r in rows}
        if rows:
            hi = max(cur.ob, max(seen))
            for missing in range(cur.ob + 1, hi):
                if missing not in seen and missing not in cur.gaps:
                    cur.gaps[missing] = now
            cur.ob = hi
        for g in list(cur.gaps):
            if g in seen or now - cur.gaps[g] > GAP_TTL_S:
                cur.gaps.pop(g, None)
        shown = [r for r in rows if activity.visible(r, perms)]
        if shown:
            names = activity.names_for(db, shown)
            for r in shown:
                item = activity.render(r, names)
                if item:
                    out.append({"type": "activity", "data": item, "scopes": item["scopes"]})
    return out


def _start_cursors(user_id: int, last_event_id: str | None) -> tuple[Cursor, bool, bool]:
    """-> (cursor, live_mode_on, resync). resync is True when the client is reconnecting and may have missed events."""
    with SessionLocal() as db:
        ev_max = db.execute(text("SELECT COALESCE(MAX(id), 0) FROM demo_live_events")).scalar() or 0
        ob_max = db.execute(text("SELECT COALESCE(MAX(id), 0) FROM outbox_events")).scalar() or 0
        nt = db.execute(text("SELECT COALESCE(MAX(id), 0) FROM user_notifications WHERE user_id = :u"), {"u": user_id}).scalar() or 0
        on = bool(guard.refusal() is None and db.execute(text("SELECT enabled AND NOT paused FROM demo_live_state WHERE id = 1")).scalar())
    ev, ob, resync = ev_max, ob_max, False
    if last_event_id:
        a, _, b = last_event_id.partition(":")
        if a.isdigit() and (not b or b.isdigit()):
            resync = True
            ev = min(int(a), ev_max)
            ob = min(int(b), ob_max) if b else ob_max                  # a snapshot restore can leave a client ahead of the database
            if ob_max - ob > REPLAY_CAP:
                ob = ob_max                                           # too far behind to replay item by item: the client does a full refresh instead
    return Cursor(ev, nt, ob), on, resync


def _running_orders() -> list[dict]:
    with SessionLocal() as db:
        return [dict(r) for r in db.execute(text("SELECT id, number FROM production_orders WHERE status = 'IN_PROGRESS' ORDER BY id DESC LIMIT 3")).mappings()]


def telemetry(orders: list[dict], t: float) -> list[dict]:
    """SIMULATED instrument readings: deterministic waves over time, per running batch. Generated here for the stream only; never stored, never read by any calculation."""
    out = []
    for o in orders:
        ph = (o["id"] % 7) * 0.9
        out.append({"order_id": o["id"], "number": o["number"], "simulated": True, "readings": {
            "temperature_c": round(74 + 3.2 * math.sin(t / 9 + ph), 1), "weigh_scale_kg": round(48.6 + 0.35 * math.sin(t / 5 + ph), 2),
            "line_speed_ppm": round(42 + 4 * math.sin(t / 13 + ph))}})
    return out


def _sse(event: str, data, eid: str | None = None) -> bytes:
    head = f"id: {eid}\n" if eid is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


MAX_STREAMS_PER_USER = 6                            # a runaway client cannot hold unlimited connections open
_open: dict[int, int] = {}


@router.get("/events")
async def events(request: Request, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    uid, perms = cu.id, set(cu.permissions)
    if _open.get(uid, 0) >= MAX_STREAMS_PER_USER:
        db.close()
        return Response(status_code=429, headers={"Retry-After": "5"})
    cur, live_on, resync = await run_in_threadpool(_start_cursors, uid, request.headers.get("last-event-id"))
    db.close()                                              # release the pooled connection: a stream must never hold one
    _open[uid] = _open.get(uid, 0) + 1

    async def gen():
        nonlocal live_on
        try:
            yield b"retry: 3000\n\n"
            yield _sse("hello", {"user_id": uid, "live": live_on, "resync": resync}, cur.sse_id)          # carries the cursor, so even a client that saw nothing else can resume from here
            if resync:                                      # reconnecting: whatever was missed is re-read from the database, never assumed
                yield _sse("kpi.invalidate", {"scope": ["all"], "resync": True}, cur.sse_id)
            last_beat = last_tel = time.monotonic()
            last_sent: dict[str, float] = {}
            pending: dict | None = None
            while not await request.is_disconnected():
                try:
                    items = await run_in_threadpool(_poll, uid, perms, cur)
                except Exception:                            # noqa: BLE001 - the client reconnects (or falls back to polling); never crash the stream
                    await asyncio.sleep(POLL_S)
                    continue
                now = time.monotonic()
                for it in items:
                    scopes = it.get("scopes") or (it["data"].get("scope") if it["type"] == "kpi.invalidate" else None)
                    if scopes:                                # coalesce refresh requests: one invalidate per MIN_GAP_S carrying the union of scopes
                        pending = {"scope": sorted(set((pending or {}).get("scope", [])) | set(scopes))}
                    if it["type"] == "kpi.invalidate":
                        continue
                    yield _sse(it["type"], it["data"], cur.sse_id)
                    last_beat = now
                if pending is not None and now - last_sent.get("kpi.invalidate", 0) >= MIN_GAP_S["kpi.invalidate"]:
                    yield _sse("kpi.invalidate", pending, cur.sse_id)
                    last_sent["kpi.invalidate"] = now
                    pending, last_beat = None, now
                if now - last_tel >= 2.0:
                    last_tel = now
                    fresh, live_on, _ = await run_in_threadpool(_start_cursors, uid, None)
                    cur.ob = min(cur.ob, fresh.ob)            # the database was restored from a snapshot: start following the new history
                    cur.ev = min(cur.ev, fresh.ev)
                    if live_on and "production.order:read" in perms:
                        orders = await run_in_threadpool(_running_orders)
                        if orders:
                            yield _sse("telemetry", {"simulated": True, "batches": telemetry(orders, time.time())})
                            last_beat = now
                if now - last_beat >= HEARTBEAT_S:
                    yield b": heartbeat\n\n"
                    last_beat = now
                await asyncio.sleep(POLL_S)
        finally:
            _open[uid] = max(0, _open.get(uid, 1) - 1)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.get("/activity")
def activity_feed(limit: int = 40, before: int | None = None, module: str | None = None, db: Session = Depends(get_db), cu: CurrentUser = Depends(current_user)):
    """Recent committed business activity the caller is allowed to see, newest first. Timestamps are ISO with offset; showing them in Asia/Kolkata is the client's job."""
    perms = set(cu.permissions)
    kinds = [t for t, k in activity.CATALOGUE.items() if k.perm in perms and (module is None or k.module == module)]
    if not kinds:
        return {"items": [], "next": None}
    limit = max(1, min(limit, 100))
    rows = [dict(r) for r in db.execute(text("""SELECT id, event_id, event_type, aggregate_type, payload, created_at FROM outbox_events
                                                  WHERE event_type = ANY(:k) AND (CAST(:b AS bigint) IS NULL OR id < :b) ORDER BY id DESC LIMIT :n"""),
                                        {"k": kinds, "b": before, "n": limit + 1}).mappings()]
    more = len(rows) > limit
    rows = rows[:limit]
    names = activity.names_for(db, rows)
    items = [i for i in (activity.render(r, names) for r in rows) if i]
    return {"items": items, "next": rows[-1]["id"] if more and rows else None}
