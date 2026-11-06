# Scalability Strategy

Target (not yet demonstrated): **1,000 concurrent business operations** = 1,000 active clients each issuing a mix
of reads and short transactional writes. This is **not** 1,000 TPS and **not** 1,000 open DB connections.

## 1. Concurrency vs throughput
Throughput needed ≈ active clients ÷ (client think time + response time). A k6 *closed loop with no sleep*
(what `baseline_phase0.js` does) makes every VU a permanently in-flight request and overstates a human workload
by 10–100×. The target and soak scenarios therefore use **think time** (configurable) and the plan reports both
"concurrent clients" and achieved req/s. With 1,000 clients and a 5 s mean cycle ≈ **200 req/s**; at 2 s ≈ 500.

## 2. Connection budget (calculate before every deployment)
```
per process capacity   = DB_POOL_SIZE + DB_MAX_OVERFLOW                      (default 10 + 0)
API total              = replicas × uvicorn_workers × per-process capacity
Workers total          = celery_processes × per-process capacity (default 2) + outbox publisher (2)
Reserved               = 10 (migrations, psql, monitoring, superuser_reserved_connections)
REQUIRED max_connections ≥ API + Workers + Reserved,  and keep ≤ ~80 % of what the DB host can serve
```
Example: 3 replicas × 4 workers × 10 = 120 API + 8 worker procs × 2 = 16 + 2 + 10 reserved = **148** →
`max_connections = 200`, or PgBouncer (transaction mode) in front. Enabling PgBouncer requires: psycopg
`prepare_threshold=None`; no session-level state (`SET`, advisory *session* locks, `LISTEN`); our
`statement_timeout`/`lock_timeout` set via `options` must move to per-role settings. Evaluated in Milestone G
**only if** connection count, not CPU, is the measured limit.

## 3. Thread/pool deadlock and admission control (found by measurement)
See `BACKEND_ARCHITECTURE.md` §2.2. Rule: **in-flight requests per process ≤ pool capacity**, enforced by
`AdmissionControl` *before* a thread or connection is touched. `threadpool_size ≥ slots`. Overload behaviour is
therefore bounded queueing then `503 Retry-After`, never a pool-timeout stampede.

## 4. Safety timeouts (server-side, per connection)
`statement_timeout=15 s`, `lock_timeout=5 s`, `idle_in_transaction_session_timeout=30 s` (all configurable).
A request stuck on a lock fails fast and is retried (bounded) by the unit of work instead of pinning a slot.

## 5. Scaling levers, in the order we will use them (each only when measured)
1. Remove wasted work per request (auth queries → 1 + cached permissions; N+1; count queries).
2. More uvicorn workers per host (CPU-bound Python, GIL): measured ≈ 97 req/s per core in this sandbox.
3. More API replicas behind a load balancer (stateless: sessions in PostgreSQL).
4. PgBouncer if connection budget, not CPU, binds.
5. Offload heavy reads/exports to Celery + precomputed tables.
6. Only then: read replica (reports), partitioning (ledger/audit), search service — each needs a measured case.

## 6. Hot rows
Stock balance rows are the contention point. Rules: lock `stock_balances` rows `FOR UPDATE` in **ascending
primary-key order** (consistent lock order); keep the locked section to validate → write → commit; never call
external services inside it; reservation + issue on the same balance serialise by design (see
`TRANSACTION_INTEGRITY.md`). Hot-stock contention is a dedicated k6 scenario (E).

## 7. Measured status
See `PERFORMANCE_BASELINE.md`. Current state: **target not demonstrated.** Sandbox only.
