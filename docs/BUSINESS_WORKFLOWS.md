# Business Workflows

For each workflow: the chain as implemented (HTTP routes under `/api`), what proves it, and what is missing. "Proven by" names real tests; `demo` means the step is seeded through the real services by `python -m scripts.demo_data init` and checked by `verify`.

## 1. Procurement to supplier payment
`purchase-requests` (create, submit, approve/reject) -> `rfqs` (create, send, `quotations`, `comparison`, `award` -> PO) -> `purchase-orders` (create, submit, approve with amount limits and optional two-level approval; `amendments` request + approve by a different person) -> `goods-receipts` (batch + quarantine + ledger) -> `quality/inspections` (auto-created from the spec; sampling plan sets sample size; inspector records, a *different* manager decides) -> stock released -> `accounts/supplier-invoices` (three-way match against received qty) -> `accounts/payments` (PAID, allocations) -> `bank-accounts/.../statements` + match. Exceptions: `goods-receipts/{id}/returns` (stock back to supplier, accrual reversed), `supplier-invoices/{id}/debit-notes`.
Proven by: `test_inventory_procurement`, `test_purchase_gaps`, `test_procurement_returns_amendments` (incl. live concurrency), `test_accounts*`; demo steps `procurement_baseline`, `procurement_exceptions`, `payables`, `banking`.
Missing: delivery schedules per PO line (only one `expected_date` per PO); supplier-performance history is a computed report, not a stored record.

## 2. R&D to manufacturing
`rnd/projects` -> `rnd/formulations` (ingredients must total exactly 100% of the batch) -> `versions/{id}/trials` + `trials/{id}/observations` (stability etc.) -> `submit` -> `approve` (four-eyes) -> `production/boms` from the approved version -> production order pinned to `bom_id` **and** `formulation_version_id`. A revision (`formulations/{id}/versions`) creates v2 while v1 stays on record and keeps its orders.
Proven by: `test_rnd_quality`, `test_production`; demo step `rnd_to_production` (v1 order completed, v2 order in progress; verify checks the pins differ).
Missing: formula-cost calculation endpoint is not part of this work; product-testing beyond trial observations is not a separate entity. **Note:** in the demo the formulation chain is shown on the bulk intermediate `BLK-OUD-WASH`; finished products use manual BOMs, and bulk-to-finished consumption is not modelled.

## 3. Production to finished inventory
`production/plans` (material check, capacity check, confirm, convert line) -> `orders` (create, release = reserve materials, issue, `stages/{n}/start|complete`, complete with actual qty + wastage) -> output batch (QUARANTINE), valuation and WIP journals -> FINISHED inspection -> release -> available stock. Yield is computed, a low yield requires wastage with reasons, consumption records exact lots (genealogy: `trace_back`/`where_used`).
Proven by: `test_production*`, `test_production_plans`; demo `production_planning`, `production_finished_goods`.
Missing: returning unused issued material to stock; labour time (machine/stage time is recorded, labour hours are not).

## 4. Quality management
Specs (versioned) + sampling plans -> inspection -> results (judged server-side, append-only, retest supersedes but keeps history) -> decision RELEASE/REJECT/HOLD (reason required, tester cannot release) -> batch QC status drives available stock -> deviation -> CAPA (owner completes, QA verifies) -> Certificate of Analysis (frozen snapshot, revocable only).
Proven by: `test_rnd_quality`, `test_quality_sampling_coa`; demo shows a passed, a rejected (with deviation, CAPA, return), a retested, a quarantined batch.

## 5. CRM to customer order
`crm/leads` (+source) -> status -> `leads/{id}/convert` (matches an existing customer by e-mail/phone or creates the single master customer) -> `opportunities` (stage machine, history) -> `sales/quotations` (send/accept) -> opportunity WON (requires an accepted quotation) -> quotation `convert` -> sales order -> fulfilment.
Proven by: `test_crm_pipeline` (full chain in one test, DB-verified single customer id across lead/opportunity/quotation/order), demo `sales_crm`.
Missing: the customer's acceptance is recorded by staff (status ACCEPTED); there is no customer portal or e-signature.

## 6. Order to cash
order -> confirm (credit-limit check) -> allocate (reservations of released, unexpired stock) -> optional pick scans -> pack -> **dispatch (one atomic posting per shipment; partial shipments allowed)** -> invoice per shipment -> courier booking (outside the transaction) -> tracking events by signed webhook -> delivery attempts / proof of delivery -> payment + allocation -> bank match.
Proven by: `test_dispatch`, `test_dispatch_concurrency`, `test_partial_dispatch` (two shipments/two invoices summing exactly, rounding, close-short, per-shipment returns, race on one order); demo `dispatch_and_receivables`.
Missing: real courier APIs (only an in-process fake adapter exists; other couriers are MANUAL).

## 7. Returns and credit notes
`dispatch/returns` (lines of ONE shipment) -> `receive` (stock back, blocked) -> `decide` RESTOCK (unblock + COGS reversal) or REJECT -> credit note against that shipment's invoice -> receivable reduced. Marketplace returns follow `ecommerce/returns`.
Missing: cash refund flow (an over-settled invoice returns `refund_required`, explicitly not implemented).

## 8. Equipment maintenance
equipment register -> preventive `schedules` -> `schedules/generate` creates work orders -> assign -> start -> checklist -> spare parts -> complete -> verify; breakdown reports open downtime that closes on completion; calibration records.
Proven by: `test_h5_modules`, `test_module_extras`; demo `maintenance`. Missing: spare-part consumption is recorded on the work order but does not yet post a stock issue to Inventory.

## 9. Marketplace lifecycle
account (DEMO) -> SKU mapping -> order import (JSON/CSV/webhook; idempotent by external id) -> exception queue for unmapped SKUs -> sales order -> normal fulfilment -> settlement (gross, commission, fees) -> received/reconciled -> posted to the books; returns/refunds.
Proven by: `test_h5_modules`, `test_module_extras`; demo `marketplaces` (Amazon, Flipkart, Myntra, Nykaa as clearly labelled DEMO accounts). **No live marketplace API is implemented or contacted.**

## 10. Branding and marketing
campaign -> content -> asset -> submit -> approve/reject -> manual publish record (`mode=MANUAL`) -> metrics. **No Meta API integration exists**; nothing is posted by the system.

## 11. Management view
`GET /analytics/management-summary` aggregates from the tables; `scripts/demo/verify.py` recomputes revenue, receivable, payable, inventory value, orders by status, below-reorder items, open deviations and late deliveries independently and compares.
