# Issue Register

Severity: **Critical** = wrong business/financial results, data corruption, security. **High** = broken workflow, missing validation, concurrency failure. **Medium** = performance, usability, incomplete functionality. **Low** = minor defects.
Status: Fixed (with the commit/test that proves it) · Open · Limitation (known, documented, deliberate for now).

Baseline for this milestone: commit `829a6f1` on `claude/variable-shadowing-fix-4bd715` (= `phase-0-foundation`), backend 364 passed / 1 skipped, browser suites 16/16, 11/11, 23/23, demo 18/18, AI 8/8. A read-only sweep of all 145 parameterless GET routes as the system administrator (`backend/scripts/audit_sweep.py`) found **0 server errors**; the two 409s it reported are correct business refusals (pick list for a DRAFT order, CoA for an unreleased inspection) and the slowest route was the first PDF render (≈ 0.9 s).

## Fixed in this milestone
| ID | Sev | Finding | Root cause | Fix / proof |
|---|---|---|---|---|
| I-001 | High | Existing databases never received the `ai.*` permissions; the AI feature was invisible to everyone but the system administrator | `seed()` only attached permissions to roles it created | additive grant for system roles on seed; `tests/test_ai_upgrade_seed.py` |
| I-002 | Medium | Assistant panel clipped to the 56 px header | `position: fixed` inside an element with `backdrop-filter` | panel lifted to the shell root; screenshot-verified; Playwright AI journey |
| I-003 | High | Demo books were not proven to reconcile (AR, AP, inventory vs sub-ledgers) | verification only checked internal consistency | 3 reconciliation checks added to `demo verify` (AR 1200 = open sales invoices; AP 2000 = open supplier invoices; inventory GL = valuation + WIP). The inventory check first "failed" by 19,234.62, which proved to be exactly the WIP of two in-progress production orders; the invariant was corrected to include WIP |
| I-004 | Medium | Meta module could not distinguish simulated from real, had no PAUSED / SCHEDULED / objective | model gap | migration 0026: `meta_connections` with CHECKs that make "CONNECTED without verification" impossible (raw SQL included), campaign objective + PAUSED, content SCHEDULED; sandbox insights adapter labelled `simulated`; Graph verification adapter; `tests/test_meta_workspace.py` |
| I-005 | Medium | Printable documents carried no logo; no PO, quotation or delivery-challan PDF existed | not built | authentic vector logo in invoice, CoA, PO, quotation, challan (`core/brand.py`, `core/print_docs.py`); PDFs refuse to render if lines do not reconcile with the stored total |
| I-006 | Medium | Stock Next.js favicon | default asset | `app/icon.svg` (the brand "M") |
| I-007 | Medium | **Throughput ceiling ≈ 250 req/s on 3 workers for cacheable reads, ≈ 100 req/s for dashboards** (p95 3.2 s at 400 target) | per-request CPU: session query + 2–3 Redis round trips + ORM + pydantic | session cache (epoch-validated), 20 ms Redis memo, versioned response cache, flagged dashboard cache. Same hardware: reads ≈ 670 req/s at p95 179 ms, dashboards ≈ 660 req/s (see `PERFORMANCE_BENCHMARK_RESULTS.md`) |
| I-008 | Medium | No cash-flow statement | not built | direct-method statement reconciled to the cash/bank ledger; `tests/test_cashflow_and_formula_cost.py` |
| I-009 | Medium | R&D could not cost a formulation | not built | `GET /rnd/formulation-versions/{id}/cost` (stock average → last PO price → flagged unpriced; never guessed) |
| I-010 | Medium | Concurrency on invoice posting, payment allocation and approvals was not tested | gap | `tests/test_concurrency_finance.py` against the live multi-process server |
| I-012 | Medium | Search and filters on list screens saw only the newest 100 rows (client-side filter over one fetched page), so older orders, shipments or leads could not be found once data grew | `ModuleKit` filtered locally; list endpoints had no `q` | server-side `q` on the generic lists, the 15 listing endpoints (whole-row, wildcard-safe), leads, orders and purchase orders; the UI sends the debounced query; tests in `test_listings.py`; the demo journeys now find records through search |
| I-013 | Medium | The AI browser journey and several demo journeys assumed the first 100 rows contained the story record; the PO API did not expose `qty_invoiced` | data growth exposed hidden assumptions | journeys search by number; `qty_invoiced` added to the PO API |
| I-014 | Low | A stale `next-server` left by a previous browser run could serve old chunks to the next run (ChunkLoadError, every journey red) | scripts killed the `npx` wrapper only | the run scripts free their ports with `fuser -k` on start and exit |
| I-015 | Low | A long full-suite run (> 5 min) failed unrelated integrity assertions because uncommitted-publisher outbox events from earlier tests aged past the 5-minute 'stuck' limit | no publisher runs in tests | autouse hygiene fixture removes pending outbox debris older than 3 minutes |
| I-016 | Low | The fake assistant provider printed a count it had computed itself, which the figure verifier (correctly) flagged as unverified | demo provider summarisation | counts removed; the verifier caught a real fabrication path |
| I-011 | Medium | Seeder rerun after a mid-step failure collides with its own deterministic Idempotency-Keys | by design: keys are per step | limitation: failed steps need a reset of a demo database; steps are small so this is cheap; documented in `DEMO_ENVIRONMENT.md` |

## Fixed in the demo-readiness milestone (full-scale data, Live Mode, run-of-show)
Found by running the system at the target volume (25k orders, 4k batches, 54k stock movements, 31k journal entries) and by replaying the demo script through the API and the browser.

| ID | Sev | Finding | Root cause | Fix / proof |
|---|---|---|---|---|
| D-001 | High | `/inventory/reports/slow-moving` ran past the 15 s statement timeout (HTTP 500) at full scale, and every quantity it did return was multiplied by the item's ledger length | the balance rows were joined to the ledger rows before aggregating (1.58 million intermediate rows for ONE item) | aggregate per item first, then join: whole report 17.6 ms; `inventory/extras.py` |
| D-002 | Medium | Purchase "vault" took a median 606 ms (p95 837 ms) | four correlated sub-selects per material, each a scan of the purchase lines | one pass for "latest PO line per item", one for "still on order"; cached as a dashboard (`module_dashboards.py`) |
| D-003 | High | `shipment_lines` had 169k sequential scans reading 1.56 billion tuples; `external_orders` 74k, `external_order_items` 35k with no index, `inspections` 24k, `purchase_order_items` 11k | indexes never added for lookups the dispatch, import, QC and purchase screens make on every call | migration 0028 (10 indexes, declared on the models): single lookups 19x to 119x faster (1.38 ms to 0.07 ms, 2.97 ms to 0.025 ms, 0.88 ms to 0.019 ms) and flat as the tables grow |
| D-004 | High | After a payment or order the Overview and module dashboards could show the old figure for 15 s | dashboards cached for a fixed TTL with no invalidation on writes | write-versioned cache with a 3 s floor: stale by at most 3 s after a business write (15 s only for raw-SQL writes); module dashboards cached the same way; `tests/test_demo_live.py` |
| D-005 | Medium | Six dashboard queries cast `timestamptz::date`, which uses the database session zone instead of the business zone (IST) | direct cast instead of `business_date()` | replaced in `module_dashboards.py`; `test_business_clock.py` green |
| D-006 | Medium | On the CRM pipeline board the "..." move menu opened **behind** the next card, so "Move to Won" (and every other item) was unreachable by menu on all but the last card of a column | each card is its own stacking context; the open one was not raised | `zIndex: 30` while a menu is open; found by the browser run-of-show |
| D-007 | High | After `reset`, the next sign-in failed with a duplicate session id | restored database carried the snapshot's sequence while the kept sessions had higher ids | the sequence is advanced past the kept sessions after the restore (`demo_live/ops.py`) |
| D-008 | Medium | The demo history looked flat: every `created_at` / `posted_at` / `received_at` was "today" while business dates spanned 12 months | server-stamped columns cannot be back-dated through the API | `demo_ops backdate` derives them from the document's business date (190k rows moved, idempotent, earlier only); all key tables now span 13 months |
| D-009 | Medium | 2,072 unread notifications ("PO awaiting approval" for long-approved orders) made the bell read "99+" | every bulk purchase order notified the approver | `demo_prep` leaves each user's newest 12 unread and spreads and reads the rest |
| D-010 | Low | The test suite read local `.env` (AI layer on) and failed `test_switched_off_by_default`; the live-server fixture used `os.killpg` (not on Windows) and left servers running, which broke later runs | test isolation | `conftest.py` pins the AI settings and kills the process tree on Windows |
| D-011 | Medium | RabbitMQ round-trip test could not find its message among thousands left by a demo publisher | shared default vhost | the demo stack uses its own vhost (`/demo`) and a worker drains it |
| D-012 | High | The event stream carried only demo-engine events; ordinary users' actions never reached other open screens | the stream read `demo_live_events` only | the stream reads the committed outbox per viewer, with a gap-safe cursor, resync and a timeline (`REALTIME_ARCHITECTURE.md`) |
| D-013 | Medium | A reconnecting client had no cursor if it had seen only the first frame, so events in the gap were lost | the `hello` frame had no id | `hello` carries the cursor; reconnect replays or forces a full refresh |
| D-014 | Medium | Module dashboards never refreshed on their own events | the page listened for module ids (`procurement`, `dispatch`) while the backend sends business-area scopes (`purchase`, `orders`) | scope map in `m/[module]/page.tsx` |
| D-015 | Medium | Pausing the scripted scenario during a running step was overwritten when the step finished (lost update) | the tick wrote a status computed from a stale read | the tick no longer writes `status` on ordinary progress; found by the black-box test |
| D-016 | Low | Control actions picked stock that existed but not where the order is fulfilled, or a BOM whose material was short | the random choice ignored warehouse, QC status and availability | only allocatable stock and BOMs whose materials are in stock are chosen |
| D-017 | Low | The "Demo mode" pill wrapped onto two lines once the Live indicator was added | missing `whitespace-nowrap` | fixed |
| D-018 | Medium | `demo_ops reset` failed on a unique daily series (campaign metrics) whenever the shift was a few days | one UPDATE moved rows onto each other mid-statement | the shift retries such a table in two steps (park far away, bring back) inside a savepoint |

## Open (not fixed; each is a deliberate scope boundary, not an oversight)
| ID | Sev | Item |
|---|---|---|
| O-001 | High | **10,000 req/s is not demonstrated.** Measured on one shared 4-vCPU box; see the results file for the per-core cost and the extrapolation, which is explicitly unvalidated |
| O-002 | Medium | Spare-part consumption does not move inventory (spare parts are free-text lines; needs a spares item kind, valuation and expense accounting) |
| O-003 | Medium | Production material return to stock, routing master, labour time; replacement shipments; refund flow; per-line PO delivery schedules; stock-transfer header document |
| O-004 | Medium | No live marketplace, courier or Meta integration; GST e-invoice runs in SANDBOX only (real IRN computation, no government registration) until a GSP adapter is written (`TIER1_OPERATIONS.md` section 5) |
| O-005 | Medium | Class H (background jobs) throughput not re-measured: RabbitMQ is not available in this sandbox |
| O-006 | Medium | No 30-min sustained / 2-hour soak, no multi-node, no database-failover or PgBouncer run |
| O-007 | Low | Mono (black) logo variant not supplied; no splash screen exists |
| O-008 | Low | One `react-hooks/exhaustive-deps` warning in `components/ui.tsx` (Drawer focus effect), left because changing its dependencies alters focus restoration behaviour |
