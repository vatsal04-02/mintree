# Mintree Factory OS

Manufacturing ERP + CRM + omnichannel commerce management for Mintree (cosmetics & skincare).
Modular monolith: **FastAPI + PostgreSQL** backend, **Next.js + TypeScript + Tailwind** frontend.

> Status: a connected manufacturing ERP + CRM backend with config-driven screens for every module. Implemented and tested: master data (with unit
> conversions), inventory (append-only ledger, batches, reservations), procurement (RFQ, PO approval and **amendments**, receipts, **returns to
> supplier**, three-way-matched invoices), R&D (immutable formulation versions), production (BOMs, **plans, work centres**, genealogy), quality
> (**sampling plans, frozen Certificates of Analysis**), dispatch (**partial shipments**, invoices per shipment, **delivery attempts and proof of
> delivery**), CRM (**opportunities, lead conversion**), accounts (double-entry, bank matching), marketplaces (DEMO accounts) and maintenance.
> `docs/MODULE_COMPLETION_MATRIX.md` states, module by module, what is Complete / Partial / Planned (**none is Complete yet**) and what is missing.
> **Not built:** live marketplace, courier or Meta integrations (a fake courier adapter, CSV/JSON/webhook ingestion and manual recording only),
> GST filing / e-invoicing, cash-flow statement, spare-parts-to-inventory link, object storage. The AI layer is built but off by default and not yet evaluated against a live model (see `docs/AI_LAYER.md`).
> **The 1,000-concurrent-operation target is NOT yet demonstrated**: see `docs/BACKEND_SCALABILITY.md`, `docs/LOAD_TEST_RESULTS.md` and `docs/PERFORMANCE_BASELINE.md`.
> A complete demonstration dataset can be generated through the real workflows: see **Demo environment** below.

## Phase 0 scope (this repository)

| Area | What exists |
|---|---|
| Authentication | Session cookies (HttpOnly, SameSite), argon2 hashing, per-session CSRF token, account lockout, forced first-login password change, session revocation on password change/deactivation |
| Access control | 15 PRD roles, 160 `resource:action` permissions, enforced **in the backend on every request**; UI hides what you can't use but is never the gate |
| Privilege-escalation guards | You can't assign roles / grant permissions you don't hold, can't modify accounts above your own rights, can't deactivate yourself, and the last System Administrator can't be removed |
| Audit | Append-only `audit_logs` (DB triggers block UPDATE/DELETE/TRUNCATE), written in the same transaction as the change; secrets redacted; request ID + IP captured |
| Master data | Items (RM/PM/BULK/FG), categories, units, suppliers (GSTIN/PAN/IFSC validation, restricted bank details), customers, brands, warehouses, storage locations, departments |
| Document centre | Versioned uploads (SHA-256, extension allow-list, size limit), links to any record, audited downloads, soft delete |
| App shell | Sidebar, breadcrumb header, Ctrl+K search, quick-create, light/dark theme, config-driven tables & forms |

Not in Phase 0 (by design): approval engine, notification centre, S3 storage backend, inventory/procurement/production workflows.

## Run it locally

Prereqs: Python 3.12+, Node 22+, PostgreSQL 16.

```bash
# 1. Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock.txt
cp .env.example .env                      # adjust MINTREE_DATABASE_URL
.venv/bin/alembic upgrade head
.venv/bin/python -m app.seed              # roles, permissions, defaults, first admin (password printed once)
.venv/bin/uvicorn app.main:app --port 8000

# 2. Frontend (proxies /api to the backend; override with BACKEND_URL)
cd frontend && npm install && npm run dev
```

### Database initialisation, migrations, development commands

```bash
createdb mintree && createdb mintree_test                      # PostgreSQL 16; the test suite builds its schema from the migrations itself
cd backend
.venv/bin/alembic upgrade head                                 # apply all migrations (0001..0023)
.venv/bin/alembic revision --autogenerate -m "what changed"    # after a model change; REVIEW the file: autogenerate misses CHECKs, triggers, sequences, backfills
.venv/bin/python -m scripts.check_integrity                    # re-derive every invariant from the tables (37 checks)
.venv/bin/python -m scripts.gen_schema_docs                    # regenerate docs/DATABASE_SCHEMA_MAP.md, ENTITY_RELATIONSHIP_MAP.md, DATA_INTEGRITY.md
```

Time: instants are UTC `timestamptz`; business dates use `Asia/Kolkata` through `app.core.clock` and SQL `business_today()`. Never use `date.today()` or `current_date` (a test enforces it).

Sign in at http://localhost:3000 with the bootstrap admin (`MINTREE_BOOTSTRAP_ADMIN_EMAIL`, default `admin@example.com`).
You'll be asked to set a new password. API docs: http://localhost:8000/api/docs.

## Backend scalability work (see `docs/`)

| Doc | What it covers |
|---|---|
| `BACKEND_ARCHITECTURE.md` | Phase 0 audit: defects found by measurement, target architecture |
| `SCALABILITY_STRATEGY.md` | connection budget maths, admission control, scaling levers |
| `TRANSACTION_INTEGRITY.md` | unit of work, lock ordering, idempotency, outbox, invariants |
| `LOAD_TEST_PLAN.md` / `PERFORMANCE_BASELINE.md` / `TEST_EXECUTION_REGISTER.md` | scenarios A–H, measured results, every run recorded |

Extra services (local dev): RabbitMQ 3.12 (`amqp://guest:guest@127.0.0.1:5672//`, management on :15672) and Redis 7.
Background processes:

```bash
cd backend
.venv/bin/python -m app.workers.outbox_publisher                                   # transactional outbox -> RabbitMQ
.venv/bin/python -m celery -A app.workers.celery_app worker -Q default,notifications -c 4 -l warning
```

Load testing (isolated `*_perf` / `*_staging` database only — the tools refuse anything else; needs k6):

```bash
MINTREE_DATABASE_URL=postgresql+psycopg://…/mintree_perf python -m scripts.perf_dataset --stock-items 300
backend/.venv/bin/python tests/load/harness.py --scenario A_smoke.js --label my-run --workers 3 --env VUS=100 --env DURATION=10m --integrity
backend/.venv/bin/python backend/scripts/check_integrity.py   # (python -m scripts.check_integrity) re-derives every invariant from the tables
```

## Demo environment

A full, connected story (procurement, QC, R&D versions, production, partial shipments, invoices, payments, CRM, marketplaces, maintenance, marketing, with realistic exceptions) created **through the real workflows**, not by inserting rows. Details: `docs/DEMO_ENVIRONMENT.md` (safety rules, users) and `docs/DEMO_SCENARIOS.md` (what to click).

```bash
cd backend
export MINTREE_ENVIRONMENT=demo                                                            # must be exactly "demo"
export MINTREE_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5433/mintree_demo     # the name must contain the word "demo"
.venv/bin/python -m scripts.demo_data reset --confirm-reset-demo-database mintree_demo     # wipes a DEMO database only; refuses anything else
.venv/bin/python -m scripts.demo_data init                                                  # ~40 s; running it again changes nothing
.venv/bin/python -m scripts.demo_data verify                                                # 100 checks incl. dashboard-vs-table reconciliation and role enforcement
.venv/bin/uvicorn app.main:app --port 8000                                                  # then start the frontend as above
```

Demo users (password `Mintree-Demo#2026`, demo databases only): `demo.admin`, `demo.buyer`, `demo.stores`, `demo.qa`, `demo.qa2`, `demo.qi`, `demo.pm`, `demo.pm2`, `demo.op`, `demo.rnd`, `demo.rnd2`, `demo.disp`, `demo.sales`, `demo.acct`, `demo.mkt`, `demo.maint`, `demo.mgmt`, `demo.biz` `@example.com`. Permissions are enforced exactly as in production.

Marketplace accounts are **labelled DEMO with sample orders**; the only courier adapter is an in-process fake; marketing posts are recorded manually. Nothing contacts a live service.

## Tests

```bash
cd backend && .venv/bin/pytest            # 320 passed + 1 skipped (needs RabbitMQ) at the last full run; real Postgres, a live 2-process server for concurrency tests, and the demo CLI end to end
cd frontend && npm run lint && ./e2e/run.sh     # 50 browser tests (Phase 0, every module screen, real UI workflows incl. scanning), hermetic DB + API + production build
cd frontend && ./e2e/run-demo.sh                  # 18 browser journeys against a demo-seeded database (seeded records visible per role, UI actions change real state)
```

The backend suite includes permission-matrix, escalation, audit-immutability, document-integrity tests and a
**model↔migration drift check** that fails if a model changes without a migration.

### Load tests

`tests/load/` holds the k6 scenarios, the harness and the result register (`tests/load/README`-style notes are in `docs/LOAD_TEST_PLAN.md`). Results so far come from a shared 4-vCPU sandbox and are **not capacity proof**; workloads for the newer modules are not written yet and no staging run exists (`docs/LOAD_TEST_RESULTS.md`).

### Known integration limitations

* No live marketplace (Amazon, Flipkart, Myntra, Nykaa), courier, Meta, GST-portal or e-invoicing integration; adapters/ingestion and manual recording only.
* Notifications are in-app only. Documents are stored on local disk (metadata, versions and SHA-256 in PostgreSQL).
* Tax class, payment method and courier masters are referenced by items, payments and shipments; references are optional when no master of that kind is configured.

## Design decisions & deviations from the PRD

- **`items` table** instead of `products`: the PRD's Inventory module calls for one item master across RM/PM/bulk/FG. Product variants/BOMs arrive with Production.
- **Soft delete only** for master data (`DELETE` deactivates) so history and references stay intact.
- **Role defaults are proposals.** Management has no access to audit logs or supplier bank data; Business Administrator has no supplier bank access. Reconcile with Mintree (PRD §17).
- **Warehouses/departments/units seeded** are the PRD's proposed defaults, not Mintree's confirmed structure.
- **Storage**: local filesystem backend behind a `Storage` protocol. The PRD's S3-compatible backend is a drop-in implementation of the same three methods; not yet written/tested.
- **Frontend data fetching** is plain `fetch` + local state. Fine for Phase 0; consider TanStack Query when screens share data.
- Document links are free-form `(entity_type, entity_id)` pairs and are not yet validated against the target record.
- Redis (permission-cache epoch, rate limits) and RabbitMQ/Celery (outbox consumers, scheduled maintenance generation) are in use; **n8n is not built**; the AI layer (`docs/AI_LAYER.md`) is built, off by default (`MINTREE_AI_ENABLED`).

## Operations

```bash
MINTREE_DATABASE_URL=postgresql://… ops/backup.sh ./backups 14        # custom-format dump + checksum + retention
MINTREE_DATABASE_URL=postgresql://… ops/restore_check.sh ./backups/mintree-<stamp>.dump   # restores to a scratch DB, compares row counts, runs the integrity checker, drops it
```

`docker-compose.yml`, the two Dockerfiles, `ops/Caddyfile` and `.env.example` describe a single-host deployment (API, worker, beat, outbox publisher, web, Postgres, Redis, RabbitMQ, Caddy with automatic HTTPS).
**They have not been built or run** (the development sandbox has no Docker daemon) — validate on a staging host. Uploaded documents live in a volume and need their own backup.

## What is left

`docs/PRD_COVERAGE.md` lists, per module, what is built, what is partial and what is not — chiefly live marketplace/courier/Meta connectors, GST e-invoice/e-way bill, partial shipments,
live-model evaluation of the AI layer, S3 storage, and the 1,000-concurrent-operation load demonstration — and which of those wait on business decisions from Mintree (PRD §17).

## Open items needing Mintree input (PRD §17)

Approval hierarchies, physical warehouse structure, QC specs, courier partners, GST registrations, marketplace
seller/API access, Meta assets, user counts, hosting model.


## Performance, demo scale and brand (latest milestone)
* Per-class load results, before/after and the honest 10,000 req/s verdict: `docs/PERFORMANCE_BENCHMARK_RESULTS.md`; design: `docs/PERFORMANCE_ARCHITECTURE.md`; reproduce with `tests/load/run_classes.sh`.
* Demo at scale (130+ sales orders, 126 marketplace orders, 125 CRM leads+opportunities, a simulated Meta workspace): `docs/DEMO_DATA_COVERAGE.md`; `python -m scripts.demo_data init|verify|report|reset`.
* Issue register (fixed / open): `docs/ISSUE_REGISTER.md`; brand asset usage: `docs/BRAND_ASSET_USAGE.md`.
