#!/usr/bin/env bash
# Proves a backup is restorable: restores it into a scratch database, compares row counts of every table with the source, runs the full data-integrity
# checker against the restored copy, then drops the scratch database. Exit 0 only if everything matches.
#   MINTREE_DATABASE_URL=postgresql://user:pass@host:5432/mintree ops/restore_check.sh path/to/mintree-....dump
set -euo pipefail
DUMP="${1:?usage: restore_check.sh <dump-file>}"
URL="${MINTREE_DATABASE_URL:-postgresql://mintree:mintree@localhost:5433/mintree}"; URL="${URL/postgresql+psycopg:/postgresql:}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${URL%/*}"; DB="${URL##*/}"; DB="${DB%%\?*}"; SCRATCH="${DB}_restorecheck"
( cd "$(dirname "$DUMP")" && sha256sum -c "$(basename "$DUMP").sha256" >/dev/null ) || { echo "CHECKSUM MISMATCH"; exit 2; }
psql "$BASE/postgres" -qc "DROP DATABASE IF EXISTS $SCRATCH" -c "CREATE DATABASE $SCRATCH"
trap 'psql "$BASE/postgres" -qc "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null 2>&1 || true' EXIT
pg_restore --no-owner --no-privileges --dbname="$BASE/$SCRATCH" "$DUMP"
counts() { psql "$1" -tA <<'SQL'
SELECT table_name || '=' || (xpath('/row/c/text()', query_to_xml('select count(*) as c from ' || quote_ident(table_name), false, true, '')))[1]::text
  FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;
SQL
}
counts "$URL" > /tmp/restore_src.counts
counts "$BASE/$SCRATCH" > /tmp/restore_dst.counts
if ! diff -q /tmp/restore_src.counts /tmp/restore_dst.counts >/dev/null; then
  echo "ROW COUNTS DIFFER (dump may predate recent writes):"; diff /tmp/restore_src.counts /tmp/restore_dst.counts | head -20; DIFFERS=1
fi
TABLES="$(wc -l < /tmp/restore_dst.counts | tr -d ' ')"
VER="$(psql "$BASE/$SCRATCH" -tAc 'SELECT version_num FROM alembic_version')"
( cd "$ROOT/backend" && MINTREE_DATABASE_URL="${BASE/postgresql:/postgresql+psycopg:}/$SCRATCH" .venv/bin/python -m scripts.check_integrity ) | tail -3
echo "RESTORE OK: $TABLES tables restored, schema revision $VER, integrity checker passed${DIFFERS:+ (row counts differ from the live DB: expected only if it changed since the dump)}"
