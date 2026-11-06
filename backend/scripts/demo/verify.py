from __future__ import annotations

from app.core.db import SessionLocal
from scripts.check_integrity import run_checks


def verify_all(ctx) -> int:
    with SessionLocal() as s:
        report = run_checks(s.connection())
    bad = [c for c in report["checks"] if not c["ok"]]
    print(f"Integrity checks: {len(report['checks']) - len(bad)}/{len(report['checks'])} pass")
    for b in bad:
        print("  FAIL", b["name"], b.get("violations"))
    return 1 if bad else 0
