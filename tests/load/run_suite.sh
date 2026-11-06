#!/usr/bin/env bash
# Sandbox-REDUCED suite (durations shortened; the plan's full durations need a staging environment).
#   tests/load/run_suite.sh <tag> [workers]
# Every run: fresh API+publisher+worker processes, metrics scraping, async drain wait, integrity check, register entry.
set -uo pipefail
TAG=${1:?tag}; W=${2:-3}
cd "$(dirname "$0")/../.."
PY=backend/.venv/bin/python
H="$PY tests/load/harness.py --workers $W --scrape-interval 15 --integrity"
export MINTREE_DATABASE_URL=${MINTREE_DATABASE_URL:-postgresql+psycopg://mintree:mintree@localhost:5433/mintree_perf}

echo "== B progressive (reduced: 20s ramps, 60s holds)"; $H --scenario B_progressive.js --label "B-progressive-$TAG" --env STAGE_S=60s --env RAMP_S=20s --env POOL_USERS=200 | tail -3
echo "== E hot stock (200 VUs, 60s)"
HOT=$(cd backend && $OLDPWD/$PY -m scripts.perf_dataset --hot-qty 1000 2>/dev/null | tail -1)
echo "$HOT"
ENVS=$(python3 - "$HOT" <<'PYEOF'
import json,sys; d=json.loads(sys.argv[1]); print(" ".join(f"--env {k}={v}" for k,v in d.items() if k!="HOT_QTY"))
PYEOF
)
$H --scenario E_hot_stock.js --label "E-hot-stock-$TAG" --env VUS=200 --env DURATION=60s --env POOL_USERS=200 $ENVS | tail -3
echo "== C target (reduced: 1000 clients, 2m ramp + 3m hold)"; $H --scenario C_target.js --label "C-target-reduced-1000vu-$TAG" --env RAMP_S=2m --env DURATION=3m --env POOL_USERS=200 | tail -3
echo "== D spike (reduced: 60s@100 -> 1000 in 30s, hold 2m)"; $H --scenario D_spike.js --label "D-spike-reduced-$TAG" --env BASE_S=60s --env HOLD=2m --env POOL_USERS=200 | tail -3
echo "== F stress (reduced: 1000/1250/1500, 90s holds)"; $H --scenario F_stress.js --label "F-stress-reduced-$TAG" --env STAGE_S=90s --env LEVELS=1000,1250,1500 --env POOL_USERS=200 | tail -3
echo "== H worker recovery (10,000 jobs, 3 worker kills + 1 publisher kill)"; $PY tests/load/worker_recovery.py --label "H-worker-recovery-$TAG" | tail -30
echo "SUITE DONE"
