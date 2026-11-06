#!/usr/bin/env bash
# Controlled A/B of two code versions under IDENTICAL load, interleaved (before, after, before, after) to cancel drift.
#   tests/load/run_ab.sh <before-code-dir> <tag>      (before-code-dir = backend dir of a git worktree of the older commit)
set -uo pipefail
BEFORE=${1:?before backend dir}; TAG=${2:?tag}
cd "$(dirname "$0")/../.."
PY=backend/.venv/bin/python
export MINTREE_DATABASE_URL=${MINTREE_DATABASE_URL:-postgresql+psycopg://mintree:mintree@localhost:5433/mintree_perf}
run() { # label codedir clients ramp hold
  $PY tests/load/harness.py --scenario C_target.js --label "$1" --workers 3 --code-dir "$2" --scrape-interval 15 --integrity \
      --env TARGET=$3 --env RAMP_S=$4 --env DURATION=$5 --env POOL_USERS=200 | grep -E "label|req_per_s|read_p95|read_p99|write_p95|posting_p95|failure|integrity|commit"
}
for pair in "250 30s 2m" "1000 1m 3m"; do set -- $pair
  run "AB-$TAG-before-$1c" "$BEFORE" $1 $2 $3
  run "AB-$TAG-after-$1c"  "$PWD/backend" $1 $2 $3
done
echo "AB DONE"
