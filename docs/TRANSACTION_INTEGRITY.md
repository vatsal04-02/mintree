# Transaction Integrity

How the backend keeps stock, documents and events correct under concurrency. Everything here is implemented and
covered by tests (`backend/tests/test_uow.py`, `test_idempotency.py`, `test_outbox.py`,
`test_inventory_procurement.py`, `test_concurrency.py`) and by `backend/scripts/check_integrity.py`.

## 1. Transaction boundary
`core/uow.run_tx(db, work, name=...)` is the explicit boundary of every business posting.
* **Commits inside the call**, i.e. before the HTTP response exists. (Phase 0 committed in dependency teardown, *after*
  the response; a failing `COMMIT` returned 200 and lost the write — reproduced and fixed, regression test
  `test_failed_commit_is_reported_to_the_client_not_swallowed`.)
* Any exception rolls back the whole transaction; nothing is half-posted.
* Ends the request's authentication read-transaction first, so the posting transaction starts clean and short, and the
  request holds **at most one DB connection** (invariant required by admission control).
* READ COMMITTED by default. `isolation="SERIALIZABLE"` is available per call for an invariant that needs it; routine
  postings use row locks, not SERIALIZABLE, because the contended invariants are all "this row's quantity".
* No network call occurs inside a transaction: broker publishing happens in the outbox publisher between two short
  transactions; HTTP handlers never call external services while holding locks.

## 2. Retry policy (bounded)
Retryable SQLSTATEs: `40P01` deadlock, `40001` serialization failure, `55P03` lock timeout. Up to **3 retries**
(4 attempts) with jittered exponential back-off (20 ms · 2ⁿ · [0.5,1.5)); the whole `work` function re-runs on a clean
transaction, so it must depend only on database state. Exhaustion → `503 + Retry-After: 1`, "nothing was saved";
counted in `mintree_tx_failures_total`. `57014` (statement timeout) → 503. Everything else rolls back and propagates.
Server-side safety nets per connection: `statement_timeout 15 s`, `lock_timeout 5 s`,
`idle_in_transaction_session_timeout 30 s`.

## 3. Lock ordering (the rule that prevents deadlocks)
Global order, enforced by `inventory/repository.py`:
1. business documents (PO, then PO lines in id order; batches) 
2. `stock_balances` rows in **`(expiry_date NULLS LAST, batch_id NULLS LAST, id)`** order — a stable total order shared by every writer
3. leaf rows (reservations, ledger inserts)

Hard-won details (each found by `test_concurrency.py`, each fixed and now regression-tested):
* **`INSERT … ON CONFLICT DO UPDATE` locks the row as a side effect, in call order.** Using it to get-or-create the
  source then destination balance made opposite-direction transfers lock A→B and B→A. Now: `ensure_balance` reads
  without locking (`ON CONFLICT DO NOTHING` only to create), then `lock_balances` locks in the global order.
* **Foreign keys take locks.** Inserting a ledger row with `batch_id` takes `FOR KEY SHARE` on the batch *after* balances
  are locked. The QC decision locked the batch `FOR UPDATE` (conflicts with `KEY SHARE`) then wanted balances →
  batch↔balance cycle. QC now uses `FOR NO KEY UPDATE` (it never changes a key). Rule: *anything locking a parent row
  uses `FOR NO KEY UPDATE` unless it changes key columns.*
* `FOR UPDATE … ORDER BY` locks in the sorted order (LockRows sits above Sort), so FEFO candidate selection acquires
  locks in the global order. Rows are re-evaluated after lock wait, so quantities used are post-lock accurate.
* Evidence: 800 concurrent opposite-direction transfers + 600 mixed random operations (reserve, issue, release,
  transfer, adjust, QC hold/release) → `pg_stat_database.deadlocks` delta **0**, no 5xx, integrity checker passes.

## 4. Inventory model
* `stock_ledger`: append-only truth. UPDATE/DELETE/TRUNCATE blocked by triggers. Corrections are new `REVERSAL` /
  `ADJUSTMENT` rows; a movement can be reversed once (`UNIQUE(reverses_ledger_id)`).
* `stock_balances`: transactionally maintained projection with `qty_physical`, `qty_reserved`, `qty_blocked` and a
  generated `qty_available = physical − reserved − blocked`. DB CHECKs: each ≥ 0 **and `reserved + blocked ≤ physical`** —
  negative or over-reserved stock is impossible even if application code is wrong (surfaces as `409 stock_constraint`).
* `stock_reservations`: ACTIVE → RELEASED | CONSUMED; `balance.reserved == Σ active reservations`.
* Ledger and balance are updated in the **same statement sequence of the same transaction**; `repository.add_ledger`
  and `apply_delta` are the only writers.
* Final duplicate guard: `UNIQUE(source_type, source_id, source_line, leg, movement_type)` — a document line can post a
  given movement once, whatever happens above it.
* QC: receipts of batch-tracked items are **quarantined** (physical but *blocked*); `QC_RELEASE` unblocks via ledger rows.
  Reservation/issue/transfer only see `RELEASED`, unexpired batches in FEFO order.

## 5. Idempotency
Header `Idempotency-Key` (8–128 chars) is **required** on every posting endpoint. Protocol (`core/idempotency.py`),
all inside the business transaction:
1. `INSERT (scope,key,request_hash) … ON CONFLICT DO NOTHING`. A concurrent duplicate **blocks on the unique index**
   until the first transaction commits or aborts — PostgreSQL serialises duplicates; no extra locks, no races.
2. Inserted → run the effect, store the response on the same row, commit once.
   Conflict → same payload: replay the stored response (`Idempotent-Replayed: true`, no new effect);
   different payload: `409 idempotency_key_reuse`.
3. A failed first attempt leaves **no key** (rolled back with the effect), so the retry proceeds.

Defence in depth (all DB constraints, independent of the above): unique ledger source key; `UNIQUE(supplier_id,
delivery_note_no)` (same physical delivery cannot be booked twice under a *different* key); `CHECK qty_received <=
qty_ordered` (over-receipt); `UNIQUE(event_id, kind)` on notifications. Mutation-tested: dropping the idempotency
unique constraint makes the replay and concurrency tests fail.
**Scope of what is implemented:** idempotent posting exists for goods receipts, QC decisions, reservations/releases, issues,
transfers, adjustments and reversals. **Not implemented yet** (their modules do not exist): invoices, production postings,
dispatch records, payment events and marketplace webhooks. The same primitive applies to them (`scope` = source, `key` =
external event id) but there is no webhook endpoint or payment flow to test it on, so it is *not* claimed for them.

## 6. Transactional outbox
* `core/outbox.add()` inserts the event **in the business transaction** (rollback ⇒ no event; commit ⇒ event, test-proved).
  Every event carries the originating `request_id` (correlation).
* Publisher (`workers/outbox_publisher.py`): *claim* (short tx, `FOR UPDATE SKIP LOCKED`, `attempts+1`, lease) →
  *publish* to RabbitMQ with publisher confirms and persistent delivery (**no transaction open**) → *mark published*
  (short tx). Crash between publish and mark ⇒ the row is reclaimed after `outbox_lease_s` and published again:
  **at-least-once**. Several publishers can run; no event is claimed twice concurrently (300 events × 4 publishers test).
* Failure: exponential back-off (cap 5 min); after `outbox_max_attempts` (8) status `dead` for investigation.
  Inspect/requeue: `GET /api/admin/outbox`, `POST /api/admin/outbox/{id}/requeue` (audited); metric
  `mintree_outbox_dead`, `mintree_outbox_oldest_unpublished_age_seconds`.
* Ordering: not guaranteed across publishers/consumers. Consumers must be order-independent (carry versions in payloads).
* Published rows are purged after `outbox_retention_days`; the hot partial index only covers unpublished rows.

## 7. Idempotent consumers & failure handling
Celery task `mintree.consume`: the **dedupe marker `processed_events(consumer, event_id)` and the consumer's effects commit
in one transaction** ⇒ exactly-once effects over at-least-once delivery; a failing handler rolls back both (so the retry is
not wrongly skipped — tested). `acks_late` + `reject_on_worker_lost`: a worker killed mid-task has its message redelivered.
Retries: 5, exponential back-off with jitter. Exhausted retries → row in `job_failures` (permanent failures, shown at
`GET /api/admin/job-failures`, resolvable, audited) and `mintree_worker_tasks_total{outcome="dead"}`. Queues declare
`x-dead-letter-exchange=mintree.dlx` → `dead_letter` queue.

## 8. Optimistic concurrency
Purchase orders/requests carry `version`; every transition requires `expected_version` and fails with
`409 version_conflict` if stale (under a `FOR UPDATE` row lock, so check-and-bump is atomic).

## 9. Approvals
`approval_policies(doc_type, role_code, max_amount, allow_self_approval)`; PO approval requires a policy covering the
amount and (by default) a different user than the creator. Enforced in the service, backend-side.

## 10. Verification: `scripts/check_integrity.py`
Run after every critical test and load run (`harness.py --integrity`, pytest). Reads one REPEATABLE READ snapshot; exit 1
on any violation. Checks: physical/blocked reconcile with ledger · reserved reconciles with active reservations · no
negative/over-reserved stock · no reservation on rejected batches · no duplicate ledger postings · no duplicate goods
receipts / each GR has exactly one key · idempotency keys unique and resolvable · PO received qty = Σ receipts and ≤ ordered ·
every receipt line in ledger with equal qty · batch↔GR↔supplier↔item traceability · audit entries exist for GRs, PO
approvals, adjustments/reversals · every GR has its outbox event · no dead/stuck outbox events · no duplicated consumer
effects · a movement is reversed at most once. **Skipped (module not built): balanced accounting journals** — the check
will be added with the Accounts module; until then "no unbalanced accounting postings" is *not* demonstrated.

## 11. Known limitations (stated, not hidden)
* Holding a RELEASED batch blocks only its *available* quantity; existing reservations stay reserved but flagged by batch status.
* Document numbers use PostgreSQL sequences (gaps on rollback). Fine for stock documents; **statutory invoices need a
  gapless locked counter** (Accounts module).
* RFQ/quotation comparison, supplier invoices and payments are not part of this slice.
