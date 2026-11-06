# Backend Scalability

Status of the design for concurrent business operations. Capacity claims are limited to what was **measured**, with the measurement environment stated; nothing here claims thousands of requests per second. The long-form material is in `SCALABILITY_STRATEGY.md`, `PERFORMANCE_BASELINE.md` and `LOAD_TEST_PLAN.md`; this page says what holds today and what does not.

## Target and how to read it

The target is **1,000 concurrent business operations**, which is not 1,000 requests per second: with a mean think time of 1.5 s, 1,000 users offer roughly 650 requests/s. Whether it is met is decided on dedicated staging hardware (`LOAD_TEST_RESULTS.md`, section 3). It has **not** been demonstrated.

## Implemented design (and where to verify it)

| Concern | Implementation | Verified by |
|---|---|---|
| Bounded connection pools | per-process pool 10 + 0 overflow, 5 s wait then HTTP 503; admission control so threads cannot outnumber connections | `core/db.py`, load runs in the register |
| Short transactions | every posting is one `run_tx`; no external call inside a transaction (courier booking claims, calls, then records) | `core/uow.py`, `dispatch/service.py::book_shipment` |
| Row-level locking against overselling | balances locked in a fixed order, FEFO, `CHECK (reserved + blocked <= physical)` as the last guard | `test_concurrency`, `test_dispatch_concurrency`, `test_partial_dispatch` (live race), `test_procurement_returns_amendments` (live race), `test_crm_pipeline` (live race) |
| Idempotent postings | `Idempotency-Key` on stock, receipt, dispatch, return, payment, settlement postings; replay returns the first response | `test_idempotency`, per-feature replay tests |
| Retry safety | deadlock / serialisation / lock-timeout retried with jittered back-off inside `run_tx` | `test_uow` |
| Transactional outbox + workers | events written with the business change, published by a separate process, consumers idempotent; kill-9 recovery lost and duplicated nothing | `test_outbox`, `H-worker-recovery-p2` |
| Pagination, no N+1 | list endpoints page; reports aggregate in SQL | code review; not formally proven by query counts |
| Observability | JSON logs with request id, Prometheus metrics for API, DB, queue, slow-transaction logs | `core/observability.py` |
| Redis | cache/coordination only (rate limits, permission cache); never the source of truth for quantities | `core/authz_cache.py` |

## What was measured (shared 4-vCPU sandbox; k6, API, PostgreSQL, RabbitMQ, Redis on one machine)

From `PERFORMANCE_BASELINE.md` and the register: about 100 requests/s per API process; about 200-270 requests/s on 3-4 processes; CPU of the API (not PostgreSQL) is the limit; at 1,000 simulated clients p95 was about 3.3 s with **0 failed requests and intact data** (the system queues, it does not fail); every integrity invariant held in every run. These are bottleneck-finding measurements, not capacity proof.

## Known gaps (honest)

1. **No load workloads exist yet for** purchase returns/amendments, CRM, partial dispatch and shipment events, production plans, certificates, marketplace imports at volume, accounting postings in isolation, or dashboards under load. The existing workload covers inventory, receipts, QC chains and master writes.
2. **No profiling or optimisation was done in this work.** The CPU cost per request is unchanged from the baseline.
3. **Heavy dashboard queries are not cached** and have not had query-plan review. `management-summary` runs ~30 aggregate queries per call.
4. **Report endpoints that scan** (ageing, GST, costing) are unpaginated aggregates; fine at demo size, unmeasured at factory size.
5. **No read replica, partitioning or archival** exists; the ledger and audit tables will need them at scale.
6. **Docker compose stack** was not run in this work (no Docker daemon in the build container).

## Rules that must survive any optimisation

Do not weaken validation, constraints, audit or idempotency for throughput; do not hold a transaction across a network call; do not let a cache answer a question that decides stock or money; measure on staging hardware before claiming any number.
