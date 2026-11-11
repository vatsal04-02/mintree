# Traceability test plan

Three layers: backend tests that build real chains and assert the read API against them; a mutation check proving the tests can fail; and a browser journey through the real interface.

## Backend (`backend/tests/test_traceability.py`, 10 tests)

Every scenario is built through the real services (PO → goods receipt → QC release → BOM → production order → allocation → dispatch → courier webhook → return → transfer) using the factories of the existing production and dispatch tests. **No row is written to any traceability table.**

| Brief requirement | Test |
|---|---|
| Backward: production order, actual consumption, supplier lots, receipts, POs | `test_backward_traceability_follows_actual_consumption_across_two_supplier_lots`: two lots (60 + 40) of one material feed one order; both appear with their consumed quantities; independently confirmed against SQL |
| Never infer from the BOM | `test_bom_alone_does_not_create_genealogy`: released stock of the same material that was never issued is absent from the ancestry |
| Forward: raw lot → orders → finished batch → shipments → customers | `test_forward_traceability_and_impact_reach_the_customers_and_returns`: partial dispatch to two customers (30 delivered, 20 in transit); reachable from **either** raw lot |
| Impact analysis quantities | same test: produced 100, dispatched 50, delivered 30, available 50, 2 customers, 2 shipments, suspect consumed 60, suspect remaining 0 |
| Returns, reconciliation | `test_return_is_visible_downstream_and_reconciles`: 100 − 30 + 4 = 74 physical; rejected return stays blocked; balanced |
| Quantities reconcile; a mismatch is reported, never corrected | `test_reconciliation_balances_and_a_mismatch_is_reported_never_corrected`: a consumption row with no stock movement is inserted; the page flags it and the ledger is byte-for-byte unchanged |
| Batch split | `test_a_batch_split_across_warehouses_keeps_one_identity_and_both_locations`: 35 + 65 across two warehouses, transfer legs in the movements, net zero, balanced |
| QC history | `test_failed_result_and_retest_stay_in_the_batch_history`: a failing Assay then a passing retest, decision RELEASE; both attempts present |
| Permissions, masking, audit, read-only | `test_permissions_masking_audit_and_read_only`: operator gets 403; role without QC-release cannot run the analysis; running it adds exactly one audit row and changes no stock; supplier and customer names are "Restricted" without masters permissions |
| Bounded and paginated | `test_graph_is_bounded_and_lists_are_paginated`: depth 1 stops at the consumed lots and reports `truncated`; `max_depth=99` and `page_size=101` are rejected |
| Product 360 figures from records | `test_product_360_summary_is_built_from_records`: manufactured 100, dispatched 25, 1 order, 1 customer, stock 75, materials planned/consumed/lots, sales, batches, unknown product 404 |

Also: every node `ref` is text (regression for an integer `shipment_no` that reached the UI as a number).

**Mutation check.** With the consumption lookup disabled in the service, `test_backward…` and `test_permissions…` fail; restored, all 10 pass. The tests detect the thing they claim to protect.

**Whole-database check** (demo database): every one of the 116 batches reconciles with no warnings, and every node reference in every batch's backward and forward graph is text.

## Browser (`frontend/e2e/traceability.mjs`, 12 steps, Playwright)

Run against a demo-seeded stack (`BASE_URL=… node e2e/traceability.mjs`). No ids are hard-coded; the journey follows links, then confirms against the API as the same user.

1 sign in, find the product in Product 360 · 2 Formulations & BOM tab · 3 Raw materials tab, open a consumed supplier lot · 4 the lot's goods receipt, PO, supplier lot and a balanced reconciliation · 5 forward from the lot, open the production order from the diagram · 6 back on the product open a finished batch, backward genealogy contains order, receipt, PO, supplier and consumption edges, list view shows them · 7 inspections with results · 8 forward to the customer, open the shipment from the diagram · 9 run impact analysis (button disabled until a batch is ticked; quantities and status unchanged; audit trail shows it) · 10 back to the product: customers, production, quality, costing tabs · 11 an operator is refused by the API (403) and the page shows the permission message · 12 no console errors.

## Run results

See `TEST_EXECUTION_REGISTER.md` (2026-11-09, traceability milestone).

## Not yet covered

* The impact-analysis **hold** action is exercised by hand, not by an automated test.
* A keyboard-only pass through the diagram (nodes are buttons with labels; pan and zoom have buttons) has not been audited with a screen reader.
* No automated test of graphs near the 300-node cap (the demo's largest is about 60 nodes).
* The full backend suite and the existing Playwright suites were not re-run end to end, only the files listed in the register.
