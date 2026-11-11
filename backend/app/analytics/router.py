"""Management analytics: every number is computed from the underlying records (no stored summaries), and each KPI names its source
so it can be drilled into. Read-only. Figures come from several statements under READ COMMITTED, so postings that land mid-request can make
two KPIs differ by that posting; this is an operational summary, not a closing statement (use /accounts/reports for period-end figures)."""
import json
from datetime import date, timedelta

from fastapi import HTTPException, APIRouter, Depends, Query, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import rcache
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.clock import business_today

router = APIRouter(prefix="/analytics", tags=["analytics"])
DB = Depends(get_db, scope="function")


def _num(v):
    return v if v is not None else 0


DASHBOARD_TTL_S = 15.0


def _cached_dashboard(ns: str, key: str, compute, fresh: bool = False) -> Response:
    """Dashboards are aggregates over many tables; serving them from a 15 s cache keeps ten browsers from running the same ten queries ten times. The figures say so:
    `meta.cached`, `meta.generated_at` and `meta.max_age_s` travel with every response (the Overview shows the as-of time). Single-flight: concurrent misses compute once."""
    body, ver = (None, None) if fresh else rcache.lookup(ns, key, DASHBOARD_TTL_S, check_version=False)       # ?fresh=true recomputes now (and refreshes the cache)
    if body is None:
        with rcache.single_flight(ns, key):
            body, ver = (None, None) if fresh else rcache.lookup(ns, key, DASHBOARD_TTL_S, check_version=False)
            if body is None:
                from datetime import datetime, timezone
                data = jsonable_encoder(compute())
                data["meta"] = {"cached": False, "generated_at": datetime.now(timezone.utc).isoformat(), "max_age_s": DASHBOARD_TTL_S}
                body = json.dumps(data).encode()
                rcache.store(ns, key, "ttl", body)
                return Response(body, media_type="application/json", headers={"X-Cache": "miss"})
    data = json.loads(body)
    data["meta"]["cached"] = True
    return Response(json.dumps(data).encode(), media_type="application/json", headers={"X-Cache": "hit"})


@router.get("/overview-period")
def overview_period(start: date, end: date, fresh: bool = False, db: Session = DB, _: CurrentUser = Depends(require("analytics.dashboard:read"))):
    return _cached_dashboard("dash_period", f"{start}|{end}", lambda: _overview_period(start, end, db), fresh)


def _overview_period(start: date, end: date, db: Session):
    """Period-scoped figures the Overview needs and the all-time summary does not carry. Read-only; business dates (Asia/Kolkata) throughout.
    revenue: net ex-tax = invoice subtotals dated in the period minus credit-note subtotals created in it, plus the same for the previous period of equal length.
    production: completed orders bucketed by ISO week of completion (business date, Asia/Kolkata); weeks with no completed order have null planned/actual (no data), not zero.
    "planned" is the planned quantity of the orders completed that week, so planned and actual always describe the same orders. The first and last weeks are usually partial: each bucket carries
    from/to/days/partial so the UI can say so.
    recent: the latest documents across modules for the transactions table."""
    if end < start:
        raise HTTPException(422, "end is before start")
    if (end - start).days > 400:
        raise HTTPException(422, "The period cannot exceed 400 days")
    span = (end - start).days + 1
    prev_end, prev_start = start - timedelta(days=1), start - timedelta(days=span)
    rev = lambda a, b: db.execute(text("""SELECT (SELECT COALESCE(sum(subtotal),0) FROM sales_invoices WHERE invoice_date BETWEEN :a AND :b)
                                               - (SELECT COALESCE(sum(subtotal),0) FROM credit_notes WHERE business_date(created_at) BETWEEN :a AND :b) AS net,
                                              (SELECT count(*) FROM sales_invoices WHERE invoice_date BETWEEN :a AND :b) AS invoices"""), {"a": a, "b": b}).mappings().one()
    cur, prev = rev(start, end), rev(prev_start, prev_end)
    weeks = {r["wk"]: r for r in db.execute(text("""SELECT date_trunc('week', business_date(completed_at))::date AS wk, count(*) AS orders, sum(planned_qty) AS planned, sum(actual_qty) AS actual
                                                      FROM production_orders WHERE status = 'COMPLETED' AND business_date(completed_at) BETWEEN :a AND :b GROUP BY 1"""), {"a": start, "b": end}).mappings()}
    buckets, wk = [], start - timedelta(days=start.weekday())
    while wk <= end:
        r = weeks.get(wk)
        first, last = max(wk, start), min(wk + timedelta(days=6), end)          # the part of this ISO week that lies inside the selected period
        buckets.append({"week_start": wk, "from": first, "to": last, "days": (last - first).days + 1, "partial": (last - first).days < 6,
                        "orders": r["orders"] if r else 0, "planned": str(r["planned"]) if r else None, "actual": str(r["actual"]) if r else None})
        wk += timedelta(days=7)
    recent = [dict(r) for r in db.execute(text("""
        SELECT * FROM (
          SELECT 'purchase_order' AS kind, p.id, p.number AS reference, s.name AS related, p.status, p.created_at FROM purchase_orders p JOIN suppliers s ON s.id = p.supplier_id
          UNION ALL SELECT 'production_order', o.id, o.number, i.name, o.status, o.created_at FROM production_orders o JOIN items i ON i.id = o.output_item_id
          UNION ALL SELECT 'sales_order', so.id, so.number, c.name, so.status, so.created_at FROM sales_orders so JOIN customers c ON c.id = so.customer_id
          UNION ALL SELECT 'shipment', sh.id, COALESCE(sh.awb, 'Shipment ' || sh.id), so.number, sh.status, sh.created_at FROM shipments sh JOIN sales_orders so ON so.id = sh.so_id
          UNION ALL SELECT 'sales_invoice', inv.id, inv.number, c.name, CASE WHEN inv.total - inv.amount_paid - inv.credited_amount <= 0 THEN 'PAID' WHEN inv.amount_paid > 0 THEN 'PARTIAL' ELSE 'OPEN' END, inv.created_at
            FROM sales_invoices inv JOIN customers c ON c.id = inv.customer_id
        ) x ORDER BY created_at DESC, id DESC LIMIT 8""")).mappings()]
    od = db.execute(text("""SELECT count(*) AS n, COALESCE(sum(i.total - i.amount_paid - i.credited_amount),0) AS amount,
                                   COALESCE(max(business_today() - (i.invoice_date + COALESCE(c.credit_days, 0))), 0) AS oldest_days FROM sales_invoices i JOIN customers c ON c.id = i.customer_id
                             WHERE i.total - i.amount_paid - i.credited_amount > 0 AND business_today() > i.invoice_date + COALESCE(c.credit_days, 0)""")).mappings().one()
    # how long each kind of exception has been waiting, and what money is tied up in it, so the Overview can say "9 late, oldest 14 days, ₹4.2L" instead of just a count
    late_po = db.execute(text("""SELECT count(*) AS n, COALESCE(sum(total_amount), 0) AS amount, COALESCE(max(business_today() - expected_date), 0) AS oldest_days FROM purchase_orders
                                  WHERE status IN ('APPROVED','PARTIALLY_RECEIVED') AND expected_date < business_today()""")).mappings().one()
    late_prod = db.execute(text("""SELECT count(*) AS n, COALESCE(max(business_today() - planned_date), 0) AS oldest_days FROM production_orders
                                    WHERE planned_date < business_today() AND status NOT IN ('COMPLETED','CANCELLED')""")).mappings().one()
    late_ship = db.execute(text("""SELECT count(*) AS n, COALESCE(max(floor(EXTRACT(EPOCH FROM now() - created_at) / 86400)), 0)::int AS oldest_days FROM shipments
                                    WHERE status NOT IN ('DELIVERED','RTO','FAILED_DELIVERY') AND created_at < now() - interval '5 days'""")).mappings().one()
    return {"overdue_invoices": {"count": od["n"], "amount": od["amount"], "oldest_days": od["oldest_days"]},
            "exposure": {"late_purchase_orders": dict(late_po), "late_production": dict(late_prod), "late_shipments": dict(late_ship)}, "start": start, "end": end, "previous": {"start": prev_start, "end": prev_end},
            "revenue": {"net_ex_tax": cur["net"], "invoices": cur["invoices"], "previous_net_ex_tax": prev["net"], "previous_invoices": prev["invoices"]},
            "production": {"granularity": "week", "buckets": buckets, "weeks_with_data": sum(1 for b in buckets if b["orders"])}, "recent": recent}


@router.get("/management-summary")
def management_summary(as_of: date | None = None, fresh: bool = False, db: Session = DB, _: CurrentUser = Depends(require("analytics.dashboard:read"))):
    d = as_of or business_today()
    return _cached_dashboard("dash_summary", str(d), lambda: _management_summary(d, db), fresh)


def _management_summary(d: date, db: Session):
    one = lambda sql, **p: db.execute(text(sql), {"d": d, **p}).mappings().one()
    rows = lambda sql, **p: [dict(r) for r in db.execute(text(sql), {"d": d, **p}).mappings().all()]

    sales = one("""SELECT COALESCE(sum(subtotal),0) revenue, count(*) invoices, COALESCE(sum(total - amount_paid - credited_amount),0) receivable
                     FROM sales_invoices WHERE invoice_date <= :d""")
    credit_notes_total = one("SELECT COALESCE(sum(subtotal),0) AS credited FROM credit_notes WHERE business_date(created_at) <= :d")
    inv = one("SELECT COALESCE(sum(value),0) value FROM item_valuation")
    inv_kind = rows("""SELECT i.kind, COALESCE(sum(v.value),0) AS value, COALESCE(sum(v.qty),0) AS qty FROM item_valuation v JOIN items i ON i.id = v.item_id GROUP BY i.kind ORDER BY i.kind""")
    payable = one("""SELECT COALESCE(sum(l.credit - l.debit),0) ap FROM journal_lines l JOIN accounts a ON a.id = l.account_id
                      JOIN journal_entries e ON e.id = l.entry_id WHERE a.code = '2000' AND e.entry_date <= :d""")
    cash = one("""SELECT COALESCE(sum(l.debit - l.credit),0) cash FROM journal_lines l JOIN accounts a ON a.id = l.account_id
                   JOIN journal_entries e ON e.id = l.entry_id WHERE a.code IN ('1000','1100') AND e.entry_date <= :d""")
    cogs = one("""SELECT COALESCE(sum(l.debit - l.credit),0) cogs FROM journal_lines l JOIN accounts a ON a.id = l.account_id
                   JOIN journal_entries e ON e.id = l.entry_id WHERE a.code = '5000' AND e.entry_date <= :d""")
    orders = rows("SELECT status, count(*) AS n, COALESCE(sum(total),0) AS value FROM sales_orders GROUP BY status ORDER BY status")
    prod_by_status = rows("SELECT status, count(*) AS n FROM production_orders GROUP BY status ORDER BY status")
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
    # ---- sections added for PRD Module 12 (each KPI names the screen/API that lists the underlying records)
    prod = one("""SELECT count(*) FILTER (WHERE status = 'COMPLETED' AND completed_at > now() - interval '30 days') AS completed_30d,
                         COALESCE(sum(planned_qty) FILTER (WHERE status = 'COMPLETED' AND completed_at > now() - interval '30 days'), 0) AS planned_qty_30d,
                         COALESCE(sum(actual_qty) FILTER (WHERE status = 'COMPLETED' AND completed_at > now() - interval '30 days'), 0) AS actual_qty_30d,
                         count(*) FILTER (WHERE planned_date < business_today() AND status NOT IN ('COMPLETED','CANCELLED')) AS delayed,
                         count(*) FILTER (WHERE status IN ('RELEASED','IN_PROGRESS')) AS active FROM production_orders""")
    wastage = one("SELECT COALESCE(sum(w.qty), 0) AS qty FROM wastage_records w JOIN production_orders o ON o.id = w.order_id WHERE o.completed_at > now() - interval '30 days'")
    inv_x = one("""SELECT (SELECT count(*) FROM inventory_batches b WHERE b.expiry_date BETWEEN business_today() AND business_today() + 90 AND EXISTS (SELECT 1 FROM stock_balances sb WHERE sb.batch_id = b.id AND sb.qty_physical > 0)) AS expiring_90d,
                          (SELECT count(*) FROM inventory_batches b WHERE b.expiry_date < business_today() AND EXISTS (SELECT 1 FROM stock_balances sb WHERE sb.batch_id = b.id AND sb.qty_physical > 0)) AS expired_with_stock,
                          (SELECT COALESCE(sum(sb.qty_physical), 0) FROM stock_balances sb JOIN inventory_batches b ON b.id = sb.batch_id WHERE b.qc_status IN ('QUARANTINE','HOLD')) AS quarantined_qty,
                          (SELECT count(*) FROM items i WHERE i.is_active AND i.reorder_level > 0 AND COALESCE((SELECT sum(sb.qty_available) FROM stock_balances sb WHERE sb.item_id = i.id), 0) < i.reorder_level) AS below_reorder""")
    proc = one("""SELECT count(*) FILTER (WHERE status = 'PENDING_APPROVAL') AS pos_pending_approval, count(*) FILTER (WHERE status IN ('APPROVED','PARTIALLY_RECEIVED')) AS open_pos,
                         COALESCE(sum(total_amount) FILTER (WHERE status NOT IN ('DRAFT','CANCELLED') AND created_at > now() - interval '30 days'), 0) AS purchase_value_30d,
                         count(*) FILTER (WHERE status IN ('APPROVED','PARTIALLY_RECEIVED') AND expected_date < business_today()) AS late_deliveries FROM purchase_orders""")
    prs = db.scalar(text("SELECT count(*) FROM purchase_requests WHERE status = 'SUBMITTED'"))
    quality_x = one("""SELECT (SELECT count(*) FROM inspections WHERE status = 'DECIDED' AND decision = 'REJECT' AND decided_at > now() - interval '30 days') AS rejections_30d,
                              (SELECT count(*) FROM inventory_batches WHERE qc_status = 'REJECTED') AS rejected_batches""")
    disp = one("""SELECT (SELECT count(*) FROM shipments WHERE status NOT IN ('DELIVERED','RTO','FAILED_DELIVERY') AND created_at < now() - interval '5 days') AS delayed_shipments,
                         (SELECT count(*) FROM shipments WHERE status = 'DELIVERED') AS delivered, (SELECT count(*) FROM shipments WHERE status IN ('FAILED_DELIVERY','RTO')) AS failed_or_rto,
                         (SELECT count(*) FROM sales_returns WHERE status = 'REQUESTED') AS returns_open""")
    margin = one("""SELECT COALESCE(sum(l.credit - l.debit) FILTER (WHERE a.type = 'REVENUE'), 0) AS revenue, COALESCE(sum(l.debit - l.credit) FILTER (WHERE a.code = '5000'), 0) AS cogs
                      FROM journal_lines l JOIN accounts a ON a.id = l.account_id JOIN journal_entries e ON e.id = l.entry_id WHERE e.entry_date BETWEEN :d - 30 AND :d""")
    outstanding = one("SELECT count(*) FILTER (WHERE total - amount_paid - credited_amount > 0) AS open_sales_invoices FROM sales_invoices")
    ap_open = db.scalar(text("SELECT count(*) FROM supplier_invoices WHERE total - amount_paid - credited_amount > 0"))
    supplier_otd = db.execute(text("""SELECT round(100.0 * count(*) FILTER (WHERE first_receipt <= expected_date) / NULLIF(count(*), 0), 1) FROM
                                       (SELECT p.expected_date, business_date(min(g.received_at)) first_receipt FROM purchase_orders p JOIN goods_receipts g ON g.po_id = p.id WHERE p.expected_date IS NOT NULL GROUP BY p.id) x""")).scalar()
    gm = None if not margin["revenue"] else round(100 * float(margin["revenue"] - margin["cogs"]) / float(margin["revenue"]), 1)
    extended = {
        "production": {"completed_30d": prod["completed_30d"], "planned_qty_30d": str(prod["planned_qty_30d"]), "actual_qty_30d": str(prod["actual_qty_30d"]), "active_orders": prod["active"],
                       "delayed_work_orders": prod["delayed"], "wastage_qty_30d": str(wastage["qty"]), "average_yield_pct": yield_["avg_yield"], "drill": "/api/production/dashboard"},
        "inventory": {"value_by_kind": inv_kind, "total_value": inv["value"], "below_reorder_level": inv_x["below_reorder"], "expiring_within_90_days": inv_x["expiring_90d"],
                      "expired_with_stock": inv_x["expired_with_stock"], "quarantined_or_held_qty": str(inv_x["quarantined_qty"]),
                      "drill": {"reorder": "/api/inventory/reports/reorder", "expiring": "/api/inventory/reports/expiring", "valuation": "/api/inventory/reports/valuation", "slow_moving": "/api/inventory/reports/slow-moving"}},
        "procurement": {"purchase_requests_awaiting_approval": prs, "purchase_orders_awaiting_approval": proc["pos_pending_approval"], "open_purchase_orders": proc["open_pos"],
                        "late_deliveries": proc["late_deliveries"], "purchase_value_30d": str(proc["purchase_value_30d"]), "supplier_on_time_pct": None if supplier_otd is None else str(supplier_otd),
                        "drill": {"approvals": "/api/approvals/pending", "delivery": "/api/procurement/delivery-tracking", "suppliers": "/api/procurement/supplier-performance"}},
        "quality_extended": {"rejected_inspections_30d": quality_x["rejections_30d"], "rejected_batches": quality_x["rejected_batches"], "drill": "/api/quality/inspections"},
        "dispatch": {"delayed_shipments": disp["delayed_shipments"], "delivered": disp["delivered"], "failed_or_rto": disp["failed_or_rto"], "returns_open": disp["returns_open"], "drill": "/api/dispatch/dashboard"},
        "finance_extended": {"open_sales_invoices": outstanding["open_sales_invoices"], "open_supplier_invoices": ap_open, "gross_margin_pct_30d": gm, "revenue_30d": str(margin["revenue"]),
                             "drill": {"ageing_receivables": "/api/accounts/reports/ageing/receivables", "ageing_payables": "/api/accounts/reports/ageing/payables", "pnl": "/api/accounts/reports/profit-and-loss"}},
    }
    return {
        "as_of": d,
        "sales": {"revenue_ex_tax": sales["revenue"], "credit_notes_ex_tax": credit_notes_total["credited"], "net_revenue_ex_tax": sales["revenue"] - credit_notes_total["credited"], "invoices": sales["invoices"],
                  "receivable_outstanding": sales["receivable"], "cogs": cogs["cogs"], "source": "sales_invoices, credit_notes, journal_lines(5000)"},
        "finance": {"payable_outstanding": payable["ap"], "cash_and_bank": cash["cash"], "inventory_value": inv["value"], "inventory_by_kind": inv_kind,
                    "source": "journal_lines(2000,1000,1100), item_valuation"},
        "orders_by_status": orders, "production_by_status": prod_by_status, "average_yield_pct": yield_["avg_yield"],
        "quality": dict(qc), "low_stock": low, "purchase_orders_by_status": po, "shipments_by_status": ship,
        "equipment_by_status": equip, "marketplace_orders_by_status": ext, "open_complaints": cmp_["open_complaints"],
        **extended,
    }
