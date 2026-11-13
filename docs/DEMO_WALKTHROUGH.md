# Demo Walkthrough (25 minutes)

A client demonstration of Mintree Factory OS from raw-material purchase to cash received, then CRM, marketplaces, marketing and management reporting. **Every reference below is a record that exists in the seeded demo database** (checked against `mintree_demo` after `reset` + `init`). Record numbers are stable because the seeder is deterministic; only dates move, relative to the day you seed.

## Before the demo

```bash
cd backend
export MINTREE_ENVIRONMENT=demo
export MINTREE_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5433/mintree_demo     # the name must contain the word "demo"
python -m scripts.demo_data reset --confirm-reset-demo-database mintree_demo                # only on a database positively identified as the demo one
python -m scripts.demo_data init                                                            # ≈ 3.5 minutes, 33 steps, then 117 verification checks
python -m scripts.demo_data verify                                                          # re-run any time: integrity, reconciliation, coverage, permissions
python -m scripts.demo_data report                                                          # records per module against the targets
uvicorn app.main:app --port 8001                                                            # and the frontend with BACKEND_URL=http://127.0.0.1:8001
```

Redis is optional but recommended (cache and permission-epoch bumps fall back to a 30-second TTL without it). The demo needs no message broker; the notifications you see were written by the services themselves.

## Demo users

Password for every demo user: `Mintree-Demo#2026`. Permissions are never relaxed in demo mode; `verify` proves restricted roles get HTTP 403 on other departments' data.

| Login | Role | Use it to show |
|---|---|---|
| `demo.mgmt@example.com` | Management / Owner | **Start here.** Read-only company dashboards |
| `demo.admin@example.com` | System Administrator | everything; approvals; users and roles |
| `demo.buyer@example.com` | Purchase Manager | requests, RFQs, orders, receipts, supplier returns |
| `demo.stores@example.com` | Stores Manager | stock, receipts, counts, transfers |
| `demo.qa@example.com`, `demo.qa2@example.com` | Quality Manager, Quality Head | inspections, release, deviations, CAPA, certificates (the Head closes what the Manager raises) |
| `demo.qi@example.com` | QC Inspector | records results only; cannot release |
| `demo.pm@example.com`, `demo.pm2@example.com` | Production Manager, Head | BOMs, plans, orders (the Head approves BOMs) |
| `demo.op@example.com` | Production Operator | runs stages only |
| `demo.rnd@example.com`, `demo.rnd2@example.com` | R&D Manager, Head | formulations (the Head approves) |
| `demo.disp@example.com` | Dispatch Manager | orders, shipments, returns, marketplace |
| `demo.sales@example.com` | Sales Executive | leads, opportunities, quotations, contacts, complaints |
| `demo.acct@example.com` | Accounts Manager | invoices, payments, bank, ledgers, statements |
| `demo.mkt@example.com` | Marketing Manager | campaigns and content |
| `demo.maint@example.com` | Maintenance Manager | equipment, work orders, calibration |
| `demo.biz@example.com` | Business Administrator | masters, users (read), audit (read) |

## What is real and what is simulated

| Area | Status |
|---|---|
| Purchase, inventory, QC, production, dispatch, invoicing, payments, ledger, approvals, audit | **Real application logic.** Every record is created through the same HTTP API, validation, permissions and ledger postings as production. |
| Marketplace accounts (Amazon, Flipkart, Myntra, Nykaa) | **DEMO.** Orders are controlled sample fixtures imported through the real ingestion path. No marketplace API is contacted. Accounts are named `[DEMO] … (sample data, no live API)`. |
| Courier | Only an in-process fake adapter (`integration_mode=DEMO`). AWBs look like `FAKE0000000001`. No courier is contacted. |
| Meta (Facebook / Instagram / Ads) | **Sandbox.** Connections are `SANDBOX`; publishing records are made manually; metrics are simulated and labelled so. Nothing is connected to Meta. |
| GST filing, e-invoicing, government portals, bank feeds | **Not implemented.** GST fields and summaries exist. |
| Uploaded documents | Small labelled demo PDFs, uploaded through the real document endpoint (allow-list, size limit, SHA-256, audit). They are not renderings of the records. |

Say this out loud in the demo. Do not call any of the simulated integrations live.

## Walkthrough

### 1. Management view (2 min), `demo.mgmt`
Open **Overview** (`/`) and then **Management → Summary** (`/m/analytics`). Show the position strip (net revenue, receivables, payables, cash, inventory value), then the departmental sections. Every figure is computed from the ledgers and tables; none is stored. Change the period (30 days, 90 days, this FY) and note the figures move together.

### 2. Open a product and trace it back (3 min), `demo.admin`
Product: **NOURISHING Oud Body Wash 200 ml** (`FG-BW-OUD-200`). *Master data → Items*.
- **Raw materials and supplier.** The Oud fragrance oil (`RM-OUD`) came in on **PO/26/000002** from Coastal Oils & Butters LLP, received as batch **B260000006**. Open it under *Purchase → Purchase orders*, then its goods receipt and the incoming inspection.
- **Procurement process.** *Purchase → Requests* (12 requests in every state: draft, awaiting approval, approved, rejected) and *Purchase → RFQs* (12, with supplier quotations compared and awarded to orders). **PO/26/000005** is waiting for approval, which is a good moment to approve it live from *Approvals*.

### 3. The approved formulation and the BOM (2 min), `demo.rnd`
*R&D → Formulations* → **F-DM-OUD-WASH**. Version 1 is `OBSOLETE`, **version 2 is `APPROVED`** (glycerin added, oud reduced); neither was overwritten. Show its trials and stability observations, then *Production → BOMs*: the BOM pins the exact approved version. Twelve other formulations show draft, trial passed and awaiting approval, approved, revised and "failed trial, reformulated" states.

### 4. Production and consumption (3 min), `demo.pm`
*Production → Orders*: **PRD/26/000001** (bulk Oud base, version 1, completed) and **PRD/26/000002** (version 2, in progress, stage 2 running). Open the completed order: materials issued from released batches, consumption, wastage and yield, and the output batch. *Production → Plans* shows plans with a material shortage, capacity overload accepted with a reason, and a converted line.

### 5. Quality control and release (2 min), `demo.qa`
*Quality → Inspections*: **QC/26/000015** (finished Oud body wash, batch **B260000016**, released) and **QC/26/000059** (batch **B260000099**). Show results against acceptance limits, the decision, the certificate of analysis (12 issued), and a rejected lot. Then *Quality → Deviations* (12, from open to closed) and a CAPA. **A held or rejected batch cannot be issued or dispatched**; show the refusal.

### 6. Customer order to cash (4 min), `demo.disp` then `demo.acct`
- **Sales order SO/26/000001** (Studio Bloom Salon, Kochi): allocated, picked, packed, dispatched on **AWB FAKE0000000001**, invoiced as **SI/2026-27/000001** (₹38,649.01), paid in full. Open the shipment's courier event history.
- **Partial dispatch.** **SO/26/000125** (Chandan Skin Clinic, Bengaluru) is partially dispatched; the remainder dispatches later and the invoice follows.
- **Receivables.** *Accounts → Receivables ageing*: **SI/2026-27/000060** (Himalaya Cosmetics Traders, Chennai) is the oldest open invoice, ₹94,653.89 outstanding since 18 July.
- **Return and credit note.** **RET/26/000001** → **CN/2026-27/000001**: received, assessed, restocked or quarantined, credited, and the receivable reduced.

### 7. The books (2 min), `demo.acct`
*Accounts → Journals*, *Trial balance*, *Profit & loss*, *Balance sheet* and *Cash flow*. 481 posted entries; every one balances, and the balance sheet states "Balanced: Yes". The receivables control account equals the open sales invoices; payables equal the open supplier invoices; inventory accounts equal stock valuation plus work in progress. `verify` proves each of these.

### 8. CRM and customer history (2 min), `demo.sales`
*CRM & Sales → Leads* and *Opportunities* (74 leads, 39 opportunities across every stage) or the **Pipeline board** (`/crm/board`): drag a deal between stages, or use the "Move to" menu. Open a converted lead: it reused an existing customer rather than creating a duplicate. *Customer contacts* (12 named contacts), *Complaints* (14), *Quotations* → accepted quotation → sales order.

### 9. Marketplaces (2 min), `demo.disp`
*E-commerce → Orders*: **DEMO-AMA-1001** and 132 more across the four DEMO accounts, imported, SKU-mapped, duplicate-protected, allocated and dispatched. Show the exception queue (an unmapped SKU and a failed synchronisation), a settlement reconciled to the bank, and a marketplace return. **Say plainly that these are controlled demo fixtures.**

### 10. Meta and brand content (1 min), `demo.mkt`
*Meta & Branding → Campaigns*: **Oud Body Wash launch (sandbox)** (done), others running, paused and planned. Content in draft, in review, approved (by someone other than its author), scheduled, published (a manual publishing record) and rejected. Every metric is labelled simulated.

### 11. Maintenance (1 min), `demo.maint`
*Maintenance → Work orders*: **MWO/26/000001** (closed preventive service), **MWO/26/000002** (assigned). Twelve machines, preventive schedules with overdue items, breakdowns with downtime and spare parts, calibration history.

### 12. Document centre (1 min), `demo.admin`
*Document centre*: 14 documents, e.g. **#1 "Purchase order PO/26/000001: signed copy"**, each linked to its record, three with a second version. Upload a file, download it, open its history.

### 13. Close (1 min), `demo.mgmt`
Return to the Management summary. Show *Approvals* and *Notifications*. State what is not built (see below).

### Live activity and the Demo Control Centre (5 minutes), `demo.admin` plus a second browser as `demo.disp`
1. In a second window sign in as **demo.disp** and open **Live activity** (sidebar). The top bar says **Live**.
2. In the first window open the **Demo mode** pill, then **Open the Demo Control Centre**. Press **New B2B order**. Within a few seconds the second window shows *New customer order SO/... (Demo)* with no refresh; open the arrow to land on the order.
3. Press **Create draft invoice** and point out that the Sales invoices list did not change: a draft posts nothing. Press **Post invoice**: the goods ship and the gapless invoice appears. **Generate invoice PDF** produces it.
4. Press **Record and allocate payment**, then open Accounts: receivables fall and the invoice is PARTIAL or PAID.
5. For the full story press **Start scenario** (use x3 if short of time): an order arrives, stock is short, a purchase request is raised and approved, materials arrive and pass QC, production runs, QC releases the batch, the order is allocated, invoiced, dispatched, delivered and paid, and the management dashboard shows it. Pause and resume it to talk over any step. It stops with a plain reason if anything cannot run.
6. Failure paths worth showing: **Fail inspection** (the batch goes on HOLD and leaves available stock; **Release a held batch** re-tests it), **Failed import** then **Retry failed import**, **Duplicate delivery** (one internal order), **Delivery exception**.

Everything created is labelled **Demo**; the courier, the supplier-delay message and instrument readings are labelled **Simulated**. See `DEMO_CONTROL_CENTRE.md`.

## Known limits to state honestly

- Notifications currently cover purchase approvals and amendments, RFQ quotations, goods receipts, follow-ups, delivery failures and failed process checks. Alerts such as low stock, QC hold, production delay, invoice overdue, payment received, maintenance due and marketplace sync failure appear as exception lists and dashboards, but **do not generate notifications yet**.
- Maintenance spare parts are recorded against work orders with a cost, but **do not consume inventory stock** yet.
- Accounts has Journals, Trial balance, Profit & loss, Balance sheet, Cash flow and both ageing reports; a per-account **general-ledger drill-down exists in the API only** (`/api/accounts/reports/ledger/{code}`), not as a screen.
- No live marketplace, courier, Meta, GST or e-invoice integration exists.
- Some secondary tables are small: purchase returns (5), debit notes (1), shipment packages (2), proofs of delivery (1).
