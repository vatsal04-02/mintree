# Load Test Plan

**Goal (target, not an achieved fact):** demonstrate 1,000 concurrent business-operation clients on a documented
staging environment with integrity preserved. This is *not* 1,000 TPS and *not* 1,000 DB connections.
Nothing is claimed until scenario **C (target)** passes on staging **and** `check_integrity` passes
(see `PERFORMANCE_BASELINE.md` for what has/hasn't been demonstrated).

## Tooling
k6 v0.54 (`tests/load/scenarios/*.js`, shared `lib/`), `tests/load/harness.py` (starts the API + publisher + Celery worker,
scrapes `/api/metrics` + system CPU/RSS every N s, runs k6, waits for the async backlog to drain, runs the integrity
checker, writes `results/<label>/` and appends `results/register.jsonl`), `worker_recovery.py` (scenario H),
`backend/scripts/perf_dataset.py` (dataset; refuses any DB not named `*_perf`/`*_staging`),
`tests/load/render_register.py` → `docs/TEST_EXECUTION_REGISTER.md`.

```
backend/.venv/bin/python backend/scripts/... ; (see below)
MINTREE_DATABASE_URL=postgresql+psycopg://…/mintree_perf python -m scripts.perf_dataset --users 200 --items 20000 --stock-items 300
backend/.venv/bin/python tests/load/harness.py --scenario A_smoke.js --label <label> --workers <n> --env VUS=100 --env DURATION=10m --integrity
```

## Workload model
* A "client" = one k6 VU = one logged-in user acting continuously with **think time** (`THINK_MIN`/`THINK_MAX`, default
  0.5–2.5 s). `THINK_MAX=0` gives a closed loop (worst case; every VU a permanently in-flight request).
  Report both *concurrent clients* and *achieved req/s*.
* Mix weights are **configurable** (`-e WORKLOAD='{"list_items":10,…}'`) so real Mintree data can replace them:

| Group | Operations (default weight %) |
|---|---|
| Reads (~51) | list items 12 · search 8 · stock balances 10 · ledger (cursor) 5 · availability 8 · PO list 4 · me 4 |
| Master data (3) | create brand |
| Inventory (~26) | reserve+release 8 · production-issue proxy 5 · dispatch proxy (reserve + consume) 5 · transfer 4 |
| Procurement + QC (6) | PO → submit → approve (second user) → goods receipt → QC release (5 requests) |
| Accounts, Integrations (0) | **not implemented — weight must stay 0** (the script refuses otherwise) |

"Production" and "Dispatch" are *proxies*: they call the real inventory posting endpoints those modules will use.
They are not production orders or dispatch notes. Accounts/marketplace/Meta traffic is not modelled because the modules do not exist.

## Scenarios (spec → file)
| | Scenario | File | Spec parameters |
|---|---|---|---|
| A | Smoke | `A_smoke.js` | 50–100 VUs, 10 min |
| B | Progressive | `B_progressive.js` | 250 → 500 → 750 → 1,000 (hold per step configurable) |
| C | Target | `C_target.js` | 1,000 VUs, 30 min |
| D | Spike | `D_spike.js` | 100 → 1,000 in 30 s, hold 5 min |
| E | Hot-stock contention | `E_hot_stock.js` | 200 VUs on one balance/batch |
| F | Stress | `F_stress.js` | 1,000 → 1,250 → 1,500 (→ more), thresholds not enforced |
| G | Soak | `G_soak.js` | 1,000 VUs, 2 h |
| H | Worker recovery | `worker_recovery.py` | 10,000 jobs, SIGKILL workers + publisher, restart |

## Metrics & classification
* **Expected business conflicts** (409: insufficient stock, version conflict, approval limit…) → `business_conflicts`,
  *not* failures. **System failures** = 5xx (incl. 503 load shedding), 401/429, timeouts, any unexpected status →
  `system_failures`; `server_errors` = 5xx only.
* Latency by kind: `read`, `write` (single business write), `posting` (multi-line goods receipt).
* Server metrics (Prometheus, exported per run): request rate/latency/status by route, admission queue depth & wait,
  in-flight, pool checked-out/wait/timeouts, query & transaction duration, `tx_retries`, `pg_deadlocks`, lock waiters,
  PG connections (active/idle/idle-in-tx), outbox backlog & age, RabbitMQ queue depth, worker task outcomes, queue-wait
  histogram, log-drop counter, process CPU/RSS.
* **Caveat — app-measured query time ≠ database time.** On a CPU-saturated host, statement time seen from Python includes
  waiting for the GIL/threads (measured: 6 ms per primary-key SELECT under saturation vs ~0.2 ms in PostgreSQL). Staging must
  enable `pg_stat_statements` + `auto_explain` for true server-side timings.

## Acceptance criteria (provisional; measured on staging, not a laptop)
Reads p95 ≤ 500 ms · standard writes p95 ≤ 1 s, p99 ≤ 3 s · multi-line postings p95 ≤ 2 s · 5xx < 0.1 % · unexpected
failures < 0.5 % · background queue wait p95 ≤ 10 s · no pool exhaustion · no unexplained deadlocks · no lost/duplicate
business effects · no negative available stock · no unbalanced accounting postings *(Accounts not built: not testable yet)*.

## After every critical run
`check_integrity` (reconciliation, duplicates, traceability, audit, outbox) — an HTTP 2xx is never the pass criterion.

## Environment requirements for a valid acceptance run
Staging comparable to production hardware; **API, PostgreSQL, RabbitMQ/Redis and k6 on separate hosts** (k6 at 1,000 VUs
needs its own machine; in the sandbox it competes with the system under test); realistic data volume; dedicated
`*_staging` database; tuned `max_connections` per the budget in `SCALABILITY_STRATEGY.md §2`; `pg_stat_statements` on.
