# Demo traceability scenarios

The demo database (`python -m scripts.demo_data init`, see `DEMO_ENVIRONMENT.md`) already contains most traceability shapes, because it is built by running the real purchase, production, dispatch and quality workflows. One step, `depth_trace`, adds the two that were missing. **Nothing is inserted into a genealogy table** (there is none); every scenario below is ordinary transactional history that Batch 360 reads.

Sign in as `demo.admin@example.com` / `Mintree-Demo#2026` (or `demo.qa` for impact analysis). Batch numbers below were read from a freshly seeded demo database; the seeder is deterministic, so they are stable, but confirm with the SQL in each row if you re-seed after changing the demo steps. Open any of them at `/inventory/batches/{id}/traceability` (id in brackets).

| # | Scenario required | Where to see it | Why it qualifies |
|---|---|---|---|
| 1 | **Complete backward traceability** | `B260000017` [17] (Olive Body Butter, FG) → **Came from** | released, dispatched; production order `PRD/26/000004` consumed 3 raw lots + a packaging lot; each has its goods receipt, PO, supplier and incoming QC. Also `B260000016` [16], `B260000018` [18] |
| 2 | **Complete forward traceability** | `B260000017` [17] → **Went to** | 4+ shipments (`FAKE0000000004…8`), their sales orders, customers, a return, stored in `RM-WH`, reservation |
| 3 | **Batch split** | `B260000032` [32] | transferred 10 units to a second warehouse by `depth_trace` through the real transfer endpoint; one batch, two balances (Locations tab), `TRANSFER_OUT/IN` in Movements, reconciliation balanced |
| 4 | **Multi-source material** | `PRD/26/000016` (2 lots of `PM-JAR-150`), `PRD/26/000017` (`PM-JAR-60`), `PRD/26/000018` (`PM-BOTTLE-200`), `PRD/26/000020` | `SELECT po.number, i.item_code, count(DISTINCT pc.batch_id) FROM production_consumptions pc … HAVING count(DISTINCT pc.batch_id) > 1` |
| 5 | **One raw batch in many production orders** | `B260000059` [59] (Aloe, 12 orders), `B260000061` [61] (Kaolin, 8), `B260000062` [62] (Olive oil, 8) | Went to → 8–12 production orders and the batches they made |
| 6 | **QC failure and retest** | `B260000013` [13], inspection `QC/26/000013` | a failed result then a passing retest on the same test; both rows kept, "Retested" and "1 failed result" shown, decision RELEASE |
| 6b | **QC rejection** | `B260000012` [12], `B260000075` [75] | REJECTED: blocked, never available. The vitamin-E incoming rejection also has a deviation, a CAPA and a return to supplier (Quality → Deviations, Purchase → Returns to supplier) |
| 7 | **Impact / recall analysis** | `B260000004` [4] (raw lot) and `B260000008` [8] | widest reach in the demo: **7 customers**. `depth_trace` also ran the analysis as `demo.qa`, so the audit trail already has an `impact_analysis` entry (Audit trail tab) |
| 8 | **Return** | `B260000017` [17], `B260000018` [18] | customer return received, decided, credit-noted; the return node hangs off its shipment and the reconciliation shows returned units |
| 9 | **Partial dispatch, several shipments** | `B260000017` [17] | 60 + 30 + 20 + 3 PCS on four shipments: three delivered, one in transit |
| 10 | **Opening-stock lot** (no ancestry, by design) | any `opening_stock` batch | shows "Loaded at go-live" as its origin, not a made-up supplier |

Finding more: `SELECT id, batch_no FROM inventory_batches ORDER BY id` or the Batch traceability list (filter by type and QC status).

## What the seeded story does not include

* **Merged batches or child batches**: the application has no such operation, so there is no scenario for it (see `PRODUCT_360_ARCHITECTURE.md`).
* **Material returned from production to stock**: no such movement exists.
* A **recall case** (customer notification, regulator report): only the analysis and an explicit QC hold are built.

## Reproducing

```bash
cd backend && export MINTREE_ENVIRONMENT=demo MINTREE_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5433/mintree_demo
python -m scripts.demo_data reset --confirm-reset-demo-database mintree_demo
python -m scripts.demo_data init        # 34 steps; second run creates nothing
python -m scripts.demo_data verify
```
