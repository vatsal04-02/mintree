"""Operator commands for the demo database (guarded: environment 'demo' and a database name containing 'demo').

    python -m scripts.demo_ops snapshot      take the golden snapshot (after seeding and a green demo-check)
    python -m scripts.demo_ops reset         restore the golden snapshot (keeps signed-in sessions), re-anchor dates, clear caches
    python -m scripts.demo_ops anchor        shift history so the newest data is today
    python -m scripts.demo_ops backdate      derive server-stamped timestamps from business dates (run once after bulk seeding)
"""
import json
import sys

from app.demo_live import ops

CMDS = {"snapshot": ops.snapshot, "reset": ops.reset, "anchor": ops.anchor_today, "backdate": ops.backdate}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in CMDS:
        sys.exit(__doc__)
    print(json.dumps(CMDS[sys.argv[1]](), indent=2))
