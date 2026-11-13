# Real-time, Control Centre and invoicing: test results

Run on 2026-11-12/13, one Windows 11 machine, Postgres 16, Redis and RabbitMQ in Docker, API with **one** worker, demo database. These are records of what was run, not guarantees for other hardware.

## Summary

| Suite | Command | Result |
|---|---|---|
| Full backend suite | `pytest tests -q` | **445 passed, 2 failed** in 15 min 37 s. Both failures were mine and are fixed (below); the affected files were re-run: `test_activity` + `test_business_clock` + `test_demo_live` **76 passed** |
| Activity, stream, cursor | `tests/test_activity.py` | 12 passed |
| Invoice drafts | `tests/test_invoice_drafts.py` | 10 passed (in the full run) |
| Demo Live / Control Centre safety | `tests/test_demo_live.py` | 17 passed (includes: controls refused outside demo, every action implemented, scenario state rules) |
| Control Centre, black-box against the running demo stack | `pytest tests/demo/test_control_centre.py` | **13 passed** (twice after the last change) |
| Browser run-of-show | `node tests/demo/ui.mjs` | **9/9** on the final run; 9/9 on four earlier consecutive runs. **One earlier run showed 1 failed step that I did not capture and could not reproduce in three reruns**; treat the browser steps as passing but not proven free of flakiness |
| Demo check | `python -m scripts.demo_check` | **GREEN**, 14 checks, 39 integrity invariants, API run-of-show 23 passed, golden snapshot restore 8.3 s |
| Earlier regression (before invoice drafts) | dispatch, procurement, production, quality, accounts, outbox, e2e, schema, idempotency files | 111 passed |

## What each black-box test proves

1. Control Centre available to the demo administrator only; other users `403`; unknown action `422`.
2. A presenter action runs the real services: order row, two audit rows, an outbox event with `source=demo-control`, a **Demo**-labelled timeline item with a link.
3. **Second session**: a dispatcher's open Live activity stream receives the order event (no refresh); a production operator's stream receives **no** sales events.
4. **Connection recovery**: a stream is dropped, two orders are created, the client reconnects with its last event id and gets `resync`, a full-refresh invalidate **and** both missed events replayed.
5. Same marketplace order delivered twice: one internal order, "1 duplicate ignored".
6. Failed import waits in the exception queue; mapping the SKU and retrying creates the order.
7. Draft invoice posts nothing (no invoice, no journal entry); posting issues exactly the drafted amount with a journal entry; the PDF is produced.
8. Payment: allocated in full, invoice PARTIAL or PAID consistent with the amounts, a payment journal entry, books balance.
9. Production: materials issued (ledger rows), finished batch quantity equals recorded output, held for QC, genealogy rows exist, books balance.
10. Quality: failed inspection puts the batch on HOLD and removes available stock; a re-test released by a second person releases it.
11. Dispatch: invoice issued for the shipment; tracking progresses; delivery sets shipment and order to DELIVERED with a delivery record.
12. **Scripted scenario**: reset, speed x3, start, pause (it does not advance), resume, 15/15 steps ok; order DELIVERED, invoice PAID, ledger balanced, activity from every module present.
13. Reset/snapshot refuse a non-demo database.

## Defects found by these runs (all fixed; see `ISSUE_REGISTER.md` D-012 to D-017)

* The stream showed only demo-engine events (the reason for the work).
* A reconnecting client with only the first frame had no cursor.
* Module dashboards listened for scope names the backend never sent.
* Pause during a running scenario step was overwritten (lost update), found by test 12.
* Actions chose stock or BOMs that could not actually be allocated or issued.
* In the full run: a test asserted the link without its record id; and the new actions used `date.today()` and SQL `current_date`, which the business-clock test bans (Asia/Kolkata rule). Both fixed.

## Measurements

* Scenario: about 5.7 s of compute across 15 steps (without the deliberate 5 s pauses); with pauses about 80 s at x1 and 40 s at x3.
* Control actions complete in 0.1 to 1.3 s each on the demo database.
* A presenter action reached a second open session inside the test's 15 s window; the timeline path is poll 1 s + coalesce 2 s.

## Not run

* **60-minute soak**, **30 minutes of Live Mode followed by invariants**, a **clean-machine run** of the runbook, a Linux/Docker two-worker run, RabbitMQ worker-crash recovery.
* Load test re-run with the new stream polling (each connected viewer adds one small primary-key query per second; the earlier 50-user, 15-minute run predates it).
* Playwright coverage for every module's dashboards; only the flows listed above.
* Real marketplace or courier connectivity (none exists; everything is labelled Demo or Simulated).
