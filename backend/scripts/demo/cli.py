from __future__ import annotations

import logging

logging.disable(logging.INFO)     # BEFORE the alembic import below: alembic logs plugin setup at import time, and the app logs every request as JSON. This tool prints its own progress.

import argparse  # noqa: E402
import sys  # noqa: E402

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import engine
from app.core.demo_models import DemoSeedStep
from app.seed import seed
from scripts.demo import guard
from scripts.demo.core import MARKER, PASSWORD, PEOPLE, STEPS, Ctx, email, load_refs, run_steps


def _db_state() -> tuple[bool, bool]:
    """(has demo marker, database is empty)"""
    with engine.connect() as c:
        tables = {r[0] for r in c.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))}
        has_marker = "demo_seed_steps" in tables and bool(c.execute(text("SELECT 1 FROM demo_seed_steps WHERE step_key = :k"), {"k": MARKER}).first())
    return has_marker, tables <= {"alembic_version"}


def _migrate_and_seed() -> None:
    from pathlib import Path
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    import logging
    logging.disable(logging.INFO)             # re-applied here: importing the app configures logging after run() set this
    command.upgrade(cfg, "head")
    with Session(engine) as s:
        seed(s)                        # idempotent: roles, permissions, units, warehouses, chart of accounts, periods
        s.commit()


def cmd_reset(args) -> int:
    s = get_settings()
    has_marker, empty = _db_state() if _can_connect() else (False, True)
    name = guard.check_reset(s.environment, s.database_url, args.confirm_reset_demo_database, has_marker=has_marker, is_empty=empty, allow_remote=args.allow_remote_host)
    print(f"Resetting demo database '{name}' ...")
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    _migrate_and_seed()
    print("Done: schema rebuilt from migrations and base data seeded. Now run: python -m scripts.demo_data init")
    return 0


def _can_connect() -> bool:
    try:
        with engine.connect():
            return True
    except Exception:
        return False


def cmd_init(args) -> int:
    s = get_settings()
    name = guard.check_demo_target(s.environment, s.database_url)
    print(f"Initialising demo data in '{name}' (idempotent; completed steps are skipped) ...")
    _migrate_and_seed()
    import scripts.demo.steps  # noqa: F401  (registers the steps)
    with Session(engine) as db:
        if not db.get(DemoSeedStep, MARKER):
            db.add(DemoSeedStep(step_key=MARKER, detail={"purpose": "demonstration database", "password_policy": "documented demo password"}))
            db.commit()
    ctx = Ctx()
    ran = run_steps(ctx)
    print(f"\n{len(ran)} step(s) ran, {len(STEPS) - len(ran)} already complete.")
    from scripts.demo.verify import verify_all
    rc = verify_all(ctx)
    if rc == 0:
        print(f"\nSign in as demo.admin@example.com / {PASSWORD}  (also: {', '.join('demo.' + k for k in PEOPLE if k != 'admin')} @example.com)")
    return rc


def cmd_verify(args) -> int:
    s = get_settings()
    guard.check_demo_target(s.environment, s.database_url)
    import scripts.demo.steps  # noqa: F401
    ctx = Ctx()
    load_refs(ctx)
    from scripts.demo.verify import verify_all
    return verify_all(ctx)


def cmd_status(args) -> int:
    s = get_settings()
    name = guard.check_demo_target(s.environment, s.database_url)
    import scripts.demo.steps  # noqa: F401
    ctx = Ctx()
    done = load_refs(ctx)
    print(f"Demo database '{name}': marker {'present' if MARKER in done else 'ABSENT'}")
    for key, _ in STEPS:
        print(f"  [{'x' if key in done else ' '}] {key}")
    return 0


def run(argv: list[str] | None = None) -> int:
    import logging
    logging.disable(logging.INFO)           # alembic and the API log as JSON; this tool prints its own progress
    p = argparse.ArgumentParser(prog="python -m scripts.demo_data", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="seed the demo stories through the real workflows (idempotent)").set_defaults(fn=cmd_init)
    sub.add_parser("verify", help="integrity + demo checks").set_defaults(fn=cmd_verify)
    sub.add_parser("status", help="which steps are complete").set_defaults(fn=cmd_status)
    r = sub.add_parser("reset", help="wipe a DEMO database and rebuild it (guarded)")
    r.add_argument("--confirm-reset-demo-database", metavar="DBNAME", help="type the database name to confirm")
    r.add_argument("--allow-remote-host", action="store_true")
    r.set_defaults(fn=cmd_reset)
    args = p.parse_args(argv)
    return args.fn(args)
