# Backend Architecture — Assessment & Target

_Milestone A. Assessed commit: `46c82cc9fd4758704f99d5609bee889980f78ea9` (branch `phase-0-foundation`)._
_Baseline test status at that commit: backend **51/51 pass**, Playwright E2E **16/16 pass** (2026-11-06)._

> Nothing in this document claims the system supports 1,000 concurrent operations. That is a **target**, to be
> demonstrated by `tests/load/` on a documented staging environment (see `LOAD_TEST_PLAN.md`). Numbers below are
> from a 4-vCPU shared sandbox and are only used to find defects and rank bottlenecks.

## 1. What Phase 0 is (inspected)

| Concern | Implementation | Verdict |
|---|---|---|
| Framework / DB | FastAPI, SQLAlchemy 2 (sync), psycopg3, PostgreSQL 16, Alembic | Keep |
| Session lifecycle | `get_db`: one `Session` per request, commit in dependency teardown | **Defect** (§2.1) |
| Pool | SQLAlchemy defaults: 5 + 10 overflow, 30 s timeout | **Defect** (§2.2), now explicit & configurable |
| Auth | Opaque session cookie (SHA-256 hashed at rest), argon2, CSRF header, lockout | Sound; costly per request (§2.3) |
| Permissions | `resource:action` codes enforced by `require()` on every route | Sound; keep |
| Audit | Append-only table, DB triggers block UPDATE/DELETE/TRUNCATE, same transaction | Sound; keep |
| Master data | Generic audited CRUD factory, soft delete, DB check/unique/FK constraints | Sound; keep |
| Middleware | `BaseHTTPMiddleware` for request-ID + headers | Replaced by pure ASGI (cheaper, no task per request) |
| Layering | route → (validation) → handler containing business logic + ORM | Acceptable for CRUD; **not** for postings (§4) |
| Tests | 51 backend (real Postgres, migration-drift check), 16 Playwright | Keep all; extend |

## 2. Findings (each was reproduced, not assumed)

### 2.1 Commit after the response — silent lost writes (severity: critical for postings)
`get_db` committed in dependency teardown. In the installed FastAPI (0.162) teardown of a default-scope `yield`
dependency runs **after the response is sent**. Experiment (`scratchpad/commit_order.py`): a deferred-FK
violation that only fails at `COMMIT` returned **HTTP 200 to the client while persisting nothing**.
For master data this is a latent risk; for a goods receipt or stock issue it is an unacceptable silent loss.

*Remedy:* all DB dependencies use `Depends(get_db, scope="function")` (teardown/commit **before** the response;
re-run of the experiment returns 500 and persists nothing). Business postings additionally use an explicit
unit of work that commits inside the service (see `TRANSACTION_INTEGRITY.md`), so success is never reported
before the commit is durable.

### 2.2 Thread-pool / connection-pool deadlock — collapse at 100 concurrent users (severity: critical)
Original code under k6 (`tests/load/run_phase0_baseline.sh`, 100 VUs, mixed read/write, 45 s, 1 worker, perf DB
with 20k items): **53.5 % failures, p95 = 60 s (client timeout), ≈ 5 req/s**; log full of
`QueuePool limit of size 5 overflow 10 reached`. `pg_stat_activity`: every connection
`idle in transaction / ClientRead`. `py-spy dump`: 10 threads blocked in pool checkout.

Root cause: a sync request takes its connection in one thread hop (`current_user`), releases the thread, and needs
another hop to run the handler. The freed threads are taken by new requests that block on checkout while every
connection is held by requests waiting for a thread. Nothing progresses until the 30 s pool timeout.
My first fix (cap threads ≤ connections) **did not work** — the dump showed the same state — because the invariant
must hold per *request*, not per thread hop.

*Remedy:* `AdmissionControl` (pure ASGI): at most `pool capacity` requests in flight per process, admitted
*before* any thread or connection is used; excess requests wait asynchronously and get `503 + Retry-After` after
`admission_timeout_s`. Same load, same data after the fix: **0 % failures, ≈ 97 req/s, p95 0.98 s** (1 worker),
**≈ 220–230 req/s, 0 % failures** (4 workers; 100 and 400 VUs). Invariant to preserve: *a request never holds two
connections* (enforced by code review + a test, see `TRANSACTION_INTEGRITY.md`).

### 2.3 Per-request authentication cost (severity: high — the dominant CPU consumer)
Every authenticated request issues 4 sequential queries (session, user, roles, role→permissions).
`py-spy record` under load: `current_user` = **33.8 %** of server time vs ≈ 12 % for the actual list query.
Login costs ≈ 100 ms of argon2 CPU (acceptable; but a login storm of 1,000 users ≈ 100 CPU-seconds).
*Plan (Milestone G, after instrumentation):* one joined query + per-process permission cache invalidated by an
authorization epoch (Redis), falling back to the DB when Redis is unavailable. Permission changes must remain
effective immediately (existing test `test_permission_changes_take_effect_immediately` guards this).

### 2.4 Lower-severity / volume-dependent risks (recorded, not yet optimised — no measurement justifies it)
* `count(*)` per list page is a sequential scan (2 ms at 20k rows; grows linearly).
* `OFFSET` pagination and unindexed `ILIKE '%x%'` search will degrade on large tables; histories need cursor
  pagination, searches need `pg_trgm` GIN indexes **if** plans show seq scans on real volumes.
* `audit_logs` grows without bound; needs retention/partition policy once volume is known.
* psycopg3 server-side prepared statements are incompatible with PgBouncer *transaction* pooling unless
  `prepare_threshold=None`; decide when PgBouncer is evaluated.
* No metrics, no tracing, no readiness/liveness split (Milestone B).
* Single-process in-memory state: none (good — the app is stateless; sessions are in PostgreSQL).

## 3. Strengths to preserve
Server-side permission enforcement; append-only audit with DB-level immutability; hashed session tokens; DB
constraints as the final integrity layer; migration-drift test; hermetic E2E. None of these were weakened.

## 4. Target architecture

```
HTTP ─► ASGI middleware (request-id, admission control, metrics)
      ─► API route  (auth + permission dependency, request validation: Pydantic)
      ─► domain service   (business rules, status machines, lock order; owns the transaction boundary)
      ─► repository       (SQL/ORM only; no business rules)
      ─► PostgreSQL       (ledger, balances, documents, audit, outbox in ONE transaction)
Outbox publisher ─► RabbitMQ ─► Celery workers (idempotent consumers)      Redis: cache/coordination only
```

Module layout (`backend/app/<module>/`): `models.py`, `schemas.py`, `repository.py`, `service.py`, `router.py`,
`permissions`, `tests/`. Modules call each other **only through services** (e.g. `procurement.service` → 
`inventory.service.post_receipt`), never through each other's tables.

Domain modules: identity & access, master data, inventory & warehouse, procurement, production, R&D,
quality, dispatch, accounts, CRM & sales, e-commerce integrations, maintenance, notifications & approvals,
audit & reporting. **Implemented now:** identity, master data, audit/documents (Phase 0), plus
shared infrastructure (§5) and **inventory + procurement** (Milestone D). The remaining modules follow the same
pattern in Milestone H; they are not stubbed.

## 5. Shared infrastructure added for the target
`core/middleware.py` (request context, admission control) · `core/observability.py` (metrics) ·
`core/uow.py` (unit of work + bounded retry) · `core/idempotency.py` · `core/outbox.py` + `workers/`.
See `TRANSACTION_INTEGRITY.md` and `SCALABILITY_STRATEGY.md`.

## 6. Decisions deliberately **not** taken
No microservices, Kubernetes, Kafka, read replicas, partitioning or distributed search: no measurement shows the
single-primary modular monolith is insufficient. Revisit when `PERFORMANCE_BASELINE.md` shows a ceiling that
vertical/horizontal API scaling and query work cannot lift.
