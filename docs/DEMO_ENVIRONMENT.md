# Demo Environment

A complete, connected manufacturing story you can click through: procurement, QC, R&D, production, dispatch, invoicing, payment, CRM, marketplaces, maintenance and marketing, with realistic exceptions. **Every business record is created by calling the real application** (the same HTTP API, validation, permissions, approvals, ledger postings, audit rows and outbox events as production). Only bootstrap master data (users, suppliers, customers, items) is written directly, as the base seed does.

## Commands

```bash
cd backend
export MINTREE_ENVIRONMENT=demo
export MINTREE_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5433/mintree_demo   # the database name must contain the word "demo"

python -m scripts.demo_data reset --confirm-reset-demo-database mintree_demo   # wipes ONLY a demo database, rebuilds schema (Alembic) + base seed
python -m scripts.demo_data init       # seeds all stories; safe to run again (completed steps are skipped)
python -m scripts.demo_data verify     # integrity suite + scenario checks + dashboard reconciliation + role enforcement
python -m scripts.demo_data status     # which steps are complete
```

Then start the API/web as usual against the same `MINTREE_DATABASE_URL`. `init` takes about 40 seconds.

## Safety rules (enforced in `backend/scripts/demo/guard.py`, tested in `tests/test_demo_system.py`)

| Command | Refused unless |
|---|---|
| `init`, `verify`, `status` | `MINTREE_ENVIRONMENT` is exactly `demo` (production is named in the refusal) **and** the database name contains the whole word `demo` (`mintree_demo`, `demo`, `x-demo-2`; not `mintree`, `mintree_test`, `mintree_demonstration`, `postgres`) |
| `reset` | everything above, **plus** the database name is typed again with `--confirm-reset-demo-database`, **plus** the host is local (override: `--allow-remote-host`), **plus** the database carries the demo marker or is empty. A demo-named database holding other data without the marker is never wiped |

No single environment variable can authorise a destructive command. The shared test database, development databases and production fail the first check. Demo data is labelled: users are `demo.*@example.com`, supplier/customer codes start with `DM-`, marketplace accounts are named `[DEMO] ... (sample data, no live API)`, and `demo_seed_steps` records what ran.

## Idempotency and determinism

* Each scenario is a **step**; completed steps are recorded in `demo_seed_steps` (migration 0023) and skipped on re-run. Verified: after a second `init` every business table has identical row counts (`tests/test_demo_system.py`; only `login` audit rows grow, because each run really logs in).
* Idempotency keys, delivery-note numbers, lot numbers and external order ids are deterministic. Amounts and quantities are fixed; only *dates* move, relative to today.
* A step that fails half-way leaves its partial records (the API has no cross-request transaction). The documented recovery is `reset` then `init`. This is a deliberate trade-off: the alternative is bypassing the services.

## Sign-in

Password for every demo user: `Mintree-Demo#2026` (documented demo credential; it exists only in demo databases).

| User | Role | Sees / does |
|---|---|---|
| demo.admin | System Administrator | everything; approves POs, amendments, content |
| demo.biz | Business Administrator | masters, users (read), audit (read) |
| demo.buyer | Purchase Manager | PR/RFQ/PO/receipt, supplier master, purchase returns |
| demo.stores | Stores Manager | stock, receipts, returns to supplier, transfers |
| demo.qa / demo.qa2 | Quality Manager / Quality Head | inspections, release, deviations, CAPA, certificates |
| demo.qi | QC Inspector | records inspection results only (cannot release) |
| demo.pm / demo.pm2 | Production Manager / Head | BOMs, plans, orders (pm2 approves BOMs) |
| demo.op | Production Operator | runs stages only |
| demo.rnd / demo.rnd2 | R&D Manager / Head | formulations (rnd2 approves) |
| demo.disp | Dispatch Manager | orders, shipments, returns, marketplace, pricing |
| demo.sales | Sales Executive | leads, opportunities, quotations, follow-ups |
| demo.acct | Accounts Manager | invoices, payments, bank, reports |
| demo.mkt | Marketing Manager | campaigns, content, metrics |
| demo.maint | Maintenance Manager | equipment, work orders |
| demo.mgmt | Management / Owner | read-only company-wide dashboards |

Permission enforcement is never relaxed in demo mode; `verify` proves restricted roles get HTTP 403 on other departments' data.

## How the past is created

History (a 45-day-old overdue invoice, a 9-day-old shipment, a PO expected 5 days ago) comes from running the real services with the application's business clock moved back (`Ctx.days_ago`). Business dates (invoice, entry, planned, expiry dates) therefore look lived-in. Honest caveats: server-side `now()` columns (`created_at`, audit timestamps) are real time, and one demo fixture back-dates `shipments.created_at` by SQL for the "delayed shipment" exception because that column is set by the database clock. A third fixture spreads the completion times of the five seeded production orders over five weeks (`production_orders.completed_at` is also a server-clock column), so the Overview's weekly output chart has genuine history instead of one point. All three are visible in `backend/scripts/demo/steps.py`.

## Background workers

The demo needs no broker. Outbox events written by the postings simply wait for a publisher; `verify` reports their number as a note (and still fails if any is dead). Start RabbitMQ and `app.workers.outbox_publisher` to deliver them (notifications from consumers appear then).

## Integration honesty

* Marketplace accounts (Amazon, Flipkart, Myntra, Nykaa) are **DEMO accounts with sample orders**; no marketplace API is contacted and there are no credentials.
* The only courier adapter is an in-process fake (`integration_mode=DEMO`); other couriers are `MANUAL`.
* Marketing posts are recorded **manually** (`mode=MANUAL`); there is no Meta integration and no social-account credentials.
* Every demo item uses the 18% cosmetics tax class; payments and shipments reference their payment-method and courier masters.
* GST fields and summaries exist; **no GST filing, e-invoicing or government integration** is implemented.

## At-scale demo (added in the performance & completeness milestone)
`scripts/demo/scale.py` adds six steps after the hand-written scenarios; the whole demo is now 21 steps and ≈ 100 s from an empty demo database. Volumes and status mix are in `DEMO_DATA_COVERAGE.md` (130+ sales orders on top of 126 marketplace orders, 125 leads + opportunities, 460 journal entries, 460 ledger movements, 140 shipments, 30 Meta records). Commands:

| Purpose | Command |
|---|---|
| Initialise / seed (idempotent) | `python -m scripts.demo_data init` |
| Verify (integrity + reconciliation + coverage + permissions, 117 checks) | `python -m scripts.demo_data verify` |
| Counts per module vs targets | `python -m scripts.demo_data report` (`--markdown` for a table) |
| Which steps are done | `python -m scripts.demo_data status` |
| Reset (destructive, guarded: environment must be `demo`, database name must contain "demo", host local, the exact name typed again, marker row present) | `python -m scripts.demo_data reset --confirm-reset-demo-database <name>` |
| Quick, smaller run for tests | `MINTREE_DEMO_SCALE=0.25 …` |

Safety: the seeder refuses any database that is not a marked demo database; development, test, demo and production data never share a database. Demo credentials are the single documented demo password printed by `init` for 17 role-specific users (`demo.admin`, `demo.mgmt`, `demo.buyer`, `demo.stores`, `demo.pm`, `demo.qa`, `demo.disp`, `demo.acct`, `demo.sales`, `demo.mkt`, `demo.maint`, …); users see exactly what their roles permit (verified by the permission checks in `verify`). No production secret is stored anywhere in the repository.

Known limitation: if a step fails part-way, its deterministic idempotency keys collide with the partial work on re-run (the system correctly refuses a different payload under the same key). Reset the demo database and re-run; steps are small and the full build takes under two minutes.

Two documented fixtures write timestamps the services stamp from the server clock: production-order completion dates are spread over ~100 days so the weekly output chart has a history, and one shipment's creation time is set to nine days ago to demonstrate the "delayed shipment" exception.
