#!/usr/bin/env bash
# Browser E2E against a DEMO-SEEDED database: reset + init the demo (guards apply), start the API (:8101) and a production web build (:3101), walk the seeded journeys.
# Usage: e2e/run-demo.sh   (needs Postgres via E2E_PG_BASE and Playwright available to node; the database name must contain "demo")
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(cd .. && pwd)"
PG_BASE="${E2E_PG_BASE:-postgresql://mintree:mintree@localhost:5433}"
DB=mintree_demo_e2e
WORK="$(mktemp -d)"; PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$WORK"; }
trap cleanup EXIT

psql "$PG_BASE/postgres" -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
export MINTREE_ENVIRONMENT=demo MINTREE_DATABASE_URL="${PG_BASE/postgresql:/postgresql+psycopg:}/$DB" MINTREE_STORAGE_ROOT="$WORK/storage"
PY="${PY:-$ROOT/backend/.venv/bin/python}"
( cd "$ROOT/backend" && "$PY" -m scripts.demo_data reset --confirm-reset-demo-database "$DB" >/dev/null && "$PY" -m scripts.demo_data init | tail -3 )
( cd "$ROOT/backend" && exec "$PY" -m uvicorn app.main:app --port 8101 --log-level warning ) & PIDS+=($!)

BACKEND_URL=http://127.0.0.1:8101 NEXT_DIST_DIR=.next-e2e-demo npm run build >/dev/null 2>&1
NEXT_DIST_DIR=.next-e2e-demo npx next start -p 3101 >"$WORK/web.log" 2>&1 & PIDS+=($!)
for i in $(seq 1 40); do curl -sf localhost:3101/api/health >/dev/null && break; sleep 1; done

REGISTER_FILE="$ROOT/docs/test-runs.jsonl" BASE_URL=http://localhost:3101 DEMO_PASSWORD='Mintree-Demo#2026' node e2e/demo.mjs
