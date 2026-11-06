#!/usr/bin/env bash
# Hermetic browser E2E: fresh database, its own API (:8100) and production web build (:3100).
# Usage: e2e/run.sh   (needs Postgres reachable via E2E_PG_URL and Playwright available to node)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(cd .. && pwd)"
PG_BASE="${E2E_PG_BASE:-postgresql://mintree:mintree@localhost:5433}"
DB_URL="postgresql+psycopg://mintree:mintree@localhost:5433/mintree_e2e"
WORK="$(mktemp -d)"; PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$WORK"; }
trap cleanup EXIT

psql "$PG_BASE/postgres" -qc "DROP DATABASE IF EXISTS mintree_e2e" -c "CREATE DATABASE mintree_e2e"
export MINTREE_DATABASE_URL="$DB_URL" MINTREE_STORAGE_ROOT="$WORK/storage" MINTREE_BOOTSTRAP_ADMIN_EMAIL=admin@example.com MINTREE_BOOTSTRAP_ADMIN_PASSWORD='Mintree#Dev2026'
( cd "$ROOT/backend" && .venv/bin/alembic upgrade head >/dev/null && .venv/bin/python -m app.seed >/dev/null )
( cd "$ROOT/backend" && exec .venv/bin/uvicorn app.main:app --port 8100 --log-level warning ) & PIDS+=($!)

BACKEND_URL=http://127.0.0.1:8100 NEXT_DIST_DIR=.next-e2e npm run build >/dev/null 2>&1
NEXT_DIST_DIR=.next-e2e npx next start -p 3100 >"$WORK/web.log" 2>&1 & PIDS+=($!)
for i in $(seq 1 40); do curl -sf localhost:3100/api/health >/dev/null && break; sleep 1; done

REGISTER_FILE="$ROOT/docs/test-runs.jsonl" BASE_URL=http://localhost:3100 ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD='Mintree#Dev2026' node e2e/phase0.mjs
REGISTER_FILE="$ROOT/docs/test-runs.jsonl" BASE_URL=http://localhost:3100 ADMIN_EMAIL=admin@example.com ADMIN_NEW_PASSWORD='Mintree-Admin#2026' node e2e/modules.mjs
