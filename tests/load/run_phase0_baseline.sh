#!/usr/bin/env bash
# Usage: [CODE_DIR=<backend dir>] run_phase0_baseline.sh <label> <uvicorn-workers> <vus> <duration>
# Starts the API in its own process group (so uvicorn workers are always cleaned up), runs the k6 scenario,
# and fails loudly if something other than our process already owns the port (that invalidated earlier runs).
set -euo pipefail
LABEL=$1; WORKERS=$2; VUS=$3; DUR=$4
HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$(cd "$HERE/../../backend" && pwd)"
CODE_DIR=${CODE_DIR:-$BACKEND}
PORT=${PORT:-8000}
export MINTREE_DATABASE_URL=${MINTREE_DATABASE_URL:-postgresql+psycopg://mintree:mintree@localhost:5433/mintree_perf}
if curl -sf "localhost:$PORT/api/health" >/dev/null 2>&1; then echo "port $PORT already in use; refusing to run" >&2; exit 2; fi
cd "$CODE_DIR"
setsid "$BACKEND/.venv/bin/python" -m uvicorn app.main:app --port "$PORT" --workers "$WORKERS" --log-level warning >/tmp/perf-api.log 2>&1 &
API=$!
trap 'kill -- -$API 2>/dev/null || true; sleep 1' EXIT
for i in $(seq 1 40); do curl -sf "localhost:$PORT/api/health" >/dev/null && break; sleep 1; done
echo "API up: code=$CODE_DIR workers=$WORKERS pid=$API"
cd "$HERE"
k6 run -q -e BASE_URL="http://127.0.0.1:$PORT" -e VUS="$VUS" -e DURATION="$DUR" -e POOL_USERS="${POOL_USERS:-200}" \
   --summary-export="results/${LABEL}.json" scenarios/baseline_phase0.js 2>&1 | tail -45
