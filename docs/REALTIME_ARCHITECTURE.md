# Real-time architecture

How a committed business change reaches every open screen, who is allowed to see it, and what happens when the connection drops. Written after an audit of what the application already had (section 1) and describing exactly what was built (sections 2 to 7). Where something is **not** done, section 8 says so.

## 1. Audit: what existed, what was wrong (before any change)

**Existing and kept, not duplicated.** FastAPI services per module; PostgreSQL as the only source of truth; a **transactional outbox** (`outbox_events`) written inside each business transaction, published to RabbitMQ by a dedicated publisher with confirms, retry with backoff, a `dead` state and a failed-event inspection screen (`admin.jobs`); idempotent consumers (`processed_events`); idempotency keys on posting endpoints; Redis for the response cache and rate limits; an SSE endpoint `/api/events` and a frontend `LiveProvider` built for Demo Live Mode.

**Gaps found (the reason for this work).**

| # | Finding | Severity |
|---|---|---|
| R-1 | `/api/events` carried **only events written by the Demo Live engine** (`demo_live_events`). An order, payment or receipt entered by a person on a screen reached the outbox (for RabbitMQ) but never the stream, so a second user's screen stayed stale until a manual refresh | High |
| R-2 | Only 19 outbox event types existed. Order created/confirmed/allocated/cancelled, quotations, purchase requests, purchase-order submit/reject/cancel, production create/release/issue/stage/cancel, marketplace import/exception, stock transfer and inspection opened emitted nothing | High |
| R-3 | Existing outbox payloads had no actor and no source, so a timeline could not say who did something or tell a person from the demo engine | Medium |
| R-4 | The stream's cursor was a single engine counter. A client that reconnected had no way to resume, and nothing told the screens to re-read after a gap | High |
| R-5 | No visible connection state for ordinary users; no per-user connection cap | Medium |
| R-6 | No activity timeline anywhere | Medium |
| R-7 | Invoices are born at dispatch (one gapless GST invoice per shipment, atomically with stock and cost of goods). There was no draft, validate, post step | Design |
| R-8 | No demo control surface beyond four triggers; no scripted scenario | Demo |

Dashboards: the module dashboards, boards and the Overview read live tables on every request (cached for at most 3 s, see `DASHBOARD_DATA_CONTRACTS.md`); none was found to use hard-coded figures. The risk was staleness, not fakeness (R-1).

## 2. The flow

```
person or demo action
  -> API route -> domain service -> PostgreSQL transaction
       writes the business rows + audit row + OUTBOX row (core/activity.emit -> core/outbox.add)   [one transaction]
  -> COMMIT
       -> outbox publisher -> RabbitMQ -> consumers (notifications, courier booking)             [at-least-once, idempotent]
       -> /api/events (SSE) reads the same committed outbox rows, per viewer, filtered by permission
            -> browser: "activity" item (timeline) + one coalesced "kpi.invalidate" (which scopes to refetch)
            -> the screen re-reads authorised data through the normal REST API
```

PostgreSQL stays authoritative. The stream says "this changed"; it never carries a record the viewer could not fetch, and it is never the source of a figure.

## 3. Transactional outbox

`app/core/activity.py` is the single entry point. `activity.emit(db, kind, entity=..., entity_id=..., number=..., actor_id=...)` inserts the outbox row in the caller's transaction. An event therefore exists **if and only if its change committed**. A refused or rolled-back operation leaves nothing to show (tests: `test_a_refused_operation_leaves_no_activity`, `test_a_rolled_back_transaction_never_reaches_the_stream`). `emit` raises on an unregistered kind, so a typo cannot silently drop events.

Existing events (goods receipt, PO approved, production completed, inspection decided, dispatched, payment, ...) gained `actor_id` and `source` in their payloads; nothing else about them changed, so the RabbitMQ consumers are unaffected.

Publisher, retry, `dead` state, lease expiry after a crash and failed-event inspection are the existing, already-tested mechanisms (`tests/test_outbox.py`). RabbitMQ or Redis being down delays RabbitMQ delivery only; the stream reads PostgreSQL, so the screens keep updating.

### Event catalogue

49 user-facing kinds. Each declares its **own** permission; a viewer without it never receives the event (section 5).

| Event | Screen it opens | Permission to see it | Scopes refreshed |
|---|---|---|---|
| `sales.order.created` | dispatch/orders | `dispatch.order:read` | overview, orders |
| `sales.order.confirmed` | dispatch/orders | `dispatch.order:read` | overview, orders |
| `sales.order.allocated` | dispatch/orders | `dispatch.order:read` | overview, orders, inventory |
| `sales.order.cancelled` | dispatch/orders | `dispatch.order:read` | overview, orders, inventory |
| `sales.quotation.created` | sales/quotations | `sales.quotation:read` | overview, sales |
| `sales.quotation.sent` | sales/quotations | `sales.quotation:read` | overview, sales |
| `sales.quotation.accepted` | sales/quotations | `sales.quotation:read` | overview, sales |
| `sales.quotation.rejected` | sales/quotations | `sales.quotation:read` | overview, sales |
| `sales.quotation.convert` | sales/quotations | `sales.quotation:read` | overview, orders, sales |
| `crm.lead.converted` | sales/leads | `crm.lead:read` | overview, sales |
| `dispatch.order.dispatched` | dispatch/invoices | `accounts.sales_invoice:read` | overview, orders, accounts, inventory |
| `dispatch.shipment.requested` | dispatch/shipments | `dispatch.shipment:read` | overview, orders |
| `dispatch.shipment.delivery_attempt` | dispatch/shipments | `dispatch.shipment:read` | overview, orders |
| `dispatch.return.decided` | dispatch/returns | `dispatch.return:read` | overview, orders, accounts, inventory |
| `accounts.invoice.drafted` | dispatch/invoice-drafts | `accounts.sales_invoice:read` | accounts |
| `accounts.invoice.draft_cancelled` | dispatch/invoice-drafts | `accounts.sales_invoice:read` | accounts |
| `accounts.payment.posted` | accounts/payments | `accounts.payment:read` | overview, accounts |
| `accounts.supplier_invoice.posted` | accounts/supplier-invoices | `accounts.supplier_invoice:read` | overview, accounts, purchase |
| `accounts.debit_note.posted` | accounts/supplier-invoices | `accounts.debit_note:read` | accounts |
| `crm.opportunity.qualified` | sales/opportunities | `crm.opportunity:read` | overview, sales |
| `crm.opportunity.proposal` | sales/opportunities | `crm.opportunity:read` | overview, sales |
| `crm.opportunity.negotiation` | sales/opportunities | `crm.opportunity:read` | overview, sales |
| `crm.opportunity.won` | sales/opportunities | `crm.opportunity:read` | overview, sales |
| `crm.opportunity.lost` | sales/opportunities | `crm.opportunity:read` | overview, sales |
| `procurement.pr.created` | procurement/requests | `procurement.pr:read` | overview, purchase |
| `procurement.pr.submitted` | procurement/requests | `procurement.pr:read` | overview, purchase |
| `procurement.pr.approved` | procurement/requests | `procurement.pr:read` | overview, purchase |
| `procurement.pr.rejected` | procurement/requests | `procurement.pr:read` | overview, purchase |
| `procurement.purchase_order.created` | procurement/orders | `procurement.po:read` | overview, purchase |
| `procurement.purchase_order.submitted` | procurement/orders | `procurement.po:read` | overview, purchase |
| `procurement.purchase_order.approved` | procurement/orders | `procurement.po:read` | overview, purchase |
| `procurement.purchase_order.rejected` | procurement/orders | `procurement.po:read` | overview, purchase |
| `procurement.purchase_order.cancelled` | procurement/orders | `procurement.po:read` | overview, purchase |
| `procurement.purchase_order.amended` | procurement/orders | `procurement.po:read` | purchase |
| `inventory.goods_receipt.posted` | procurement/receipts | `procurement.receipt:read` | overview, purchase, inventory, quality |
| `procurement.purchase_return.posted` | procurement/returns | `procurement.receipt:read` | purchase, inventory |
| `inventory.stock.transferred` | inventory/ledger | `inventory.stock:read` | overview, inventory |
| `inventory.batch.qc_decided` | inventory/batches | `inventory.batch:read` | overview, inventory, quality |
| `quality.inspection.created` | quality/inspections | `quality.inspection:read` | overview, quality |
| `quality.inspection.decided` | quality/inspections | `quality.inspection:read` | overview, quality, inventory |
| `quality.coa.issued` | quality/coa | `quality.coa:read` | quality |
| `production.order.created` | production/orders | `production.order:read` | overview, production |
| `production.order.released` | production/orders | `production.order:read` | overview, production, inventory |
| `production.order.materials_issued` | production/orders | `production.order:read` | overview, production, inventory |
| `production.order.stage` | production/orders | `production.order:read` | overview, production |
| `production.order.completed` | production/orders | `production.order:read` | overview, production, inventory, quality |
| `production.order.cancelled` | production/orders | `production.order:read` | overview, production, inventory |
| `ecommerce.order.imported` | ecommerce/orders | `ecommerce.order:read` | overview, orders |
| `ecommerce.order.exception` | ecommerce/orders | `ecommerce.order:read` | orders |

## 4. Transport: Server-Sent Events

Kept (it was already the codebase's choice): one-way, plain HTTP, auto-reconnect built into `EventSource`, works through the existing proxy settings (`X-Accel-Buffering: no`).

* **Authenticated**: the session cookie, same as every API call (`401` without it).
* **Per viewer**: permissions are read from the session on connect and each event is filtered against them in the server.
* **Event ids and cursor**: every frame carries `id: <engine>:<outbox>`; the browser echoes it as `Last-Event-ID` on reconnect. Even the first `hello` frame carries the cursor, so a client that saw nothing else can still resume.
* **Gap-safe delivery.** Ids are allocated before commit, so a lower id can commit after a higher one. The cursor remembers ids it skipped over and asks for them again for 15 s; each committed event is delivered once, in id order within a poll (`test_a_late_committing_transaction_is_not_skipped`).
* **Duplicate detection**: delivery is cursor-based, so a row is not sent twice on one connection; the timeline also de-duplicates by id on the client.
* **Coalescing**: any number of events within 2 s produce one `kpi.invalidate` carrying the union of scopes, so screens do not flicker.
* **Heartbeat** every 15 s; `retry: 3000`.
* **Connection limit**: 6 concurrent streams per user (`429` beyond).
* **Reconnect resync**: a reconnecting client receives `hello{resync:true}` and a `kpi.invalidate` for scope `all`, so every screen re-reads from the database and the timeline reloads. A client further behind than 400 events skips the replay and relies on that full refresh. A client *ahead* of the database (after a snapshot restore) is clamped back.
* **Degradation**: after 3 failed reconnects the frontend polls every 20 s and the top bar shows "Reconnecting". No error page, no screen stuck stale.
* **Multi-instance**: every API instance reads the same PostgreSQL rows, so no Redis pub/sub fan-out is needed for correctness. The cost is one cheap primary-key range query per connected viewer per second.

## 5. Security

* Every event kind carries a permission; `visible()` is checked per viewer per event. A production operator never receives a sales event (`test_a_second_session_receives_the_event_without_a_refresh_and_an_unauthorised_one_does_not`, `test_activity_is_filtered_by_what_the_viewer_may_see`).
* Titles can contain document numbers and amounts, so they are only sent to viewers with permission to read that kind of record. Full records are always fetched through the permission-checked REST routes.
* `source` ("demo-live" / "demo-control") is honoured **only** when `MINTREE_ENVIRONMENT=demo`; it labels activity and grants nothing.
* Demo controls refuse to exist outside a demo database (`demo_live/guard.py`, three independent conditions).
* `/api/activity` applies the same permission filter and pages by cursor.

## 6. What the screens do

* `LiveProvider` (one `EventSource` per tab) exposes `useLiveEvent` and `useLiveRevision(scopes)`. A screen puts the revision number in its fetch effect's dependencies; only screens whose scopes match refetch, after the server's 3 s dashboard cache floor and never more often than every 1.5 s. Refreshes are silent (no loading state, no scroll jump).
* **Live activity** (`/activity`, in the sidebar) lists committed events with who, which module, Asia/Kolkata time, a link to the record and a **Demo** badge for demo-generated ones. New items animate in; module filter chips.
* A quiet **Live / Reconnecting** indicator sits in the top bar.
* Nothing is shown optimistically: figures change only after the screen re-reads committed data.

## 7. Targets and measurements

Measured on one Windows machine, one API worker, demo database (run records in `REALTIME_TEST_RESULTS.md`): the second browser session received a presenter action well inside the 15 s the test allows. The path is poll 1 s, coalesce 2 s, client delay 3.3 s for dashboards; the timeline itself updates in roughly 1 to 3 s. These are measurements of this machine, not guarantees.

## 8. Not done / limits (stated plainly)

* The 1 to 3 s **dashboard** target is not met end to end: the client waits 3.3 s before re-reading so that it is past the server's 3 s dashboard-cache floor (otherwise it would re-read the old figure).
* Timeline history is the outbox's retention (`outbox_retention_days`, 7 by default); older activity is not kept.
* Dashboard figures are refetched per scope, not patched in place; there is no per-widget incremental update.
* No Redis pub/sub or WebSocket path was built (not needed for correctness; see section 4).
* No live external marketplace or courier connectivity exists. Marketplace orders in demos are DEMO imports through the real import code, labelled as such.
* Multi-worker uvicorn on Windows still shows an intermittent stall (documented in the runbook); the demo stack defaults to one worker there.
* Order creation by a person through the screens and through the API both emit; direct SQL does not (by design: nothing bypasses the services).
