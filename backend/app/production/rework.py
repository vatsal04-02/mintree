"""Material return and rework: the two ways stock legitimately goes backwards on the shop floor.

MATERIAL RETURN. Materials are issued to a production order in full when it starts. Whatever the line did not use goes back: to the very lots it came from, at
the cost it left at, out of the order's work-in-progress. The order's cost, its genealogy and the lot balances all stay true: the order's real consumption is
`issued - returned`, and the output's unit cost falls accordingly. Returned stock goes back as available only if its lot is still released; if the lot has since
been held or rejected the returned quantity comes back blocked.

REWORK. A held or rejected batch can be re-processed instead of scrapped. Starting a rework takes the blocked quantity out of the source batch (ledgered), holds
its cost, and records the process; completing it receives the good output into a NEW batch of the same item (same manufacturing and expiry dates, because rework
does not extend shelf life) that waits in quarantine for a fresh QC inspection like any production output. Loss is absorbed into the unit cost, as in production.
The source batch keeps whatever was not reworked. Nothing is released or sold without the new batch passing QC.

Neither changes the ledger totals: value moves between item valuation, the order's WIP and the rework's held cost, which the inventory reconciliation counts."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.accounts import service as acct
from app.audit import service as audit
from app.core import activity
from app.core.clock import business_today
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.core.uow import run_tx
from app.inventory import repository as repo
from app.inventory import service as inv
from app.production.models import ProductionConsumption, ProductionOrder
from app.tier1_models import MaterialReturn, ReworkOrder

router = APIRouter(prefix="/production", tags=["rework"])
DB = Depends(get_db, scope="function")
Q3 = Decimal("0.001")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReturnLine(_In):
    item_id: int
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=3)


class ReturnIn(_In):
    lines: list[ReturnLine] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=3, max_length=300)


class ReworkIn(_In):
    batch_id: int
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    reason: str = Field(min_length=5, max_length=300)
    process: str = Field(min_length=5, max_length=500, description="what will be done to the stock (re-blend, re-filter, re-fill ...)")


class ReworkDoneIn(_In):
    output_qty: Decimal = Field(gt=0, max_digits=18, decimal_places=3)


# ------------------------------------------------------------------------------------------------------------------------------- material return
def returnable(db: Session, order_id: int) -> list[dict]:
    """Per material: issued, already returned, still returnable (never more than was actually consumed from a lot)."""
    rows = db.execute(text("""
        SELECT m.item_id, i.item_code, i.name, m.issued_qty,
               COALESCE((SELECT sum(r.qty) FROM material_returns r WHERE r.order_id = m.order_id AND r.item_id = m.item_id), 0) AS returned
          FROM production_materials m JOIN items i ON i.id = m.item_id WHERE m.order_id = :o ORDER BY m.line_no"""), {"o": order_id}).mappings().all()
    return [{"item_id": r["item_id"], "item_code": r["item_code"], "name": r["name"], "issued": str(r["issued_qty"]), "returned": str(r["returned"]),
             "returnable": str(max(Decimal(0), r["issued_qty"] - r["returned"]))} for r in rows]


def return_materials(db: Session, cu: CurrentUser, order_id: int, lines: list[dict], reason: str) -> dict:
    o = db.scalar(select(ProductionOrder).where(ProductionOrder.id == order_id).with_for_update())
    if not o:
        raise NotFound("Production order")
    if o.status != "IN_PROGRESS":
        raise BusinessConflict("invalid_status", f"Unused material is returned while the order is in progress (this one is {o.status})")
    if len({l["item_id"] for l in lines}) != len(lines):
        raise Unprocessable("A material may appear once per return")
    mats = {m.item_id for m in o.materials}
    for l in lines:
        if l["item_id"] not in mats:
            raise Unprocessable(f"Item {l['item_id']} is not a material of this order")
    from app.masters.models import Item
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_([l["item_id"] for l in lines])))}
    vrows = acct.lock_valuations(db, [l["item_id"] for l in lines])
    debit: dict[str, Decimal] = {}
    done: list[dict] = []
    total = Decimal(0)
    first_id = None
    for l in sorted(lines, key=lambda x: x["item_id"]):
        left = Decimal(str(l["qty"])).quantize(Q3)
        cons = db.scalars(select(ProductionConsumption).where(ProductionConsumption.order_id == o.id, ProductionConsumption.item_id == l["item_id"]).order_by(ProductionConsumption.id.desc())).all()
        back = {b: q for b, q in db.execute(text("SELECT batch_id, sum(qty) FROM material_returns WHERE order_id = :o AND item_id = :i GROUP BY batch_id"), {"o": o.id, "i": l["item_id"]})}
        for c in cons:
            avail = c.qty - back.get(c.batch_id, Decimal(0))
            take = min(avail, left)
            if take <= 0:
                continue
            cost = acct.money(c.cost * take / c.qty)
            mr = MaterialReturn(order_id=o.id, item_id=l["item_id"], batch_id=c.batch_id, qty=take, cost=cost, reason=reason, created_by=cu.id)
            db.add(mr)
            db.flush()
            first_id = first_id or mr.id
            status = db.execute(text("SELECT qc_status FROM inventory_batches WHERE id = :b"), {"b": c.batch_id}).scalar() if c.batch_id else "RELEASED"
            loc = db.execute(text("SELECT location_id FROM stock_balances WHERE batch_id IS NOT DISTINCT FROM :b AND item_id = :i AND warehouse_id = :w ORDER BY qty_physical DESC LIMIT 1"),
                             {"b": c.batch_id, "i": l["item_id"], "w": o.warehouse_id}).scalar()
            inv.receive(db, item_id=l["item_id"], warehouse_id=o.warehouse_id, location_id=loc, batch_id=c.batch_id, qty=take, source_type="material_return", source_id=mr.id,
                        source_line=0, actor_id=cu.id, quarantine=status != "RELEASED", movement="RETURN")
            acct.val_receive(db, vrows, l["item_id"], take, cost)
            code = acct.INVENTORY_ACCOUNT[items[l["item_id"]].kind]
            debit[code] = debit.get(code, Decimal(0)) + cost
            total += cost
            left -= take
            back[c.batch_id] = back.get(c.batch_id, Decimal(0)) + take
            done.append({"item_id": l["item_id"], "batch_id": c.batch_id, "qty": str(take), "cost": str(cost), "returned_blocked": status != "RELEASED"})
            if left <= 0:
                break
        if left > 0:
            raise BusinessConflict("return_exceeds_consumed", f"More was returned than was consumed for item {l['item_id']}", item_id=l["item_id"], over=str(left))
    o.wip_cost = o.wip_cost - total                       # input_cost stays the gross issued cost (it equals the consumption rows); the output is costed from what is left in WIP
    if total:
        acct.post_journal(db, entry_date=business_today(), description=f"Production {o.number} material return", source_type="production_order", source_id=o.id, source_event=f"return:{first_id}",
                          lines=[(c, v, Decimal(0)) for c, v in sorted(debit.items())] + [("1310", Decimal(0), total)], actor_id=cu.id)
    audit.record(db, user=cu, action="return_materials", entity="production_order", entity_id=o.id, after={"returned_cost": str(total), "lines": len(done)}, reason=reason)
    activity.emit(db, "production.material.returned", entity="production_order", entity_id=o.id, number=o.number, actor_id=cu.id, cost=total)
    db.flush()
    return {"order_id": o.id, "number": o.number, "returned": done, "returned_cost": str(total), "wip_cost": str(o.wip_cost), "returnable": returnable(db, o.id)}


# ------------------------------------------------------------------------------------------------------------------------------- rework
def _rework(db: Session, rid: int, lock: bool = False) -> ReworkOrder:
    q = select(ReworkOrder).where(ReworkOrder.id == rid)
    r = db.scalar(q.with_for_update() if lock else q)
    if not r:
        raise NotFound("Rework order")
    return r


def rework_dict(db: Session, r: ReworkOrder) -> dict:
    names = db.execute(text("SELECT b.batch_no, i.item_code, i.name FROM inventory_batches b JOIN items i ON i.id = b.item_id WHERE b.id = :b"), {"b": r.source_batch_id}).mappings().one()
    out_no = db.execute(text("SELECT batch_no FROM inventory_batches WHERE id = :b"), {"b": r.output_batch_id}).scalar() if r.output_batch_id else None
    return {"id": r.id, "number": r.number, "status": r.status, "source_batch_id": r.source_batch_id, "source_batch_no": names["batch_no"], "item_code": names["item_code"], "item_name": names["name"],
            "input_qty": str(r.input_qty), "input_cost": str(r.input_cost), "reason": r.reason, "process": r.process, "output_batch_id": r.output_batch_id, "output_batch_no": out_no,
            "output_qty": None if r.output_qty is None else str(r.output_qty), "loss_qty": None if r.loss_qty is None else str(r.loss_qty),
            "unit_cost": str((r.input_cost / r.output_qty).quantize(Decimal("0.0001"))) if r.output_qty else None, "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None}


def start_rework(db: Session, cu: CurrentUser, batch_id: int, qty, reason: str, process: str) -> dict:
    b = db.execute(text("SELECT id, item_id, qc_status FROM inventory_batches WHERE id = :i FOR NO KEY UPDATE"), {"i": batch_id}).mappings().first()
    if not b:
        raise NotFound("Batch")
    if b["qc_status"] not in ("HOLD", "REJECTED"):
        raise BusinessConflict("not_reworkable", f"Only a held or rejected batch can be reworked (this one is {b['qc_status']})")
    q = Decimal(str(qty)).quantize(Q3)
    ids = [r[0] for r in db.execute(text("SELECT id FROM stock_balances WHERE batch_id = :b AND qty_blocked > 0 ORDER BY id"), {"b": batch_id})]
    bals = repo.lock_balances(db, ids)
    bal = next((x for x in sorted(bals.values(), key=lambda x: x["id"]) if x["qty_blocked"] >= q), None)
    if not bal:
        have = max((x["qty_blocked"] for x in bals.values()), default=Decimal(0))
        raise BusinessConflict("not_enough_blocked", "The batch has less blocked stock in one place than the quantity to rework", available=str(have))
    vrows = acct.lock_valuations(db, [b["item_id"]])
    cost = acct.val_consume(vrows, b["item_id"], q)
    n = db.execute(text("SELECT nextval('rework_number_seq')")).scalar_one()
    r = ReworkOrder(number=f"RWK/{business_today():%y}/{n:05d}", source_batch_id=batch_id, item_id=b["item_id"], warehouse_id=bal["warehouse_id"], input_qty=q, input_cost=cost,
                    reason=reason, process=process, created_by=cu.id)
    db.add(r)
    db.flush()
    repo.add_ledger(db, balance=bal, qty_delta=-q, blocked_delta=-q, movement_type="PRODUCTION_ISSUE", source_type="rework_order", source_id=r.id, source_line=0, actor_id=cu.id, reason=f"Rework {r.number}: {reason}")
    repo.apply_delta(db, bal["id"], physical=-q, blocked=-q)
    audit.record(db, user=cu, action="start", entity="rework_order", entity_id=r.id, after={"number": r.number, "batch_id": batch_id, "qty": str(q), "cost": str(cost)}, reason=reason)
    activity.emit(db, "production.rework.started", entity="rework_order", entity_id=r.id, number=r.number, actor_id=cu.id)
    db.flush()
    return rework_dict(db, r)


def complete_rework(db: Session, cu: CurrentUser, rid: int, output_qty) -> dict:
    r = _rework(db, rid, lock=True)
    if r.status != "STARTED":
        raise BusinessConflict("invalid_status", f"Cannot complete a rework that is {r.status}")
    out = Decimal(str(output_qty)).quantize(Q3)
    if out > r.input_qty:
        raise BusinessConflict("output_exceeds_input", "Rework cannot produce more than went in; check the measurement", input_qty=str(r.input_qty))
    src = db.execute(text("SELECT mfg_date, expiry_date FROM inventory_batches WHERE id = :b"), {"b": r.source_batch_id}).mappings().one()
    batch_id = inv.create_batch(db, item_id=r.item_id, supplier_id=None, supplier_lot_no=None, mfg_date=src["mfg_date"], expiry_date=src["expiry_date"], source_type="rework_order", source_id=r.id)
    inv.receive(db, item_id=r.item_id, warehouse_id=r.warehouse_id, location_id=None, batch_id=batch_id, qty=out, source_type="rework_order", source_id=r.id, source_line=1, actor_id=cu.id,
                quarantine=True, movement="PRODUCTION_OUTPUT")
    vrows = acct.lock_valuations(db, [r.item_id])
    acct.val_receive(db, vrows, r.item_id, out, r.input_cost)                    # the whole input cost lands on the good output: loss raises the unit cost
    r.status, r.output_batch_id, r.output_qty, r.loss_qty, r.completed_at = "COMPLETED", batch_id, out, r.input_qty - out, datetime.now(timezone.utc)
    kind = db.execute(text("SELECT kind FROM items WHERE id = :i"), {"i": r.item_id}).scalar()
    from app.quality import service as quality
    insp = quality.auto_inspection(db, batch_id, r.item_id, "INCOMING" if kind in ("RM", "PM") else "FINISHED")
    audit.record(db, user=cu, action="complete", entity="rework_order", entity_id=r.id, after={"output_batch_id": batch_id, "output_qty": str(out), "loss_qty": str(r.loss_qty), "inspection_id": insp})
    activity.emit(db, "production.rework.completed", entity="rework_order", entity_id=r.id, number=r.number, actor_id=cu.id, output=out)
    db.flush()
    return rework_dict(db, r) | {"inspection_id": insp, "note": "The reworked stock is in quarantine until a quality inspection releases it." if insp else "No quality specification exists for this item; the new batch stays in quarantine until QC decides it."}


def cancel_rework(db: Session, cu: CurrentUser, rid: int, reason: str) -> dict:
    r = _rework(db, rid, lock=True)
    if r.status != "STARTED":
        raise BusinessConflict("invalid_status", f"Cannot cancel a rework that is {r.status}")
    ids = [x[0] for x in db.execute(text("SELECT id FROM stock_balances WHERE batch_id = :b AND warehouse_id = :w ORDER BY id"), {"b": r.source_batch_id, "w": r.warehouse_id})]
    bals = repo.lock_balances(db, ids)
    bal = sorted(bals.values(), key=lambda x: x["id"])[0] if bals else None
    if not bal:
        raise BusinessConflict("source_gone", "The source stock position no longer exists")
    repo.add_ledger(db, balance=bal, qty_delta=r.input_qty, blocked_delta=r.input_qty, movement_type="RETURN", source_type="rework_cancel", source_id=r.id, source_line=0, actor_id=cu.id, reason=reason)
    repo.apply_delta(db, bal["id"], physical=r.input_qty, blocked=r.input_qty)
    acct.val_receive(db, acct.lock_valuations(db, [r.item_id]), r.item_id, r.input_qty, r.input_cost)
    r.status, r.completed_at = "CANCELLED", datetime.now(timezone.utc)
    audit.record(db, user=cu, action="cancel", entity="rework_order", entity_id=r.id, reason=reason)
    activity.emit(db, "production.rework.cancelled", entity="rework_order", entity_id=r.id, number=r.number, actor_id=cu.id)
    db.flush()
    return rework_dict(db, r)


# ------------------------------------------------------------------------------------------------------------------------------- routes
@router.get("/orders/{oid}/returnable")
def returnable_route(oid: int, db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    if not db.get(ProductionOrder, oid):
        raise NotFound("Production order")
    return {"order_id": oid, "materials": returnable(db, oid)}


@router.post("/orders/{oid}/return-materials")
def return_route(oid: int, body: ReturnIn, db: Session = DB, cu: CurrentUser = Depends(require("production.order:issue"))):
    return run_tx(db, lambda d: return_materials(d, cu, oid, [l.model_dump() for l in body.lines], body.reason), name="production.material.return")


@router.post("/rework", status_code=201)
def start_route(body: ReworkIn, db: Session = DB, cu: CurrentUser = Depends(require("production.rework:create", "inventory.batch:read"))):
    return run_tx(db, lambda d: start_rework(d, cu, body.batch_id, body.qty, body.reason, body.process), name="production.rework.start")


@router.get("/rework")
def list_route(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB, _: CurrentUser = Depends(require("production.rework:read"))):
    q = select(ReworkOrder).order_by(ReworkOrder.id.desc())
    if status:
        q = q.where(ReworkOrder.status == status)
    return {"items": [rework_dict(db, r) for r in db.scalars(q.limit(page_size).offset((page - 1) * page_size))], "page": page}


@router.get("/rework/{rid}")
def get_route(rid: int, db: Session = DB, _: CurrentUser = Depends(require("production.rework:read"))):
    return rework_dict(db, _rework(db, rid))


@router.post("/rework/{rid}/complete")
def complete_route(rid: int, body: ReworkDoneIn, db: Session = DB, cu: CurrentUser = Depends(require("production.rework:complete"))):
    return run_tx(db, lambda d: complete_rework(d, cu, rid, body.output_qty), name="production.rework.complete")


class CancelIn(_In):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/rework/{rid}/cancel")
def cancel_route(rid: int, body: CancelIn, db: Session = DB, cu: CurrentUser = Depends(require("production.rework:complete"))):
    return run_tx(db, lambda d: cancel_rework(d, cu, rid, body.reason), name="production.rework.cancel")
