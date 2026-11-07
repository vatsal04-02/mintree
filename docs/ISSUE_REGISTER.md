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

## Open (not fixed; each is a deliberate scope boundary, not an oversight)
| ID | Sev | Item |
|---|---|---|
| O-001 | High | **10,000 req/s is not demonstrated.** Measured on one shared 4-vCPU box; see the results file for the per-core cost and the extrapolation, which is explicitly unvalidated |
| O-002 | Medium | Spare-part consumption does not move inventory (spare parts are free-text lines; needs a spares item kind, valuation and expense accounting) |
| O-003 | Medium | Production material return to stock, routing master, labour time; replacement shipments; refund flow; per-line PO delivery schedules; stock-transfer header document |
| O-004 | Medium | No live marketplace, courier, Meta publishing/insights or GST e-invoice integration (sandbox/manual only, labelled) |
| O-005 | Medium | Class H (background jobs) throughput not re-measured: RabbitMQ is not available in this sandbox |
| O-006 | Medium | No 30-min sustained / 2-hour soak, no multi-node, no database-failover or PgBouncer run |
| O-007 | Low | Mono (black) logo variant not supplied; no splash screen exists |
| O-008 | Low | One `react-hooks/exhaustive-deps` warning in `components/ui.tsx` (Drawer focus effect), left because changing its dependencies alters focus restoration behaviour |
