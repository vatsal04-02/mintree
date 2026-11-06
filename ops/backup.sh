#!/usr/bin/env bash
# Logical backup of the Mintree database (custom format, compressed) with a checksum and simple retention.
#   MINTREE_DATABASE_URL=postgresql://user:pass@host:5432/mintree ops/backup.sh [output-dir] [keep]
# Schedule it (cron / systemd timer) at least daily, copy the output off the host, and run ops/restore_check.sh regularly:
# a backup that has never been restored is not a backup. Uploaded documents live in MINTREE_STORAGE_ROOT and need their own copy (see README).
set -euo pipefail
URL="${MINTREE_DATABASE_URL:-postgresql://mintree:mintree@localhost:5433/mintree}"
URL="${URL/postgresql+psycopg:/postgresql:}"
OUT="${1:-./backups}"; KEEP="${2:-14}"
mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$OUT/mintree-$STAMP.dump"
pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="$FILE" "$URL"
( cd "$OUT" && sha256sum "$(basename "$FILE")" > "$(basename "$FILE").sha256" )
ls -1t "$OUT"/mintree-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f "$old" "$old.sha256"; done
echo "$FILE"
