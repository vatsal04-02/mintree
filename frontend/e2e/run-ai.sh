#!/usr/bin/env bash
# Browser E2E for the AI layer against a DEMO-SEEDED database with the scripted (fake) provider switched on: reset + init the demo, start the API (:8102) and a web build (:3102).
# Usage: e2e/run-ai.sh   (needs Postgres via E2E_PG_BASE and Playwright available to node; the database name must contain "demo")
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(cd .. && pwd)"
PG_BASE="${E2E_PG_BASE:-postgresql://mintree:mintree@localhost:5433}"
DB=mintree_demo_e2e_ai
WORK="$(mktemp -d)"; PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; fuser -k 3102/tcp 8102/tcp >/dev/null 2>&1 || true; rm -rf "$WORK"; }   # `kill` only stops the npx wrapper: free the ports so a stale web server can never serve old chunks to the next run
trap cleanup EXIT

fuser -k 3102/tcp 8102/tcp >/dev/null 2>&1 || true
psql "$PG_BASE/postgres" -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
export MINTREE_ENVIRONMENT=demo MINTREE_DATABASE_URL="${PG_BASE/postgresql:/postgresql+psycopg:}/$DB" MINTREE_STORAGE_ROOT="$WORK/storage"
PY="${PY:-$ROOT/backend/.venv/bin/python}"
( cd "$ROOT/backend" && "$PY" -m scripts.demo_data reset --confirm-reset-demo-database "$DB" >/dev/null && "$PY" -m scripts.demo_data init | tail -3 )
( cd "$ROOT/backend" && MINTREE_AI_ENABLED=true MINTREE_AI_PROVIDER=fake exec "$PY" -m uvicorn app.main:app --port 8102 --log-level warning ) & PIDS+=($!)

BACKEND_URL=http://127.0.0.1:8102 NEXT_DIST_DIR=.next-e2e-ai npm run build >/dev/null 2>&1
NEXT_DIST_DIR=.next-e2e-ai npx next start -p 3102 >"$WORK/web.log" 2>&1 & PIDS+=($!)
for i in $(seq 1 40); do curl -sf localhost:3102/api/health >/dev/null && break; sleep 1; done

REGISTER_FILE="$ROOT/docs/test-runs.jsonl" BASE_URL=http://localhost:3102 DEMO_PASSWORD='Mintree-Demo#2026' node e2e/ai.mjs
