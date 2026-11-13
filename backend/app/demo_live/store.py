"""Control-plane persistence for Live Mode (state row, event log, triggers). Plain SQL; no business tables."""
from __future__ import annotations

import json
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings


def get_state(db: Session) -> dict:
    r = db.execute(text("SELECT enabled, paused, speed, heartbeat_at, last_action_at, counters, updated_at FROM demo_live_state WHERE id = 1")).mappings().one()
    return dict(r)


def set_state(db: Session, **fields) -> None:
    allowed = {"enabled", "paused", "speed"}
    sets = ", ".join(f"{k} = :{k}" for k in fields if k in allowed)
    if sets:
        db.execute(text(f"UPDATE demo_live_state SET {sets}, updated_at = now() WHERE id = 1"), {k: v for k, v in fields.items() if k in allowed})


def beat(db: Session, counters: dict, acted: bool) -> None:
    db.execute(text("UPDATE demo_live_state SET heartbeat_at = now(), counters = CAST(:c AS json)" + (", last_action_at = now()" if acted else "") + " WHERE id = 1"),
               {"c": json.dumps(counters)})


def emit(db: Session, type_: str, payload: dict, permission: str | None = None) -> None:
    """Append a typed event (tagged source='demo-live'); keep only the newest N rows so the log cannot balloon."""
    db.execute(text("INSERT INTO demo_live_events (type, permission, payload) VALUES (:t, :p, CAST(:j AS json))"), {"t": type_, "p": permission, "j": json.dumps(payload, default=str)})
    cap = get_settings().demo_live_max_events
    db.execute(text("DELETE FROM demo_live_events WHERE id < (SELECT COALESCE(MAX(id), 0) - :cap FROM demo_live_events)"), {"cap": cap})


def pop_trigger(db: Session) -> tuple[int, str] | None:
    r = db.execute(text("SELECT id, kind FROM demo_live_triggers WHERE done_at IS NULL ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED")).first()
    return (r[0], r[1]) if r else None


def finish_trigger(db: Session, tid: int, result: str) -> None:
    db.execute(text("UPDATE demo_live_triggers SET done_at = now(), result = :r WHERE id = :i"), {"r": result[:200], "i": tid})


def add_trigger(db: Session, kind: str, user_id: int | None) -> int:
    return db.execute(text("INSERT INTO demo_live_triggers (kind, requested_by) VALUES (:k, :u) RETURNING id"), {"k": kind, "u": user_id}).scalar_one()


