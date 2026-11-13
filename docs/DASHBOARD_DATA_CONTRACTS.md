# Dashboard data contracts

What every dashboard reads, how fresh it is, and what makes it refresh. Status of the checklist at the end is stated per item: *verified* means a test or run in this repository proved it; *not re-verified* means it was not re-checked in this pass.

## 1. Principles

* **One source of truth.** Every figure is computed by the backend from PostgreSQL on request. No dashboard holds a hard-coded KPI or a decorative counter; none was found in the audit.
* **Shared definitions.** Receivables, revenue, stock and the like are computed in `analytics/router.py`, `module_dashboards.py`, `accounts/reports.py` and `inventory/*`; the Overview, module dashboards and reports call the same functions or the same SQL shape. `scripts/demo_check.py` holds 39 integrity invariants that tie the pieces together (for example: the receivables control account equals the sum of open sales invoices; stock balances equal the stock ledger; every posted journal balances).
* **Business time is Asia/Kolkata.** Day boundaries come from `business_today()` / `business_date()` in the database and `businessToday()` on the client, never from the server's or browser's zone (`tests/test_business_clock.py`).
* **Aggregate endpoints, not request storms.** A module dashboard is one request; the Overview is a few (`management-summary`, `overview-period`, `overview-trends`).
* **Freshness.** Module dashboards are cached **at most 3 s after a business write** (write-versioned, TTL 15 s, `core/rcache.py`); the cache never serves a figure older than the last write by more than that floor. Critical allocation decisions never use a cache (they lock and read stock rows).

## 2. Dashboards and boards

| Dashboard / board | Endpoint | Reads | Cached | Refreshes on scope |
|---|---|---|---|---|
| Overview (position, KPIs, needs attention) | `/analytics/management-summary`, `/analytics/overview-period`, `/analytics/overview-trends` | sales orders and invoices, ledger, stock, production, QC, dispatch | summary: short | `overview` (almost every event) |
| CRM | `/crm/dashboard`, `/crm/customers-board` | leads, opportunities, quotations, customers | yes | `overview`, `sales` |
| E-commerce | `/ecommerce/dashboard`, `/ecommerce/omnihub`, `/ecommerce/orders-board` | external orders, accounts, mappings, settlements | yes | `overview`, `orders` |
| Purchase | `/procurement/dashboard`, `/procurement/vault`, `/procurement/pr-board`, `/procurement/po-board`, `/procurement/rfq-board`, `/procurement/returns-board` | requests, RFQs, POs, receipts, supplier history | yes | `overview`, `purchase` |
| Inventory | `/inventory/dashboard`, `/inventory/hub` | balances, ledger, batches, valuation, reorder | yes | `overview`, `inventory` |
| Production | `/production/dashboard`, `/production/floor`, `/production/orders-board`, `/production/work-centres-board`, `/production/boms-board`, `/production/wastage-board` | orders, stages, materials, wastage, work centres | yes | `overview`, `production` |
| Quality | `/quality/dashboard`, `/quality/control-chart` | inspections, batches on hold, deviations, readings | yes | `overview`, `quality` |
| Dispatch | `/dispatch/dashboard`, `/dispatch/invoices/{id}/paper` | orders, shipments, delivery attempts, invoices | yes | `overview`, `orders` |
| Accounts | `/accounts/dashboard`, accounts reports | journals, invoices, payments, ageing | yes | `overview`, `accounts` |
| R&D, Maintenance, Meta/Branding | `/rnd/dashboard`, `/maintenance/dashboard`, `/branding/dashboard` | projects and versions, work orders and equipment, campaigns | yes | `overview` (and a manual reload) |
| Growth / annotations | `/analytics/growth`, `/analytics/annotations` | history aggregates | yes | `overview` |
| Notifications / Approvals | `/notifications`, `/approvals/pending` | per-user rows | no | the stream's `notification.new` |

How a screen refreshes: it calls `useLiveRevision(scopes)` and re-runs its fetch when the number changes. Module pages map their module id to the backend's scope names (`dispatch -> orders, accounts`, `procurement -> purchase`, ...). A bug found and fixed in this pass: module dashboards listened for scope names the backend never sent, so only the "overview" events reached them.

## 3. Event to scope contract

The event catalogue in `REALTIME_ARCHITECTURE.md` lists, for every business event, the scopes it refreshes. In short: an order refreshes `overview` and `orders`; dispatch (which posts the invoice and moves stock) refreshes `overview`, `orders`, `accounts`, `inventory`; a payment refreshes `overview` and `accounts`; a goods receipt refreshes `overview`, `purchase`, `inventory`, `quality`; production completion refreshes `overview`, `production`, `inventory`, `quality`; a QC decision refreshes `overview`, `quality`, `inventory`. Only screens listening to those scopes refetch; the rest are left alone.

## 4. Checklist status (Part 5 of the brief)

| # | Requirement | Status |
|---|---|---|
| 1 | Metrics come from real API responses | **Verified** in code audit (no hard-coded values found) and by `demo_check` / run-of-show assertions |
| 2 | Counts reconcile with underlying records | **Verified** for receivables, stock, ledger, invoices, shipments (39 invariants; the run-of-show asserts the Overview receivable tracks open invoices). Not every individual widget was reconciled one by one |
| 3 | Date filters work | **Verified** for the Overview period (`tests/test_overview_period.py`; the run-of-show asserts 90 days >= 30 days) |
| 4 | Custom date ranges | **Verified** for the Overview period and accounts reports; not re-checked on every module dashboard |
| 5 | Timezone is Asia/Kolkata | **Verified** (`tests/test_business_clock.py`; six `::date` casts were found and replaced) |
| 6 | Charts use real historical data | **Verified** by code audit for the module dashboards and trends; the demo seed has 13 months of history |
| 7 | Charts update after events | **Verified** for the Overview, module dashboards and the lists (browser run: a second session sees changes with no reload); chart-level checks not individually scripted |
| 8-9 | Tables are current; search and filter | **Verified** by the browser runs for the lists exercised; not every list |
| 10 | Pagination | Existing list behaviour; not re-verified in this pass |
| 11 | Drill-down opens the right record | **Verified** for the activity timeline links (`?open=<id>`); other drill-downs not re-verified here |
| 12 | Permission restrictions | **Verified** server-side (every endpoint and every event is permission-checked; `tests/test_permissions.py`, `tests/test_activity.py`) |
| 13-15 | Empty, error, loading states | **Not re-verified** in this pass (they exist in the shared components; the new pages implement all three) |

Anything marked *not re-verified* should be treated as unchecked, not as passing.

## 4a. Known gaps

* There is no single machine-readable registry of every KPI definition; the shared definitions are shared by code, not declared in one place.
* A few dashboards (R&D, Maintenance, Meta) refresh on the Overview scope and on reload, not on their own business events (they have none yet).
