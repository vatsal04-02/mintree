"""Summary figures for the module landing pages (CRM & Sales, Meta & Branding, E-commerce, R&D, Quality, Purchase, Inventory, Accounts).

Each endpoint is read-only and returns plain counts and sums computed from the business tables, the same way /production/dashboard and
/dispatch/dashboard do. Scalars become the figure cards at the top of the page; the nested objects become small breakdown sections.
Money keys carry a money word (value, total, spend, amount ...) because the UI formats a figure from its name."""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require

router = APIRouter(tags=["module-dashboards"])
DB = Depends(get_db, scope="function")


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def _one(db: Session, sql: str):
    v = db.scalar(text(sql))
    return 0 if v is None else _num(v)


def _by(db: Session, sql: str) -> dict:
    return {str(k): _num(v) for k, v in db.execute(text(sql))}


def _trend(db: Session, table: str, date_col: str, value: str = "count(t.id)", days: int = 30, where: str = "TRUE") -> list[dict]:
    """One point per day for the last `days` days, zero where nothing happened, so a chart never skips a date."""
    rows = db.execute(text(
        f"SELECT g::date, COALESCE({value}, 0) FROM generate_series(business_today() - {days - 1}, business_today(), interval '1 day') g "
        f"LEFT JOIN {table} t ON t.{date_col}::date = g::date AND {where} GROUP BY g ORDER BY g")).all()
    return [{"label": d.isoformat(), "value": _num(v)} for d, v in rows]


def _weekly(db: Session, table: str, date_col: str, value: str = "count(t.id)", weeks: int = 13, first: int | None = None, where: str = "TRUE") -> list[dict]:
    """One point per week (weeks start on Monday). By default the last `weeks` weeks ending this week; `first` is the first week relative to this one
    (0 = start now and look forward, -6 = six weeks back and six ahead). Weeks with nothing in them are zero, so the chart never skips one."""
    f = -(weeks - 1) if first is None else first
    rows = db.execute(text(
        f"SELECT g::date, COALESCE({value}, 0) FROM generate_series(date_trunc('week', business_today()::timestamp) + interval '{f} weeks', "
        f"date_trunc('week', business_today()::timestamp) + interval '{f + weeks - 1} weeks', interval '1 week') g "
        f"LEFT JOIN {table} t ON date_trunc('week', t.{date_col}::timestamp) = g AND {where} GROUP BY g ORDER BY g")).all()
    return [{"label": d.isoformat(), "value": _num(v)} for d, v in rows]


def _pct(part, whole):
    return None if not whole else round(100 * float(part) / float(whole), 1)


@router.get("/crm/dashboard")
def crm_dashboard(db: Session = DB, _: CurrentUser = Depends(require("crm.lead:read"))):
    open_ = "stage NOT IN ('WON', 'LOST')"
    won = _one(db, "SELECT count(*) FROM opportunities WHERE stage = 'WON'")
    lost = _one(db, "SELECT count(*) FROM opportunities WHERE stage = 'LOST'")
    return {
        "leads": _one(db, "SELECT count(*) FROM leads"),
        "open_opportunities": _one(db, f"SELECT count(*) FROM opportunities WHERE {open_}"),
        "open_pipeline_value": _one(db, f"SELECT COALESCE(sum(expected_value), 0) FROM opportunities WHERE {open_}"),
        "weighted_pipeline_value": _one(db, f"SELECT COALESCE(round(sum(expected_value * probability / 100.0)), 0) FROM opportunities WHERE {open_}"),
        "won_value_90d": _one(db, "SELECT COALESCE(sum(expected_value), 0) FROM opportunities WHERE stage = 'WON' AND closed_at > now() - interval '90 days'"),
        "win_rate_pct": _pct(won, won + lost),
        "open_followups": _one(db, "SELECT count(*) FROM followups WHERE status = 'OPEN'"),
        "overdue_followups": _one(db, "SELECT count(*) FROM followups WHERE status = 'OPEN' AND due_date < business_today()"),
        "quotations_awaiting_reply": _one(db, "SELECT count(*) FROM sales_quotations WHERE status = 'SENT'"),
        "open_complaints": _one(db, "SELECT count(*) FROM complaints WHERE status <> 'RESOLVED'"),
        "activities_30d": _one(db, "SELECT count(*) FROM crm_activities WHERE occurred_at > now() - interval '30 days'"),
        "sales_revenue_weekly_trend": _weekly(db, "sales_invoices", "invoice_date", "sum(t.subtotal)"),
        "followups_due_weekly_trend": _weekly(db, "followups", "due_date"),
        "expected_close_value_weekly_trend": _weekly(db, "opportunities", "expected_close", "sum(t.expected_value)", weeks=13, first=0, where="t.stage NOT IN ('WON', 'LOST')"),
        "leads_by_status": _by(db, "SELECT status, count(*) FROM leads GROUP BY 1 ORDER BY 2 DESC"),
        "opportunities_by_stage": _by(db, "SELECT stage, count(*) FROM opportunities GROUP BY 1 ORDER BY 2 DESC"),
        "pipeline_value_by_stage": _by(db, f"SELECT stage, COALESCE(sum(expected_value), 0) FROM opportunities WHERE {open_} GROUP BY 1 ORDER BY 2 DESC"),
        "complaints_by_status": _by(db, "SELECT status, count(*) FROM complaints GROUP BY 1 ORDER BY 2 DESC"),
    }


@router.get("/branding/dashboard")
def branding_dashboard(db: Session = DB, _: CurrentUser = Depends(require("branding.campaign:read"))):
    window = "metric_date > business_today() - 30"
    impressions = _one(db, f"SELECT COALESCE(sum(impressions), 0) FROM campaign_metrics WHERE {window}")
    clicks = _one(db, f"SELECT COALESCE(sum(clicks), 0) FROM campaign_metrics WHERE {window}")
    simulated = _one(db, "SELECT count(*) FROM campaign_metrics WHERE source = 'DEMO'")
    out = {
        "campaigns_running": _one(db, "SELECT count(*) FROM marketing_campaigns WHERE status = 'RUNNING'"),
        "spend_30d": _one(db, f"SELECT COALESCE(sum(spend), 0) FROM campaign_metrics WHERE {window}"),
        "reach_30d": _one(db, f"SELECT COALESCE(sum(reach), 0) FROM campaign_metrics WHERE {window}"),
        "impressions_30d": impressions,
        "clicks_30d": clicks,
        "click_through_rate_pct": _pct(clicks, impressions),
        "results_30d": _one(db, f"SELECT COALESCE(sum(results), 0) FROM campaign_metrics WHERE {window}"),
        "content_awaiting_review": _one(db, "SELECT count(*) FROM content_calendar WHERE status = 'IN_REVIEW'"),
        "content_scheduled": _one(db, "SELECT count(*) FROM content_calendar WHERE status = 'SCHEDULED'"),
        "content_published": _one(db, "SELECT count(*) FROM content_calendar WHERE status = 'PUBLISHED'"),
        "social_accounts_active": _one(db, "SELECT count(*) FROM social_accounts WHERE status = 'ACTIVE'"),
        "brand_library_items": _one(db, "SELECT count(*) FROM brand_assets"),
        "spend_trend": _trend(db, "campaign_metrics", "metric_date", "sum(t.spend)"),
        "results_trend": _trend(db, "campaign_metrics", "metric_date", "sum(t.results)"),
        "campaigns_by_status": _by(db, "SELECT status, count(*) FROM marketing_campaigns GROUP BY 1 ORDER BY 2 DESC"),
        "content_by_status": _by(db, "SELECT status, count(*) FROM content_calendar GROUP BY 1 ORDER BY 2 DESC"),
        "spend_by_campaign_30d": _by(db, f"SELECT c.name, sum(m.spend) FROM campaign_metrics m JOIN marketing_campaigns c ON c.id = m.campaign_id WHERE m.{window} GROUP BY c.name ORDER BY 2 DESC LIMIT 6"),
    }
    if simulated:
        out["note"] = "Includes simulated sandbox metrics. Nothing here is a live Meta result."
    return out


@router.get("/ecommerce/dashboard")
def ecommerce_dashboard(db: Session = DB, _: CurrentUser = Depends(require("ecommerce.order:read"))):
    recent = "order_date > business_today() - 30"
    return {
        "marketplace_accounts": _one(db, "SELECT count(*) FROM marketplace_accounts WHERE status = 'ACTIVE'"),
        "orders_30d": _one(db, f"SELECT count(*) FROM external_orders WHERE {recent}"),
        "order_value_30d": _one(db, f"SELECT COALESCE(sum(total), 0) FROM external_orders WHERE {recent}"),
        "orders_all_time": _one(db, "SELECT count(*) FROM external_orders"),
        "orders_in_exception": _one(db, "SELECT count(*) FROM external_orders WHERE status = 'EXCEPTION'"),
        "settlements_net_total": _one(db, "SELECT COALESCE(sum(net_settlement), 0) FROM marketplace_settlements"),
        "settlements_not_posted": _one(db, "SELECT count(*) FROM marketplace_settlements WHERE posted_at IS NULL"),
        "marketplace_returns": _one(db, "SELECT count(*) FROM marketplace_returns"),
        "order_value_trend": _trend(db, "external_orders", "order_date", "sum(t.total)"),
        "orders_trend": _trend(db, "external_orders", "order_date"),
        "orders_by_status": _by(db, "SELECT status, count(*) FROM external_orders GROUP BY 1 ORDER BY 2 DESC"),
        "orders_by_platform": _by(db, "SELECT a.platform, count(*) FROM external_orders o JOIN marketplace_accounts a ON a.id = o.account_id GROUP BY 1 ORDER BY 2 DESC"),
        "order_value_by_platform": _by(db, "SELECT a.platform, COALESCE(sum(o.total), 0) FROM external_orders o JOIN marketplace_accounts a ON a.id = o.account_id GROUP BY 1 ORDER BY 2 DESC"),
        "top_skus_by_units": _by(db, "SELECT platform_sku, sum(qty) FROM external_order_items GROUP BY 1 ORDER BY 2 DESC LIMIT 8"),
        "fees_by_platform": _by(db, "SELECT a.platform, COALESCE(sum(s.commission + s.shipping_fees + s.ad_fees), 0) FROM marketplace_settlements s JOIN marketplace_accounts a ON a.id = s.account_id GROUP BY 1 ORDER BY 2 DESC"),
    }


@router.get("/rnd/dashboard")
def rnd_dashboard(db: Session = DB, _: CurrentUser = Depends(require("rnd.formula:read"))):
    passed = _one(db, "SELECT count(*) FROM lab_trials WHERE status = 'PASSED'")
    failed = _one(db, "SELECT count(*) FROM lab_trials WHERE status = 'FAILED'")
    return {
        "active_projects": _one(db, "SELECT count(*) FROM development_projects WHERE status IN ('CONCEPT', 'DEVELOPMENT', 'TRIALS')"),
        "formulations": _one(db, "SELECT count(*) FROM formulations"),
        "versions_awaiting_approval": _one(db, "SELECT count(*) FROM formulation_versions WHERE status = 'SUBMITTED'"),
        "approved_versions": _one(db, "SELECT count(*) FROM formulation_versions WHERE status = 'APPROVED'"),
        "trials_passed": passed,
        "trials_failed": failed,
        "trial_pass_rate_pct": _pct(passed, passed + failed),
        "planned_launches_by_month": _by(db, "SELECT m, n FROM (SELECT date_trunc('month', target_launch_date) d, to_char(target_launch_date, 'Mon YYYY') m, count(*) n FROM development_projects WHERE status <> 'CANCELLED' AND target_launch_date IS NOT NULL GROUP BY 1, 2 ORDER BY 1) x"),
        "projects_by_status": _by(db, "SELECT status, count(*) FROM development_projects GROUP BY 1 ORDER BY 2 DESC"),
        "versions_by_status": _by(db, "SELECT status, count(*) FROM formulation_versions GROUP BY 1 ORDER BY 2 DESC"),
        "trials_by_status": _by(db, "SELECT status, count(*) FROM lab_trials GROUP BY 1 ORDER BY 2 DESC"),
    }


@router.get("/quality/dashboard")
def quality_dashboard(db: Session = DB, _: CurrentUser = Depends(require("quality.inspection:read"))):
    release = _one(db, "SELECT count(*) FROM inspections WHERE decision = 'RELEASE'")
    reject = _one(db, "SELECT count(*) FROM inspections WHERE decision = 'REJECT'")
    return {
        "inspections_open": _one(db, "SELECT count(*) FROM inspections WHERE status <> 'DECIDED'"),
        "inspections_decided_30d": _one(db, "SELECT count(*) FROM inspections WHERE decided_at > now() - interval '30 days'"),
        "release_rate_pct": _pct(release, release + reject),
        "deviations_open": _one(db, "SELECT count(*) FROM deviations WHERE status <> 'CLOSED'"),
        "capa_open": _one(db, "SELECT count(*) FROM capa_actions WHERE status = 'OPEN'"),
        "capa_overdue": _one(db, "SELECT count(*) FROM capa_actions WHERE status = 'OPEN' AND due_date < business_today()"),
        "batches_in_quarantine": _one(db, "SELECT count(*) FROM inventory_batches WHERE qc_status = 'QUARANTINE'"),
        "batches_rejected": _one(db, "SELECT count(*) FROM inventory_batches WHERE qc_status = 'REJECTED'"),
        "certificates_issued": _one(db, "SELECT count(*) FROM certificates_of_analysis WHERE revoked_at IS NULL"),
        "failed_tests_by_parameter": _by(db, "SELECT test_name, count(*) FROM inspection_results WHERE NOT passed GROUP BY 1 ORDER BY 2 DESC LIMIT 8"),
        "inspections_by_decision": _by(db, "SELECT COALESCE(decision, 'PENDING'), count(*) FROM inspections GROUP BY 1 ORDER BY 2 DESC"),
        "inspections_by_stage": _by(db, "SELECT stage, count(*) FROM inspections GROUP BY 1 ORDER BY 2 DESC"),
        "deviations_by_status": _by(db, "SELECT status, count(*) FROM deviations GROUP BY 1 ORDER BY 2 DESC"),
        "capa_by_status": _by(db, "SELECT status, count(*) FROM capa_actions GROUP BY 1 ORDER BY 2 DESC"),
    }


@router.get("/procurement/dashboard")
def procurement_dashboard(db: Session = DB, _: CurrentUser = Depends(require("procurement.po:read"))):
    open_po = "status IN ('APPROVED', 'PARTIALLY_RECEIVED')"
    return {
        "requests_awaiting_approval": _one(db, "SELECT count(*) FROM purchase_requests WHERE status = 'SUBMITTED'"),
        "orders_awaiting_approval": _one(db, "SELECT count(*) FROM purchase_orders WHERE status = 'PENDING_APPROVAL'"),
        "open_orders": _one(db, f"SELECT count(*) FROM purchase_orders WHERE {open_po}"),
        "open_order_value": _one(db, f"SELECT COALESCE(sum(total_amount), 0) FROM purchase_orders WHERE {open_po}"),
        "late_deliveries": _one(db, f"SELECT count(*) FROM purchase_orders WHERE {open_po} AND expected_date < business_today()"),
        "rfqs_awaiting_quotes": _one(db, "SELECT count(*) FROM rfqs WHERE status = 'SENT'"),
        "receipts_30d": _one(db, "SELECT count(*) FROM goods_receipts WHERE received_at > now() - interval '30 days'"),
        "supplier_invoices_unpaid_total": _one(db, "SELECT COALESCE(sum(total - amount_paid - credited_amount), 0) FROM supplier_invoices WHERE status <> 'PAID'"),
        "supplier_invoice_value_weekly_trend": _weekly(db, "supplier_invoices", "invoice_date", "sum(t.total)"),
        "po_deliveries_due_weekly_trend": _weekly(db, "purchase_orders", "expected_date", weeks=13, first=-6, where="t.status NOT IN ('DRAFT', 'CANCELLED')"),
        "orders_by_status": _by(db, "SELECT status, count(*) FROM purchase_orders GROUP BY 1 ORDER BY 2 DESC"),
        "requests_by_status": _by(db, "SELECT status, count(*) FROM purchase_requests GROUP BY 1 ORDER BY 2 DESC"),
        "supplier_invoices_by_status": _by(db, "SELECT status, count(*) FROM supplier_invoices GROUP BY 1 ORDER BY 2 DESC"),
        "order_value_by_supplier": _by(db, "SELECT s.name, sum(p.total_amount) FROM purchase_orders p JOIN suppliers s ON s.id = p.supplier_id WHERE p.status NOT IN ('DRAFT', 'CANCELLED') GROUP BY s.name ORDER BY 2 DESC LIMIT 6"),
    }


@router.get("/inventory/dashboard")
def inventory_dashboard(db: Session = DB, _: CurrentUser = Depends(require("inventory.stock:read"))):
    return {
        "active_items": _one(db, "SELECT count(*) FROM items WHERE is_active"),
        "stocked_lines": _one(db, "SELECT count(*) FROM stock_balances WHERE qty_physical > 0"),
        "batches_released": _one(db, "SELECT count(*) FROM inventory_batches WHERE qc_status = 'RELEASED'"),
        "batches_in_quarantine": _one(db, "SELECT count(*) FROM inventory_batches WHERE qc_status = 'QUARANTINE'"),
        "expiring_within_90_days": _one(db, "SELECT count(DISTINCT b.id) FROM inventory_batches b JOIN stock_balances s ON s.batch_id = b.id AND s.qty_physical > 0 WHERE b.expiry_date BETWEEN business_today() AND business_today() + 90"),
        "expired_with_stock": _one(db, "SELECT count(DISTINCT b.id) FROM inventory_batches b JOIN stock_balances s ON s.batch_id = b.id AND s.qty_physical > 0 WHERE b.expiry_date < business_today()"),
        "items_below_reorder": _one(db, "SELECT count(*) FROM (SELECT i.id FROM items i JOIN stock_balances s ON s.item_id = i.id WHERE i.reorder_level IS NOT NULL AND i.is_active GROUP BY i.id, i.reorder_level HAVING sum(s.qty_available) < i.reorder_level) x"),
        "stocktakes_in_progress": _one(db, "SELECT count(*) FROM stocktakes WHERE status NOT IN ('APPROVED', 'CANCELLED')"),
        "batches_made_weekly_trend": _weekly(db, "inventory_batches", "mfg_date"),
        "stocked_lines_by_warehouse": _by(db, "SELECT w.name, count(*) FROM stock_balances s JOIN warehouses w ON w.id = s.warehouse_id WHERE s.qty_physical > 0 GROUP BY w.name ORDER BY 2 DESC"),
        "batches_expiring_by_month": _by(db, "SELECT m, n FROM (SELECT date_trunc('month', b.expiry_date) d, to_char(b.expiry_date, 'Mon YYYY') m, count(DISTINCT b.id) n FROM inventory_batches b JOIN stock_balances s ON s.batch_id = b.id AND s.qty_physical > 0 WHERE b.expiry_date >= business_today() GROUP BY 1, 2 ORDER BY 1 LIMIT 12) x"),
        "batches_by_qc_status": _by(db, "SELECT qc_status, count(*) FROM inventory_batches GROUP BY 1 ORDER BY 2 DESC"),
        "items_by_kind": _by(db, "SELECT kind, count(*) FROM items WHERE is_active GROUP BY 1 ORDER BY 2 DESC"),
        "stocktakes_by_status": _by(db, "SELECT status, count(*) FROM stocktakes GROUP BY 1 ORDER BY 2 DESC"),
    }


@router.get("/accounts/dashboard")
def accounts_dashboard(db: Session = DB, _: CurrentUser = Depends(require("accounts.report:read"))):
    return {
        "receivable_outstanding": _one(db, "SELECT COALESCE(sum(total - amount_paid - credited_amount), 0) FROM sales_invoices WHERE status <> 'PAID'"),
        "payable_outstanding": _one(db, "SELECT COALESCE(sum(total - amount_paid - credited_amount), 0) FROM supplier_invoices WHERE status <> 'PAID'"),
        "invoiced_revenue_30d": _one(db, "SELECT COALESCE(sum(subtotal), 0) FROM sales_invoices WHERE invoice_date > business_today() - 30"),
        "open_sales_invoices": _one(db, "SELECT count(*) FROM sales_invoices WHERE status <> 'PAID'"),
        "open_supplier_invoices": _one(db, "SELECT count(*) FROM supplier_invoices WHERE status <> 'PAID'"),
        "bank_lines_unmatched": _one(db, "SELECT count(*) FROM bank_transactions WHERE status = 'UNMATCHED'"),
        "journal_entries_30d": _one(db, "SELECT count(*) FROM journal_entries WHERE entry_date > business_today() - 30"),
        "invoiced_revenue_trend": _trend(db, "sales_invoices", "invoice_date", "sum(t.subtotal)"),
        "payments_received_trend": _trend(db, "payments", "payment_date", "sum(t.amount)", where="t.direction = 'RECEIVED'"),
        "sales_invoices_by_status": _by(db, "SELECT status, count(*) FROM sales_invoices GROUP BY 1 ORDER BY 2 DESC"),
        "supplier_invoices_by_status": _by(db, "SELECT status, count(*) FROM supplier_invoices GROUP BY 1 ORDER BY 2 DESC"),
        "payments_30d_total_by_direction": _by(db, "SELECT direction, COALESCE(sum(amount), 0) FROM payments WHERE payment_date > business_today() - 30 GROUP BY 1 ORDER BY 2 DESC"),
        "bank_lines_by_status": _by(db, "SELECT status, count(*) FROM bank_transactions GROUP BY 1 ORDER BY 2 DESC"),
    }


# ------------------------------------------------------------------ the three dashboards that already existed: same figures, plus charts
from app.dispatch import extras as _dispatch_extras  # noqa: E402
from app.maintenance.router import dashboard as _maintenance_base  # noqa: E402
from app.production import extras as _production_extras  # noqa: E402


@router.get("/production/dashboard")
def production_dashboard(db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    out = _production_extras.dashboard(db)
    out["units_made_weekly_trend"] = _weekly(db, "production_orders", "completed_at", "sum(t.actual_qty)")
    out["orders_completed_weekly_trend"] = _weekly(db, "production_orders", "completed_at")
    out["output_by_product"] = _by(db, "SELECT i.name, sum(p.actual_qty) FROM production_orders p JOIN items i ON i.id = p.output_item_id WHERE p.status = 'COMPLETED' GROUP BY 1 ORDER BY 2 DESC LIMIT 8")
    out.pop("wastage_by_category_30d", None)       # the base dashboard returns this as a table; the bars below replace it
    out["wastage_by_category"] = _by(db, "SELECT category, sum(qty) FROM wastage_records GROUP BY 1 ORDER BY 2 DESC")
    return out


@router.get("/dispatch/dashboard")
def dispatch_dashboard(db: Session = DB, _: CurrentUser = Depends(require("dispatch.order:read"))):
    out = _dispatch_extras.dashboard(db)
    # a count, not rupees: a name containing "charge" would be formatted as money
    out["uncharged_shipment_count"] = out.pop("shipments_without_charge")
    out["shipments_delivered_trend"] = _trend(db, "shipments", "delivered_at")
    out["shipments_by_status"] = _by(db, "SELECT status, count(*) FROM shipments GROUP BY 1 ORDER BY 2 DESC")
    out["shipments_by_courier"] = _by(db, "SELECT COALESCE(courier, 'Unassigned'), count(*) FROM shipments GROUP BY 1 ORDER BY 2 DESC")
    return out


@router.get("/maintenance/dashboard")
def maintenance_dashboard(db: Session = DB, _: CurrentUser = Depends(require("maintenance.equipment:read"))):
    out = _maintenance_base(db=db, _=None)
    out["work_orders_by_status"] = _by(db, "SELECT status, count(*) FROM maintenance_work_orders GROUP BY 1 ORDER BY 2 DESC")
    out["work_orders_by_kind"] = _by(db, "SELECT kind, count(*) FROM maintenance_work_orders GROUP BY 1 ORDER BY 2 DESC")
    out["downtime_hours_by_equipment"] = _by(db, "SELECT e.name, round(sum(EXTRACT(EPOCH FROM COALESCE(d.ended_at, now()) - d.started_at)) / 3600) FROM downtime_records d JOIN equipment e ON e.id = d.equipment_id GROUP BY 1 HAVING round(sum(EXTRACT(EPOCH FROM COALESCE(d.ended_at, now()) - d.started_at)) / 3600) > 0 ORDER BY 2 DESC LIMIT 8")
    out["spare_parts_cost_by_equipment"] = _by(db, "SELECT e.name, sum(sp.qty * sp.unit_cost) FROM spare_part_usage sp JOIN maintenance_work_orders w ON w.id = sp.work_order_id JOIN equipment e ON e.id = w.equipment_id WHERE sp.unit_cost IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 8")
    return out


# ------------------------------------------------------------------ the Overview page: series for the selected period
from datetime import date, timedelta  # noqa: E402

from app.core.errors import Unprocessable  # noqa: E402


def _range(db: Session, table: str, col: str, value: str, start: date, end: date, where: str = "TRUE") -> list[dict]:
    """Daily points for a range of up to 93 days, weekly points beyond that, zero where nothing happened."""
    weekly = (end - start).days + 1 > 93
    on = f"date_trunc('week', t.{col}::timestamp) = g AND t.{col}::date BETWEEN CAST(:s AS date) AND CAST(:e AS date)" if weekly else f"t.{col}::date = g::date"
    gen = ("date_trunc('week', CAST(:s AS timestamp)), CAST(:e AS timestamp), interval '1 week'" if weekly else "CAST(:s AS timestamp), CAST(:e AS timestamp), interval '1 day'")
    rows = db.execute(text(f"SELECT g::date, COALESCE({value}, 0) FROM generate_series({gen}) g LEFT JOIN {table} t ON {on} AND {where} GROUP BY g ORDER BY g"), {"s": start, "e": end}).all()
    return [{"label": d.isoformat(), "value": _num(v)} for d, v in rows]


@router.get("/analytics/overview-trends")
def overview_trends(start: date, end: date, db: Session = DB, _: CurrentUser = Depends(require("analytics.dashboard:read"))):
    days = (end - start).days + 1
    if days < 1 or days > 400:
        raise Unprocessable("The period must be between 1 and 400 days")
    shift = timedelta(days=days)
    p_start, p_end = start - shift, end - shift
    sp = {"s": start, "e": end}
    return {
        "granularity": "week" if days > 93 else "day",
        "revenue_trend": _range(db, "sales_invoices", "invoice_date", "sum(t.subtotal)", start, end),
        "revenue_prev_trend": _range(db, "sales_invoices", "invoice_date", "sum(t.subtotal)", p_start, p_end),
        "collections_trend": _range(db, "payments", "payment_date", "sum(t.amount)", start, end, "t.direction = 'RECEIVED'"),
        "units_trend": _range(db, "production_orders", "completed_at", "sum(t.actual_qty)", start, end),
        "invoice_value_by_status": {str(k): _num(v) for k, v in db.execute(text("SELECT status, COALESCE(sum(total - credited_amount), 0) FROM sales_invoices GROUP BY 1 ORDER BY 2 DESC"))},
        "top_customers": {str(k): _num(v) for k, v in db.execute(text(
            "SELECT c.name, sum(i.subtotal) FROM sales_invoices i JOIN customers c ON c.id = i.customer_id WHERE i.invoice_date BETWEEN :s AND :e GROUP BY c.name ORDER BY 2 DESC LIMIT 6"), sp)},
        "stock_health": _by(db, "SELECT qc_status, count(*) FROM inventory_batches GROUP BY 1 ORDER BY 2 DESC"),
        # founder views: where the period's revenue came from, which products earned it, and how old the money still owed is
        "revenue_by_channel": {str(k): _num(v) for k, v in db.execute(text(
            "SELECT so.channel, sum(i.subtotal) FROM sales_invoices i JOIN sales_orders so ON so.id = i.so_id WHERE i.invoice_date BETWEEN :s AND :e GROUP BY 1 ORDER BY 2 DESC"), sp)},
        "top_products": {str(k): _num(v) for k, v in db.execute(text(
            """SELECT it.name, sum(soi.qty * soi.unit_price) FROM sales_invoices i JOIN sales_order_items soi ON soi.so_id = i.so_id JOIN items it ON it.id = soi.item_id
                WHERE i.invoice_date BETWEEN :s AND :e GROUP BY it.name ORDER BY 2 DESC LIMIT 5"""), sp)},
        "receivable_ageing": {str(k): _num(v) for k, v in db.execute(text(
            """SELECT bucket, sum(due) FROM (
                 SELECT i.total - i.amount_paid - i.credited_amount AS due,
                        CASE WHEN business_today() <= i.invoice_date + COALESCE(c.credit_days, 0) THEN '0. Not yet due'
                             WHEN business_today() - (i.invoice_date + COALESCE(c.credit_days, 0)) <= 30 THEN '1. 1-30 days overdue'
                             WHEN business_today() - (i.invoice_date + COALESCE(c.credit_days, 0)) <= 60 THEN '2. 31-60 days overdue'
                             WHEN business_today() - (i.invoice_date + COALESCE(c.credit_days, 0)) <= 90 THEN '3. 61-90 days overdue'
                             ELSE '4. Over 90 days overdue' END AS bucket
                   FROM sales_invoices i JOIN customers c ON c.id = i.customer_id WHERE i.total - i.amount_paid - i.credited_amount > 0) x
               GROUP BY bucket ORDER BY bucket"""))},
    }


# ------------------------------------------------------------------ the three screen layouts (Production floor, PM & RM vault, Omni-channel hub)
def _rows(db: Session, sql: str, **p) -> list[dict]:
    return [{k: _num(v) for k, v in r.items()} for r in db.execute(text(sql), p).mappings()]


@router.get("/production/floor")
def production_floor(db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    """Orders on the floor (running, released, planned) with their stages and material issue progress; three pieces of equipment; and the dispensing
    log of the first running order: each material, the lots issued against it and who issued them."""
    orders = _rows(db, """
        SELECT o.id, o.number, o.status, o.planned_qty, o.actual_qty, o.yield_pct, o.planned_date, o.bom_id, i.item_code, i.name AS item_name, u.code AS uom,
               (SELECT COALESCE(sum(m.required_qty), 0) FROM production_materials m WHERE m.order_id = o.id) AS required_qty,
               (SELECT COALESCE(sum(m.issued_qty), 0) FROM production_materials m WHERE m.order_id = o.id) AS issued_qty,
               (SELECT count(*) FROM production_materials m WHERE m.order_id = o.id) AS materials
          FROM production_orders o JOIN items i ON i.id = o.output_item_id LEFT JOIN units_of_measure u ON u.id = i.uom_id
         WHERE o.status IN ('IN_PROGRESS', 'RELEASED', 'PLANNED')
         ORDER BY CASE o.status WHEN 'IN_PROGRESS' THEN 0 WHEN 'RELEASED' THEN 1 ELSE 2 END, o.planned_date NULLS LAST, o.id DESC LIMIT 9""")
    for o in orders:
        o["stages"] = _rows(db, """SELECT s.seq, s.name, s.status, e.name AS equipment, us.full_name AS operator, w.name AS work_centre
                                     FROM production_stages s LEFT JOIN equipment e ON e.id = s.equipment_id LEFT JOIN users us ON us.id = s.operator_id
                                     LEFT JOIN work_centres w ON w.id = s.work_centre_id WHERE s.order_id = :o ORDER BY s.seq""", o=o["id"])
    counts = {k: _num(v) for k, v in db.execute(text("SELECT status, count(*) FROM production_orders GROUP BY 1"))}
    equipment = _rows(db, """SELECT e.id, e.asset_id, e.name, e.category, e.status, e.location, e.calibration_due,
                                    (SELECT min(ms.next_due) FROM maintenance_schedules ms WHERE ms.equipment_id = e.id AND ms.is_active) AS next_pm
                               FROM equipment e WHERE e.status <> 'RETIRED' ORDER BY CASE e.status WHEN 'OPERATIONAL' THEN 0 ELSE 1 END, e.id LIMIT 3""")
    focus = next((o for o in orders if o["status"] == "IN_PROGRESS"), orders[0] if orders else None)
    dispensing = []
    if focus:
        dispensing = _rows(db, """
            SELECT i.item_code, i.name, i.kind, u.code AS uom, m.required_qty, m.issued_qty,
                   (SELECT string_agg(DISTINCT b.batch_no, ', ') FROM stock_ledger l JOIN inventory_batches b ON b.id = l.batch_id
                     WHERE l.movement_type = 'PRODUCTION_ISSUE' AND l.source_id = m.order_id AND l.item_id = m.item_id) AS lots,
                   (SELECT string_agg(DISTINCT b.qc_status, ', ') FROM stock_ledger l JOIN inventory_batches b ON b.id = l.batch_id
                     WHERE l.movement_type = 'PRODUCTION_ISSUE' AND l.source_id = m.order_id AND l.item_id = m.item_id) AS lot_qc,
                   (SELECT string_agg(DISTINCT us.full_name, ' / ') FROM stock_ledger l JOIN users us ON us.id = l.actor_id
                     WHERE l.movement_type = 'PRODUCTION_ISSUE' AND l.source_id = m.order_id AND l.item_id = m.item_id) AS issued_by
              FROM production_materials m JOIN items i ON i.id = m.item_id LEFT JOIN units_of_measure u ON u.id = i.uom_id
             WHERE m.order_id = :o ORDER BY m.line_no""", o=focus["id"])
    return {"orders": orders, "counts": counts, "equipment": equipment, "dispensing": dispensing, "dispensing_order": focus and {"id": focus["id"], "number": focus["number"], "item_name": focus["item_name"]}}


@router.get("/procurement/vault")
def procurement_vault(db: Session = DB, _: CurrentUser = Depends(require("procurement.po:read"))):
    """Raw and packaging material capital, open commitments, items below their reorder point (with the last supplier and price), the next inbound
    purchase order as a receipt tracker, the material table, supplier on-time scores and open spend split by material class."""
    one = lambda sql: _one(db, sql)
    value_by_kind = _by(db, "SELECT i.kind, COALESCE(sum(v.value), 0) FROM item_valuation v JOIN items i ON i.id = v.item_id GROUP BY 1")
    count_by_kind = _by(db, "SELECT i.kind, count(*) FROM items i WHERE i.is_active GROUP BY 1")
    open_po = "status IN ('APPROVED', 'PARTIALLY_RECEIVED')"
    last_supplier = """(SELECT s.name FROM purchase_order_items pi JOIN purchase_orders po ON po.id = pi.po_id JOIN suppliers s ON s.id = po.supplier_id
                         WHERE pi.item_id = i.id AND po.status NOT IN ('DRAFT', 'CANCELLED') ORDER BY po.id DESC LIMIT 1)"""
    last_city = """(SELECT s.city FROM purchase_order_items pi JOIN purchase_orders po ON po.id = pi.po_id JOIN suppliers s ON s.id = po.supplier_id
                     WHERE pi.item_id = i.id AND po.status NOT IN ('DRAFT', 'CANCELLED') ORDER BY po.id DESC LIMIT 1)"""
    last_price = """(SELECT pi.unit_price FROM purchase_order_items pi JOIN purchase_orders po ON po.id = pi.po_id
                      WHERE pi.item_id = i.id AND po.status NOT IN ('DRAFT', 'CANCELLED') ORDER BY po.id DESC LIMIT 1)"""
    materials = _rows(db, f"""
        SELECT i.id, i.item_code, i.name, i.kind, i.description, u.code AS uom, i.reorder_level,
               COALESCE(sum(b.qty_physical), 0) AS on_hand, COALESCE(sum(b.qty_reserved), 0) AS allocated, COALESCE(sum(b.qty_available), 0) AS free,
               {last_supplier} AS vendor, {last_city} AS vendor_city, {last_price} AS last_price
          FROM items i LEFT JOIN stock_balances b ON b.item_id = i.id LEFT JOIN units_of_measure u ON u.id = i.uom_id
         WHERE i.is_active AND i.kind IN ('RM', 'PM') GROUP BY i.id, u.code
         ORDER BY (COALESCE(sum(b.qty_available), 0) < COALESCE(i.reorder_level, 0)) DESC, i.kind, i.item_code""")
    for m in materials:
        lvl, free = float(m["reorder_level"] or 0), float(m["free"] or 0)
        m["state"] = "BELOW" if lvl and free < lvl else "SOON" if lvl and free < lvl * 1.5 else "HEALTHY"
    nxt = _rows(db, f"""SELECT po.id, po.number, po.status, po.total_amount, po.approved_at, po.expected_date, s.name AS supplier, s.city,
                               (SELECT i.name FROM purchase_order_items pi JOIN items i ON i.id = pi.item_id WHERE pi.po_id = po.id ORDER BY pi.line_no LIMIT 1) AS first_item,
                               (SELECT pi.qty_ordered FROM purchase_order_items pi WHERE pi.po_id = po.id ORDER BY pi.line_no LIMIT 1) AS first_qty,
                               (SELECT count(*) FROM goods_receipts g WHERE g.po_id = po.id) AS receipts
                          FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id WHERE po.{open_po}
                         ORDER BY po.expected_date NULLS LAST, po.id LIMIT 1""")
    vendors = _rows(db, """SELECT s.name, count(g.id) AS receipts, round(100.0 * count(*) FILTER (WHERE g.received_at::date <= po.expected_date) / NULLIF(count(g.id), 0), 1) AS on_time_pct
                             FROM goods_receipts g JOIN purchase_orders po ON po.id = g.po_id JOIN suppliers s ON s.id = po.supplier_id
                            GROUP BY s.name ORDER BY count(g.id) DESC LIMIT 3""")
    spend = _by(db, f"""SELECT i.kind, COALESCE(sum(pi.qty_ordered * pi.unit_price), 0) FROM purchase_order_items pi JOIN purchase_orders po ON po.id = pi.po_id
                        JOIN items i ON i.id = pi.item_id WHERE po.status NOT IN ('DRAFT', 'CANCELLED') GROUP BY 1""")
    return {
        "inventory_value": one("SELECT COALESCE(sum(value), 0) FROM item_valuation"),
        "value_by_kind": value_by_kind, "count_by_kind": count_by_kind,
        "below_reorder": sum(1 for m in materials if m["state"] == "BELOW"),
        "open_orders": one(f"SELECT count(*) FROM purchase_orders WHERE {open_po}"),
        "arriving_this_week": one(f"SELECT count(*) FROM purchase_orders WHERE {open_po} AND expected_date BETWEEN business_today() AND business_today() + 7"),
        "late_orders": one(f"SELECT count(*) FROM purchase_orders WHERE {open_po} AND expected_date < business_today()"),
        "quarantine_lots": one("SELECT count(*) FROM inventory_batches WHERE qc_status = 'QUARANTINE'"),
        "open_order_value": one(f"SELECT COALESCE(sum(total_amount), 0) FROM purchase_orders WHERE {open_po}"),
        "approved_suppliers": one("SELECT count(*) FROM suppliers WHERE approval_status = 'APPROVED' AND is_active"),
        "priority": [m for m in materials if m["state"] == "BELOW"][:4],
        "inbound": nxt[0] if nxt else None,
        "materials": materials,
        "vendors": vendors,
        "spend_by_kind": spend,
    }


@router.get("/ecommerce/omnihub")
def ecommerce_omnihub(db: Session = DB, _: CurrentUser = Depends(require("ecommerce.order:read"))):
    """Marketplace channels with what is still to ship, the 30-day dispatch velocity, the five fulfilment stages for marketplace orders, the live
    dispatch queue (order, customer, items, invoice, courier, AWB, status) and shipments by courier."""
    pending_so = "so.status NOT IN ('DELIVERED', 'DISPATCHED', 'CANCELLED')"
    channels = _rows(db, f"""
        SELECT a.platform, a.name, a.status,
               count(o.id) AS orders,
               count(o.id) FILTER (WHERE o.status = 'EXCEPTION' OR (so.id IS NOT NULL AND {pending_so})) AS pending,
               COALESCE(sum(o.total), 0) AS value
          FROM marketplace_accounts a LEFT JOIN external_orders o ON o.account_id = a.id LEFT JOIN sales_orders so ON so.id = o.sales_order_id
         GROUP BY a.id ORDER BY count(o.id) DESC""")
    mk = "so.channel = 'MARKETPLACE'"
    stages = {
        "ingested": _one(db, "SELECT count(*) FROM external_orders"),
        "ingested_ok": _one(db, "SELECT count(*) FROM external_orders WHERE status = 'SO_CREATED'"),
        "invoiced": _one(db, f"SELECT count(*) FROM sales_invoices si JOIN sales_orders so ON so.id = si.so_id WHERE {mk}"),
        "to_invoice": _one(db, f"SELECT count(*) FROM sales_orders so WHERE {mk} AND so.status IN ('DRAFT', 'CONFIRMED')"),
        "pick_pack": _one(db, f"SELECT count(*) FROM sales_orders so WHERE {mk} AND so.status IN ('ALLOCATED', 'PACKED')"),
        "packed": _one(db, f"SELECT count(*) FROM sales_orders so WHERE {mk} AND so.status = 'PACKED'"),
        "staged": _one(db, f"SELECT count(*) FROM shipments sh JOIN sales_orders so ON so.id = sh.so_id WHERE {mk} AND sh.status = 'BOOKED'"),
        "in_transit": _one(db, f"SELECT count(*) FROM shipments sh JOIN sales_orders so ON so.id = sh.so_id WHERE {mk} AND sh.status IN ('PICKED_UP', 'IN_TRANSIT', 'OUT_FOR_DELIVERY')"),
        "delivered": _one(db, f"SELECT count(*) FROM shipments sh JOIN sales_orders so ON so.id = sh.so_id WHERE {mk} AND sh.status = 'DELIVERED'"),
    }
    queue = _rows(db, """
        SELECT o.id, o.external_order_id, a.platform, o.customer_name, o.shipping_address, o.order_date, o.total, o.status AS import_status,
               so.number AS so_number, so.status AS so_status,
               (SELECT string_agg(it.platform_sku || ' x' || trim(to_char(it.qty, 'FM999990.###')), ', ') FROM external_order_items it WHERE it.external_order_pk = o.id) AS items,
               (SELECT si.number FROM sales_invoices si WHERE si.so_id = so.id ORDER BY si.id DESC LIMIT 1) AS invoice,
               (SELECT sh.courier FROM shipments sh WHERE sh.so_id = so.id ORDER BY sh.id DESC LIMIT 1) AS courier,
               (SELECT sh.awb FROM shipments sh WHERE sh.so_id = so.id ORDER BY sh.id DESC LIMIT 1) AS awb,
               (SELECT sh.status FROM shipments sh WHERE sh.so_id = so.id ORDER BY sh.id DESC LIMIT 1) AS ship_status
          FROM external_orders o JOIN marketplace_accounts a ON a.id = o.account_id LEFT JOIN sales_orders so ON so.id = o.sales_order_id
         ORDER BY o.order_date DESC, o.id DESC LIMIT 12""")
    couriers = _rows(db, """SELECT COALESCE(courier, 'Unassigned') AS courier, count(*) FILTER (WHERE status NOT IN ('DELIVERED', 'RTO', 'FAILED_DELIVERY')) AS active,
                                   count(*) FILTER (WHERE status = 'DELIVERED') AS delivered, count(*) AS total FROM shipments GROUP BY 1 ORDER BY count(*) DESC""")
    return {
        "channels": channels,
        "gross_value_30d": _one(db, "SELECT COALESCE(sum(total), 0) FROM external_orders WHERE order_date > business_today() - 30"),
        "previous_gross_value_30d": _one(db, "SELECT COALESCE(sum(total), 0) FROM external_orders WHERE order_date BETWEEN business_today() - 60 AND business_today() - 30"),
        "to_dispatch": _one(db, f"SELECT count(*) FROM sales_orders so WHERE {mk} AND so.status IN ('CONFIRMED', 'ALLOCATED', 'PACKED')"),
        "stages": stages, "queue": queue, "couriers": couriers,
    }


@router.get("/procurement/rfq-board")
def rfq_board(status: str | None = None, page_size: int = 100, db: Session = DB, _: CurrentUser = Depends(require("procurement.rfq:read"))):
    """RFQs with everything a card needs: the first item, each invited supplier's quote (or that it is still awaited), the recommended quotation, the price the
    item was last bought at, and for an awarded RFQ the supplier, price and purchase order. Plus the tab counts and the four figures above the board."""
    from app.procurement import rfq as svc   # local import: the RFQ service pulls in the procurement stack

    page_size = max(1, min(page_size, 100))
    where = "WHERE r.status = :st" if status else ""
    rfqs = _rows(db, f"SELECT r.id, r.number, r.status, r.version, r.due_date, r.created_at, r.notes, r.awarded_quotation_id, r.awarded_po_id, r.award_reason FROM rfqs r {where} ORDER BY r.id DESC LIMIT :n",
                 **({"st": status} if status else {}), n=page_size)
    items = []
    for r in rfqs:
        lines = _rows(db, """SELECT ri.id AS rfq_item_id, ri.qty, i.id AS item_id, i.item_code, i.name, i.kind, u.code AS uom, c.name AS category
                               FROM rfq_items ri JOIN items i ON i.id = ri.item_id LEFT JOIN units_of_measure u ON u.id = i.uom_id LEFT JOIN product_categories c ON c.id = i.category_id
                              WHERE ri.rfq_id = :r ORDER BY ri.line_no""", r=r["id"])
        first = lines[0] if lines else None
        invited = _rows(db, "SELECT s.id, s.name, s.city FROM rfq_suppliers rs JOIN suppliers s ON s.id = rs.supplier_id WHERE rs.rfq_id = :r ORDER BY s.name", r=r["id"])
        cmp = svc.compare(db, r["id"])
        by_sup = {q["supplier_id"]: q for q in cmp["quotations"]}
        rec = cmp["recommended_quotation_id"]
        bids = []
        for s in invited:
            q = by_sup.get(s["id"])
            price = next((float(l["unit_price"]) for l in q["lines"] if first and l["rfq_item_id"] == first["rfq_item_id"] and l["unit_price"] is not None), None) if q else None
            bids.append({"supplier_id": s["id"], "name": s["name"], "city": s["city"], "quoted": q is not None, "unit_price": price, "landed_total": float(q["landed_total"]) if q else None,
                         "lead_time_days": q["lead_time_days"] if q else None, "complete": q["complete"] if q else False, "expired": q["expired"] if q else False,
                         "quotation_id": q["quotation_id"] if q else None, "recommended": bool(q and q["quotation_id"] == rec),
                         "awarded": bool(q and q["quotation_id"] == r["awarded_quotation_id"])})
        prices = [b["unit_price"] for b in bids if b["unit_price"] is not None]
        last_paid = None
        if first:
            last_paid = db.scalar(text("""SELECT pi.unit_price FROM purchase_order_items pi JOIN purchase_orders po ON po.id = pi.po_id
                                           WHERE pi.item_id = :i AND po.status NOT IN ('DRAFT', 'CANCELLED') AND po.id IS DISTINCT FROM :x ORDER BY po.id DESC LIMIT 1"""), {"i": first["item_id"], "x": r["awarded_po_id"]})
        win = next((b for b in bids if b["awarded"]), None)
        po = _rows(db, "SELECT po.id, po.number, po.total_amount, po.expected_date FROM purchase_orders po WHERE po.id = :p", p=r["awarded_po_id"])[0] if r["awarded_po_id"] else None
        items.append({**{k: _num(v) for k, v in r.items()}, "item": {**first, "qty": float(first["qty"])} if first else None, "line_count": len(lines),
                      "item_name": first["name"] if first else None, "bids": bids, "bids_in": sum(1 for b in bids if b["quoted"]), "bids_total": len(bids), "bids_label": f"{sum(1 for b in bids if b['quoted'])} of {len(bids)}", "recommended_quotation_id": rec,
                      "lowest_unit_price": min(prices) if prices else None, "last_paid": _num(last_paid),
                      "award": {"supplier": win["name"] if win else None, "unit_price": win["unit_price"] if win else None, "lead_time_days": win["lead_time_days"] if win else None,
                                "po_id": po["id"] if po else None, "po_number": po["number"] if po else None, "commitment": _num(po["total_amount"]) if po else None} if r["status"] == "AWARDED" else None})
    counts = _by(db, "SELECT status, count(*) FROM rfqs GROUP BY 1")
    counts["ALL"] = sum(counts.values())
    # the four figures: quotes still awaited, how the best price compares with the last price paid, how many invitations suppliers have answered, and the value already awarded
    sent = _rows(db, "SELECT r.id, (SELECT count(*) FROM rfq_suppliers rs WHERE rs.rfq_id = r.id) AS invited, (SELECT count(*) FROM quotations q WHERE q.rfq_id = r.id AND q.status <> 'REJECTED') AS quoted FROM rfqs r WHERE r.status = 'SENT'")
    cmp_rows = _rows(db, """SELECT x.first_price, x.last_paid FROM (
              SELECT (SELECT min(qi.unit_price) FROM quotations q JOIN quotation_items qi ON qi.quotation_id = q.id WHERE q.rfq_id = r.id AND q.status <> 'REJECTED') AS first_price,
                     (SELECT pi.unit_price FROM rfq_items ri JOIN purchase_order_items pi ON pi.item_id = ri.item_id JOIN purchase_orders po ON po.id = pi.po_id
                       WHERE ri.rfq_id = r.id AND po.status NOT IN ('DRAFT', 'CANCELLED') AND po.id IS DISTINCT FROM r.awarded_po_id ORDER BY po.id DESC LIMIT 1) AS last_paid
                FROM rfqs r) x WHERE x.first_price IS NOT NULL AND x.last_paid IS NOT NULL AND x.last_paid > 0""")
    variance = (sum((float(c["first_price"]) - float(c["last_paid"])) / float(c["last_paid"]) for c in cmp_rows) / len(cmp_rows) * 100) if cmp_rows else None
    turn = db.execute(text("SELECT 0, count(*) FROM quotations q JOIN rfqs r ON r.id = q.rfq_id WHERE q.status <> 'REJECTED'")).one()
    won = db.execute(text("SELECT COALESCE(sum(po.total_amount), 0), count(*) FROM rfqs r JOIN purchase_orders po ON po.id = r.awarded_po_id WHERE r.status = 'AWARDED'")).one()
    summary = {"awaited_quotes": sum(int(s["invited"]) - int(s["quoted"]) for s in sent), "open_rfqs": len(sent), "suppliers_invited": _one(db, "SELECT count(DISTINCT rs.supplier_id) FROM rfq_suppliers rs JOIN rfqs r ON r.id = rs.rfq_id WHERE r.status = 'SENT'"),
               "variance_pct": variance, "variance_sample": len(cmp_rows), "quotes_recorded": turn[1], "invitations": _one(db, "SELECT count(*) FROM rfq_suppliers rs JOIN rfqs r ON r.id = rs.rfq_id WHERE r.status IN ('SENT', 'AWARDED')"), "awarded_value": _num(won[0]), "awarded_count": won[1]}
    return {"items": items, "page": 1, "counts": counts, "summary": summary}


@router.get("/procurement/pr-board")
def pr_board(status: str | None = None, page_size: int = 100, db: Session = DB, _: CurrentUser = Depends(require("procurement.pr:read"))):
    """Purchase requests with what a buyer needs on the card: who asked, for which items and how much, what is in stock for each, whether it has been taken
    on to an RFQ or a purchase order, and how close the required-by date is. Plus the tab counts and the four figures above the board."""
    page_size = max(1, min(page_size, 100))
    where = "WHERE p.status = :st" if status else ""
    prs = _rows(db, f"""SELECT p.id, p.number, p.status, p.version, p.required_by, p.created_at, p.notes, u.full_name AS requester, d.name AS department,
                               (p.required_by - business_today()) AS days_left,
                               (SELECT r.number FROM rfqs r WHERE r.pr_id = p.id ORDER BY r.id DESC LIMIT 1) AS rfq_number,
                               (SELECT r.status FROM rfqs r WHERE r.pr_id = p.id ORDER BY r.id DESC LIMIT 1) AS rfq_status,
                               (SELECT po.number FROM purchase_orders po WHERE po.pr_id = p.id AND po.status <> 'CANCELLED' ORDER BY po.id DESC LIMIT 1) AS po_number,
                               (SELECT po.status FROM purchase_orders po WHERE po.pr_id = p.id AND po.status <> 'CANCELLED' ORDER BY po.id DESC LIMIT 1) AS po_status
                          FROM purchase_requests p LEFT JOIN users u ON u.id = p.requested_by LEFT JOIN departments d ON d.id = p.department_id
                          {where} ORDER BY p.id DESC LIMIT :n""", **({"st": status} if status else {}), n=page_size)
    out = []
    for p in prs:
        lines = _rows(db, """SELECT i.id AS item_id, i.item_code, i.name, i.kind, u.code AS uom, pi.qty, i.reorder_level,
                                    COALESCE((SELECT sum(b.qty_available) FROM stock_balances b WHERE b.item_id = i.id), 0) AS free
                               FROM purchase_request_items pi JOIN items i ON i.id = pi.item_id LEFT JOIN units_of_measure u ON u.id = i.uom_id
                              WHERE pi.pr_id = :p ORDER BY pi.line_no""", p=p["id"])
        for l in lines:
            l["short"] = float(l["free"]) < float(l["qty"])          # stock on hand would not cover this request
        sourced = "PO" if p["po_number"] else "RFQ" if p["rfq_number"] else None
        out.append({**p, "lines": lines, "line_count": len(lines), "item_name": lines[0]["name"] if lines else None, "sourced": sourced,
                    "sourcing": p["po_number"] or p["rfq_number"], "lines_label": f"{len(lines)} item{'' if len(lines) == 1 else 's'}"})
    counts = _by(db, "SELECT status, count(*) FROM purchase_requests GROUP BY 1")
    counts["ALL"] = sum(counts.values())
    one = lambda sql: _one(db, sql)
    summary = {
        "awaiting_approval": one("SELECT count(*) FROM purchase_requests WHERE status = 'SUBMITTED'"),
        "oldest_waiting_days": one("SELECT COALESCE(max(business_today() - created_at::date), 0) FROM purchase_requests WHERE status = 'SUBMITTED'"),
        "to_source": one("SELECT count(*) FROM purchase_requests p WHERE p.status = 'APPROVED' AND NOT EXISTS (SELECT 1 FROM rfqs r WHERE r.pr_id = p.id) AND NOT EXISTS (SELECT 1 FROM purchase_orders o WHERE o.pr_id = p.id AND o.status <> 'CANCELLED')"),
        "approved": one("SELECT count(*) FROM purchase_requests WHERE status = 'APPROVED'"),
        "due_soon": one("SELECT count(*) FROM purchase_requests WHERE status IN ('SUBMITTED', 'APPROVED') AND required_by BETWEEN business_today() AND business_today() + 7"),
        "overdue": one("SELECT count(*) FROM purchase_requests WHERE status IN ('SUBMITTED', 'APPROVED') AND required_by < business_today()"),
        "sourced": one("SELECT count(*) FROM purchase_requests p WHERE p.status = 'APPROVED' AND (EXISTS (SELECT 1 FROM rfqs r WHERE r.pr_id = p.id) OR EXISTS (SELECT 1 FROM purchase_orders o WHERE o.pr_id = p.id AND o.status <> 'CANCELLED'))"),
    }
    return {"items": out, "page": 1, "counts": counts, "summary": summary}


@router.get("/ecommerce/orders-board")
def ecommerce_orders_board(status: str | None = None, page_size: int = 100, db: Session = DB, _: CurrentUser = Depends(require("ecommerce.order:read"))):
    """Imported marketplace orders with the channel, the buyer, each line matched to our item (or flagged unmapped), the sales order it became, and the reason
    when it is stuck in the exception queue. Plus the tab counts and the four figures above the board."""
    page_size = max(1, min(page_size, 100))
    where = "WHERE e.status = :st" if status else ""
    orders = _rows(db, f"""SELECT e.id, e.external_order_id, e.status, e.order_date, e.received_at, e.customer_name, e.shipping_address, e.total, e.exception_reason, e.account_id,
                                  a.platform, a.name AS account_name, so.number AS so_number, so.status AS so_status
                             FROM external_orders e JOIN marketplace_accounts a ON a.id = e.account_id LEFT JOIN sales_orders so ON so.id = e.sales_order_id
                             {where} ORDER BY e.id DESC LIMIT :n""", **({"st": status} if status else {}), n=page_size)
    for o in orders:
        o["lines"] = _rows(db, """SELECT i.platform_sku AS sku, i.qty, i.unit_price, it.name AS item_name, it.item_code
                                    FROM external_order_items i LEFT JOIN sku_mappings m ON m.account_id = :a AND m.platform_sku = i.platform_sku
                                    LEFT JOIN items it ON it.id = m.item_id WHERE i.external_order_pk = :o ORDER BY i.id""", a=o["account_id"], o=o["id"])
        o["line_count"] = len(o["lines"])
        o["unmapped"] = sum(1 for l in o["lines"] if not l["item_name"])
        o["city"] = (o["shipping_address"] or "").split(",")[-1].strip() or None if o["shipping_address"] else None
        o["buyer"] = (o["customer_name"] or "").split(",")[0].strip() or None
        o["platform_label"] = o["platform"]
        o["first_item"] = (o["lines"][0]["item_name"] or o["lines"][0]["sku"]) if o["lines"] else None
        o["lines_label"] = f"{len(o['lines'])} line{'' if len(o['lines']) == 1 else 's'}"
    counts = _by(db, "SELECT status, count(*) FROM external_orders GROUP BY 1")
    counts["ALL"] = sum(counts.values())
    one = lambda sql: _one(db, sql)
    summary = {
        "received_30d": one("SELECT count(*) FROM external_orders WHERE received_at >= now() - interval '30 days'"),
        "value_30d": one("SELECT COALESCE(sum(total), 0) FROM external_orders WHERE received_at >= now() - interval '30 days' AND status <> 'EXCEPTION'"),
        "exceptions": counts.get("EXCEPTION", 0),
        "exception_value": one("SELECT COALESCE(sum(total), 0) FROM external_orders WHERE status = 'EXCEPTION'"),
        "converted": counts.get("SO_CREATED", 0), "total": counts["ALL"],
        "channels": one("SELECT count(DISTINCT account_id) FROM external_orders"),
        "unmapped_skus": one("""SELECT count(DISTINCT (e.account_id, i.platform_sku)) FROM external_orders e JOIN external_order_items i ON i.external_order_pk = e.id
                                 LEFT JOIN sku_mappings m ON m.account_id = e.account_id AND m.platform_sku = i.platform_sku WHERE e.status = 'EXCEPTION' AND m.id IS NULL"""),
    }
    return {"items": orders, "page": 1, "counts": counts, "summary": summary}


@router.get("/procurement/po-board")
def po_board(status: str | None = None, page_size: int = 100, db: Session = DB, _: CurrentUser = Depends(require("procurement.po:read"))):
    """Purchase orders with the supplier, each line's ordered and received quantity, how far the receipt has got, where the order came from (RFQ or request)
    and how close the expected date is. Plus the tab counts and the four figures above the board."""
    page_size = max(1, min(page_size, 100))
    where = "WHERE po.status = :st" if status else ""
    pos = _rows(db, f"""SELECT po.id, po.number, po.status, po.version, po.expected_date, po.total_amount, po.notes, po.created_at, po.approved_at, po.rejection_reason,
                               s.name AS supplier, s.city AS supplier_city, (po.expected_date - business_today()) AS days_left,
                               pr.number AS pr_number, (SELECT r.number FROM rfqs r WHERE r.awarded_po_id = po.id LIMIT 1) AS rfq_number
                          FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id LEFT JOIN purchase_requests pr ON pr.id = po.pr_id
                          {where} ORDER BY po.id DESC LIMIT :n""", **({"st": status} if status else {}), n=page_size)
    for po in pos:
        po["lines"] = _rows(db, """SELECT i.item_code, i.name, u.code AS uom, pi.qty_ordered, pi.qty_received, pi.unit_price
                                     FROM purchase_order_items pi JOIN items i ON i.id = pi.item_id LEFT JOIN units_of_measure u ON u.id = i.uom_id
                                    WHERE pi.po_id = :p ORDER BY pi.line_no""", p=po["id"])
        ordered = sum(float(l["qty_ordered"]) for l in po["lines"])
        received = sum(float(l["qty_received"]) for l in po["lines"])
        po["received_pct"] = round(100 * received / ordered, 1) if ordered else 0
        po["line_count"] = len(po["lines"])
        po["item_name"] = po["lines"][0]["name"] if po["lines"] else None
        po["lines_label"] = f"{len(po['lines'])} line{'' if len(po['lines']) == 1 else 's'}"
        po["source"] = po["rfq_number"] or po["pr_number"]
        po["late"] = po["status"] in ("APPROVED", "PARTIALLY_RECEIVED") and po["days_left"] is not None and po["days_left"] < 0
    counts = _by(db, "SELECT status, count(*) FROM purchase_orders GROUP BY 1")
    counts["ALL"] = sum(counts.values())
    one = lambda sql: _one(db, sql)
    openq = "status IN ('APPROVED', 'PARTIALLY_RECEIVED')"
    summary = {
        "open_value": one(f"SELECT COALESCE(sum(total_amount), 0) FROM purchase_orders WHERE {openq}"), "open_count": one(f"SELECT count(*) FROM purchase_orders WHERE {openq}"),
        "pending_count": counts.get("PENDING_APPROVAL", 0), "pending_value": one("SELECT COALESCE(sum(total_amount), 0) FROM purchase_orders WHERE status = 'PENDING_APPROVAL'"),
        "due_soon": one(f"SELECT count(*) FROM purchase_orders WHERE {openq} AND expected_date BETWEEN business_today() AND business_today() + 7"),
        "overdue": one(f"SELECT count(*) FROM purchase_orders WHERE {openq} AND expected_date < business_today()"),
        "received_30d": one("SELECT count(*) FROM purchase_orders WHERE status = 'RECEIVED' AND updated_at >= now() - interval '30 days'"),
        "received_value_30d": one("SELECT COALESCE(sum(total_amount), 0) FROM purchase_orders WHERE status = 'RECEIVED' AND updated_at >= now() - interval '30 days'"),
    }
    return {"items": pos, "page": 1, "counts": counts, "summary": summary}


# ------------------------------------------------------------------ returns to supplier
@router.get("/procurement/returns-board")
def returns_board(reason_type: str | None = None, page_size: int = 100, db: Session = DB, _: CurrentUser = Depends(require("procurement.return:read"))):
    """Goods sent back to suppliers: who, why, which receipt and PO, what was returned and its value, plus the tab counts and four figures."""
    page_size = max(1, min(page_size, 100))
    where = "WHERE r.reason_type = :rt" if reason_type else ""
    rows = _rows(db, f"""SELECT r.id, r.number, r.reason_type, r.reason, r.total_value, r.posted_at, s.name AS supplier, s.city AS supplier_city, po.number AS po_number, gr.number AS receipt_number, u.full_name AS posted_by
                           FROM purchase_returns r JOIN suppliers s ON s.id = r.supplier_id JOIN purchase_orders po ON po.id = r.po_id JOIN goods_receipts gr ON gr.id = r.goods_receipt_id
                           LEFT JOIN users u ON u.id = r.posted_by {where} ORDER BY r.id DESC LIMIT :n""", **({"rt": reason_type} if reason_type else {}), n=page_size)
    for r in rows:
        r["lines"] = _rows(db, """SELECT it.name, it.item_code, u.code AS uom, l.qty, l.unit_price, l.value FROM purchase_return_lines l JOIN items it ON it.id = l.item_id
                                    LEFT JOIN units_of_measure u ON u.id = it.uom_id WHERE l.return_id = :r ORDER BY l.line_no""", r=r["id"])
        r["line_count"] = len(r["lines"])
        r["item_name"] = r["lines"][0]["name"] if r["lines"] else None
        r["lines_label"] = f"{len(r['lines'])} line{'' if len(r['lines']) == 1 else 's'}"
    counts = _by(db, "SELECT reason_type, count(*) FROM purchase_returns GROUP BY 1")
    counts["ALL"] = sum(counts.values())
    one = lambda sql: _one(db, sql)
    summary = {"returns_30d": one("SELECT count(*) FROM purchase_returns WHERE posted_at >= now() - interval '30 days'"),
               "value_30d": one("SELECT COALESCE(sum(total_value), 0) FROM purchase_returns WHERE posted_at >= now() - interval '30 days'"),
               "total": counts["ALL"], "qc_rejected": counts.get("QC_REJECTED", 0), "value_total": one("SELECT COALESCE(sum(total_value), 0) FROM purchase_returns"),
               "suppliers": one("SELECT count(DISTINCT supplier_id) FROM purchase_returns"),
               "top_supplier": db.execute(text("SELECT s.name FROM purchase_returns r JOIN suppliers s ON s.id = r.supplier_id GROUP BY s.name ORDER BY sum(r.total_value) DESC LIMIT 1")).scalar()}
    return {"items": rows, "page": 1, "counts": counts, "summary": summary}


# ------------------------------------------------------------------ production orders
@router.get("/production/orders-board")
def production_orders_board(status: str | None = None, page_size: int = 100, db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    """Production orders with the product, quantities, yield and cost, how far the stages and the material issue have got, and the planned date."""
    page_size = max(1, min(page_size, 100))
    where = "WHERE o.status = :st" if status else ""
    rows = _rows(db, f"""SELECT o.id, o.number, o.status, o.version, o.bom_id, o.planned_qty, o.actual_qty, o.yield_pct, o.unit_cost, o.planned_date, o.created_at, o.started_at, o.completed_at,
                                it.name AS item_name, it.item_code, u.code AS uom, (o.planned_date - business_today()) AS days_left
                           FROM production_orders o JOIN items it ON it.id = o.output_item_id LEFT JOIN units_of_measure u ON u.id = it.uom_id
                           {where} ORDER BY o.id DESC LIMIT :n""", **({"st": status} if status else {}), n=page_size)
    for o in rows:
        o["stages"] = _rows(db, """SELECT s.seq, s.name, s.status, us.full_name AS operator FROM production_stages s LEFT JOIN users us ON us.id = s.operator_id WHERE s.order_id = :o ORDER BY s.seq""", o=o["id"])
        mats = db.execute(text("SELECT COALESCE(sum(required_qty), 0), COALESCE(sum(issued_qty), 0), count(*) FROM production_materials WHERE order_id = :o"), {"o": o["id"]}).one()
        o["materials_pct"] = round(100 * float(mats[1]) / float(mats[0]), 1) if float(mats[0]) else 0
        o["material_lines"] = mats[2]
        o["stages_done"] = sum(1 for s in o["stages"] if s["status"] == "COMPLETED")
        o["stages_total"] = len(o["stages"])
        cur = next((s for s in o["stages"] if s["status"] == "IN_PROGRESS"), None) or next((s for s in o["stages"] if s["status"] == "PENDING"), None)
        o["current_stage"] = cur["name"] if cur else None
        o["operator"] = cur["operator"] if cur else None
        o["late"] = o["status"] in ("PLANNED", "RELEASED", "IN_PROGRESS") and o["days_left"] is not None and o["days_left"] < 0
    counts = _by(db, "SELECT status, count(*) FROM production_orders GROUP BY 1")
    counts["ALL"] = sum(counts.values())
    one = lambda sql: _one(db, sql)
    avg = db.execute(text("SELECT avg(yield_pct), COALESCE(sum(actual_qty), 0), count(*) FROM production_orders WHERE status = 'COMPLETED' AND completed_at >= now() - interval '30 days'")).one()
    summary = {"in_progress": counts.get("IN_PROGRESS", 0), "released": counts.get("RELEASED", 0), "planned": counts.get("PLANNED", 0),
               "late": one("SELECT count(*) FROM production_orders WHERE status IN ('PLANNED', 'RELEASED', 'IN_PROGRESS') AND planned_date < business_today()"),
               "completed_30d": avg[2], "units_30d": _num(avg[1]), "avg_yield_30d": _num(avg[0]),
               "wip_value": one("SELECT COALESCE(sum(wip_cost), 0) FROM production_orders WHERE status IN ('RELEASED', 'IN_PROGRESS')")}
    return {"items": rows, "page": 1, "counts": counts, "summary": summary}


# ------------------------------------------------------------------ work centres
@router.get("/production/work-centres-board")
def work_centres_board(page_size: int = 100, db: Session = DB, _: CurrentUser = Depends(require("production.work_centre:read"))):
    """Work centres with capacity per day, whether they are in use, the stages run on them in the last 30 days and the stages waiting."""
    rows = _rows(db, """SELECT w.id, w.code, w.name, w.kind, w.capacity_per_day, w.is_active, d.name AS department,
                               (SELECT count(*) FROM production_stages s WHERE s.work_centre_id = w.id AND s.status = 'IN_PROGRESS') AS running,
                               (SELECT count(*) FROM production_stages s JOIN production_orders o ON o.id = s.order_id WHERE s.work_centre_id = w.id AND s.status = 'PENDING' AND o.status IN ('RELEASED', 'IN_PROGRESS')) AS waiting,
                               (SELECT count(*) FROM production_stages s WHERE s.work_centre_id = w.id AND s.status = 'COMPLETED' AND s.completed_at >= now() - interval '30 days') AS done_30d
                          FROM work_centres w LEFT JOIN departments d ON d.id = w.department_id ORDER BY w.is_active DESC, w.kind, w.code LIMIT :n""", n=max(1, min(page_size, 100)))
    counts = {"ALL": len(rows), **{k: sum(1 for r in rows if r["kind"] == k) for k in {r["kind"] for r in rows}}}
    summary = {"active": sum(1 for r in rows if r["is_active"]), "total": len(rows), "capacity_per_day": sum(float(r["capacity_per_day"] or 0) for r in rows if r["is_active"]),
               "unplanned": sum(1 for r in rows if r["is_active"] and not r["capacity_per_day"]), "running": sum(int(r["running"]) for r in rows), "waiting": sum(int(r["waiting"]) for r in rows),
               "done_30d": sum(int(r["done_30d"]) for r in rows)}
    for r in rows:
        r["capacity_label"] = f"{float(r['capacity_per_day']):g} units/day" if r["capacity_per_day"] else "Not capacity-planned"
    return {"items": rows, "page": 1, "counts": counts, "summary": summary}


# ------------------------------------------------------------------ bills of materials
@router.get("/production/boms-board")
def boms_board(status: str | None = None, page_size: int = 100, db: Session = DB, _: CurrentUser = Depends(require("production.bom:read"))):
    """Bills of materials with the product, batch size, the components per batch, whether it is linked to an approved formula, and how it has been used."""
    page_size = max(1, min(page_size, 100))
    where = "WHERE b.status = :st" if status else ""
    rows = _rows(db, f"""SELECT b.id, b.version_no, b.status, b.output_qty, b.created_at, b.formulation_version_id IS NOT NULL AS from_formula, it.name AS output_item, it.item_code, u.code AS uom,
                                (SELECT count(*) FROM production_orders o WHERE o.bom_id = b.id) AS orders_made,
                                (SELECT o.unit_cost FROM production_orders o WHERE o.bom_id = b.id AND o.unit_cost IS NOT NULL ORDER BY o.id DESC LIMIT 1) AS last_unit_cost,
                                (SELECT us.full_name FROM users us WHERE us.id = b.approved_by) AS approved_by
                           FROM boms b JOIN items it ON it.id = b.output_item_id LEFT JOIN units_of_measure u ON u.id = it.uom_id
                           {where} ORDER BY b.id DESC LIMIT :n""", **({"st": status} if status else {}), n=page_size)
    for b in rows:
        b["lines"] = _rows(db, """SELECT it.name, it.item_code, it.kind, u.code AS uom, l.qty FROM bom_lines l JOIN items it ON it.id = l.item_id LEFT JOIN units_of_measure u ON u.id = it.uom_id
                                   WHERE l.bom_id = :b ORDER BY l.line_no""", b=b["id"])
        b["line_count"] = len(b["lines"])
        b["lines_label"] = f"{len(b['lines'])} component{'' if len(b['lines']) == 1 else 's'}"
        b["batch_label"] = f"{float(b['output_qty']):g} {str(b['uom'] or '').lower()}".strip()
    counts = _by(db, "SELECT status, count(*) FROM boms GROUP BY 1")
    counts["ALL"] = sum(counts.values())
    one = lambda sql: _one(db, sql)
    summary = {"active": counts.get("ACTIVE", 0), "draft": counts.get("DRAFT", 0), "obsolete": counts.get("OBSOLETE", 0),
               "products_covered": one("SELECT count(DISTINCT b.output_item_id) FROM boms b JOIN items i ON i.id = b.output_item_id WHERE b.status = 'ACTIVE' AND i.kind = 'FG' AND i.is_active"),
               "products_total": one("SELECT count(*) FROM items WHERE kind = 'FG' AND is_active"),
               "from_formula": one("SELECT count(*) FROM boms WHERE status = 'ACTIVE' AND formulation_version_id IS NOT NULL"),
               "avg_components": one("SELECT COALESCE(round(avg(c), 1), 0) FROM (SELECT count(*) AS c FROM bom_lines l JOIN boms b ON b.id = l.bom_id WHERE b.status = 'ACTIVE' GROUP BY l.bom_id) x")}
    return {"items": rows, "page": 1, "counts": counts, "summary": summary}


# ------------------------------------------------------------------ wastage & yield
@router.get("/production/wastage-board")
def wastage_board(start: date, end: date, db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    """Wastage and yield for a period: totals, wastage by category, and the completed orders with the lowest yield (with the product named)."""
    from app.production.extras import wastage_report
    rep = wastage_report(db, start, end)
    p = {"s": start, "e": end}
    agg = db.execute(text("""SELECT count(*), COALESCE(sum(planned_qty), 0), COALESCE(sum(actual_qty), 0), avg(yield_pct) FROM production_orders
                              WHERE status = 'COMPLETED' AND business_date(completed_at) BETWEEN :s AND :e"""), p).one()
    names = {r["id"]: r["name"] for r in _rows(db, "SELECT id, name FROM items WHERE id = ANY(:ids)", ids=[y["output_item_id"] for y in rep["lowest_yield_orders"]] or [0])}
    orders = [{**y, "item_name": names.get(y["output_item_id"]), "short_by": (float(y["planned_qty"]) - float(y["actual_qty"])) if y["actual_qty"] is not None else None} for y in rep["lowest_yield_orders"]]
    return {"period": rep["period"], "total_value": float(rep["total_value_at_cost"]), "records": sum(int(r["records"]) for r in rep["by_category"]),
            "by_category": [{"category": r["category"], "department": r["department"], "qty": float(r["qty"]), "records": r["records"], "value": float(r["value_at_cost"])} for r in rep["by_category"]],
            "orders_completed": agg[0], "planned_units": _num(agg[1]), "made_units": _num(agg[2]), "avg_yield": _num(agg[3]), "lowest_yield_orders": orders}
