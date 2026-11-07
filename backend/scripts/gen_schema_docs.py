"""Generate docs/DATABASE_SCHEMA_MAP.md and docs/ENTITY_RELATIONSHIP_MAP.md from a MIGRATED database (so they cannot drift from the real schema).

    MINTREE_DATABASE_URL=postgresql+psycopg://.../some_migrated_db python -m scripts.gen_schema_docs
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text

from app.core.db import engine

DOCS = Path(__file__).resolve().parents[2] / "docs"

# explicit domain ownership (table-name prefixes / names). Anything unmatched is listed under "Unassigned" so it is visible, never silently dropped.
DOMAINS: list[tuple[str, list[str]]] = [
    ("AI layer", ["ai_conversations", "ai_messages", "ai_tool_calls", "ai_usage", "ai_proposals", "ai_extractions", "ai_briefings"]),
    ("Platform & infrastructure", ["alembic_version", "idempotency_keys", "outbox_events", "processed_events", "job_failures", "webhook_events", "integration_logs", "document_counters", "demo_seed_steps", "company_settings", "notifications"]),
    ("Identity, access & audit", ["users", "roles", "permissions", "user_roles", "role_permissions", "sessions", "departments", "audit_logs"]),
    ("Master data", ["units_of_measure", "unit_conversions", "product_categories", "items", "suppliers", "customers", "customer_contacts", "customer_prices", "brands", "warehouses", "storage_locations",
                     "tax_classes", "payment_methods", "courier_partners"]),
    ("Inventory", ["inventory_batches", "stock_balances", "stock_ledger", "stock_reservations", "stocktakes", "stocktake_lines", "opening_stock_postings", "item_valuation"]),
    ("Procurement", ["purchase_requests", "purchase_request_items", "rfqs", "rfq_items", "rfq_suppliers", "quotations", "quotation_items", "purchase_orders", "purchase_order_items", "po_amendments",
                     "po_amendment_lines", "goods_receipts", "goods_receipt_items", "purchase_returns", "purchase_return_lines", "supplier_invoices", "supplier_invoice_lines", "debit_notes"]),
    ("R&D & formulation", ["development_projects", "formulations", "formulation_versions", "formulation_ingredients", "lab_trials", "trial_observations"]),
    ("Production", ["boms", "bom_lines", "production_orders", "production_materials", "production_stages", "production_consumptions", "wastage_records", "work_centres", "production_plans", "production_plan_lines"]),
    ("Quality", ["quality_specs", "quality_spec_tests", "sampling_plans", "inspections", "inspection_results", "process_checks", "deviations", "capa_actions", "certificates_of_analysis"]),
    ("Dispatch & logistics", ["sales_orders", "sales_order_items", "shipments", "shipment_lines", "shipment_packages", "delivery_attempts", "proofs_of_delivery", "courier_events", "pick_scans", "sales_returns",
                              "sales_return_lines", "credit_notes"]),
    ("CRM & sales", ["leads", "lead_sources", "opportunities", "opportunity_stage_history", "crm_activities", "followups", "sales_quotations", "sales_quotation_items", "complaints"]),
    ("Accounts & finance", ["accounts", "accounting_periods", "journal_entries", "journal_lines", "sales_invoices", "payments", "payment_allocations", "bank_accounts", "bank_transactions"]),
    ("E-commerce & marketplaces", ["marketplace_accounts", "sku_mappings", "external_orders", "external_order_items", "marketplace_settlements", "marketplace_returns"]),
    ("Maintenance", ["equipment", "maintenance_schedules", "maintenance_work_orders", "downtime_records", "spare_part_usage", "calibration_records"]),
    ("Branding & marketing", ["marketing_campaigns", "content_calendar", "brand_assets", "publishing_records", "social_accounts", "campaign_metrics"]),
    ("Approvals, notifications & documents", ["approval_policies", "approval_records", "approval_delegations", "user_notifications", "notification_preferences", "documents", "document_versions", "document_links"]),
]


def _domain_of(t: str) -> str:
    for name, tables in DOMAINS:
        if t in tables:
            return name
    return "Unassigned"


def main() -> None:
    with engine.connect() as c:
        tables = [r[0] for r in c.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1"))]
        cols = defaultdict(list)
        for t, col, typ, nullable, ml, np_, ns in c.execute(text("""SELECT table_name, column_name, data_type, is_nullable, character_maximum_length, numeric_precision, numeric_scale
                                                                       FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name, ordinal_position""")):
            ty = typ.replace("timestamp with time zone", "timestamptz").replace("character varying", "varchar").replace("double precision", "float8")
            if typ == "numeric" and np_:
                ty = f"numeric({np_},{ns})"
            elif ml:
                ty = f"{ty}({ml})"
            cols[t].append((col, ty, nullable == "YES"))
        pks = defaultdict(list)
        fks = []
        checks = defaultdict(int)
        uniques = defaultdict(int)
        for t, kind, defn in c.execute(text("SELECT cl.relname, co.contype, pg_get_constraintdef(co.oid) FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid WHERE cl.relnamespace = 'public'::regnamespace")):
            if kind == "p":
                pks[t] = re.findall(r"\((.*)\)", defn)[0].split(", ")
            elif kind == "f":
                m = re.match(r"FOREIGN KEY \((.*?)\) REFERENCES (\w+)\((.*?)\)", defn)
                fks.append((t, m.group(1), m.group(2)))
            elif kind == "c":
                checks[t] += 1
            elif kind == "u":
                uniques[t] += 1
        idx = defaultdict(int)
        for t, n in c.execute(text("SELECT tablename, count(*) FROM pg_indexes WHERE schemaname='public' GROUP BY 1")):
            idx[t] = n
        trig = defaultdict(list)
        for t, n in c.execute(text("SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal AND c.relnamespace='public'::regnamespace ORDER BY 1,2")):
            trig[t].append(n)
        rev = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    tables = [t for t in tables if not re.match(r"^(dl_|rb_|fk_c7b4)", t)]            # scratch tables created by tests/test_uow.py, never part of a migration
    by_domain = defaultdict(list)
    for t in tables:
        by_domain[_domain_of(t)].append(t)
    order = [d for d, _ in DOMAINS] + ["Unassigned"]

    out = ["# Database Schema Map", "",
           f"Generated by `backend/scripts/gen_schema_docs.py` from a database migrated to Alembic revision **{rev}**: {len(tables)} tables, "
           f"{sum(checks[t] for t in tables)} CHECK constraints, {sum(uniques[t] for t in tables)} UNIQUE constraints, {sum(1 for f in fks if f[0] in tables)} foreign keys, {sum(idx[t] for t in tables)} indexes. "
           "Regenerate after any migration; do not edit by hand.", "",
           "Conventions: money is `numeric(18,2)`, quantities `numeric(18,3/4)` (never float); instants are `timestamptz` (UTC); calendar dates are `date` in the business zone Asia/Kolkata (see DATABASE_ARCHITECTURE.md, Time). "
           "Status columns carry CHECK constraints. `Triggers` lists non-internal database triggers (append-only ledgers, journal balance, frozen certificates).", ""]
    for d in order:
        if not by_domain.get(d):
            continue
        out += [f"## {d}", "", "| Table | Primary key | Columns | FKs out | CHECK | UNIQUE | Indexes | Triggers |", "|---|---|---|---|---|---|---|---|"]
        for t in by_domain[d]:
            out.append(f"| `{t}` | {', '.join(pks[t])} | {len(cols[t])} | {sum(1 for f in fks if f[0] == t)} | {checks[t]} | {uniques[t]} | {idx[t]} | {', '.join(trig[t]) or '-'} |")
        out.append("")
    out += ["## Column dictionary", ""]
    for d in order:
        for t in by_domain.get(d, []):
            out += [f"### `{t}`  ·  {d}", "", "| Column | Type | Null |", "|---|---|---|"] + [f"| {n} | {ty} | {'yes' if nl else 'no'} |" for n, ty, nl in cols[t]] + [""]
    (DOCS / "DATABASE_SCHEMA_MAP.md").write_text("\n".join(out))

    er = ["# Entity Relationship Map", "",
          f"Generated by `backend/scripts/gen_schema_docs.py` (Alembic revision **{rev}**). Each diagram lists the foreign keys that start in that domain; a table drawn without attributes belongs to another domain. "
          "Cross-domain links are exactly the places where one module reuses another module's master record (items, suppliers, customers, warehouses, batches, users).", ""]
    names = {t: re.sub(r"\W", "_", t) for t in tables}
    for d in order:
        ts = set(by_domain.get(d, []))
        edges = sorted({(a, b, col) for a, col, b in fks if a in ts and b in names})
        if not ts or not edges:
            continue
        er += [f"## {d}", "", "```mermaid", "erDiagram"]
        for a, b, col in edges:
            er.append(f"    {names[b]} ||--o{{ {names[a]} : \"{col}\"")
        er += ["```", ""]
    cross = defaultdict(set)
    dom = {t: _domain_of(t) for t in tables}
    for a, col, b in fks:
        if a in dom and b in dom and dom[a] != dom[b]:
            cross[(dom[a], dom[b])].add(b)
    er += ["## Cross-domain references (who reuses whose records)", "", "| Domain | References records owned by | Via tables |", "|---|---|---|"]
    for (a, b), ts in sorted(cross.items()):
        er.append(f"| {a} | {b} | {', '.join(f'`{t}`' for t in sorted(ts))} |")
    (DOCS / "ENTITY_RELATIONSHIP_MAP.md").write_text("\n".join(er) + "\n")
    # ---- DATA_INTEGRITY.md: what is enforced where
    from scripts.check_integrity import CHECKS
    tr_rows = []
    with engine.connect() as c:
        for t, n, fn_src in c.execute(text("""SELECT c.relname, t.tgname, pg_get_functiondef(t.tgfoid) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                                                WHERE NOT t.tgisinternal AND c.relnamespace='public'::regnamespace ORDER BY 1,2""")):
            m = re.search(r"RAISE EXCEPTION '([^']+)'", fn_src)
            tr_rows.append((t, n, m.group(1) if m else "-"))
    di = ["# Data Integrity", "",
          "Integrity is enforced in layers; each layer catches what the one above it could miss. Generated by `backend/scripts/gen_schema_docs.py`.", "",
          "1. **Domain services** validate and compute (status transitions, limits, tax, costing) and take row locks in a fixed order.",
          "2. **PostgreSQL constraints** are the last line of defence: CHECK (status sets, non-negative stock, `reserved + blocked <= physical`, `no_over_receipt`, `shipped_returned_within`, "
          "opportunity/lead consistency), UNIQUE (document numbers, `(supplier, delivery note)`, `(channel, external_ref)`, ledger `(source, line, leg, movement)`), FOREIGN KEYS, and partial unique indexes "
          "(one open inspection per batch+stage, one pending amendment per PO, one active BOM per item).",
          "3. **Database triggers** make ledgers append-only and unbalanced journals impossible (listed below).",
          "4. **The integrity suite** (`backend/scripts/check_integrity.py`) re-derives every invariant from the tables after the fact. It runs inside the test-suite after workflows, and in `demo_data init/verify`.",
          "5. **Demo verification** (`backend/scripts/demo/verify.py`) adds scenario checks, dashboard-vs-table reconciliation and role-enforcement checks to the integrity suite.", "",
          f"## Database triggers ({len(tr_rows)})", "", "| Table | Trigger | Rule (message raised) |", "|---|---|---|"] + [f"| `{t}` | `{n}` | {m} |" for t, n, m in tr_rows] + [
          "", f"## Integrity suite: {len(CHECKS)} checks", "", "Each check is a query returning the *violating* rows; zero rows = pass.", "", "| # | Check | What must always be true |", "|---|---|---|"]
    di += [f"| {i} | `{name}` | {desc} |" for i, (name, desc, _sql) in enumerate(CHECKS, 1)]
    di += ["", "## Money, quantity and time rules", "",
           "* Money is `numeric(18,2)`, quantities `numeric(18,3)` or `(18,4)`; application arithmetic uses `Decimal` only. Rounding is explicit (`ROUND_HALF_UP`) and applied per document line; when a line is billed in parts, the final part takes the exact remainder so documents always sum to the order.",
           "* Instants are `timestamptz` stored in UTC; every connection pins `timezone=UTC`. Business dates (invoice date, due date, planned date, expiry) are `date` values in `Asia/Kolkata` produced by `app.core.clock.business_today()` / SQL `business_today()`; `date.today()`, `current_date` and `timestamptz::date` are banned by `tests/test_business_clock.py`.",
           "* Posted journals, stock-ledger rows, inspection results and Certificates of Analysis are append-only; corrections are reversals or new rows, never edits.", ""]
    (DOCS / "DATA_INTEGRITY.md").write_text("\n".join(di))
    unassigned = by_domain.get("Unassigned", [])
    print(f"{len(tables)} tables, {len(unassigned)} unassigned: {unassigned}")


if __name__ == "__main__":
    main()
