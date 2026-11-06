"""Seeder runtime: ordered, resumable steps that drive the REAL application (HTTP API, in-process) so every business rule, approval, ledger posting,
audit row and outbox event happens exactly as in production. Only bootstrap master data is written directly."""
from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.db import SessionLocal
from app.core.demo_models import DemoSeedStep
from app.main import app

PASSWORD = "Mintree-Demo#2026"
MARKER = "__marker__"
STEPS: list[tuple[str, Callable[["Ctx"], dict | None]]] = []

# demo person key -> (role code, display name)
PEOPLE = {
    "admin": ("system_admin", "Demo System Admin"), "biz": ("business_admin", "Demo Business Admin"), "buyer": ("purchase_manager", "Demo Purchase Manager"),
    "stores": ("stores_manager", "Demo Stores Manager"), "qa": ("quality_manager", "Demo Quality Manager"), "qa2": ("quality_manager", "Demo Quality Head"),
    "qi": ("qc_inspector", "Demo QC Inspector"), "pm": ("production_manager", "Demo Production Manager"), "pm2": ("production_manager", "Demo Production Head"),
    "op": ("production_operator", "Demo Production Operator"), "rnd": ("rnd_manager", "Demo R&D Manager"), "rnd2": ("rnd_manager", "Demo R&D Head"),
    "disp": ("dispatch_manager", "Demo Dispatch Manager"), "sales": ("sales_operator", "Demo Sales Executive"), "acct": ("accounts_manager", "Demo Accounts Manager"),
    "mkt": ("marketing_manager", "Demo Marketing Manager"), "maint": ("maintenance_manager", "Demo Maintenance Manager"), "mgmt": ("management", "Demo Owner / Management"),
}


def email(key: str) -> str:
    return f"demo.{key}@example.com"


def step(key: str):
    def deco(fn):
        STEPS.append((key, fn))
        return fn
    return deco


def money(v: Decimal | float | int | str) -> str:
    return str(Decimal(str(v)).quantize(Decimal("0.01"), ROUND_HALF_UP))


class Ctx:
    def __init__(self) -> None:
        self.refs: dict[str, dict] = {}
        self._clients: dict[str, TestClient] = {}
        self._offset_days = 0
        self.step_key = ""
        self._n = 0

    # ---- time -------------------------------------------------------------------------------------------------------------------------
    @contextlib.contextmanager
    def days_ago(self, n: int):
        """Run the enclosed code as if it were `n` days ago: every business date the services stamp (invoice, entry, order, planned dates) moves back.
        Server-side `now()` timestamps (created_at, audit) stay real: that is documented, not hidden."""
        real = clock.utc_now
        shifted = lambda: real() - timedelta(days=n)
        clock.utc_now = shifted
        try:
            yield
        finally:
            clock.utc_now = real

    def today(self) -> date:
        return clock.business_today()

    def d(self, offset: int = 0) -> str:
        return str(clock.business_today() + timedelta(days=offset))

    # ---- http -------------------------------------------------------------------------------------------------------------------------
    def u(self, key: str) -> TestClient:
        if key not in self._clients:
            c = TestClient(app)
            r = c.post("/api/auth/login", json={"email": email(key), "password": PASSWORD})
            if r.status_code != 200:
                raise SystemExit(f"demo login failed for {email(key)}: {r.status_code} {r.text[:200]}")
            c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            self._clients[key] = c
        return self._clients[key]

    def k(self, label: str = "") -> dict:
        """Deterministic Idempotency-Key: the same step issues the same keys on a re-run, so a retried posting replays instead of duplicating."""
        self._n += 1
        return {"Idempotency-Key": f"demo-{self.step_key}-{label}{self._n}"[:128]}

    def ok(self, r, code=(200, 201)):
        if r.status_code not in code:
            raise SystemExit(f"FAILED in step '{self.step_key}': {r.request.method} {r.request.url.path} -> {r.status_code} {r.text[:500]}")
        return r.json()

    def expect(self, r, code: int, contains: str | None = None) -> dict:
        """An intended refusal (the demo shows the system saying no): assert it really happened."""
        if r.status_code != code or (contains and contains not in r.text):
            raise SystemExit(f"EXPECTED {code} {contains or ''} in step '{self.step_key}': {r.request.method} {r.request.url.path} -> {r.status_code} {r.text[:300]}")
        return r.json()

    def ref(self, step_key: str, name: str):
        return self.refs[step_key][name]


def load_refs(ctx: Ctx) -> set[str]:
    with SessionLocal() as s:
        rows = s.scalars(select(DemoSeedStep)).all()
    done = set()
    for r in rows:
        done.add(r.step_key)
        if r.step_key != MARKER:
            ctx.refs[r.step_key] = r.detail or {}
    return done


def run_steps(ctx: Ctx, only: set[str] | None = None, say=print) -> list[str]:
    """Run every step not yet recorded as complete, in dependency order. Returns the keys that ran."""
    logging.disable(logging.ERROR)                 # the API logs each request as JSON; the seeder prints its own progress
    done = load_refs(ctx)
    ran = []
    for key, fn in STEPS:
        if only and key not in only:
            continue
        if key in done:
            say(f"  = {key}: already done")
            continue
        ctx.step_key, ctx._n = key, 0
        detail = fn(ctx) or {}
        with SessionLocal() as s:
            s.add(DemoSeedStep(step_key=key, detail=detail))
            s.commit()
        ctx.refs[key] = detail
        ran.append(key)
        say(f"  + {key}")
    return ran
