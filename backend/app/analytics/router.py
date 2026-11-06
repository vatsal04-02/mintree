"""Management analytics: every number is computed from the underlying records (no stored summaries), and each KPI names its source
so it can be drilled into. Read-only. Figures come from several statements under READ COMMITTED, so postings that land mid-request can make
two KPIs differ by that posting; this is an operational summary, not a closing statement (use /accounts/reports for period-end figures)."""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require

router = APIRouter(prefix="/analytics", tags=["analytics"])
DB = Depends(get_db, scope="function")


def _num(v):
    return v if v is not None else 0


@router.get("/management-summary")
def management_summary(as_of: date | None = None, db: Session = DB, _: CurrentUser = Depends(require("analytics.dashboard:read"))):
    d = as_of or date.today()
    one = lambda sql, **p: db.execute(text(sql), {"d": d, **p}).mappings().one()
    rows = lambda sql, **p: [dict(r) for r in db.execute(text(sql), {"d": d, **p}).mappings().all()]

    sales = one("""SELECT COALESCE(sum(subtotal),0) revenue, count(*) invoices, COALESCE(sum(total - amount_paid - credited_amount),0) receivable
                     FROM sales_invoices WHERE invoice_date <= :d""")
    credits = one("SELECT COALESCE(sum(subtotal),0) AS credited FROM credit_notes WHERE created_at::date <= :d")
    inv = one("SELECT COALESCE(sum(value),0) value FROM item_valuation")
    inv_kind = rows("""SELECT i.kind, COALESCE(sum(v.value),0) AS value, COALESCE(sum(v.qty),0) AS qty FROM item_valuation v JOIN items i ON i.id = v.item_id GROUP BY i.kind ORDER BY i.kind""")
    payable = one("""SELECT COALESCE(sum(l.credit - l.debit),0) ap FROM journal_lines l JOIN accounts a ON a.id = l.account_id
                      JOIN journal_entries e ON e.id = l.entry_id WHERE a.code = '2000' AND e.entry_date <= :d""")
    cash = one("""SELECT COALESCE(sum(l.debit - l.credit),0) cash FROM journal_lines l JOIN accounts a ON a.id = l.account_id
                   JOIN journal_entries e ON e.id = l.entry_id WHERE a.code IN ('1000','1100') AND e.entry_date <= :d""")
    cogs = one("""SELECT COALESCE(sum(l.debit - l.credit),0) cogs FROM journal_lines l JOIN accounts a ON a.id = l.account_id
                   JOIN journal_entries e ON e.id = l.entry_id WHERE a.code = '5000' AND e.entry_date <= :d""")
    orders = rows("SELECT status, count(*) AS n, COALESCE(sum(total),0) AS value FROM sales_orders GROUP BY status ORDER BY status")
    prod = rows("SELECT status, count(*) AS n FROM production_orders GROUP BY status ORDER BY status")
    yield_ = one("SELECT round(avg(yield_pct),2) avg_yield FROM production_orders WHERE status = 'COMPLETED'")
    qc = one("""SELECT (SELECT count(*) FROM inspections WHERE status IN ('PENDING','IN_PROGRESS')) AS open_inspections,
                       (SELECT count(*) FROM inventory_batches WHERE qc_status = 'HOLD') AS batches_on_hold,
                       (SELECT count(*) FROM inventory_batches WHERE qc_status = 'QUARANTINE') AS batches_quarantined,
                       (SELECT count(*) FROM deviations WHERE status <> 'CLOSED') AS open_deviations,
                       (SELECT count(*) FROM capa_actions WHERE status NOT IN ('VERIFIED','CLOSED')) AS open_capa""")
    low = rows("""SELECT i.item_code, i.name, COALESCE(sum(sb.qty_available),0) AS available, i.reorder_level
                    FROM items i LEFT JOIN stock_balances sb ON sb.item_id = i.id
                   WHERE i.is_active AND i.reorder_level IS NOT NULL AND i.reorder_level > 0
                   GROUP BY i.id HAVING COALESCE(sum(sb.qty_available),0) < i.reorder_level ORDER BY i.item_code LIMIT 20""")
    po = rows("SELECT status, count(*) AS n FROM purchase_orders GROUP BY status ORDER BY status")
    ship = rows("SELECT status, count(*) AS n FROM shipments GROUP BY status ORDER BY status")
    equip = rows("SELECT status, count(*) AS n FROM equipment GROUP BY status ORDER BY status")
    ext = rows("SELECT status, count(*) AS n FROM external_orders GROUP BY status ORDER BY status")
    cmp_ = one("SELECT count(*) FILTER (WHERE status <> 'RESOLVED') AS open_complaints FROM complaints")
    return {
        "as_of": d,
        "sales": {"revenue_ex_tax": sales["revenue"], "credit_notes_ex_tax": credits["credited"], "net_revenue_ex_tax": sales["revenue"] - credits["credited"], "invoices": sales["invoices"],
                  "receivable_outstanding": sales["receivable"], "cogs": cogs["cogs"], "source": "sales_invoices, credit_notes, journal_lines(5000)"},
        "finance": {"payable_outstanding": payable["ap"], "cash_and_bank": cash["cash"], "inventory_value": inv["value"], "inventory_by_kind": inv_kind,
                    "source": "journal_lines(2000,1000,1100), item_valuation"},
        "orders_by_status": orders, "production_by_status": prod, "average_yield_pct": yield_["avg_yield"],
        "quality": dict(qc), "low_stock": low, "purchase_orders_by_status": po, "shipments_by_status": ship,
        "equipment_by_status": equip, "marketplace_orders_by_status": ext, "open_complaints": cmp_["open_complaints"],
    }
