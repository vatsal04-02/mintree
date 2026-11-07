#!/usr/bin/env bash
# Per-traffic-class throughput run against ONE API configuration on a DEMO-seeded database copy.
#   tests/load/run_classes.sh <label> <api_workers> "<classes>" "<rates>" [duration]
# Starts the API (uvicorn, <api_workers> processes) on a throwaway copy of $SRC_DB, runs k6 once per (class, rate), saves k6 summaries and a one-line result each, stops the API.
set -uo pipefail
LABEL=${1:?label}; W=${2:?workers}; CLASSES=${3:-"A B C D E F G"}; RATES=${4:-"250 500 1000"}; DUR=${5:-20s}
cd "$(dirname "$0")/../.."
ROOT=$PWD; PY=${PY:-/tmp/venv/bin/python}; K6=${K6:-/tmp/k6-v0.54.0-linux-amd64/k6}
PG=${E2E_PG_BASE:-postgresql://mintree:mintree@localhost:5433}; SRC=${SRC_DB:-mintree_demo_full}; DB="mintree_perf_${LABEL//[^a-zA-Z0-9]/_}"; DB=${DB,,}
OUT=tests/load/results/classes-$LABEL; mkdir -p $OUT
psql "$PG/postgres" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB TEMPLATE $SRC" >/dev/null
export MINTREE_ENVIRONMENT=demo MINTREE_DATABASE_URL="${PG/postgresql:/postgresql+psycopg:}/$DB" MINTREE_STORAGE_ROOT=/tmp/perf-storage MINTREE_LOG_LEVEL=WARNING ${API_ENV:-}
( cd backend && exec $PY -m uvicorn app.main:app --port 8200 --workers $W --log-level warning --no-access-log ) >$OUT/api.log 2>&1 & API=$!
trap 'kill $API 2>/dev/null; wait $API 2>/dev/null; psql "$PG/postgres" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1' EXIT
for i in $(seq 1 40); do curl -sf localhost:8200/api/health >/dev/null && break; sleep 0.5; done
{ echo "label=$LABEL workers=$W commit=$(git rev-parse --short=10 HEAD) cpu=$(nproc) $(date -u +%FT%TZ)"; echo "api_env=${API_ENV:-}"; } > $OUT/meta.txt
for C in $CLASSES; do for R in $RATES; do
  $K6 run -q -e CLASS=$C -e RATE=$R -e DURATION=$DUR -e BASE_URL=http://127.0.0.1:8200 --summary-export $OUT/$C-$R.json tests/load/scenarios/classes.js >$OUT/$C-$R.log 2>&1
  $PY - "$OUT/$C-$R.json" $C $R <<'PYEOF'
import json,sys
d=json.load(open(sys.argv[1]))["metrics"]; dur=d["http_req_duration"]; n=d["http_reqs"]
fail=d.get("unexpected_status",{}).get("count",0); total=n["count"]
print(f"class {sys.argv[2]} target {int(sys.argv[3]):>6}/s  achieved {n['rate']:>8.1f}/s  p50 {dur.get('p(50)', dur.get('med', 0)):>7.1f}  p95 {dur['p(95)']:>8.1f}  p99 {dur['p(99)']:>8.1f} ms  unexpected {fail}/{total} ({100*fail/max(total,1):.2f}%)  dropped_iter {d.get('dropped_iterations',{}).get('count',0)}")
PYEOF
done; done | tee $OUT/results.txt
