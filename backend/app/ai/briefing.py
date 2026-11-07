"""Daily briefing: what needs attention for this user today. Built entirely from the read-only tools the user is already permitted to use,
so it carries the same figures, links and permission limits as the assistant. No model is involved, no tokens are spent."""
from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.ai import tools as T
from app.ai.models import AiBriefing
from app.ai import proposals
from app.core.clock import business_today
from app.core.deps import CurrentUser
from app.core.uow import run_tx


def _plain(o):
    return json.loads(json.dumps(o, default=str))


def build(db: Session, cu: CurrentUser) -> dict:
    sections = []

    def run(name):
        st, res, _ = T.run_tool(db, cu, name, {})
        return res.data if st == "OK" and res else None

    d = run("late_purchase_orders")
    if d is not None:
        sections.append({"key": "late_pos", "title": "Late purchase orders", "count": len(d["late_orders"]),
                         "items": [{"label": r["number"], "detail": f"{r['supplier']} · {r['days_late']} days late", "href": f"/procurement/orders?open={_id(db, 'purchase_orders', r['number'])}"} for r in d["late_orders"][:5]]})
    d = run("receivables")
    if d is not None:
        sections.append({"key": "receivables", "title": "Overdue receivables", "count": len(d["most_overdue"]), "headline": f"Outstanding {d['total_outstanding']} across {d['open_invoices']} open invoices",
                         "items": [{"label": r["number"], "detail": f"{r['customer']} · {r['outstanding']} · {r['days_overdue']} days overdue", "href": f"/dispatch/invoices?open={_id(db, 'sales_invoices', r['number'])}"} for r in d["most_overdue"][:5]]})
    d = run("low_stock")
    if d is not None:
        sections.append({"key": "low_stock", "title": "Below reorder level", "count": len(d["below_reorder_level"]),
                         "items": [{"label": r["item_code"], "detail": f"available {r['available']} · reorder at {r['reorder_level']}", "href": "/inventory/stock"} for r in d["below_reorder_level"][:5]]})
    d = run("quality_status")
    if d is not None:
        sections.append({"key": "quality", "title": "Quality", "count": d["open_inspections"] + d["batches_on_hold"] + d["open_deviations"],
                         "headline": f"{d['open_inspections']} open inspections · {d['batches_on_hold']} batches on hold · {d['open_deviations']} open deviations", "items": []})
    d = run("production_status")
    if d is not None:
        sections.append({"key": "production", "title": "Delayed production", "count": len(d["delayed"]),
                         "items": [{"label": r["number"], "detail": f"{r['output_item']} · planned {r['planned_date']} · {r['status']}", "href": "/production/orders"} for r in d["delayed"][:5]]})
    if cu.can("ai.proposal:review"):
        n = len(proposals.visible(db, cu))
        sections.append({"key": "proposals", "title": "Suggestions waiting for you", "count": n, "headline": f"{n} suggestion(s) to accept or reject", "items": [], "href": "/ai/proposals"})
    return {"day": str(business_today()), "generated_without_ai_model": True, "sections": _plain([s for s in sections])}


def _id(db: Session, table: str, number: str):
    return db.scalar(text(f"SELECT id FROM {table} WHERE number = :n"), {"n": number})        # table names come from the literals above only


def today(db: Session, cu: CurrentUser, refresh: bool = False) -> dict:
    uid, day = cu.id, business_today()
    row = db.scalar(select(AiBriefing).where(AiBriefing.user_id == uid, AiBriefing.day == day))
    if row and not refresh:
        return {**row.content, "cached": True}
    content = build(db, cu)

    def save(d: Session):
        d.execute(pg_insert(AiBriefing).values(user_id=uid, day=day, content=content).on_conflict_do_update(index_elements=["user_id", "day"], set_={"content": content}))
    run_tx(db, save, name="ai.briefing.save")
    return {**content, "cached": False}
