# Module Completion Matrix and Gap Register

Audit baseline: commit `e122aac`, plus the business-timezone fix (`9bfc910`). Written 2026-11-06.

States, used honestly:

* **Complete** = implemented *and* proven end-to-end against the target spec (backend workflow test, DB-level verification, browser test).
* **Partial** = real backend logic and tests exist, but required spec items are missing or unproven.
* **Planned** = not implemented.

**No module is marked Complete yet.** The existing suite proves the *existing* behaviour, not the full 13-part target spec. A module only moves to Complete when its row in section 3 is closed.

## 1. Measured facts about the current system

| Fact | Value | How measured |
|---|---|---|
| Tables in the migrated schema | 118 (incl. `alembic_version`); excludes 4 scratch tables created by `tests/test_uow.py` | `information_schema.tables` after `alembic upgrade head` |
| CHECK constraints / indexes | 154 / 275 | `pg_constraint`, `pg_indexes` |
| Alembic revisions | 15 (`0001`..`0015`) | `migrations/versions` |
| Backend tests | 237 passed, 1 skipped, 0 failed (TZ=Pacific/Kiritimati) | `docs/TEST_EXECUTION_REGISTER.md` |
| Skipped test | `tests/test_outbox.py:186`, needs live RabbitMQ (not available in this container) | pytest `-rs` |
| Money / quantity types | `NUMERIC` (money `MONEY`, qty `NUMERIC`), no floats in postings | models reviewed |
| Timestamps | `TIMESTAMPTZ` throughout; sessions pinned to UTC | `app/core/db.py` |
| Business dates | single zone `Asia/Kolkata` via `app/core/clock.py` + SQL `business_today()` | tests/test_business_clock.py |

## 2. Module status against the target spec

| Module | Status | Evidence it exists | Missing / unproven against spec |
|---|---|---|---|
| Identity, RBAC, audit | Partial | users, roles, permissions, role_permissions, sessions, audit_logs; auth + permission tests | login-history table (only session/lockout state), user-activity feed, per-user department assignment and approval authority limits to be confirmed |
| Master data | Partial | items, units_of_measure, warehouses, storage_locations, departments, suppliers, customers, brands, product_categories | **unit conversions**, item-category hierarchy by type, employees, courier partner master, tax classification master, payment-method master |
| Inventory | Partial | stock_ledger (movement-type CHECK incl. TRANSFER_IN/OUT, uniqueness on source), stock_balances, batches, reservations, stocktakes, opening stock, hot-stock concurrency tests | stock-transfer *document* and material-return workflow need verifying; ledger-to-balance reconciliation exists in `check_integrity` but not yet as a continuous DB assertion |
| Procurement | Partial | PR, RFQ, quotations, PO, goods receipt, supplier invoices, 3-way match, tests | **PO amendments, purchase returns, delivery schedules, supplier performance history**; end-to-end PR→payment test is a single test |
| R&D / formulation | Partial | development_projects, formulations, formulation_versions, ingredients, lab_trials, trial_observations | stability/product-testing breadth, formula cost calc and revision-history proof to be verified |
| Production | Partial | BOMs, production_orders (reference formulation version), stages, materials, consumptions, wastage, concurrency test | **production plans/schedules, work centres, routing, labour/machine time**, material return to stock |
| Quality | Partial | quality_specs, inspections, results, deviations, CAPA, QC status gating of availability | **sampling plans, inspection templates as separate entities, Certificate of Analysis, retest lineage proof** |
| Dispatch | Partial | sales_orders, shipments, shipment_lines, courier_events, pick_scans, returns, credit notes | **shipment packages, delivery attempts, proof of delivery**, replacement shipments |
| CRM / sales | Partial | leads, customer_contacts, followups, sales_quotations, complaints | **opportunities and pipeline stages, lead-source master, calls/activities as records**, customer-conversion trace |
| Accounts | Partial | chart, journal_entries/lines, payments + allocations, debit/credit notes, bank reconciliation, periods, ageing, P&L/BS | cash-flow report and GST validation depth to be verified; no GST filing / e-invoicing exists and none is claimed |
| E-commerce | Partial | marketplace_accounts, external_orders(+items), sku_mappings, webhook_events, settlements, returns | per-marketplace adapters (Amazon/Flipkart/Myntra/Nykaa) are not live; demo adapter only; fees as first-class rows to be verified |
| Maintenance | Partial | equipment, schedules, work orders, downtime, spare_part_usage, calibration | breakdown-report and service-reminder notification end-to-end proof |
| Branding / Meta | Partial | brands, brand_assets, campaigns, content_calendar, publishing_records, campaign_metrics, social_accounts | draft→approval workflow proof; no live Meta publishing exists and none is claimed |
| Approvals, notifications, documents | Partial | approval_policies/records/delegations, notifications, documents + versions + links, outbox, idempotency, job_failures | **notification preferences**; document file-hash verification to be confirmed |
| Management dashboard | Partial | `analytics` router computes from SQL | every figure not yet reconciled by an automated check |
| AI layer | Planned (shelved) | none | out of scope until separately authorised |

## 3. Gaps verified absent (zero code hits)

Confirmed by searching `backend/app` (case-insensitive) with no match: `unit conversion`, `opportunity`, `work centre`, `package` (shipment packages), `purchase return`, `amendment`, `delivery attempt` / `proof of delivery`, `employee`, `notification preference`, `production plan`, `sampling`, `material return`.

Not verified either way (keyword too ambiguous to trust): Certificate of Analysis, stock-transfer document, tax classification master.

## 4. Other findings

1. The tables `dl_*`, `rb_*`, `fk_c7b4f588a668_p/_c` seen in a test database are scratch tables created by `tests/test_uow.py` (they are not in any migration) and are excluded from all schema counts.
2. `backend/dump.rdb` (a Redis dump) is tracked in git even though an earlier commit says Redis dumps are no longer tracked. It should be removed from the index and ignored; not done here because it was not asked for.
3. The reported failing ageing test is **timezone-dependent**. It passes under UTC and fails whenever the OS date and the DB date differ. It is now fixed at the root (section 1) and covered by boundary tests.

## 5. Implementation order (dependency-driven)

Each step closes specific gaps above and ends with targeted tests, a register entry and a commit.

1. **Stabilise (done):** timezone authority, full baseline, register.
2. **Schema gaps with downstream dependencies first:** unit conversions, tax/payment-method/courier masters, stock transfer and material return, PO amendments and purchase returns.
3. **Production and quality depth:** work centres, routing, production plans, sampling plans, CoA, retest lineage.
4. **Dispatch and commercial depth:** shipment packages, delivery attempts, POD; opportunities and pipeline; marketplace fee rows.
5. **Demo generator extension** through the service layer, then the verification command (extends the existing 34 checks).
6. **End-to-end and concurrency tests** for accounts, production, dispatch→invoice and marketplace.
7. **Profiling and caching**, then staging load tests on dedicated hardware.

Each of steps 2-7 is a multi-day engineering item. They are not done and this document does not claim them.
