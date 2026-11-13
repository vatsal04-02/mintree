"""Pre-flight for the demo (docs/DEMO_RUNBOOK.md): a red/green checklist in about a minute.

    cd backend && python -m scripts.demo_check [--api http://127.0.0.1:8101] [--no-reset] [--quick]

Checks, in order: environment and database name -> Postgres / Redis / RabbitMQ -> API readiness (/api/ready) -> migrations current -> seed present and at volume ->
warm-up -> the whole run-of-show (tests/demo) -> integrity invariants -> integrations reachable or cleanly stubbed -> golden snapshot (and, unless --no-reset, a restore
so the demo starts from the golden state). Exit code 1 if anything REQUIRED is red; amber items are advice.
Passwords come from the environment (DEMO_PASSWORD, default is the documented demo credential); nothing is printed that is secret."""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
GREEN, RED, AMBER, DIM, END = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
results: list[tuple[str, str, str, float, bool]] = []                       # name, status(ok|warn|fail), detail, seconds, required


def check(name: str, required: bool = True):
    def deco(fn):
        def run(*a, **kw):
            t0 = time.perf_counter()
            try:
                out = fn(*a, **kw)
                status, detail = ("ok", out or "") if not isinstance(out, tuple) else out
            except AssertionError as e:
                status, detail = ("fail" if required else "warn"), str(e)
            except Exception as e:  # noqa: BLE001
                status, detail = ("fail" if required else "warn"), f"{type(e).__name__}: {e}"
            dt = time.perf_counter() - t0
            results.append((name, status, detail, dt, required))
            mark = {"ok": f"{GREEN}PASS{END}", "warn": f"{AMBER}WARN{END}", "fail": f"{RED}FAIL{END}"}[status]
            print(f"[{mark}] {name:<44} {DIM}{dt:5.1f}s{END}  {detail[:160]}", flush=True)
            return status == "ok"
        return run
    return deco


def tcp(url: str, default_port: int) -> None:
    u = urlparse(url if "//" in url else f"//{url}")
    with socket.create_connection((u.hostname or "127.0.0.1", u.port or default_port), timeout=2):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("DEMO_API", "http://127.0.0.1:8101"))
    ap.add_argument("--no-reset", action="store_true", help="do not restore the golden snapshot at the end")
    ap.add_argument("--quick", action="store_true", help="skip the run-of-show test (readiness, seed, invariants only)")
    a = ap.parse_args()
    sys.path.insert(0, str(BACKEND))
    from app.core.config import get_settings
    s = get_settings()
    pw = os.environ.get("DEMO_PASSWORD", "Mintree-Demo#2026")
    print(f"\nMintree demo pre-flight  ·  api {a.api}\n")

    @check("environment is 'demo' and the database is a demo database")
    def env():
        from scripts.demo.guard import check_demo_target
        name = check_demo_target(s.environment, s.database_url)
        assert not s.ai_enabled or s.ai_provider in ("fake", "anthropic", "ollama")
        return f"database {name}"

    @check("PostgreSQL reachable")
    def pg():
        with create_engine(s.database_url).connect() as c:
            return c.execute(text("select version()")).scalar().split(",")[0]

    @check("Redis reachable (cache, rate limits)", required=False)
    def rd():
        tcp(s.redis_url.replace("redis://", ""), 6379)
        return s.redis_url.rsplit("/", 1)[0]

    @check("RabbitMQ reachable (background work)", required=False)
    def mq():
        tcp(s.broker_url.split("@")[-1].split("/")[0], 5672)

    @check("API ready (/api/ready: database, migrations, disk)")
    def ready():
        r = httpx.get(f"{a.api}/api/ready", timeout=10)
        body = r.json()
        assert r.status_code == 200, f"not ready: {[k for k, v in body['checks'].items() if not v['ok']]}"
        degraded = [f"{k}: {v['detail']}" for k, v in body["checks"].items() if not v["ok"]]
        return ("warn", "degraded: " + "; ".join(degraded)) if degraded else f"{body['status']} ({', '.join(body['checks'])})"

    @check("migrations at head")
    def mig():
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        heads = set(ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_heads())
        with create_engine(s.database_url).connect() as c:
            cur = {r[0] for r in c.execute(text("select version_num from alembic_version"))}
        assert cur == heads, f"database at {sorted(cur)}, code at {sorted(heads)}"
        return f"head {sorted(heads)[0]}"

    @check("seed present and at volume")
    def seed():
        from scripts.demo.bulk import counts
        n = counts()
        base = n["sales_orders"] > 100 and n["customers"] > 20
        assert base, f"demo seed missing: {n}"
        floor = {"customers": 2500, "suppliers": 60, "sales_orders": 25000, "invoices": 6000, "production_batches": 4000, "purchase_lines": 10000, "ledger_entries": 30000}
        short = {k: f"{n[k]}/{v}" for k, v in floor.items() if n[k] < v}
        det = f"{n['customers']} customers, {n['sales_orders']} orders, {n['invoices']} invoices, {n['production_batches']} batches, {n['stock_movements']} stock movements"
        return ("warn", f"{det}; below target: {short}") if short else det

    @check("warm-up (caches, pool, first-paint queries)")
    def warm():
        p = subprocess.run([sys.executable, "-m", "scripts.warm", "--base", a.api, "--email", "demo.admin@example.com", "--password", pw, "--passes", "2"], cwd=BACKEND, capture_output=True, text=True)
        last = [x for x in p.stdout.splitlines() if x.strip()][-1:] or [p.stderr[-200:]]
        assert p.returncode == 0 or "pick-list" in p.stdout, p.stdout[-400:]
        return last[0]

    @check("demo preconditions guaranteed (deals, approval, held batch)")
    def prep():
        p = subprocess.run([sys.executable, "-m", "scripts.demo_prep"], cwd=BACKEND, capture_output=True, text=True)
        assert p.returncode == 0, (p.stderr or p.stdout)[-300:]
        return [x for x in p.stdout.splitlines() if x.startswith("{")][-1]

    @check("demo run-of-show (a to i), DB state asserted")
    def story():
        if a.quick:
            return ("warn", "skipped (--quick)")
        env_ = dict(os.environ, DEMO_API=a.api, DEMO_PASSWORD=pw)
        p = subprocess.run([sys.executable, "-m", "pytest", "tests/demo", "-q", "-p", "no:cacheprovider", "--no-header"], cwd=ROOT, env=env_, capture_output=True, text=True)
        tail = [x for x in p.stdout.splitlines() if re.search(r"passed|failed|step timings", x)]
        assert p.returncode == 0, "\n".join(tail[-3:]) or p.stdout[-600:]
        return tail[-1] if tail else "green"

    @check("integrity invariants (stock, ledger, books, outbox)")
    def inv():
        p = subprocess.run([sys.executable, "-m", "scripts.check_integrity"], cwd=BACKEND, capture_output=True, text=True)
        fails = [x for x in p.stdout.splitlines() if x.startswith("FAIL")]
        assert p.returncode == 0, "; ".join(fails[:3])
        return f"{p.stdout.count('PASS')} checks pass"

    @check("integrations reachable or cleanly stubbed (Simulated)", required=False)
    def integ():
        notes = []
        with create_engine(s.database_url).connect() as c:
            modes = dict(c.execute(text("select code, integration_mode from courier_partners")).all())
            meta = c.execute(text("select count(*) from meta_connections where mode = 'LIVE' and status = 'CONNECTED'")).scalar()
        notes.append("courier: " + ("Simulated courier (in-process)" if modes.get("FAKE") == "DEMO" else "NO demo courier!"))
        notes.append("Meta: " + ("LIVE connection present" if meta else "sandbox, Simulated"))
        notes.append("AI: " + (f"{s.ai_provider}" + (" (needs internet; breaker + offline fake provider as fallback)" if s.ai_enabled and s.ai_provider != "fake" else "") if s.ai_enabled else "off"))
        assert modes.get("FAKE") == "DEMO", "the demo courier is missing"
        return "; ".join(notes)

    @check("golden snapshot exists", required=False)
    def snap():
        from app.demo_live import ops
        return "present" if ops.has_snapshot() else ("warn", "none yet: take one with `make demo-snapshot` after this check is green")

    eng = create_engine(s.database_url)
    with eng.begin() as c:                                                    # Live Mode would perturb counts the story asserts on: pause it for the check, restore afterwards
        was = c.execute(text("select paused from demo_live_state where id = 1")).scalar()
        c.execute(text("update demo_live_state set paused = true where id = 1"))
    time.sleep(4)                                                              # let an action that was already running finish
    try:
        for fn in (env, pg, rd, mq, ready, mig, seed, prep, warm, story, inv, integ, snap):
            fn()
    finally:
        with eng.begin() as c:
            c.execute(text("update demo_live_state set paused = :p where id = 1"), {"p": bool(was)})
    if not a.no_reset and all(r[1] != "fail" for r in results):
        @check("restore golden snapshot (< 60 s) and re-verify", required=False)
        def restore():
            from app.demo_live import ops
            if not ops.has_snapshot():
                return ("warn", "skipped: no snapshot")
            out = ops.reset()
            assert out["seconds"] < 60, f"restore took {out['seconds']} s"
            r = httpx.get(f"{a.api}/api/ready", timeout=10)
            assert r.status_code == 200
            return f"restored in {out['seconds']} s, sessions kept {out['sessions_kept']}"
        restore()
    red = [r for r in results if r[1] == "fail"]
    amber = [r for r in results if r[1] == "warn"]
    total = sum(r[3] for r in results)
    print(f"\n{'GREEN' if not red else 'RED'}: {len(results) - len(red) - len(amber)} passed, {len(amber)} advice, {len(red)} failed  ({total:.0f} s)")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
