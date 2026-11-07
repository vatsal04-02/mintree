#!/usr/bin/env bash
# Failure drills under load (sandbox-scale): Redis interruption and API replica restart, class A reads at a fixed rate.
#   tests/load/resilience.sh <rate> <api_workers>
# Drill 1: stop Redis for 8 s mid-run, start it again. Expect: no 5xx (caches bypass to the database), latency rises, then recovers.
# Drill 2: SIGTERM the API supervisor mid-run (a restart), bring it back after 4 s. Expect: connection errors only while it is down, then recovery.
set -uo pipefail
RATE=${1:-300}; W=${2:-3}
cd "$(dirname "$0")/../.."
PY=${PY:-/tmp/venv/bin/python}; K6=${K6:-/tmp/k6-v0.54.0-linux-amd64/k6}; PG=${E2E_PG_BASE:-postgresql://mintree:mintree@localhost:5433}
DB=mintree_perf_resilience; OUT=tests/load/results/resilience; mkdir -p $OUT
psql "$PG/postgres" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB TEMPLATE ${SRC_DB:-mintree_demo_full}" >/dev/null
export MINTREE_ENVIRONMENT=demo MINTREE_DATABASE_URL="${PG/postgresql:/postgresql+psycopg:}/$DB" MINTREE_STORAGE_ROOT=/tmp/perf-storage
start_api() { ( cd backend && exec $PY -m uvicorn app.main:app --port 8200 --workers $W --log-level warning --no-access-log ) >$OUT/api-$1.log 2>&1 & echo $!; }
wait_up() { for i in $(seq 1 60); do curl -sf localhost:8200/api/health >/dev/null && return 0; sleep 0.5; done; return 1; }
trap 'pkill -f "uvicorn app.main:app --port 8200" 2>/dev/null; psql "$PG/postgres" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1' EXIT

API=$(start_api d1); wait_up
( sleep 12; redis-cli shutdown nosave >/dev/null 2>&1; sleep 8; redis-server --port 6379 --daemonize yes --dir /tmp >/dev/null ) &
$K6 run -q -e CLASS=A -e RATE=$RATE -e DURATION=30s -e BASE_URL=http://127.0.0.1:8200 --summary-export $OUT/redis-outage.json tests/load/scenarios/classes.js >$OUT/redis-outage.log 2>&1
wait
$PY - $OUT/redis-outage.json "Drill 1: Redis stopped for 8 s of a 30 s run" <<'PYEOF' | tee $OUT/results.txt
import json,sys
m=json.load(open(sys.argv[1]))["metrics"]; d=m["http_req_duration"]; n=m["http_reqs"]["count"]; f=m.get("unexpected_status",{}).get("count",0); fl=m["http_req_failed"]["value"]
print(f"{sys.argv[2]}: {n} requests, unexpected {f} ({100*f/n:.2f}%), http_req_failed {100*fl:.2f}%, p50 {d['p(50)']:.0f} ms, p95 {d['p(95)']:.0f} ms, p99 {d['p(99)']:.0f} ms")
PYEOF
pkill -f "uvicorn app.main:app --port 8200"; sleep 2

API=$(start_api d2); wait_up
( sleep 12; pkill -TERM -f "uvicorn app.main:app --port 8200"; sleep 4; start_api d2b >/dev/null ) &
$K6 run -q -e CLASS=A -e RATE=$RATE -e DURATION=30s -e BASE_URL=http://127.0.0.1:8200 --summary-export $OUT/api-restart.json tests/load/scenarios/classes.js >$OUT/api-restart.log 2>&1
wait
$PY - $OUT/api-restart.json "Drill 2: API restarted (down ~4 s) in a 30 s run" <<'PYEOF' | tee -a $OUT/results.txt
import json,sys
m=json.load(open(sys.argv[1]))["metrics"]; d=m["http_req_duration"]; n=m["http_reqs"]["count"]; f=m.get("unexpected_status",{}).get("count",0); fl=m["http_req_failed"]["value"]
print(f"{sys.argv[2]}: {n} requests, unexpected {f} ({100*f/n:.2f}%), http_req_failed {100*fl:.2f}%, p50 {d['p(50)']:.0f} ms, p95 {d['p(95)']:.0f} ms, p99 {d['p(99)']:.0f} ms")
PYEOF
