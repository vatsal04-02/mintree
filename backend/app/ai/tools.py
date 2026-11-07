"""Read-only tools the assistant may call. Rules enforced here, not in the prompt:

* each tool names the permission it needs and it is re-checked on every call against the signed-in user (a model cannot widen access);
* arguments are validated by a pydantic model with `extra="forbid"`; unknown tools and bad arguments are refused and recorded;
* execution happens inside a SAVEPOINT with `transaction_read_only = on`, so even a defective tool cannot write (PostgreSQL refuses);
* results carry only business facts: no bank details, password or token columns exist in any query here; text values are clipped;
* every result lists the records it was built from, which become the citations shown to the user."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.deps import CurrentUser

CLIP = 160           # longest free-text value returned to the model
MAX_ROWS = 15


class _A(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_A):
    pass


class ItemArg(_A):
    item: str = Field(min_length=1, max_length=80, description="Item code or part of the name")


class TermArg(_A):
    term: str = Field(min_length=2, max_length=60, description="A document number such as PO/26/000012, or a name")


class BatchArg(_A):
    batch_no: str = Field(min_length=1, max_length=60)


class OrderArg(_A):
    number: str = Field(min_length=1, max_length=40, description="Sales order number")


@dataclass
class ToolResult:
    data: Any
    cites: list[dict]        # [{label, href}]


@dataclass
class Tool:
    name: str
    description: str
    permission: str
    args: type[BaseModel]
    fn: Callable[[Session, CurrentUser, Any], ToolResult]

    def spec(self) -> dict:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}


def _clip(v: Any) -> Any:
    if isinstance(v, str) and len(v) > CLIP:
        return v[:CLIP] + "…"
    return v


def _row(r: Any) -> dict:
    return {k: _clip(v) for k, v in dict(r._mapping).items()}


def _q(db: Session, sql: str, **p) -> list[dict]:
    return [_row(r) for r in db.execute(text(sql), p)]


def _like(s: str) -> str:
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ------------------------------------------------------------------ tools
def find_item(db, cu, a: ItemArg) -> ToolResult:
    rows = _q(db, """SELECT i.id, i.item_code, i.name, i.kind, u.code AS uom, i.reorder_level, i.is_active
                       FROM items i JOIN units_of_measure u ON u.id = i.uom_id
                      WHERE i.item_code ILIKE :p OR i.name ILIKE :p ORDER BY i.item_code LIMIT :n""", p=_like(a.item), n=MAX_ROWS)
    return ToolResult({"items": rows}, [{"label": r["item_code"], "href": f"/masters/items?open={r['id']}"} for r in rows])


def stock_availability(db, cu, a: ItemArg) -> ToolResult:
    items = _q(db, "SELECT id, item_code, name FROM items WHERE item_code ILIKE :p OR name ILIKE :p ORDER BY item_code LIMIT 5", p=_like(a.item))
    out, cites = [], []
    for it in items:
        tot = _q(db, """SELECT COALESCE(sum(qty_physical),0) AS physical, COALESCE(sum(qty_reserved),0) AS reserved,
                               COALESCE(sum(qty_blocked),0) AS blocked, COALESCE(sum(qty_available),0) AS available
                          FROM stock_balances WHERE item_id = :i""", i=it["id"])[0]
        batches = _q(db, """SELECT b.batch_no, b.qc_status, b.expiry_date, sum(sb.qty_physical) AS physical, sum(sb.qty_available) AS available
                              FROM stock_balances sb JOIN inventory_batches b ON b.id = sb.batch_id
                             WHERE sb.item_id = :i GROUP BY b.id HAVING sum(sb.qty_physical) <> 0 ORDER BY b.expiry_date NULLS LAST, b.id LIMIT :n""", i=it["id"], n=MAX_ROWS)
        out.append({"item": it["item_code"], "name": it["name"], **tot, "batches": batches})
        cites.append({"label": it["item_code"], "href": f"/masters/items?open={it['id']}"})
    return ToolResult({"results": out, "note": "available = physical - reserved - blocked; quarantined/held batches are blocked"}, cites)


def batch_trace(db, cu, a: BatchArg) -> ToolResult:
    b = _q(db, """SELECT b.id, b.batch_no, i.item_code, b.qc_status, b.mfg_date, b.expiry_date, b.source_type, s.name AS supplier
                    FROM inventory_batches b JOIN items i ON i.id = b.item_id LEFT JOIN suppliers s ON s.id = b.supplier_id
                   WHERE b.batch_no = :n LIMIT 5""", n=a.batch_no)
    out = []
    for r in b:
        moves = _q(db, "SELECT posted_at, movement_type, qty_delta, source_type, source_id FROM stock_ledger WHERE batch_id = :b ORDER BY id DESC LIMIT 10", b=r["id"])
        used = _q(db, "SELECT o.number, c.qty FROM production_consumptions c JOIN production_orders o ON o.id = c.order_id WHERE c.batch_id = :b ORDER BY o.id DESC LIMIT 10", b=r["id"])
        out.append({**{k: v for k, v in r.items() if k != "id"}, "recent_movements": moves, "consumed_by_production_orders": used})
    return ToolResult({"batches": out}, [{"label": r["batch_no"], "href": "/inventory/batches"} for r in b])


def order_status(db, cu, a: OrderArg) -> ToolResult:
    o = _q(db, """SELECT o.id, o.number, c.name AS customer, o.status, o.total, business_date(o.created_at) AS created
                    FROM sales_orders o JOIN customers c ON c.id = o.customer_id WHERE o.number = :n""", n=a.number)
    if not o:
        return ToolResult({"found": False}, [])
    sh = _q(db, "SELECT number, status FROM shipments WHERE so_id = :i ORDER BY id LIMIT 10", i=o[0]["id"])
    inv = _q(db, "SELECT number, total, amount_paid, status FROM sales_invoices WHERE so_id = :i ORDER BY id LIMIT 10", i=o[0]["id"])
    return ToolResult({"order": {k: v for k, v in o[0].items() if k != "id"}, "shipments": sh, "invoices": inv}, [{"label": o[0]["number"], "href": f"/dispatch/orders?open={o[0]['id']}"}])


def receivables(db, cu, a: NoArgs) -> ToolResult:
    today = business_today()
    rows = _q(db, """SELECT i.id, i.number, c.name AS customer, i.invoice_date, (:t - i.invoice_date - c.credit_days) AS days_overdue,
                            (i.total - i.amount_paid - i.credited_amount) AS outstanding
                       FROM sales_invoices i JOIN customers c ON c.id = i.customer_id
                      WHERE i.total - i.amount_paid - i.credited_amount > 0 AND i.invoice_date + c.credit_days < :t
                      ORDER BY days_overdue DESC, i.id LIMIT :n""", t=today, n=MAX_ROWS)
    tot = _q(db, "SELECT COALESCE(sum(total - amount_paid - credited_amount),0) AS total_outstanding, count(*) AS open_invoices FROM sales_invoices WHERE total - amount_paid - credited_amount > 0")[0]
    return ToolResult({"as_of": today, **tot, "most_overdue": rows}, [{"label": r["number"], "href": f"/dispatch/invoices?open={r['id']}"} for r in rows])


def late_purchase_orders(db, cu, a: NoArgs) -> ToolResult:
    today = business_today()
    rows = _q(db, """SELECT p.id, p.number, s.name AS supplier, p.status, p.expected_date, (:t - p.expected_date) AS days_late, p.total_amount
                       FROM purchase_orders p JOIN suppliers s ON s.id = p.supplier_id
                      WHERE p.status IN ('APPROVED','PARTIALLY_RECEIVED') AND p.expected_date < :t ORDER BY days_late DESC, p.id LIMIT :n""", t=today, n=MAX_ROWS)
    return ToolResult({"as_of": today, "late_orders": rows}, [{"label": r["number"], "href": f"/procurement/orders?open={r['id']}"} for r in rows])


def quality_status(db, cu, a: NoArgs) -> ToolResult:
    c = _q(db, """SELECT (SELECT count(*) FROM inspections WHERE status IN ('PENDING','IN_PROGRESS')) AS open_inspections,
                         (SELECT count(*) FROM inventory_batches WHERE qc_status = 'HOLD') AS batches_on_hold,
                         (SELECT count(*) FROM inventory_batches WHERE qc_status = 'QUARANTINE') AS batches_in_quarantine,
                         (SELECT count(*) FROM deviations WHERE status <> 'CLOSED') AS open_deviations""")[0]
    ins = _q(db, """SELECT n.id, n.number, i.item_code, n.status FROM inspections n JOIN inventory_batches b ON b.id = n.batch_id JOIN items i ON i.id = b.item_id
                     WHERE n.status IN ('PENDING','IN_PROGRESS') ORDER BY n.id LIMIT :n""", n=MAX_ROWS)
    return ToolResult({**c, "open_inspection_list": [{k: v for k, v in r.items() if k != "id"} for r in ins]}, [{"label": r["number"], "href": f"/quality/inspections?open={r['id']}"} for r in ins])


def production_status(db, cu, a: NoArgs) -> ToolResult:
    today = business_today()
    by = _q(db, "SELECT status, count(*) AS orders FROM production_orders GROUP BY status ORDER BY status")
    delayed = _q(db, """SELECT o.id, o.number, i.item_code AS output_item, o.status, o.planned_date, o.planned_qty FROM production_orders o JOIN items i ON i.id = o.output_item_id
                         WHERE o.planned_date < :t AND o.status NOT IN ('COMPLETED','CANCELLED') ORDER BY o.planned_date LIMIT :n""", t=today, n=MAX_ROWS)
    return ToolResult({"as_of": today, "orders_by_status": by, "delayed": [{k: v for k, v in r.items() if k != "id"} for r in delayed]},
                      [{"label": r["number"], "href": f"/production/orders?open={r['id']}"} for r in delayed])


def low_stock(db, cu, a: NoArgs) -> ToolResult:
    rows = _q(db, """SELECT i.id, i.item_code, i.name, COALESCE(sum(sb.qty_available),0) AS available, i.reorder_level
                       FROM items i LEFT JOIN stock_balances sb ON sb.item_id = i.id
                      WHERE i.is_active AND i.reorder_level > 0 GROUP BY i.id HAVING COALESCE(sum(sb.qty_available),0) < i.reorder_level
                      ORDER BY i.item_code LIMIT :n""", n=MAX_ROWS)
    return ToolResult({"below_reorder_level": [{k: v for k, v in r.items() if k != "id"} for r in rows]}, [{"label": r["item_code"], "href": f"/masters/items?open={r['id']}"} for r in rows])


def business_overview(db, cu, a: NoArgs) -> ToolResult:
    from app.analytics.router import management_summary
    raw = management_summary(as_of=None, db=db, _=cu)
    return ToolResult(_shrink(raw), [{"label": "Overview", "href": "/"}])


def record_search(db, cu, a: TermArg) -> ToolResult:
    p, out = _like(a.term), []
    for kind, sql, base in (
        ("purchase_order", "SELECT id, number, status FROM purchase_orders WHERE number ILIKE :p ORDER BY id DESC LIMIT 5", "/procurement/orders"),
        ("sales_order", "SELECT id, number, status FROM sales_orders WHERE number ILIKE :p ORDER BY id DESC LIMIT 5", "/dispatch/orders"),
        ("sales_invoice", "SELECT id, number, status FROM sales_invoices WHERE number ILIKE :p ORDER BY id DESC LIMIT 5", "/dispatch/invoices"),
        ("production_order", "SELECT id, number, status FROM production_orders WHERE number ILIKE :p ORDER BY id DESC LIMIT 5", "/production/orders"),
        ("inspection", "SELECT id, number, status FROM inspections WHERE number ILIKE :p ORDER BY id DESC LIMIT 5", "/quality/inspections"),
    ):
        for r in _q(db, sql, p=p):
            out.append({"kind": kind, "number": r["number"], "status": r["status"], "href": f"{base}?open={r['id']}"})
    return ToolResult({"matches": out}, [{"label": m["number"], "href": m["href"]} for m in out])


def _shrink(o: Any, depth: int = 0) -> Any:
    if isinstance(o, dict):
        return {k: _shrink(v, depth + 1) for k, v in o.items()}
    if isinstance(o, list):
        return [_shrink(v, depth + 1) for v in o[:10]]
    return _clip(o)


_T = [
    Tool("find_item", "Find items by code or name.", "masters.item:read", ItemArg, find_item),
    Tool("stock_availability", "Physical, reserved, blocked and available stock for an item, with batches (QC status, expiry).", "inventory.stock:read", ItemArg, stock_availability),
    Tool("batch_trace", "Where a batch came from, its QC status, recent movements and which production orders consumed it.", "inventory.stock:read", BatchArg, batch_trace),
    Tool("order_status", "Status of a sales order with its shipments and invoices.", "dispatch.order:read", OrderArg, order_status),
    Tool("receivables", "Total outstanding customer invoices and the most overdue ones.", "accounts.sales_invoice:read", NoArgs, receivables),
    Tool("late_purchase_orders", "Approved purchase orders past their expected delivery date.", "procurement.po:read", NoArgs, late_purchase_orders),
    Tool("quality_status", "Open inspections, batches on hold or in quarantine, open deviations.", "quality.inspection:read", NoArgs, quality_status),
    Tool("production_status", "Production orders by status and those past their planned date.", "production.order:read", NoArgs, production_status),
    Tool("low_stock", "Active items whose available stock is below the reorder level.", "inventory.stock:read", NoArgs, low_stock),
    Tool("business_overview", "Company-wide headline figures (sales, receivables, payables, cash, inventory value, orders, production, quality).", "analytics.dashboard:read", NoArgs, business_overview),
    Tool("record_search", "Find purchase orders, sales orders, invoices, production orders or inspections by number.", "analytics.dashboard:read", TermArg, record_search),
]
REGISTRY: dict[str, Tool] = {t.name: t for t in _T}


def tools_for(cu: CurrentUser) -> list[Tool]:
    """Only the tools the user could use directly; the model is never told about the others."""
    return [t for t in REGISTRY.values() if cu.can(t.permission)]


def _jsonable(o: Any) -> Any:
    if isinstance(o, (Decimal, date)):
        return str(o)
    raise TypeError(type(o))


def run_tool(db: Session, cu: CurrentUser, name: str, raw_args: dict) -> tuple[str, ToolResult | None, str]:
    """Returns (status, result, message-for-the-model). Never raises: a refusal is an answer the model can explain."""
    t = REGISTRY.get(name)
    if t is None:
        return "UNKNOWN", None, f"There is no tool called '{name}'."
    if not cu.can(t.permission):
        return "DENIED", None, f"The signed-in user is not permitted to use '{name}'. Say so plainly; do not try another route to the same data."
    try:
        args = t.args.model_validate(raw_args or {})
    except ValidationError as e:
        return "ERROR", None, "Invalid arguments: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())[:300]
    sp = db.begin_nested()
    try:
        db.execute(text("SET LOCAL transaction_read_only = on"))
        res = t.fn(db, cu, args)
        return "OK", res, json.dumps(res.data, default=_jsonable)
    except Exception as e:         # a tool failure must not take the conversation down
        return "ERROR", None, f"The lookup failed ({type(e).__name__})."
    finally:
        sp.rollback()             # reverts the read-only flag and releases any locks; tools write nothing anyway


def result_hash(text_: str) -> str:
    return hashlib.sha256(text_.encode()).hexdigest()
