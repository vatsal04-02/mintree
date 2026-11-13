"""Expiry control: what is about to expire, what already has, and the one action that stops expired stock being used.

Sales allocation already ignores expired stock (FEFO candidates are unexpired and QC-released). This adds what was missing around it:
  * a worklist grouped by urgency with quantity, value and who still has it reserved, so the stores team knows what to sell first, rework or dispose of;
  * a sweep that places expired stock on HOLD through the normal QC path (ledgered, audited), so it also stops appearing as released stock elsewhere;
  * a minimum remaining shelf life for sales allocation (company setting `dispatch.min_shelf_life_days`, default 0 = off): short-dated stock is not allocated to
    orders, only sold by an explicit choice.
Nothing is deleted or written off here; disposal stays a stock adjustment with its own approval."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.approvals.service import get_setting
from app.audit import service as audit
from app.core import activity
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.uow import run_tx
from app.inventory import service as inv
from app.notifications.service import notify

router = APIRouter(prefix="/inventory/expiry", tags=["expiry"])
DB = Depends(get_db, scope="function")


class SweepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(None, max_length=300)


def _bucket(days_left: int) -> str:
    return "EXPIRED" if days_left < 0 else "WITHIN_30" if days_left <= 30 else "WITHIN_90" if days_left <= 90 else "LATER"


ACTION = {"EXPIRED": "Hold and dispose: it cannot be sold", "WITHIN_30": "Sell first or rework now", "WITHIN_90": "Plan to sell first", "LATER": "Monitor"}


def worklist(db: Session, within_days: int) -> dict:
    rows = db.execute(text("""
        SELECT b.id, b.batch_no, b.expiry_date, b.qc_status, i.item_code, i.name, i.kind, b.expiry_date - business_today() AS days_left,
               sum(sb.qty_physical) AS physical, sum(sb.qty_reserved) AS reserved, sum(sb.qty_available) AS available,
               COALESCE(max(CASE WHEN v.qty > 0 THEN v.value / v.qty END), 0) AS unit_cost
          FROM inventory_batches b JOIN items i ON i.id = b.item_id JOIN stock_balances sb ON sb.batch_id = b.id LEFT JOIN item_valuation v ON v.item_id = i.id
         WHERE b.expiry_date IS NOT NULL AND b.expiry_date <= business_today() + :d
         GROUP BY b.id, i.id HAVING sum(sb.qty_physical) > 0 ORDER BY b.expiry_date, b.id LIMIT 500"""), {"d": within_days}).mappings().all()
    items, totals = [], {"EXPIRED": Decimal(0), "WITHIN_30": Decimal(0), "WITHIN_90": Decimal(0), "LATER": Decimal(0)}
    ids = [r["id"] for r in rows]
    orders: dict[int, list[str]] = {}
    if ids:
        for bid, num in db.execute(text("""SELECT DISTINCT b.batch_id, o.number FROM stock_reservations r JOIN stock_balances b ON b.id = r.balance_id
                                             JOIN sales_orders o ON r.reference_type = 'sales_order' AND r.reference_id = o.id::text WHERE b.batch_id = ANY(:i) AND r.status = 'ACTIVE'"""), {"i": ids}):
            orders.setdefault(bid, []).append(num)
    for r in rows:
        bucket = _bucket(r["days_left"])
        value = (r["physical"] * r["unit_cost"]).quantize(Decimal("0.01"))
        totals[bucket] += value
        items.append({"batch_id": r["id"], "batch_no": r["batch_no"], "item_code": r["item_code"], "name": r["name"], "kind": r["kind"], "expiry_date": r["expiry_date"], "days_left": r["days_left"],
                      "bucket": bucket, "qc_status": r["qc_status"], "physical": str(r["physical"]), "reserved": str(r["reserved"]), "available": str(r["available"]), "value": str(value),
                      "reserved_for_orders": orders.get(r["id"], []), "suggested_action": ACTION[bucket],
                      "needs_hold": bucket == "EXPIRED" and r["qc_status"] in ("RELEASED", "QUARANTINE")})
    return {"within_days": within_days, "items": items, "value_by_bucket": {k: str(v) for k, v in totals.items()},
            "counts": {b: sum(1 for x in items if x["bucket"] == b) for b in totals}, "min_shelf_life_days": int(get_setting(db, "dispatch.min_shelf_life_days") or 0),
            "truncated": len(rows) >= 500}


def sweep(db: Session, cu: CurrentUser, note: str | None) -> dict:
    """Hold every expired batch that still counts as released or quarantined stock."""
    rows = db.execute(text("""SELECT b.id, b.batch_no FROM inventory_batches b
                               WHERE b.expiry_date < business_today() AND b.qc_status IN ('RELEASED','QUARANTINE')
                                 AND EXISTS (SELECT 1 FROM stock_balances sb WHERE sb.batch_id = b.id AND sb.qty_physical > 0) ORDER BY b.id""")).all()
    held = []
    for bid, no in rows:
        inv.set_qc_status(db, batch_id=bid, decision="HOLD", reason=f"Expired stock held by expiry sweep{': ' + note if note else ''}", actor_id=cu.id)
        held.append(no)
    near = int(get_setting(db, "quality.near_expiry_days") or 90)
    soon = db.execute(text("""SELECT count(DISTINCT b.id) FROM inventory_batches b JOIN stock_balances sb ON sb.batch_id = b.id AND sb.qty_physical > 0
                               WHERE b.expiry_date BETWEEN business_today() AND business_today() + 30 AND b.qc_status = 'RELEASED'""")).scalar() or 0
    audit.record(db, user=cu, action="expiry_sweep", entity="inventory_batch", entity_id="sweep", after={"held": len(held), "expiring_in_30_days": soon}, reason=note)
    if held or soon:
        activity.emit(db, "inventory.expiry.swept", entity="expiry_sweep", entity_id=f"sweep-{cu.id}", number=f"{len(held)} held", actor_id=cu.id, held=len(held), soon=soon)
        notify(db, kind="inventory.expiry", title=f"Expiry sweep: {len(held)} batch(es) held, {soon} expiring within 30 days", link="/m/inventory/expiry-control", permission="inventory.batch:qc_release",
               priority="HIGH" if held else "NORMAL", exclude_user_id=cu.id)
    return {"held": held, "held_count": len(held), "expiring_within_30_days": soon, "near_expiry_days": near}


@router.get("/worklist")
def worklist_route(within_days: int = Query(90, ge=0, le=730), db: Session = DB, _: CurrentUser = Depends(require("inventory.stock:read"))):
    return worklist(db, within_days)


@router.post("/sweep")
def sweep_route(body: SweepIn, db: Session = DB, cu: CurrentUser = Depends(require("inventory.batch:qc_release"))):
    return run_tx(db, lambda d: sweep(d, cu, body.note), name="inventory.expiry.sweep")
