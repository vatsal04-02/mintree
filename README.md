# Mintree Factory OS

Manufacturing ERP + CRM + omnichannel commerce management for Mintree (cosmetics & skincare).
Modular monolith: **FastAPI + PostgreSQL** backend, **Next.js + TypeScript + Tailwind** frontend.

> Status: **Phase 0 — Foundation**, the **Inventory + Procurement** slice (on a scalable transactional backend: idempotent
> postings, append-only stock ledger, transactional outbox, workers) and the **Milestone H backend modules** — accounts,
> R&D, quality, production, dispatch/invoicing/returns, maintenance, e-commerce ingestion, CRM, branding and a management
> summary — are implemented and tested, **with config-driven screens for every module** (60+ screens, approvals inbox, notification bell). See
> `docs/PRD_COVERAGE.md` for the module-by-module statement of what is built, partial and not built. Not built: live marketplace/Meta/courier integrations (adapters and file/webhook
> ingestion only), RFQ, bank reconciliation, partial shipments, report builder.
> **The 1,000-concurrent-operation target is NOT yet demonstrated** — see `docs/PERFORMANCE_BASELINE.md` for what has
> and has not been measured, and `docs/BACKEND_ARCHITECTURE.md` for the assessment that drove the changes.

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

## Tests

```bash
cd backend && .venv/bin/pytest            # 173 tests, real Postgres + RabbitMQ + a live 2-process server for concurrency tests
cd frontend && npm run lint && ./e2e/run.sh   # 27 browser tests (Playwright: Phase 0 + every module screen) against a hermetic DB + API + production build
```

The backend suite includes permission-matrix, escalation, audit-immutability, document-integrity tests and a
**model↔migration drift check** that fails if a model changes without a migration.

## Design decisions & deviations from the PRD

- **`items` table** instead of `products`: the PRD's Inventory module calls for one item master across RM/PM/bulk/FG. Product variants/BOMs arrive with Production.
- **Soft delete only** for master data (`DELETE` deactivates) so history and references stay intact.
- **Role defaults are proposals.** Management has no access to audit logs or supplier bank data; Business Administrator has no supplier bank access. Reconcile with Mintree (PRD §17).
- **Warehouses/departments/units seeded** are the PRD's proposed defaults, not Mintree's confirmed structure.
- **Storage**: local filesystem backend behind a `Storage` protocol. The PRD's S3-compatible backend is a drop-in implementation of the same three methods; not yet written/tested.
- **Frontend data fetching** is plain `fetch` + local state. Fine for Phase 0; consider TanStack Query when screens share data.
- Document links are free-form `(entity_type, entity_id)` pairs and are not yet validated against the target record.
- Redis (permission-cache epoch, rate limits) and RabbitMQ/Celery (outbox consumers, scheduled maintenance generation) are in use; **n8n and the AI features are not built**.

## Operations

```bash
MINTREE_DATABASE_URL=postgresql://… ops/backup.sh ./backups 14        # custom-format dump + checksum + retention
MINTREE_DATABASE_URL=postgresql://… ops/restore_check.sh ./backups/mintree-<stamp>.dump   # restores to a scratch DB, compares row counts, runs the integrity checker, drops it
```

`docker-compose.yml`, the two Dockerfiles, `ops/Caddyfile` and `.env.example` describe a single-host deployment (API, worker, beat, outbox publisher, web, Postgres, Redis, RabbitMQ, Caddy with automatic HTTPS).
**They have not been built or run** (the development sandbox has no Docker daemon) — validate on a staging host. Uploaded documents live in a volume and need their own backup.

## What is left

`docs/PRD_COVERAGE.md` lists, per module, what is built, what is partial and what is not — chiefly live marketplace/courier/Meta connectors, GST e-invoice/e-way bill, partial shipments,
AI features, S3 storage, and the 1,000-concurrent-operation load demonstration — and which of those wait on business decisions from Mintree (PRD §17).

## Open items needing Mintree input (PRD §17)

Approval hierarchies, physical warehouse structure, QC specs, courier partners, GST registrations, marketplace
seller/API access, Meta assets, user counts, hosting model.
