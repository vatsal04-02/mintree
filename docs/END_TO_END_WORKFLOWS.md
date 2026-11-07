# End-to-End Workflows

Every stage below runs through the real domain services and database transactions; none is a decorative status. "Proof" names the automated test that exercises it; the demo seeder walks the same chain through the HTTP API and `demo verify` re-derives the results from the tables.

| Stage | Rule enforced (examples) | Proof |
|---|---|---|
| Purchase request → PO → approval | approval authority and limits, author ≠ approver, rejected PR/PO keep a reason, version conflicts refused | `test_purchase_gaps.py`, `test_procurement_returns_amendments.py`, `test_concurrency_finance.py::two_approvers…` |
| Goods receipt | cannot exceed the PO line, duplicate receipts replay (idempotency), batches created quarantined | `test_inventory_procurement.py`, `test_concurrency.py::…identical_receipts…`, `…cannot_over_receive…` |
| Incoming QC | server-side judgement of results, release by a different person, rejected batches never become available | `test_rnd_quality.py`, `test_quality_sampling_coa.py` |
| Inventory | ledger append-only (DB trigger), balances = ledger, reservations ≤ available, FEFO, QC-gated availability | `test_inventory_procurement.py`, integrity suite (39 checks) |
| R&D → approved formulation → BOM | 100 % rule, immutable approved versions, production pins the version, formula cost from stock/PO price | `test_rnd_quality.py`, `test_production.py`, `test_cashflow_and_formula_cost.py` |
| Production plan → reserve → issue → stages → yield/wastage → finished batch | material and capacity checks, wastage needs a reason, costing, genealogy | `test_production.py`, `test_production_plans.py`, `test_production_concurrency.py` |
| Finished-goods QC → release → CoA | CoA only for released inspections, frozen snapshot | `test_quality_sampling_coa.py` |
| Order → allocation → pick → pack → dispatch | allocation FEFO on released stock only, partial shipments with one invoice each, close-short | `test_partial_dispatch.py`, `test_dispatch.py`, `test_dispatch_concurrency.py` |
| Courier tracking | signed webhooks, duplicate events ignored, sequence kept | `test_dispatch.py`, demo seeding re-sends delivered events (9 duplicates asserted harmless) |
| Invoice → payment → accounting | balanced journals (DB trigger), three-way match, allocation ≤ balance, AR/AP/inventory control accounts reconcile | `test_accounts*.py`, `test_concurrency_finance.py`, `demo verify` |
| Returns, credit notes, supplier returns | return scoped to a shipment, restock/reject, credit note, purchase return lowers received qty | `test_partial_dispatch.py::return…`, `test_procurement_returns_amendments.py` |
| Marketplace import → dispatch → settlement | idempotent import, exception queue with retry, settlement posted to the books | `test_e2e_workflows.py::marketplace_order_through_every_module`, `test_accounts_gaps.py` |
| CRM lead → customer → quotation → order | no duplicate customer on conversion, stage transitions one step at a time | `test_crm_pipeline.py` |
| Maintenance | verify by a different user, downtime, calibration | `test_module_extras.py` |
| Meta content approval → schedule → publish | author ≠ approver, scheduled needs a time, sandbox never "connected" | `test_meta_workspace.py` |
| Cash flow | direct method, opening + net = closing, ties to the trial balance | `test_cashflow_and_formula_cost.py` |

Browser journeys (Playwright): `e2e/run.sh` (16 + 11 + 23), `e2e/run-demo.sh` (18 demo journeys), `e2e/run-ai.sh` (8 AI journeys).

## Not covered end-to-end (honest list)
Spare-part consumption to inventory, production material return, replacement shipment, refund flow, per-line PO delivery schedules and any live external integration are not implemented (see `ISSUE_REGISTER.md` O-002…O-004).
