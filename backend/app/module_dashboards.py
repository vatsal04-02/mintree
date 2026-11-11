"""Summary figures for the module landing pages (CRM & Sales, Meta & Branding, E-commerce, R&D, Quality, Purchase, Inventory, Accounts).

Each endpoint is read-only and returns plain counts and sums computed from the business tables, the same way /production/dashboard and
/dispatch/dashboard do. Scalars become the figure cards at the top of the page; the nested objects become small breakdown sections.
Money keys carry a money word (value, total, spend, amount ...) because the UI formats a figure from its name."""
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
