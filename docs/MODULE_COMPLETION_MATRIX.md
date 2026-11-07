# Module Completion Matrix and Gap Register

State of the repository after the work on branch `claude/variable-shadowing-fix-4bd715`. Measured, not estimated; regenerate the counts with `backend/scripts/gen_schema_docs.py` and the test register.

States, used strictly:

* **Complete**: implemented *and* proven end-to-end against the whole target specification (backend workflow tests, database-level verification, demo data, browser test).
* **Partial**: real backend logic and tests exist, but required specification items are missing or not proven.
* **Planned**: not implemented.

**No module is Complete.** Several are close; the exact remaining items are listed so nobody has to guess. A table, endpoint, page or seeded record does not make a module Complete.

## 1. Measured facts

| Fact | Value | Source |
|---|---|---|
| Tables / CHECK / UNIQUE / FK / indexes | see header of `DATABASE_SCHEMA_MAP.md` (140 tables at revision 0023, 0 unassigned to a domain) | generated from the migrated database |
| Alembic revisions | 23 (`0001`..`0023`) | `backend/migrations/versions` |
| Database triggers (append-only ledgers, journal balance, frozen certificates) | 15 | `DATA_INTEGRITY.md` |
| Integrity-suite checks | 37 (was 34) | `backend/scripts/check_integrity.py` |
| Demo verification | 100 checks (37 integrity + 63 scenario / reconciliation / permission) | `python -m scripts.demo_data verify` |
| Backend tests | 320 passed, 1 skipped (needs a live RabbitMQ), 0 failed | `TEST_EXECUTION_REGISTER.md` |
| Browser tests | existing suites 16/16, 11/11, 23/23 on this code; new demo journeys 18/18 | `TEST_EXECUTION_REGISTER.md` |

## 2. Module status

| Module | State | Built and proven | Remaining before Complete |
|---|---|---|---|
| Identity, RBAC, audit | Partial | users, roles, permissions, sessions, lockout, append-only audit log, approval limits and delegation, role matrix enforced (verified for the demo roles) | a login-history *table* (logins are audit rows today); employee master; per-user department assignment and approval-authority screens not verified |
| Master data | Partial | units **and conversions** (exact Decimal), categories, items, suppliers, customers, brands, warehouses, locations; **tax classes, payment methods, courier partners** | tax class, payment method and courier are now referenced by items (rate enforced by a DB trigger), payments and shipments (resolved at posting, optional when no master is configured); invoice lines still copy the rate as text by design (a historical document must not change when a class does); item-category hierarchy; employees |
| Inventory | Partial | batches, balances, append-only ledger, reservations (partial consumption for partial dispatch), valuation, stocktakes, opening stock, FEFO, QC-gated availability, concurrency tests | stock-transfer *document* (transfers are idempotent ledger postings with no header record); returning unused material from production to stock |
| Procurement | Partial | PR approve/reject, RFQ, quotation comparison and award, PO approval limits and two-level approval, **PO amendments**, goods receipts with batches, **purchase returns**, supplier invoices (three-way match), debit notes, payments | per-line delivery schedules (one expected date per PO); supplier-performance *history* is a computed report |
| R&D | Partial | projects, formulations (100% rule), immutable approved versions, revisions, trials, observations, four-eyes approval, BOM from an approved version, orders pinned to the version | formula-cost calculation; product-testing beyond trial observations; demo shows the formulation chain on a bulk intermediate only |
| Production | Partial | BOMs, **plans with material and capacity checks**, **work centres**, orders, reservation/issue/stages/completion, wastage, yield, costing, genealogy, utilisation report | routing master (stages are per-order names); labour time; material returns; bulk-into-finished consumption |
| Quality | Partial | specs (versioned), **sampling plans**, inspections, append-only results with retest, release segregation, deviations, CAPA, **frozen Certificates of Analysis**, QC gating of stock | separate inspection-template entity (specs serve as templates); the pre-existing "COA (PDF)" renders live data and is not yet generated from the frozen certificate |
| Dispatch | Partial | order lifecycle, FEFO allocation, pick scans, **partial shipments with one invoice each**, **close-short**, **packages**, **delivery attempts**, **proof of delivery**, courier webhooks (signed, idempotent), returns scoped to a shipment, credit notes | replacement shipments; refund flow (refused with `refund_required`); real courier APIs (fake adapter only) |
| CRM and sales | Partial | leads, sources, **lead conversion with customer matching**, **opportunities with history**, activities, follow-ups, quotations, order conversion; complaints; pipeline summary | customer acceptance is recorded by staff (no portal/e-signature); sales-performance reporting beyond the pipeline summary |
| Accounts | Partial | chart, periods, balanced append-only journals (DB trigger), invoices, credit/debit notes, payments + allocation, bank import and matching, ageing, statements, P&L, balance sheet, GST working summary | **cash-flow statement is not implemented**; GST filing / e-invoicing are not implemented (and are not claimed) |
| E-commerce | Partial | accounts, SKU maps, idempotent imports (JSON/CSV/webhook), exception queue, fulfilment, settlements with fees, posting to the books, returns, inventory-sync *preview* | **no live marketplace adapter** (DEMO accounts only); inventory sync is preview-only; fees are columns of the settlement, not separate rows |
| Maintenance | Partial | equipment, preventive schedules, work orders (verify by a different user), breakdown + downtime, calibration, spare parts (cost now exact Decimal) | spare parts are free-text lines **not linked to inventory** (needs a spares item kind, procurement and accounting treatment) |
| Branding / Meta | Partial | campaigns, content workflow with approval, assets, manual publishing records, metrics, analytics | live publishing does not exist (and is not claimed); asset-to-content attachment |
| Approvals, notifications, documents | Partial | approval policies/records/delegation, in-app notifications with **per-user mute** (urgent alerts cannot be muted), documents with versions and SHA-256, outbox, idempotency, job failures | e-mail/SMS/push channels do not exist; object-storage backend (local disk only) |
| Management dashboard | Partial | one endpoint aggregating from tables; 8 headline figures recomputed independently by `verify` | not every figure is reconciled by an automated check; heavy queries are not cached |
| AI layer | Planned (shelved by decision) | none | out of scope until separately authorised |

## 3. What changed in the system during this work (for reviewers)

Timezone authority (migration 0015) · master completion (0016) · purchase returns and PO amendments (0017) · CRM pipeline (0018) · partial dispatch, packages, delivery attempts and proof of delivery (0019) · sampling plans and certificates (0020) · work centres and production plans (0021) · notification preferences (0022) · demo bookkeeping (0023). Each has its own tests; see `TEST_EXECUTION_REGISTER.md`.

## 4. Defects found and fixed along the way

* Ageing off by one day when the OS date and the database date differed (root cause: `date.today()` vs `current_date`).
* `timestamptz::date` casts in reports and the management dashboard used the database session zone.
* Lead could be marked WON without any customer; unknown `lead_source_id` and orphan activities returned HTTP 500.
* Inspections were opened *before* stock arrived, so the lot size they recorded was 0.
* `capacity_exceeded` error carried a raw date (HTTP 500).
* Spare-part cost was typed as a float.
* A fully received PO could not be re-received after a return (now it can: a return lowers `qty_received`).
* Order status column was too short for `PARTIALLY_DISPATCHED`.

## 5. Not done in this work (explicitly)

Load tests for the new workloads and any staging-hardware run; query-plan analysis and dashboard caching; the full Docker stack run; browser tests that *create* partial shipments, opportunities or plans (the demo journeys read them and drive two actions); UI for PO amendments and for creating a purchase return; linking the new masters into transactions; cash-flow statement; live integrations of every kind.
