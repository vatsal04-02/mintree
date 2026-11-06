# Database Architecture

One PostgreSQL 16 database is the authority for every structured business record (stock, money, documents, approvals, audit). There is no microservice split and no per-module database: modules are *packages with table ownership* inside one application (`backend/app/<module>`), sharing the same master records. See `DATABASE_SCHEMA_MAP.md` (every table and column, generated) and `ENTITY_RELATIONSHIP_MAP.md` (relationships and cross-module references, generated).

## 1. Principles that are actually enforced

| Principle | How it is enforced | Where to see it |
|---|---|---|
| Money and quantities are exact | `numeric(18,2)` money, `numeric(18,3/4)` quantities, `Decimal` in code; explicit `ROUND_HALF_UP`; no floats in postings | schema map; `DATA_INTEGRITY.md` |
| One master record per real-world thing | items, suppliers, customers, warehouses, batches, users are referenced by id from every module; marketplace accounts and CRM leads point at the same `customers` row | ER map, "Cross-domain references" |
| History is never overwritten | stock ledger, journals, audit log, inspection results, process checks, trial observations, certificates of analysis are append-only (DB triggers); formulation versions are immutable once submitted | `DATA_INTEGRITY.md`, trigger table |
| Documents reference what they came from | PO ← PR/RFQ; GRN ← PO line; batch ← GRN line; supplier invoice ← PO (3-way match); production order ← BOM ← formulation version; shipment line ← batch; invoice ← shipment; credit note ← return ← shipment line | schema FKs |
| Transactions are short and atomic | every posting runs in `run_tx` (one transaction, bounded retries on deadlock/serialisation/lock-timeout, no external call inside); postings are idempotent (`Idempotency-Key`) | `backend/app/core/uow.py`, `posting.py`, `idempotency.py` |
| Concurrent stock operations cannot oversell | stock balances are locked in a fixed order (`FOR UPDATE`), reservations and ledger rows are written in the same transaction, CHECK constraints (`reserved + blocked <= physical`) are the last guard | `inventory/repository.py`, concurrency tests |
| Side effects are reliable | transactional outbox (`outbox_events`) written with the business change, published by a separate worker, consumers are idempotent (`processed_events`) | `core/outbox.py` |

## 2. Time

* **Instants** (`created_at`, `posted_at`, `decided_at`...) are `timestamptz`, stored as UTC. Every database connection pins `timezone=UTC` (`app/core/db.py`), so nothing depends on the server or session setting.
* **Calendar dates** (invoice date, due date, planned date, expiry date, period dates) are `date` values in the **business timezone `Asia/Kolkata`**. Python uses `app.core.clock.business_today()` / `business_date(ts)`; SQL uses `business_today()` / `business_date(timestamptz)` (migration 0015). Both are tested to agree under four different session time zones and across IST midnight, month-end, financial-year and year-end boundaries (`tests/test_business_clock.py`).
* `date.today()`, `current_date` and `timestamptz::date` are **banned** in application code; a test fails if they reappear.
* Origin: an ageing test that expected 20 days overdue produced 21 whenever the OS date and the database date differed. Reproduced with `TZ=Pacific/Kiritimati`, fixed at the root, covered by boundary tests.

## 3. Domain ownership (summary; full list in the schema map)

| Domain | Owns | Reuses (does not duplicate) |
|---|---|---|
| Master data | units (+conversions), categories, items, suppliers, customers, contacts, price lists, brands, warehouses, locations, tax classes, payment methods, courier partners | users, departments |
| Inventory | batches, balances, ledger, reservations, valuation, stocktakes, opening stock | items, warehouses, suppliers (as batch source) |
| Procurement | PR, RFQ, quotations, PO (+amendments), GRN, purchase returns, supplier invoices, debit notes | items, suppliers, batches, journals |
| R&D | projects, formulations, versions, ingredients, trials, observations | items |
| Production | BOM, orders, materials, stages, consumptions, wastage, work centres, plans | items, formulation versions, batches |
| Quality | specs, sampling plans, inspections, results, deviations, CAPA, certificates of analysis | batches, items |
| Dispatch | sales orders, shipments, packages, attempts, proof of delivery, courier events, returns, credit notes | customers, batches, invoices |
| CRM & sales | leads, sources, opportunities (+history), activities, follow-ups, quotations | customers, sales orders |
| Accounts | chart, periods, journals, sales/supplier invoices, payments + allocations, bank accounts/transactions | customers, suppliers |
| E-commerce | marketplace accounts, SKU maps, external orders, settlements, returns | customers, sales orders, items |
| Maintenance / Branding / Approvals / Documents / Notifications | own tables | users, items (spare parts), customers |

Multi-tenancy is deliberately **not** introduced. Company, factory, warehouse, department and location are modelled (`company_settings`, `warehouses`, `storage_locations`, `departments`, `work_centres`).

## 4. Migrations

Alembic, linear history `0001`..`0023`, built into every test run (`tests/conftest.py` upgrades an empty database to `head`). Autogenerate is used and then reviewed by hand because it cannot see CHECK constraints, triggers, sequences or data backfills; those are added explicitly (see 0015 functions, 0017/0018/0020/0021 sequences, 0019 backfill and status-check replacement, 0020 certificate trigger).

## 5. What is NOT done (honest)

* **Object storage:** documents keep metadata, versions and hashes in PostgreSQL; file bytes are stored through the storage layer configured by `MINTREE_STORAGE_ROOT` (local disk). An S3-compatible backend is not implemented.
* **Archival/partitioning:** no table partitioning or archival job exists yet; "safe deletion" is implemented as deactivation (`is_active`) and append-only history, not as retention policies.
* **Employees, labour time, stock-transfer documents and production material returns** are not modelled (see `MODULE_COMPLETION_MATRIX.md`).
* **Read replicas / caching of heavy dashboard queries:** not implemented (see `BACKEND_SCALABILITY.md`).
