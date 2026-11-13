from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import activity
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound
from app.core.idempotency import require_idempotency_key
from app.core.posting import idempotent_post
from app.core.uow import run_tx
from app.inventory import schemas as s
from app.inventory import service as svc

router = APIRouter(prefix="/inventory", tags=["inventory"])
DB = Depends(get_db, scope="function")


def _rows(db, sql, params):
    return [dict(r) for r in db.execute(text(sql), params).mappings()]


# ------------------------------------------------------------------ reads (primary DB; correctness decisions never use caches)
@router.get("/balances")
def balances(item_id: int | None = None, warehouse_id: int | None = None, batch_id: int | None = None, include_zero: bool = False,
             page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = DB,
             _: CurrentUser = Depends(require("inventory.stock:read"))):
    where = "WHERE (:z OR sb.qty_physical <> 0) AND (CAST(:i AS int) IS NULL OR sb.item_id = :i) AND (CAST(:w AS int) IS NULL OR sb.warehouse_id = :w) AND (CAST(:b AS int) IS NULL OR sb.batch_id = :b)"
    p = {"z": include_zero, "i": item_id, "w": warehouse_id, "b": batch_id, "n": page_size, "o": (page - 1) * page_size}
    total = db.execute(text(f"SELECT count(*) FROM stock_balances sb {where}"), p).scalar()
    items = _rows(db, f"""SELECT sb.id, sb.item_id, i.item_code, i.name AS item_name, sb.warehouse_id, w.code AS warehouse_code,
                                 sb.location_id, sb.batch_id, ib.batch_no, ib.qc_status, ib.expiry_date,
                                 sb.qty_physical, sb.qty_reserved, sb.qty_blocked, sb.qty_available
                            FROM stock_balances sb JOIN items i ON i.id = sb.item_id JOIN warehouses w ON w.id = sb.warehouse_id
                            LEFT JOIN inventory_batches ib ON ib.id = sb.batch_id {where}
                           ORDER BY sb.id LIMIT :n OFFSET :o""", p)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/availability")
def availability(item_id: int, db: Session = DB, _: CurrentUser = Depends(require("inventory.stock:read"))):
    rows = _rows(db, """SELECT warehouse_id, sum(qty_physical) physical, sum(qty_reserved) reserved, sum(qty_blocked) blocked,
                               sum(qty_available) available FROM stock_balances WHERE item_id = :i GROUP BY warehouse_id ORDER BY warehouse_id""",
                 {"i": item_id})
    tot = {k: sum(r[k] for r in rows) for k in ("physical", "reserved", "blocked", "available")} if rows else \
        {"physical": 0, "reserved": 0, "blocked": 0, "available": 0}
    return {"item_id": item_id, "by_warehouse": rows, "total": tot}


@router.get("/ledger")
def ledger(item_id: int | None = None, batch_id: int | None = None, balance_id: int | None = None,
           before_id: int | None = Query(None, description="cursor: return entries with id < before_id"), limit: int = Query(50, ge=1, le=200),
           db: Session = DB, _: CurrentUser = Depends(require("inventory.stock:read"))):
    """Cursor pagination (id DESC): stable and O(limit) however large the ledger grows."""
    rows = _rows(db, """SELECT id, posted_at, balance_id, item_id, warehouse_id, batch_id, qty_delta, blocked_delta, movement_type,
                               source_type, source_id, source_line, reverses_ledger_id, reason, actor_id, request_id
                          FROM stock_ledger
                         WHERE (CAST(:i AS int) IS NULL OR item_id = :i) AND (CAST(:b AS bigint) IS NULL OR batch_id = :b)
                           AND (CAST(:bal AS bigint) IS NULL OR balance_id = :bal) AND (CAST(:c AS bigint) IS NULL OR id < :c)
                         ORDER BY id DESC LIMIT :n""", {"i": item_id, "b": batch_id, "bal": balance_id, "c": before_id, "n": limit + 1})
    nxt = rows[limit - 1]["id"] if len(rows) > limit else None
    return {"items": rows[:limit], "next_before_id": nxt}


@router.get("/batches")
def batches(item_id: int | None = None, qc_status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
            db: Session = DB, _: CurrentUser = Depends(require("inventory.batch:read"))):
    p = {"i": item_id, "q": qc_status, "n": page_size, "o": (page - 1) * page_size}
    w = "WHERE (CAST(:i AS int) IS NULL OR ib.item_id = :i) AND (CAST(:q AS text) IS NULL OR ib.qc_status = :q)"
    total = db.execute(text(f"SELECT count(*) FROM inventory_batches ib {w}"), p).scalar()
    items = _rows(db, f"""SELECT ib.id, ib.batch_no, ib.item_id, i.item_code, ib.supplier_id, ib.supplier_lot_no, ib.mfg_date, ib.expiry_date,
                                 ib.qc_status, ib.source_type, ib.source_id, ib.created_at
                            FROM inventory_batches ib JOIN items i ON i.id = ib.item_id {w} ORDER BY ib.id DESC LIMIT :n OFFSET :o""", p)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/batches/{batch_id}")
def batch_trace(batch_id: int, db: Session = DB, _: CurrentUser = Depends(require("inventory.batch:read"))):
    """Traceability: batch -> source goods receipt/PO/supplier lot -> current balances -> ledger history."""
    b = _rows(db, """SELECT ib.*, i.item_code, s.code AS supplier_code, s.name AS supplier_name FROM inventory_batches ib
                       JOIN items i ON i.id = ib.item_id LEFT JOIN suppliers s ON s.id = ib.supplier_id WHERE ib.id = :b""", {"b": batch_id})
    if not b:
        raise NotFound("Batch")
    src = _rows(db, """SELECT gr.id AS goods_receipt_id, gr.number AS goods_receipt_number, gr.delivery_note_no, po.id AS po_id,
                              po.number AS po_number FROM goods_receipt_items gri JOIN goods_receipts gr ON gr.id = gri.gr_id
                         JOIN purchase_orders po ON po.id = gr.po_id WHERE gri.batch_id = :b""", {"b": batch_id})
    return {"batch": b[0], "source": src[0] if src else None,
            "balances": _rows(db, "SELECT id, warehouse_id, location_id, qty_physical, qty_reserved, qty_blocked, qty_available FROM stock_balances WHERE batch_id=:b ORDER BY id", {"b": batch_id}),
            "ledger": _rows(db, "SELECT id, posted_at, movement_type, qty_delta, blocked_delta, source_type, source_id, actor_id, request_id FROM stock_ledger WHERE batch_id=:b ORDER BY id", {"b": batch_id})}


# ------------------------------------------------------------------ postings (idempotent, atomic)
@router.post("/batches/{batch_id}/qc-decision", status_code=200)
def qc_decision(batch_id: int, body: s.QCDecision, key: str = Depends(require_idempotency_key), db: Session = DB,
                cu: CurrentUser = Depends(require("inventory.batch:qc_release"))):
    def effect(d):
        from app.audit import service as audit
        from app.core import outbox
        from app.core.errors import BusinessConflict
        if d.execute(text("SELECT 1 FROM inspections WHERE batch_id = :b LIMIT 1"), {"b": batch_id}).first():
            raise BusinessConflict("inspection_required", "This batch is governed by a quality inspection; decide it through /quality/inspections")
        r = svc.set_qc_status(d, batch_id=batch_id, decision=body.decision, reason=body.reason, actor_id=cu.id)
        audit.record(d, user=cu, action=f"qc_{body.decision.lower()}", entity="inventory_batch", entity_id=batch_id,
                     before={"qc_status": r["from"]}, after={"qc_status": r["to"]}, reason=body.reason)
        outbox.add(d, event_type="inventory.batch.qc_decided", aggregate_type="inventory_batch", aggregate_id=batch_id, payload={**r, "number": f"batch {batch_id}", "actor_id": cu.id})
        return 200, r, "inventory_batch", batch_id
    return idempotent_post(db, cu, scope="inventory.batch.qc", key=key, payload={"batch_id": batch_id, **body.model_dump()}, effect=effect)


@router.post("/reservations", status_code=201)
def reserve(body: s.ReserveIn, key: str = Depends(require_idempotency_key), db: Session = DB,
            cu: CurrentUser = Depends(require("inventory.reservation:create"))):
    def effect(d):
        res = svc.reserve(d, item_id=body.item_id, warehouse_id=body.warehouse_id, qty=body.qty, reference_type=body.reference_type,
                          reference_id=body.reference_id, actor_id=cu.id, batch_id=body.batch_id)
        return 201, {"reservations": res}, "reservation", res[0]["reservation_id"]
    return idempotent_post(db, cu, scope="inventory.reservation.create", key=key, payload=body.model_dump(mode="json"), effect=effect)


@router.post("/reservations/release")
def release(body: s.ReleaseIn, key: str = Depends(require_idempotency_key), db: Session = DB,
            cu: CurrentUser = Depends(require("inventory.reservation:release"))):
    return idempotent_post(db, cu, scope="inventory.reservation.release", key=key, payload=body.model_dump(mode="json"),
                           effect=lambda d: (200, {"released": svc.release_reservations(d, body.reservation_ids)}, None, None))


@router.post("/issues", status_code=201)
def issue(body: s.IssueIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("inventory.issue:post"))):
    def effect(d):
        legs = svc.issue(d, item_id=body.item_id, warehouse_id=body.warehouse_id, qty=body.qty, source_type=body.source_type,
                         source_id=body.source_id, source_line=body.source_line, actor_id=cu.id, reservation_ids=body.reservation_ids,
                         batch_id=body.batch_id)
        return 201, {"issued": legs}, "stock_issue", f"{body.source_type}:{body.source_id}"
    return idempotent_post(db, cu, scope="inventory.issue.post", key=key, payload=body.model_dump(mode="json"), effect=effect)


@router.post("/transfers", status_code=201)
def transfer(body: s.TransferIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("inventory.transfer:post"))):
    def effect(d):
        r = svc.transfer(d, item_id=body.item_id, batch_id=body.batch_id, from_warehouse_id=body.from_warehouse_id, to_warehouse_id=body.to_warehouse_id, qty=body.qty,
                         source_type="transfer", source_id=body.source_id, actor_id=cu.id, from_location_id=body.from_location_id, to_location_id=body.to_location_id)
        activity.emit(d, "inventory.stock.transferred", entity="stock_transfer", entity_id=body.source_id, number=f"TR-{body.source_id}", actor_id=cu.id)
        return 201, r, "transfer", body.source_id
    return idempotent_post(db, cu, scope="inventory.transfer.post", key=key, payload=body.model_dump(mode="json"), effect=effect)


@router.post("/adjustments", status_code=201)
def adjust(body: s.AdjustIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("inventory.adjustment:post"))):
    def effect(d):
        from app.audit import service as audit
        from app.inventory.adjustments import post_adjustment
        r = post_adjustment(d, balance_id=body.balance_id, delta=body.delta, reason=body.reason, source_id=body.source_id, actor_id=cu.id)
        audit.record(d, user=cu, action="stock_adjustment", entity="stock_balance", entity_id=body.balance_id, after=r, reason=body.reason)
        return 201, r, "adjustment", body.source_id
    return idempotent_post(db, cu, scope="inventory.adjustment.post", key=key, payload=body.model_dump(mode="json"), effect=effect)


@router.post("/ledger/{ledger_id}/reverse", status_code=201)
def reverse(ledger_id: int, body: s.ReverseIn, key: str = Depends(require_idempotency_key), db: Session = DB,
            cu: CurrentUser = Depends(require("inventory.reversal:post"))):
    def effect(d):
        from app.audit import service as audit
        from app.inventory.adjustments import reverse_adjustment
        r = reverse_adjustment(d, cu, ledger_id=ledger_id, reason=body.reason)
        audit.record(d, user=cu, action="stock_reversal", entity="stock_ledger", entity_id=ledger_id, after=r, reason=body.reason)
        return 201, r, "reversal", ledger_id
    return idempotent_post(db, cu, scope="inventory.reversal.post", key=key, payload={"ledger_id": ledger_id, **body.model_dump()}, effect=effect)
