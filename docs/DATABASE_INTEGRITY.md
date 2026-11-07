# Database Integrity

The authoritative description is `DATA_INTEGRITY.md` (triggers, constraints, the 39-check suite) and `DATABASE_SCHEMA_MAP.md` (generated from the migrated database: 148 tables at revision 0026). This page records what changed in the performance & completeness milestone.

* **New constraints (migration 0026):** `meta_connections` — `mode_status_consistent` (DEMO ⇔ SANDBOX; LIVE is NOT_CONNECTED or CONNECTED) and `connected_means_verified` (CONNECTED requires `verified_at`), so a fabricated "connected" Meta state is impossible even through raw SQL (tested); campaign `objective_valid`, status now includes PAUSED; content `SCHEDULED` requires an approver and a time.
* **New demo-level reconciliation checks** (`demo verify`, 117 checks): accounts receivable (1200) = open sales invoices; accounts payable (2000) = open supplier invoices; inventory accounts (1300/1310/1320) = stock valuation + WIP of open production orders. Finding recorded while building them: the inventory difference of 19,234.62 was exactly the WIP of two in-progress orders (a correct state, so the invariant includes WIP).
* **Integrity suite:** 39 checks, run by `scripts/check_integrity.py` (also inside `demo verify` and after load runs).
* **Caches never hold authoritative state.** Session/permission/response caches are validated against Redis versions and bypassed when Redis is unavailable; stock allocation, QC release and every posting read the database inside the transaction.
