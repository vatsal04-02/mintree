"""Live Mode may only run when ALL hold: DEMO_LIVE=true, MINTREE_ENVIRONMENT is exactly 'demo', and the database name contains the word 'demo'.
Reuses the demo seeder's guard (scripts.demo.guard) so there is one definition of "a demo database"."""
from __future__ import annotations

from app.core.config import get_settings


def refusal() -> str | None:
    """None when Live Mode is allowed to run; otherwise the human reason it is not."""
    s = get_settings()
    if not s.demo_live:
        return "Demo Live Mode is switched off (MINTREE_DEMO_LIVE is not true)."
    from scripts.demo.guard import DemoGuardError, check_demo_target
    try:
        check_demo_target(s.environment, s.database_url)
    except DemoGuardError as e:
        return str(e)
    return None


def assert_safe() -> None:
    why = refusal()
    if why:
        raise SystemExit(f"Demo Live Mode refused to start: {why}")
