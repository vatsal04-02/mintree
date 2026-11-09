# Demo Data Coverage

Everything below is **measured from the tables of a freshly seeded demo database** (`python -m scripts.demo_data report`), not from the seeder's own counters. The database is built only through the real API (permissions, validation, ledger, journals, audit, outbox); the sole direct writes are users and base master records (as in the earlier milestone) and two documented fixtures (completion-date spreading for production orders, courier-delay age for one shipment).

Reproduce from an empty demo database (name must contain "demo"; guarded):
```bash
MINTREE_ENVIRONMENT=demo MINTREE_DATABASE_URL=postgresql+psycopg://…/mintree_demo python -m scripts.demo_data reset --confirm-reset-demo-database mintree_demo
MINTREE_ENVIRONMENT=demo MINTREE_DATABASE_URL=… python -m scripts.demo_data init      # ≈ 100 s; 21 steps; then 117 verification checks
MINTREE_ENVIRONMENT=demo MINTREE_DATABASE_URL=… python -m scripts.demo_data report    # counts vs targets; exit 1 if a target is missed
```
`MINTREE_DEMO_SCALE=0.25` shrinks the volumes for quick tests (the report/coverage checks are enforced only at scale ≥ 1). A second `init` runs 0 steps and creates nothing (tested in `tests/test_demo_system.py`).

## Counts by module (full scale)
| Module | Records | Count | Target | Met |
|---|---|---:|---:|---|
| Sales | Sales orders (all channels) | 262 | 100 | yes |
| Sales | of which direct / B2B / distributor / brand / manual | 136 | 100 | yes |
| Sales | Sales quotations | 20 |  |  |
| E-commerce | Marketplace orders imported | 133 | 100 | yes |
| E-commerce | Marketplace settlements | 9 |  |  |
| E-commerce | Marketplace returns | 13 |  |  |
| CRM | Leads | 74 |  |  |
| CRM | Opportunities | 39 |  |  |
| CRM | Leads + opportunities | 113 | 100 | yes |
| CRM | Activities and follow-ups | 105 |  |  |
| Inventory | Stock ledger movements | 474 | 100 | yes |
| Inventory | Batches / lots | 110 | 100 | yes |
| Procurement | Purchase orders | 23 |  |  |
| Procurement | Goods receipts | 22 |  |  |
| Production | Production orders | 25 |  |  |
| Production | Production records (orders + stages + material lines + consumptions + wastage) | 294 | 100 | yes |
| Quality | Inspections | 70 |  |  |
| Quality | Inspection results | 139 | 100 | yes |
| Dispatch | Shipments | 141 | 100 | yes |
| Dispatch | Courier tracking events | 442 |  |  |
| Accounts | Sales invoices | 141 |  |  |
| Accounts | Supplier invoices | 16 |  |  |
| Accounts | Payments (received + paid) | 74 |  |  |
| Accounts | Financial transactions (journal entries) | 462 | 100 | yes |
| Masters | Master records (items, suppliers, customers, warehouses, locations, brands) | 117 | 100 | yes |
| Meta (simulated) | Meta connections (sandbox) | 4 |  |  |
| Meta (simulated) | Campaigns | 16 |  |  |
| Meta (simulated) | Content items | 30 |  |  |
| Meta (simulated) | Campaign + content records | 46 | 30 | yes |
| Meta (simulated) | Simulated daily metrics | 288 |  |  |
| Maintenance | Equipment / work orders | 7 |  |  |
| Audit | Audit log rows | 2554 |  |  |

## Distributions (full scale)
* **Sales orders by status (non-marketplace, 150):** delivered 71 · draft 16 · dispatched 15 · confirmed 8 · packed 7 · allocated 7 · cancelled 6 · partially dispatched 6. Returns with credit notes sit inside delivered orders. Draft orders include 7 deliberate drafts and the orders created by converting accepted CRM quotations, which wait for confirmation.
* **Sales orders by channel:** marketplace 126 · B2B 56 · distributor 37 · brand/online 23 · manual/retail 20.
* **Sales invoices:** paid 56 · partially paid 15 · open 70 (a large share past 30-day terms, so ageing and the overdue list are populated).
* **Marketplace external orders:** 126 became sales orders; 7 stay in the exception queue (unmapped SKU, the failed-synchronisation demo); the ones for Amazon and Flipkart were mapped and retried successfully; duplicates re-sent by the platform were detected and ignored (asserted during seeding).
* **CRM opportunities by stage:** lost 13 · won 9 · negotiation 7 · proposal 5 · qualified 3 · prospecting 2 (leads in every status, converted leads linked to master customers, quotations → orders).
* **Meta sandbox:** 4 SANDBOX connections (business, page, Instagram, ad account), 16 campaigns (running / paused / done / planned, objectives awareness, traffic, engagement, leads, sales), 30 content items (published 8, scheduled 4, approved 3, in review 3, rejected 2, draft 5 …), 288 simulated daily metrics. **Every one is labelled simulated; nothing is connected to Meta.**

## Connected stories (all present, all navigable by links between records)
1 procurement → receipt → QC → stock → production → finished QC → order → dispatch → invoice → payment (several complete chains) · 2 ongoing production (2 orders in progress with WIP in the books) · 3 shortage → purchase request → PO · 4 QC rejection (rejected batches at incoming and finished QC) · 5 delayed dispatch (shipment stuck 9 days; failed delivery) · 6 customer return → restock/reject decision → credit note → reduced payment · 7 marketplace settlement received, posted, reconciled to the bank (and later periods left for the accountant) · 8 maintenance breakdown and resolution (earlier milestone) · 9 CRM lead → customer → quotation → order · 10 Meta campaign with content approval (author ≠ approver) and manual publishing record.

## Reconciliation proven by `verify`
AR control account = open sales invoices; AP control account = open supplier invoices; inventory accounts = stock valuation + work in progress; every journal balances; every stock balance equals its ledger; plus 30+ further integrity checks and role-permission checks (117 in total).


## Depth top-up (2026-11-09)

Twelve further steps (`scripts/demo/depth.py`, 33 steps in all, ≈ 3.5 minutes from an empty demo database) bring the thin modules up to the 12-record minimum, all through the real API: 9 purchase requests, 11 RFQs with quotations (4 awarded to orders), 4 more QC-rejected receipts returned to the supplier, 11 formulations and projects in every state (draft, awaiting approval, approved, revised, reformulated after a failed trial), 8 more BOMs and 4 plans, 11 deviations through raised → investigated → CAPA → verified → closed (closed by a different person, as the product requires), 11 certificates of analysis, 6 complaints, 8 machines with calibrations, preventive schedules and 9 breakdowns, customer contacts brought up to 12, 4 stock counts, and 14 documents uploaded and linked (3 with a second version, each downloaded back and checked).

Re-measure with `python -m scripts.demo_data report`; the remaining small tables are listed in `MODULE_COMPLETION_MATRIX.md`.