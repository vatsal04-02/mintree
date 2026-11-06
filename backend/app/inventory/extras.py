"""Inventory reports + stocktake (cycle count). Reports are read-only and computed from balances / ledger / valuation."""
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.approvals.service import get_setting
from app.audit import service as audit
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.inventory.adjustments import post_adjustment
from app.inventory.stocktake_models import Stocktake, StocktakeLine
from app.core.clock import business_today, business_date


def reorder_report(db: Session) -> list[dict]:
    rows = db.execute(text("""
        SELECT i.id, i.item_code, i.name, i.kind, i.reorder_level, COALESCE(a.avail, 0) AS available, COALESCE(po.on_order, 0) AS on_order
          FROM items i
          LEFT JOIN (SELECT sb.item_id, sum(sb.qty_available) FILTER (WHERE sb.batch_id IS NULL OR (b.qc_status = 'RELEASED' AND (b.expiry_date IS NULL OR b.expiry_date > business_today()))) avail
                       FROM stock_balances sb LEFT JOIN inventory_batches b ON b.id = sb.batch_id GROUP BY sb.item_id) a ON a.item_id = i.id
          LEFT JOIN (SELECT poi.item_id, sum(poi.qty_ordered - poi.qty_received) on_order FROM purchase_order_items poi JOIN purchase_orders p ON p.id = poi.po_id
                      WHERE p.status IN ('APPROVED','PARTIALLY_RECEIVED') GROUP BY poi.item_id) po ON po.item_id = i.id
         WHERE i.is_active AND i.reorder_level IS NOT NULL AND i.reorder_level > 0 AND COALESCE(a.avail, 0) < i.reorder_level ORDER BY (i.reorder_level - COALESCE(a.avail,0)) DESC, i.item_code""")).mappings().all()
    return [{"item_id": r["id"], "item_code": r["item_code"], "name": r["name"], "kind": r["kind"], "reorder_level": str(r["reorder_level"]), "available": str(r["available"]),
             "on_order": str(r["on_order"]), "shortfall": str(r["reorder_level"] - r["available"]), "covered_by_open_pos": r["available"] + r["on_order"] >= r["reorder_level"]} for r in rows]


def expiring(db: Session, days: int | None) -> dict:
    days = days if days is not None else int(get_setting(db, "quality.near_expiry_days") or 90)
    rows = db.execute(text("""SELECT b.id, b.batch_no, b.expiry_date, b.qc_status, i.item_code, i.name, sum(sb.qty_physical) AS qty, b.expiry_date - business_today() AS days_left
                                FROM inventory_batches b JOIN items i ON i.id = b.item_id JOIN stock_balances sb ON sb.batch_id = b.id
                               WHERE b.expiry_date IS NOT NULL AND b.expiry_date <= business_today() + :d GROUP BY b.id, i.id HAVING sum(sb.qty_physical) > 0 ORDER BY b.expiry_date"""), {"d": days}).mappings().all()
    return {"within_days": days, "expired": [r_ for r_ in map(_exp, rows) if r_["days_left"] < 0], "expiring": [r_ for r_ in map(_exp, rows) if r_["days_left"] >= 0]}


def _exp(r) -> dict:
    return {"batch_id": r["id"], "batch_no": r["batch_no"], "item_code": r["item_code"], "name": r["name"], "expiry_date": r["expiry_date"], "days_left": r["days_left"], "qty": str(r["qty"]), "qc_status": r["qc_status"]}


def valuation(db: Session) -> dict:
    rows = db.execute(text("""SELECT i.kind, count(*) AS items, sum(v.qty) AS qty, sum(v.value) AS value FROM item_valuation v JOIN items i ON i.id = v.item_id WHERE v.qty > 0 GROUP BY i.kind ORDER BY i.kind""")).mappings().all()
    top = db.execute(text("""SELECT i.item_code, i.name, v.qty, v.value, round(v.value / v.qty, 4) AS avg_cost FROM item_valuation v JOIN items i ON i.id = v.item_id WHERE v.qty > 0 ORDER BY v.value DESC LIMIT 20""")).mappings().all()
    return {"by_kind": [{"kind": r["kind"], "items": r["items"], "qty": str(r["qty"]), "value": str(r["value"])} for r in rows], "total_value": str(sum((Decimal(r["value"]) for r in rows), Decimal(0))),
            "top_items_by_value": [{**dict(t), "qty": str(t["qty"]), "value": str(t["value"]), "avg_cost": str(t["avg_cost"])} for t in top], "basis": "moving-average cost (ties to GL inventory accounts)"}


def slow_moving(db: Session, days: int) -> list[dict]:
    rows = db.execute(text("""
        SELECT i.id, i.item_code, i.name, i.kind, sum(sb.qty_physical) AS qty, v.value, max(l.posted_at) FILTER (WHERE l.qty_delta < 0 AND l.movement_type IN ('ISSUE','DISPATCH','PRODUCTION_ISSUE')) AS last_outflow
          FROM items i JOIN stock_balances sb ON sb.item_id = i.id LEFT JOIN item_valuation v ON v.item_id = i.id LEFT JOIN stock_ledger l ON l.item_id = i.id
         GROUP BY i.id, v.value HAVING sum(sb.qty_physical) > 0
            AND COALESCE(max(l.posted_at) FILTER (WHERE l.qty_delta < 0 AND l.movement_type IN ('ISSUE','DISPATCH','PRODUCTION_ISSUE')), '-infinity') < now() - make_interval(days => :d)
         ORDER BY v.value DESC NULLS LAST, i.item_code LIMIT 200"""), {"d": days}).mappings().all()
    return [{"item_id": r["id"], "item_code": r["item_code"], "name": r["name"], "kind": r["kind"], "qty": str(r["qty"]), "value": None if r["value"] is None else str(r["value"]),
             "last_outflow": r["last_outflow"], "idle_days": None if r["last_outflow"] is None else (business_today() - business_date(r["last_outflow"])).days} for r in rows]


# ------------------------------------------------------------------ stocktake
def start_stocktake(db: Session, cu: CurrentUser, warehouse_id: int, note: str | None) -> dict:
    n = db.execute(text("SELECT nextval('stocktake_seq')")).scalar_one()
    st = Stocktake(number=f"ST/{business_today():%y}/{n:05d}", warehouse_id=warehouse_id, note=note, created_by=cu.id)
    db.add(st)
    db.flush()
    rows = db.execute(text("SELECT id, qty_physical FROM stock_balances WHERE warehouse_id = :w AND qty_physical > 0 ORDER BY id"), {"w": warehouse_id}).all()
    if not rows:
        raise BusinessConflict("nothing_to_count", "This warehouse holds no stock")
    db.add_all(StocktakeLine(stocktake_id=st.id, balance_id=r[0], system_qty=r[1]) for r in rows)
    db.flush()
    audit.record(db, user=cu, action="start", entity="stocktake", entity_id=st.id, after={"lines": len(rows)})
    return stocktake_dict(db, st)


def _st(db: Session, sid: int, lock: bool = False) -> Stocktake:
    q = select(Stocktake).where(Stocktake.id == sid)
    s = db.scalar(q.with_for_update() if lock else q)
    if not s:
        raise NotFound("Stocktake")
    return s


def record_counts(db: Session, cu: CurrentUser, sid: int, counts: list[dict]) -> dict:
    st = _st(db, sid, lock=True)
    if st.status != "COUNTING":
        raise BusinessConflict("invalid_status", f"Stocktake is {st.status}")
    lines = {l.balance_id: l for l in db.scalars(select(StocktakeLine).where(StocktakeLine.stocktake_id == sid))}
    for c in counts:
        l = lines.get(c["balance_id"])
        if not l:
            raise Unprocessable(f"Balance {c['balance_id']} is not part of this stocktake")
        l.counted_qty, l.counted_by, l.note = c["counted_qty"], cu.id, c.get("note")
    audit.record(db, user=cu, action="count", entity="stocktake", entity_id=st.id, after={"lines": len(counts)})
    return stocktake_dict(db, st)


def approve_stocktake(db: Session, cu: CurrentUser, sid: int) -> dict:
    """Posts one valued adjustment per variance, in balance-id order (consistent lock order). The approver must not be the person who started the count.
    Counts are taken as of approval: movements since the snapshot are included in the adjustment base (current physical), and the report shows `moved_since_snapshot`."""
    st = _st(db, sid, lock=True)
    if st.status != "COUNTING":
        raise BusinessConflict("invalid_status", f"Stocktake is {st.status}")
    if st.created_by == cu.id:
        raise BusinessConflict("segregation_of_duties", "The stocktake must be approved by someone other than the person who started it")
    lines = list(db.scalars(select(StocktakeLine).where(StocktakeLine.stocktake_id == sid).order_by(StocktakeLine.balance_id)))
    if any(l.counted_qty is None for l in lines):
        raise BusinessConflict("count_incomplete", "Every line must be counted before approval", uncounted=sum(1 for l in lines if l.counted_qty is None))
    posted = 0
    for l in lines:
        cur = db.execute(text("SELECT qty_physical FROM stock_balances WHERE id = :b"), {"b": l.balance_id}).scalar()
        delta = l.counted_qty - cur
        if delta != 0:
            reason = f"Stocktake {st.number}: counted {l.counted_qty}, system {cur}"
            r = post_adjustment(db, balance_id=l.balance_id, delta=delta, reason=reason, source_id=l.id, actor_id=cu.id)
            audit.record(db, user=cu, action="stock_adjustment", entity="stock_balance", entity_id=l.balance_id, after=r, reason=reason)       # same trail as a manual adjustment
            posted += 1
    st.status, st.approved_by = "APPROVED", cu.id
    audit.record(db, user=cu, action="approve", entity="stocktake", entity_id=st.id, after={"adjustments": posted})
    return stocktake_dict(db, st) | {"adjustments_posted": posted}


def cancel_stocktake(db: Session, cu: CurrentUser, sid: int) -> dict:
    st = _st(db, sid, lock=True)
    if st.status != "COUNTING":
        raise BusinessConflict("invalid_status", f"Stocktake is {st.status}")
    st.status = "CANCELLED"
    audit.record(db, user=cu, action="cancel", entity="stocktake", entity_id=st.id)
    return stocktake_dict(db, st)


def stocktake_dict(db: Session, st: Stocktake) -> dict:
    rows = db.execute(text("""SELECT l.balance_id, l.system_qty, l.counted_qty, l.note, sb.qty_physical AS current_qty, i.item_code, b.batch_no
                                FROM stocktake_lines l JOIN stock_balances sb ON sb.id = l.balance_id JOIN items i ON i.id = sb.item_id LEFT JOIN inventory_batches b ON b.id = sb.batch_id
                               WHERE l.stocktake_id = :s ORDER BY l.balance_id"""), {"s": st.id}).mappings().all()
    return {"id": st.id, "number": st.number, "status": st.status, "warehouse_id": st.warehouse_id, "created_by": st.created_by, "approved_by": st.approved_by,
            "lines": [{"balance_id": r["balance_id"], "item_code": r["item_code"], "batch_no": r["batch_no"], "system_qty": str(r["system_qty"]),
                       "counted_qty": None if r["counted_qty"] is None else str(r["counted_qty"]),
                       "variance": None if r["counted_qty"] is None else str(r["counted_qty"] - r["system_qty"]), "moved_since_snapshot": r["current_qty"] != r["system_qty"]} for r in rows]}
