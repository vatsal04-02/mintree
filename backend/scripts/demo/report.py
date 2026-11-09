"""Coverage report: how many records the demo database holds per module, measured from the tables (never from the seeder's own counters)."""
from __future__ import annotations

from sqlalchemy import text

from app.core.db import SessionLocal

# label -> (SQL returning one number, target for the full-scale demo or None)
COVERAGE: list[tuple[str, str, str, int | None]] = [
    ("Sales", "Sales orders (all channels)", "SELECT count(*) FROM sales_orders", 100),
    ("Sales", "  of which direct / B2B / distributor / brand / manual", "SELECT count(*) FROM sales_orders WHERE channel <> 'MARKETPLACE'", 100),
    ("Sales", "Sales quotations", "SELECT count(*) FROM sales_quotations", None),
    ("E-commerce", "Marketplace orders imported", "SELECT count(*) FROM external_orders", 100),
    ("E-commerce", "Marketplace settlements", "SELECT count(*) FROM marketplace_settlements", None),
    ("E-commerce", "Marketplace returns", "SELECT count(*) FROM marketplace_returns", None),
    ("CRM", "Leads", "SELECT count(*) FROM leads", None),
    ("CRM", "Opportunities", "SELECT count(*) FROM opportunities", None),
    ("CRM", "Leads + opportunities", "SELECT (SELECT count(*) FROM leads) + (SELECT count(*) FROM opportunities)", 100),
    ("CRM", "Activities and follow-ups", "SELECT (SELECT count(*) FROM crm_activities) + (SELECT count(*) FROM followups)", None),
    ("Inventory", "Stock ledger movements", "SELECT count(*) FROM stock_ledger", 100),
    ("Inventory", "Batches / lots", "SELECT count(*) FROM inventory_batches", 100),
    ("Procurement", "Purchase orders", "SELECT count(*) FROM purchase_orders", None),
    ("Procurement", "Goods receipts", "SELECT count(*) FROM goods_receipts", None),
    ("Production", "Production orders", "SELECT count(*) FROM production_orders", None),
    ("Production", "Production records (orders + stages + material lines + consumptions + wastage)",
     "SELECT (SELECT count(*) FROM production_orders) + (SELECT count(*) FROM production_stages) + (SELECT count(*) FROM production_materials) + (SELECT count(*) FROM production_consumptions) + (SELECT count(*) FROM wastage_records)", 100),
    ("Quality", "Inspections", "SELECT count(*) FROM inspections", None),
    ("Quality", "Inspection results", "SELECT count(*) FROM inspection_results", 100),
    ("Dispatch", "Shipments", "SELECT count(*) FROM shipments", 100),
    ("Dispatch", "Courier tracking events", "SELECT count(*) FROM courier_events", None),
    ("Accounts", "Sales invoices", "SELECT count(*) FROM sales_invoices", None),
    ("Accounts", "Supplier invoices", "SELECT count(*) FROM supplier_invoices", None),
    ("Accounts", "Payments (received + paid)", "SELECT count(*) FROM payments", None),
    ("Accounts", "Financial transactions (journal entries)", "SELECT count(*) FROM journal_entries", 100),
    ("Masters", "Master records (items, suppliers, customers, warehouses, locations, brands)",
     "SELECT (SELECT count(*) FROM items) + (SELECT count(*) FROM suppliers) + (SELECT count(*) FROM customers) + (SELECT count(*) FROM warehouses) + (SELECT count(*) FROM storage_locations) + (SELECT count(*) FROM brands)", 100),
    ("Meta (simulated)", "Meta connections (sandbox)", "SELECT count(*) FROM meta_connections WHERE mode = 'DEMO'", None),
    ("Meta (simulated)", "Campaigns", "SELECT count(*) FROM marketing_campaigns", None),
    ("Meta (simulated)", "Content items", "SELECT count(*) FROM content_calendar", None),
    ("Meta (simulated)", "Campaign + content records", "SELECT (SELECT count(*) FROM marketing_campaigns) + (SELECT count(*) FROM content_calendar)", 30),
    ("Meta (simulated)", "Simulated daily metrics", "SELECT count(*) FROM campaign_metrics WHERE source = 'DEMO'", None),
    ("Maintenance", "Equipment / work orders", "SELECT (SELECT count(*) FROM equipment) + (SELECT count(*) FROM maintenance_work_orders)", None),
    # ---- depth (depth.py): the 12-record minimum per module, as measured from the tables
    ("Procurement", "Purchase requests (every state)", "SELECT count(*) FROM purchase_requests", 12),
    ("Procurement", "RFQs", "SELECT count(*) FROM rfqs", 12),
    ("Procurement", "Supplier quotations", "SELECT count(*) FROM quotations", 12),
    ("Procurement", "Purchase returns", "SELECT count(*) FROM purchase_returns", None),
    ("R&D", "Development projects", "SELECT count(*) FROM development_projects", 12),
    ("R&D", "Formulations", "SELECT count(*) FROM formulations", 12),
    ("R&D", "Formulation versions", "SELECT count(*) FROM formulation_versions", None),
    ("Production", "Approved BOMs", "SELECT count(*) FROM boms WHERE status = 'ACTIVE'", 12),
    ("Production", "Production plans", "SELECT count(*) FROM production_plans", None),
    ("Quality", "Deviations", "SELECT count(*) FROM deviations", 12),
    ("Quality", "Certificates of analysis", "SELECT count(*) FROM certificates_of_analysis", 12),
    ("Inventory", "Stock counts (approved or cancelled)", "SELECT count(*) FROM stocktakes", None),
    ("CRM", "Customer contacts", "SELECT count(*) FROM customer_contacts", 12),
    ("CRM", "Customer complaints", "SELECT count(*) FROM complaints", 12),
    ("Maintenance", "Equipment", "SELECT count(*) FROM equipment", 12),
    ("Maintenance", "Work orders", "SELECT count(*) FROM maintenance_work_orders", 12),
    ("Documents", "Documents (with versions and links)", "SELECT count(*) FROM documents", 12),
    ("Approvals", "Approval decisions recorded", "SELECT count(*) FROM approval_records", 12),
    ("Notifications", "Notifications delivered to users", "SELECT count(*) FROM user_notifications", 12),    ("Audit", "Audit log rows", "SELECT count(*) FROM audit_logs", None),
]


def collect() -> list[dict]:
    out = []
    with SessionLocal() as s:
        for module, label, sql, target in COVERAGE:
            n = s.execute(text(sql)).scalar() or 0
            out.append({"module": module, "label": label, "count": int(n), "target": target, "met": target is None or n >= target})
    return out


def print_table(rows: list[dict]) -> None:
    last = None
    for r in rows:
        if r["module"] != last:
            print(f"\n{r['module']}")
            last = r["module"]
        mark = "" if r["target"] is None else ("  [target %d: met]" % r["target"] if r["met"] else "  [target %d: NOT MET]" % r["target"])
        print(f"  {r['label']:<92}{r['count']:>8}{mark}")


def markdown(rows: list[dict]) -> str:
    lines = ["| Module | Records | Count | Target | Met |", "|---|---|---:|---:|---|"]
    for r in rows:
        lines.append(f"| {r['module']} | {r['label'].strip()} | {r['count']} | {r['target'] if r['target'] else ''} | {'yes' if r['met'] and r['target'] else ('' if not r['target'] else 'NO')} |")
    return "\n".join(lines)
