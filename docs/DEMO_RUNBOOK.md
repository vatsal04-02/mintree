# Demo runbook

How to prepare, run, and recover the Mintree Factory OS stakeholder demo. Everything here is a command or a click you can do from this file alone.

The demo database is a real database filled **through the real application** (same API, validation, permissions, approvals, ledger postings, audit rows and outbox events as production). Nothing on screen is invented: anything simulated is labelled **Simulated** and is never stored or used in a calculation.

---

## 1. One-time setup (about 10 minutes, plus the optional full-scale history)

Prerequisites: Docker Desktop, Python 3.12, Node 20+, `make` (on Windows: Git Bash, or run the commands in the `Makefile` by hand).

```bash
python -m venv backend/.venv && backend/.venv/Scripts/pip install -r backend/requirements.lock.txt   # bin/ instead of Scripts/ on macOS/Linux
cd frontend && npm ci && cd ..
make up            # Postgres 16, Redis, RabbitMQ in Docker; creates mintree_demo and mintree_test
make seed          # ~2 min: schema + base data + 36 story steps (suppliers, POs, QC, production, dispatch, invoices, payments, CRM, marketplaces, maintenance, marketing)
make seed-scale    # optional, hours, resumable: full-scale history (see "Seed spec")
cd backend && python -m scripts.demo_ops backdate && cd ..     # once after seed-scale: server timestamps follow the business dates, so charts show 12 months
cd frontend && BACKEND_URL=http://127.0.0.1:8101 NEXT_DIST_DIR=.next-e2e-demo npm run build && cd ..
```

Sign in (every demo user shares one documented password, which exists **only** in demo databases): `demo.admin@example.com` / `Mintree-Demo#2026`. `demo.admin` is presented as **Kanika, Founder**. Other roles: buyer, stores, qa, qi, pm, op, rnd, disp, sales, acct, mkt, maint, mgmt (all `demo.<role>@example.com`).

## 2. Before every demo (5 minutes)

```bash
make demo-up       # API (2 workers) :8101, outbox publisher, Live Mode worker, web app :3101   (logs in ./logs)
make demo-check    # the red/green pre-flight; must end GREEN
make demo-snapshot # freeze the golden state (only after a GREEN check)
```

`make demo-check` runs, in about a minute: environment and database-name guard; Postgres / Redis / RabbitMQ; `/api/ready`; migrations at head; seed present and at volume; the demo preconditions (below); warm-up; **the whole run-of-show with database assertions**; the 30+ integrity invariants; integrations reachable or stubbed; the snapshot, and (unless `--no-reset`) a restore so you start from the golden state.

Dates: seeded history is anchored to the day it was seeded. If you run the demo on a later day, `make demo-reset` (or `python -m scripts.demo_ops anchor`) shifts every business date forward so the newest data is **today** and the 12-month history still ends today. "Past planned date", expiry buckets and overdue counts stay meaningful.

### Preconditions the seed guarantees (so no step starts from an empty screen)
| Needed by | Guaranteed by |
|---|---|
| a. "Needs attention" never empty | held / quarantined batches, a failed delivery, a delayed order (story seed) + `demo_prep` |
| b. a low-stock raw material | reorder levels on raw materials; the story consumes below them |
| c. an approved BOM with stock for it | story seed + bulk opening stock |
| d. a quarantined / failed batch | `demo_prep` holds one finished-goods batch in QC |
| e. a marketplace account with SKU mappings and released finished-goods stock | story seed + bulk opening stock |
| f. three deals in Negotiation with an **accepted quotation** (Won is refused without one) | `demo_prep` ("Demo deal A/B/C") |
| g. an open invoice | step e raises one; hundreds exist in the seed |
| h. something waiting for approval, and an unread alert | `demo_prep` raises a PO for approval |

## 3. Run of show

Total budget **5 minutes**. Each step lists what to do, what you should see, what is true in the database, its time budget, and what to click if it goes wrong. The same steps are executable: `make test-demo` (API + database assertions, ~1 minute) and `make ui-demo` (browser).

| # | Do | See on screen | In the database | Budget | If it goes wrong |
|---|---|---|---|---|---|
| a | Sign in as Kanika. Toggle 30 days / 90 days. Point at the four KPI chips, Needs attention, the Approvals button count. | Greeting "Good …, Kanika"; 4 KPI chips; the 90-day figures are at least the 30-day ones; orange/yellow attention cards | read-only | 10 s | Reload. If figures look stale, click the period toggle once: it refetches. |
| b | Purchase → pick a low-stock material → request → approve → PO → approve → Receive goods. | Stock for that batch equals the received quantity; PO shows Received | purchase request APPROVED, PO RECEIVED, one stock-ledger GRN row, a balanced journal entry, audit rows for both approvals | 25 s | Purchase → Purchase orders → the approved PO → Receive goods. |
| c | Production → new order for Oud body wash → release → issue materials → run stages → record a temperature reading → complete → Quality releases the batch. | Materials consumed, finished batch in QC then Released and **available** | issue ledger rows, output batch QUARANTINE then RELEASED, finished-goods stock +196, books balance | 30 s | Production → the order → Complete; Quality → Inspections → record results → Release. |
| d | Quality → the held batch shows orange → raise a deviation → add a CAPA → reject the batch. | Batch listed as quarantined/rejected; deviation and CAPA linked | deviation + capa_actions rows for the batch, batch REJECTED, available stock 0, audit rows | 30 s | Quality → Deviations → New; Inventory → the batch → QC decision → Reject. |
| e | E-commerce → an order arrives → confirm → allocate → pack → dispatch with the **Simulated courier** → delivered. | Order in the queue; AWB; "Simulated courier"; Delivered; an invoice | external order → sales order; stock-ledger out rows (exactly the ordered quantity); shipment DELIVERED; courier partner `DEMO`; invoice OPEN | 30 s | Dispatch → Orders → the order → Allocate / Pack / Dispatch, then Shipments → simulate delivery. |
| f | CRM → Board → move "Demo deal A" to **Won**. A toast appears with **Undo**. Click Undo once (nothing is sent), then move it again and wait 4 seconds. | Toast with Undo; the deal lands in Won; KPIs update | Undo: the server never hears (stage still NEGOTIATION after 5 s). Committed: stage WON, one history row per move | 20 s | CRM → Opportunities list → the deal → Move to Won. |
| g | Accounts → the invoice from step e → record two payments. | PARTIAL then PAID; receivables outstanding falls; the invoice leaves the ageing report | amount_paid grows, status PARTIAL → PAID, balanced journal entry; ageing totals add up | 15 s | Accounts → Invoices → the invoice → Record payment. Dashboards lag a write by at most **3 s**. |
| h | Approvals → approve the waiting PO. Notifications → Open one. | Bell count −1; Approvals count −1; approving twice does not approve twice | notification `read_at` set; PO APPROVED with approver; exactly one audit row | 15 s | Approvals → pick the item → Approve; Notifications → Mark all read. |
| i | Ctrl+K and type "oud". Alt+S (Scan). Switch to dark. Resize to a phone. | Results as you type; the scanner dialog; dark theme; bottom bar, no sideways scrolling | read-only | 15 s | Use the module list's own search box (same endpoints). |

### Live Mode (optional, makes the demo "breathe")
The **Demo mode** pill in the top bar (visible only while Live Mode runs) opens the **Demo controls** drawer for the demo administrator:

| Control | Effect |
|---|---|
| Pause live / Resume live | stops / restarts new activity immediately (within one second) |
| Speed ×1 / ×3 | divides the interval between actions |
| Trigger: New order / Quality alert / Big payment / Low stock | queues a one-shot scenario now (works while paused) |
| Server panel | requests/s, p95, error rate, DB connections, in flight, queue depth (3 s refresh) |
| Instruments | **Simulated** temperature / weigh-scale / line speed for running batches. Generated by the event stream, stored nowhere, used in no calculation |
| Reset to golden snapshot | restores the snapshot in seconds; you stay signed in; the page reloads |

**What Live Mode does** (all through the real API as the seeded demo users; every record is tagged `source: demo-live`; volume capped at 400 actions per run):

| Event | Rate (at ×1) | Real service called | Pushed to the UI as |
|---|---|---|---|
| New marketplace order (random platform, SKU, customer name) | ~40% of actions | `POST /ecommerce/accounts/{id}/orders` as sales | `order.created`, `kpi.invalidate` |
| Customer payment against an open invoice | ~25% | `POST /accounts/payments` as accounts | `payment.received`, `kpi.invalidate` |
| Goods receipt (re-orders a past PO through submit → approve when none is waiting) | ~15% | `POST /procurement/goods-receipts` as stores | `stock.changed`, `kpi.invalidate` |
| Production step: release → issue → start/complete a stage → complete the batch | ~20% | `/production/orders/...` as production manager / operator | `batch.stage`, `kpi.invalidate` |
| Out-of-limit temperature reading (rate-limited to one per 10 min, 15% chance on a production tick, or on demand) | rare | `POST /production/orders/{id}/stages/{n}/checks` as the QC inspector → HIGH notification | `quality.alert`, `notification.new` |
| Simulated instrument readings for running batches | every 2 s | none (generated in the stream) | `telemetry` (labelled Simulated) |

Actions happen every **20 to 60 seconds** (jittered, deterministic seed), divided by the speed multiplier. `notification.new` is pushed for every new alert the signed-in user receives, whatever created it. The browser re-reads only the screens that care, 3.3 s after an event (just past the server cache floor), at most once per 1.5 s, so figures update quietly without a reload. If the stream is unavailable the browser falls back to polling every 20 s.

**Safety.** Live Mode refuses to start unless `MINTREE_DEMO_LIVE=true`, `MINTREE_ENVIRONMENT=demo` **and** the database name contains the word `demo`. It never deletes anything; it can only call endpoints that the demo users may call. In production the API refuses to start if `MINTREE_DEMO_LIVE` is set.

### Live activity, the Control Centre and the invoice flow (added)
* **Live activity** (`/activity`) and the top-bar **Live** indicator work for every signed-in user, in any environment: they read the committed outbox, not the demo engine.
* **Demo Control Centre** (`/admin/demo-control`, demo administrator only): 28 real actions and a 15-step scripted order-to-cash scenario. Full guide: `DEMO_CONTROL_CENTRE.md`. Start the scenario at x3 for a 40-second version.
* **Invoice drafts** (Dispatch -> Invoice drafts): a draft posts nothing; posting ships the goods and issues the gapless invoice. Guide: `INVOICE_WORKFLOW.md`.
* After **any migration** (0029 scenario state, 0030 invoice drafts) run `python -m scripts.demo_ops snapshot` once the database is in the state you want to present, otherwise a reset restores the older schema. Existing databases also need the two new invoice permissions: run `python -m app.seed` once (it adds them to the roles that carry them by default, additively).
* Black-box checks: `pytest tests/demo/test_control_centre.py` (13 tests, needs the stack running) and `node tests/demo/ui.mjs` (9 browser steps).

## 4. Recovering mid-demo (one command each)

| Situation | Do this |
|---|---|
| The data looks wrong / a step left a mess | `make demo-reset` (or the **Reset to golden snapshot** button). ~2 s. Keeps your session, re-anchors dates, flushes caches, and Live Mode counters restart. |
| The browser shows a stale figure | Click the period toggle (refetch) or press F5. Dashboards are at most 3 s behind a write (15 s if a write bypassed the ORM). |
| The API is down | `make demo-up` restarts API, publisher, Live worker and web. `/api/ready` says which part is unhealthy. |
| Live Mode misbehaves | Demo controls → Pause live. If the drawer is unreachable: `pkill -f app.demo_live` (the app is unaffected). |
| Wi-Fi / third party down | Nothing in the demo needs the internet. Courier = in-process Simulated courier; Meta = labelled sandbox; AI assistant = offline demo provider (a circuit breaker stops retrying after 3 failures). |
| The whole machine is gone | See "Fallback instance". |

### Fallback instance (a second warm copy)
On the primary: `make demo-export` writes `ops/demo/golden.dump`. Copy the repo and that file to any machine with Docker, then: `make up && make migrate && make demo-import && make demo-up && make demo-check`. You get the same data, snapshot included. Keep that machine warm (`make warm`) and signed in.

## 5. Operations

| Need | Command / address |
|---|---|
| Liveness | `GET /api/health` |
| Readiness (database, migrations, disk required; cache, queue, outbox, Live scheduler advisory) | `GET /api/ready` (200 ready or degraded, 503 not ready) |
| Metrics | `GET /api/metrics` (Prometheus; token required in production) and the Server panel in Demo controls |
| Logs | JSON, one line per event, with `request_id` (also returned to the browser on errors); slow requests (> 1 s) and slow transactions (> 300 ms) are logged with their slowest statements |
| Warm-up | the API warms ORM metadata and its connection pool at start; `make warm` primes dashboards and lists over HTTP |
| Read latency report | `make perf` (p50/p95/p99 per class against the budgets) |
| Load test | `make load` (50 users, 15 min, 80/20 reads/writes) and `make soak` (10 users, 60 min) |
| Backups | `ops/backup.sh`, `ops/restore_check.sh`; the golden snapshot is itself a restorable database and exports to a dump |
| Deploy | `docker-compose.yml` (Postgres, Redis, RabbitMQ, migrate-then-start, API, worker, beat, publisher, web, Caddy with HTTPS and gzip/zstd). The API **refuses to start in production** with development webhook secrets, insecure cookies, no rate limiting, the default database credentials, or `MINTREE_DEMO_LIVE` on |

Security hygiene: rate limiting per IP (login 20/min, webhooks 600/min; on in production, Redis-backed), same-origin proxy (no CORS exposure), secrets only from the environment, the metrics endpoint hidden at the proxy, sessions 12 h normally and **72 h for the demo administrator only, and only in a demo environment**, so the presentation never signs out.

## 6. Seed spec

Deterministic (fixed seeds), resumable, and every business document is created through the real API. Masters (customers, suppliers, raw / packaging items) are inserted directly, as the base seed does.

Counts are read from the demo database after seeding (`python -m scripts.demo.bulk status`). "Target" is the brief; shortfalls are stated, not hidden.

| Entity | Target | Delivered | Notes |
|---|---|---|---|
| Items (materials, packaging, finished goods) | ~300 | **326** | 12 finished products (storefront names and prices), raw and packaging materials, 1 BOM per produced product |
| Suppliers | 60 | **60** | approved, 15 to 45 day terms |
| Customers | 2,500 | **2,574** | B2B, distributors, brands, in 20 cities; realistic names; credit days |
| Sales and marketplace orders | 25,000 | **25,024** | ~8,000 direct orders through the full dispatch chain (confirm, allocate, pack, ship, deliver, invoice, pay, some returns, some cancelled, some left open) + ~16,800 marketplace orders (4 platforms, duplicates ignored, a few SKU exceptions) |
| Purchase order lines | 10,000 | **10,054** | ~2,000 POs through request, approval, receipt (some partial), incoming QC (some failed), supplier invoice and payment |
| Stock movements | 150,000 | **54,463** | **Shortfall.** Every movement is a real posting (receipt, issue, output, dispatch, return, release). 150k would need ~3x the fulfilled volume; the extra movements are not invented. `make seed-scale` is resumable and can be re-run with higher targets |
| Production batches | 4,000 | **4,022** | each: create, release, issue, stages, check, complete, QC inspection (≈5% fail, ≈2% left in quarantine) |
| QC readings (inspection results + in-process checks) | 40,000 | **8,519** | **Shortfall.** The number of readings follows the number of specified tests per item; more would need more specs, not more rows |
| Ledger entries (journal entries) | 30,000 | **31,667** | every one balanced; trial balance, AR, AP and inventory control accounts reconcile |
| Invoices | 6,000 | **6,805** | sales invoices; 2,300+ open so receivables and ageing are real |
| Warehouses / bins | 3 / bins | **8 / 18** | RM, PM, FG, bulk, dispatch, QC quarantine, rejected, WIP |
| History | 12 months | **13 months** | seasonality (festive peak Oct to Dec, monsoon dip) and gentle growth; business dates span 13 months; server timestamps are derived from them (`demo_ops backdate`) |

Realism: en-IN names and cities, rupee amounts with GST (18%), CGST/SGST vs IGST by state, FEFO batches with expiry dates spread over 2 years, a few problem records of every kind (failed delivery, rejected lot, delayed order, overdue invoices, a held batch) so "Needs attention" is never empty. Referential integrity is enforced by the database (foreign keys, check constraints, append-only ledgers); the integrity checker re-derives 39 invariants from the tables.

Determinism and reset: fixed random seeds per worker, deterministic keys, resumable runs (`scripts/demo/bulk.py run` tops up to the targets), idempotent steps (`demo_seed_steps`). Demo rows are identifiable: customers `BK-CUS-*` / `SC-CUS-*` / `DM-CUS-*`, suppliers `BK-SUP-*` / `DM-SUP-*`, users `demo.*@example.com`, marketplace orders `BK-*` / `SC-*`, Live Mode records `LIVE-*` and the `demo_live_events` log, the whole database `mintree_demo`; `python -m scripts.demo_data reset` wipes only a database that carries the demo marker.

Timing on the demo hardware (a 4-vCPU laptop, Docker Desktop, everything on one machine): story seed ~2 min, full-scale history ~75 min with 3 to 4 workers, backdate + analyse ~1 min.

## 7. Results

Measured on the demo machine (Windows 11, 4 vCPU shared with Docker Desktop and the load generator, **one** API worker, Postgres 16 / Redis / RabbitMQ in Docker, full-scale data). Not a production-hardware result.

**Load: 50 virtual users, 15 minutes, 80% reads / 20% writes** (`tests/demo/load.py`, report `tests/load/results/load-50vu-15min.json`): 48,383 requests, 53.6 req/s, **0 errors, 0 business conflicts, 0 requests over 2 s**; API memory 182 MB at start and end (trend −3.7 MB/h); integrity checker after the run: see the line below.

| Class | n | p50 ms | p95 ms | p99 ms | max ms | Budget (p95) |
|---|---|---|---|---|---|---|
| Dashboards / boards | 12,539 | 10.9 | 84 | 116 | 256 | 300 |
| Lists (50/page) | 16,749 | 13.4 | 39 | 74 | 213 | 250 |
| Record drawers | 2,088 | 13.9 | 37 | 72 | 166 | 200 |
| Search / palette | 6,323 | 11.5 | 35 | 60 | 149 | 150 |
| Writes (orders, payments, requests, deals, confirms) | ~11,000 | 14 to 37 | 28 to 71 | 45 to 128 | 80 to 203 | n/a |

Warm read probe without load (`make perf`): dashboards p95 194 ms, lists 33, details 27, search 36.

Run-of-show: 10/10 in 11 to 20 s, repeated 8 times each after a `reset` (one API worker). Reset takes 2 to 8 s.

**Not measured:** the 60-minute soak (`make soak`), 30 minutes of Live Mode followed by invariants, a clean-machine run, and a Linux/Docker run of the two-worker configuration.

## 8. Known risks and the exact fallback for each

| # | Risk | Likelihood / impact | Exact fallback |
|---|---|---|---|
| 1 | Browser shows a figure that is a few seconds old | certain by design (≤ 3 s after a write) / low | Click the period toggle, or F5. Dashboards rebuild within 3 s of any write. |
| 2 | Moving a deal to Won is refused ("quotation not accepted") | only for a deal without an accepted quotation / medium | Use "Demo deal A / B / C" (prepared by `demo_prep`); or in CRM → Opportunities → the deal → Quotation → Send → Accept first. |
| 3 | A step leaves data in an unexpected state | medium / medium | `make demo-reset` (≈2 s). Keeps you signed in. |
| 4 | The API or a worker dies | low / high | `python -m scripts.demo_stack up` (or `make demo-up`); `/api/ready` names the failing part; `make demo-check` re-verifies. |
| 5 | Live Mode creates something awkward during a step | low / low | Demo controls → Pause live (or `pkill -f app.demo_live`). It only calls permitted endpoints and caps itself at 400 actions per run. |
| 6 | RabbitMQ or Redis down | low / low | The demo runs without them: rate limiting falls back to the process, dashboards bypass the cache, outbox events wait in the database. `/api/ready` reports "degraded", not "not ready". |
| 7 | Event stream blocked by a corporate proxy | low / low | The browser falls back to polling every 20 s; figures still update, the Demo drawer says "Polling fallback". |
| 8 | The AI assistant needs the internet | low / low | Three failures open the circuit breaker (no waiting); set `MINTREE_AI_PROVIDER=fake` for a scripted offline assistant that is labelled as such. |
| 9 | Session expires mid-demo | very low / medium | The demo administrator's session lasts 72 h in a demo environment; sign in again otherwise. |
| 10 | The hosted environment fails | low / high | Fallback instance: copy the repo and `ops/demo/golden.dump` to a second machine, `make up && make migrate && make demo-import && make demo-up && make demo-check`. |
| 11 | 10,000 concurrent users, multi-node, failover | not demonstrated | Out of scope; see `ISSUE_REGISTER.md` O-001 and O-006. The load results below are for 50 users on one machine and must not be extrapolated. |
| 12a | On **Windows**, uvicorn with two workers intermittently left one request waiting about 61 s before reaching the app (one step in roughly a third of runs; never logged by the API). Not seen with one worker (8 of 8 reset+run cycles green, about 11 s each) | observed / high on Windows | `scripts.demo_stack` starts **one** API worker on Windows (`API_WORKERS=2` to override). Linux and Docker deployments keep two workers; this was not reproduced there. |
| 12 | Job throughput and worker-crash recovery were measured earlier without RabbitMQ | known / low | RabbitMQ now runs in the dev stack; `tests/load/worker_recovery.py` exists but was not re-run for this milestone. |
