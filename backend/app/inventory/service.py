"""Inventory domain service. All functions run inside the caller's transaction (see core/uow.run_tx) and never commit.

Posting recipe (every function below): validate -> lock (global order, repository.py) -> re-validate on locked rows ->
write ledger row(s) + balance deltas together -> return. The caller adds audit + outbox and commits once.
"""
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import BusinessConflict, InsufficientStock, NotFound
from app.inventory import repository as repo
from app.core.clock import business_today

Q = Decimal("0.001")


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(Q)


def _positive(qty) -> Decimal:
    q = _q(qty)
    if q <= 0:
        raise BusinessConflict("invalid_quantity", "Quantity must be greater than zero")
    return q


def next_batch_no(db: Session) -> str:
    n = db.execute(text("SELECT nextval('batch_number_seq')")).scalar_one()
    return f"B{business_today():%y}{n:07d}"


def create_batch(db: Session, *, item_id: int, supplier_id: int | None, supplier_lot_no: str | None, mfg_date, expiry_date,
                 source_type: str, source_id: int) -> int:
    from app.inventory.models import InventoryBatch
    b = InventoryBatch(item_id=item_id, batch_no=next_batch_no(db), supplier_id=supplier_id, supplier_lot_no=supplier_lot_no,
                       mfg_date=mfg_date, expiry_date=expiry_date, source_type=source_type, source_id=source_id)
    db.add(b)
    db.flush()
    return b.id


def receive(db: Session, *, item_id: int, warehouse_id: int, location_id: int | None, batch_id: int | None, qty, source_type: str,
            source_id: int, source_line: int, actor_id: int, quarantine: bool, movement: str = "RECEIPT") -> dict:
    """Receive stock. Quarantined stock is physical but *blocked* until QC releases it."""
    qty = _positive(qty)
    bid = repo.ensure_balance(db, item_id=item_id, warehouse_id=warehouse_id, location_id=location_id, batch_id=batch_id)
    bal = repo.lock_balances(db, [bid])[bid]
    blocked = qty if quarantine else Decimal(0)
    repo.add_ledger(db, balance=bal, qty_delta=qty, blocked_delta=blocked, movement_type=movement, source_type=source_type,
                    source_id=source_id, source_line=source_line, actor_id=actor_id)
    repo.apply_delta(db, bid, physical=qty, blocked=blocked)
    return {"balance_id": bid, "qty": str(qty)}


def reserve(db: Session, *, item_id: int, warehouse_id: int, qty, reference_type: str, reference_id: str, actor_id: int,
            batch_id: int | None = None) -> list[dict]:
    """Reserve eligible stock (QC-released, unexpired) in FEFO order, possibly across several batches."""
    need = _positive(qty)
    cands = repo.fefo_candidates(db, item_id=item_id, warehouse_id=warehouse_id, batch_id=batch_id)
    total = sum((c["qty_available"] for c in cands), Decimal(0))
    if total < need:
        raise InsufficientStock(item_id, need, total)
    out = []
    for c in cands:
        if need <= 0:
            break
        take = min(c["qty_available"], need)
        rid = repo.add_reservation(db, balance_id=c["id"], item_id=item_id, qty=take, reference_type=reference_type,
                                   reference_id=reference_id, created_by=actor_id)
        repo.apply_delta(db, c["id"], reserved=take)
        out.append({"reservation_id": rid, "balance_id": c["id"], "batch_id": c["batch_id"], "qty": str(take)})
        need -= take
    return out


def release_reservations(db: Session, reservation_ids: list[int]) -> int:
    bids = repo.get_balance_ids_for_reservations(db, reservation_ids)
    if not bids:
        raise NotFound("Reservation")
    repo.lock_balances(db, bids)                      # lock order: balances first ...
    released = 0
    for r in repo.active_reservations(db, reservation_ids):   # ... then reservation rows
        if r["status"] != "ACTIVE":
            continue
        repo.apply_delta(db, r["balance_id"], reserved=-r["qty"])
        repo.close_reservation(db, r["id"], "RELEASED")
        released += 1
    return released


def issue(db: Session, *, item_id: int, warehouse_id: int, qty, source_type: str, source_id: int, source_line: int = 0, actor_id: int,
          reservation_ids: list[int] | None = None, batch_id: int | None = None, movement: str = "ISSUE", allow_split: bool = False) -> list[dict]:
    """Issue stock out. With reservations: consumes exactly those (they must cover `qty`). Without: FEFO over available stock.
    allow_split=True lets the last reservation be consumed only in part (partial dispatch): its remaining quantity stays reserved and ACTIVE."""
    need = _positive(qty)
    legs: list[tuple[dict, Decimal, int | None, Decimal | None]] = []   # (balance row, qty, reservation id, that reservation's qty)
    if reservation_ids:
        bids = repo.get_balance_ids_for_reservations(db, reservation_ids)
        bal = repo.lock_balances(db, bids)
        res = [r for r in repo.active_reservations(db, reservation_ids) if r["status"] == "ACTIVE" and r["item_id"] == item_id]
        covered = sum((r["qty"] for r in res), Decimal(0))
        if covered < need:
            raise InsufficientStock(item_id, need, covered)
        left = need
        for r in res:
            take = min(r["qty"], left)
            if take < r["qty"] and not allow_split:
                raise BusinessConflict("partial_reservation_issue", "Issue quantity must consume whole reservations; release or split first")
            legs.append((bal[r["balance_id"]], take, r["id"], r["qty"]))
            left -= take
            if left <= 0:
                break
    else:
        cands = repo.fefo_candidates(db, item_id=item_id, warehouse_id=warehouse_id, batch_id=batch_id)
        total = sum((c["qty_available"] for c in cands), Decimal(0))
        if total < need:
            raise InsufficientStock(item_id, need, total)
        left = need
        bal = repo.lock_balances(db, [c["id"] for c in cands])
        for c in cands:
            if left <= 0:
                break
            take = min(c["qty_available"], left)
            legs.append((bal[c["id"]], take, None, None))
            left -= take
    out = []
    for leg, (b, take, rid, res_qty) in enumerate(legs):
        repo.add_ledger(db, balance=b, qty_delta=-take, movement_type=movement, source_type=source_type, source_id=source_id,
                        source_line=source_line, leg=leg, actor_id=actor_id)
        repo.apply_delta(db, b["id"], physical=-take, reserved=-take if rid else Decimal(0))
        if rid and take < res_qty:
            repo.shrink_reservation(db, rid, take)                       # partial: the rest stays reserved for the next shipment
        elif rid:
            repo.close_reservation(db, rid, "CONSUMED")
        out.append({"balance_id": b["id"], "batch_id": b["batch_id"], "qty": str(take)})
    return out


def transfer(db: Session, *, item_id: int, batch_id: int | None, from_warehouse_id: int, to_warehouse_id: int, qty, source_type: str,
             source_id: int, actor_id: int, from_location_id: int | None = None, to_location_id: int | None = None) -> dict:
    """Move *available* stock between warehouses. Both balances are locked in global (id) order to avoid deadlocks."""
    need = _positive(qty)
    if from_warehouse_id == to_warehouse_id and from_location_id == to_location_id:
        raise BusinessConflict("same_location", "Source and destination are the same")
    src = repo.ensure_balance(db, item_id=item_id, warehouse_id=from_warehouse_id, location_id=from_location_id, batch_id=batch_id)
    dst = repo.ensure_balance(db, item_id=item_id, warehouse_id=to_warehouse_id, location_id=to_location_id, batch_id=batch_id)
    bal = repo.lock_balances(db, [src, dst])
    s, d = bal[src], bal[dst]
    if s["qty_available"] < need:
        raise InsufficientStock(item_id, need, s["qty_available"])
    repo.add_ledger(db, balance=s, qty_delta=-need, movement_type="TRANSFER_OUT", source_type=source_type, source_id=source_id, actor_id=actor_id)
    repo.add_ledger(db, balance=d, qty_delta=need, movement_type="TRANSFER_IN", source_type=source_type, source_id=source_id, actor_id=actor_id)
    repo.apply_delta(db, src, physical=-need)
    repo.apply_delta(db, dst, physical=need)
    return {"from_balance_id": src, "to_balance_id": dst, "qty": str(need)}


def adjust(db: Session, *, balance_id: int, delta, reason: str, source_id: int, actor_id: int) -> dict:
    d = _q(delta)
    if d == 0:
        raise BusinessConflict("invalid_quantity", "Adjustment must be non-zero")
    if not reason or len(reason.strip()) < 5:
        raise BusinessConflict("reason_required", "A reason of at least 5 characters is required for stock adjustments")
    bal = repo.lock_balances(db, [balance_id]).get(balance_id)
    if not bal:
        raise NotFound("Stock balance")
    repo.add_ledger(db, balance=bal, qty_delta=d, movement_type="ADJUSTMENT", source_type="adjustment", source_id=source_id,
                    actor_id=actor_id, reason=reason)
    repo.apply_delta(db, balance_id, physical=d)
    return {"balance_id": balance_id, "delta": str(d)}


def reverse(db: Session, *, ledger_id: int, reason: str, actor_id: int) -> dict:
    row = db.execute(text("SELECT * FROM stock_ledger WHERE id = :i"), {"i": ledger_id}).mappings().first()
    if not row:
        raise NotFound("Ledger entry")
    if row["movement_type"] == "REVERSAL":
        raise BusinessConflict("cannot_reverse_reversal", "A reversal cannot itself be reversed; post a new document")
    if not reason or len(reason.strip()) < 5:
        raise BusinessConflict("reason_required", "A reason is required to reverse a stock movement")
    bal = repo.lock_balances(db, [row["balance_id"]])[row["balance_id"]]
    repo.add_ledger(db, balance=bal, qty_delta=-row["qty_delta"], blocked_delta=-row["blocked_delta"], movement_type="REVERSAL",
                    source_type="reversal", source_id=ledger_id, actor_id=actor_id, reason=reason, reverses_ledger_id=ledger_id)
    repo.apply_delta(db, bal["id"], physical=-row["qty_delta"], blocked=-row["blocked_delta"])
    return {"reversed_ledger_id": ledger_id}


QC_TRANSITIONS = {("QUARANTINE", "RELEASED"), ("QUARANTINE", "HOLD"), ("QUARANTINE", "REJECTED"), ("HOLD", "RELEASED"),
                  ("HOLD", "REJECTED"), ("RELEASED", "HOLD"), ("RELEASED", "REJECTED")}


def set_qc_status(db: Session, *, batch_id: int, decision: str, reason: str | None, actor_id: int) -> dict:
    """Release / hold / reject a batch. Keeps `blocked` quantity in step with the status, with ledger rows for each change."""
    # FOR NO KEY UPDATE, not FOR UPDATE: inserting a ledger row with a batch_id FK takes FOR KEY SHARE on the batch row
    # *after* balances are locked by other postings. FOR UPDATE conflicts with KEY SHARE (batch->balance vs balance->batch
    # cycle = deadlock, found by tests/test_concurrency.py); NO KEY UPDATE does not, and is all we need (qc_status is not a key).
    batch = db.execute(text("SELECT id, qc_status, item_id FROM inventory_batches WHERE id = :i FOR NO KEY UPDATE"),
                       {"i": batch_id}).mappings().first()
    if not batch:
        raise NotFound("Batch")
    cur = batch["qc_status"]
    if (cur, decision) not in QC_TRANSITIONS:
        raise BusinessConflict("invalid_qc_transition", f"Cannot move a batch from {cur} to {decision}")
    ids = [r[0] for r in db.execute(text("SELECT id FROM stock_balances WHERE batch_id = :b"), {"b": batch_id})]
    bals = repo.lock_balances(db, ids)
    rev = (db.execute(text("SELECT COALESCE(max(source_line),0) FROM stock_ledger WHERE source_type='batch_qc' AND source_id=:b"),
                      {"b": batch_id}).scalar() or 0) + 1
    changed = 0
    for leg, b in enumerate(sorted(bals.values(), key=lambda x: x["id"])):
        if decision == "RELEASED":
            delta, mv = -b["qty_blocked"], "QC_RELEASE"
        else:  # HOLD / REJECTED: block whatever is still available (reserved stock stays reserved but flagged by status)
            delta, mv = b["qty_available"], "QC_BLOCK"
        if delta != 0:
            repo.add_ledger(db, balance=b, blocked_delta=delta, movement_type=mv, source_type="batch_qc", source_id=batch_id,
                            source_line=rev, leg=leg, actor_id=actor_id, reason=reason)
            repo.apply_delta(db, b["id"], blocked=delta)
            changed += 1
    db.execute(text("UPDATE inventory_batches SET qc_status=:s, qc_decided_at=now(), qc_decided_by=:u WHERE id=:i"),
               {"s": decision, "u": actor_id, "i": batch_id})
    return {"batch_id": batch_id, "from": cur, "to": decision, "balances_changed": changed}


def unblock(db: Session, *, balance_id: int, qty, source_type: str, source_id: int, source_line: int, actor_id: int, reason: str | None = None) -> dict:
    """Release previously blocked quantity (e.g. a restocked customer return) back to available stock, via the ledger."""
    q = _positive(qty)
    bal = repo.lock_balances(db, [balance_id]).get(balance_id)
    if not bal:
        raise NotFound("Stock balance")
    if bal["qty_blocked"] < q:
        raise BusinessConflict("not_enough_blocked", "Less stock is blocked than the quantity to release")
    repo.add_ledger(db, balance=bal, blocked_delta=-q, movement_type="QC_RELEASE", source_type=source_type, source_id=source_id, source_line=source_line,
                    leg=1, actor_id=actor_id, reason=reason)
    repo.apply_delta(db, balance_id, blocked=-q)
    return {"balance_id": balance_id, "released": str(q)}
