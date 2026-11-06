"""Data access for inventory. SQL only -- no business rules (those live in service.py).

GLOBAL LOCK ORDER (every code path must follow it, otherwise deadlocks):
    1. business documents (PO items, batches)  in primary-key order
    2. stock_balances rows                      in the order given by `FEFO_ORDER` (expiry, batch id, balance id),
                                                which is a stable total order shared by all writers
    3. stock_reservations / ledger inserts      (leaf rows; nobody locks balances while holding them)

NOTE foreign keys take locks too: inserting a ledger row / reservation takes FOR KEY SHARE on the referenced balance,
batch and item rows. Anything that locks a *parent* row must therefore use FOR NO KEY UPDATE unless it really changes
key columns, otherwise it conflicts with every child insert and creates parent<->child lock cycles.
"""
from decimal import Decimal

from psycopg import errors as pgerr
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import observability as obs
from app.core.context import request_id_var
from app.core.errors import BusinessConflict, InsufficientStock
from app.inventory.models import StockBalance, StockLedger, StockReservation
from app.core.clock import business_today

FEFO_ORDER = "ib.expiry_date NULLS LAST, sb.batch_id NULLS LAST, sb.id"


def ensure_balance(db: Session, *, item_id, warehouse_id, location_id, batch_id) -> int:
    """Get-or-create the balance row id WITHOUT taking a row lock on an existing row.

    (`INSERT .. ON CONFLICT DO UPDATE` locks the row as a side effect, in call order -- which silently breaks the
    global lock order: two opposite-direction transfers would lock A->B and B->A and deadlock. Callers lock via
    `lock_balances`, which orders rows globally. Found by tests/test_concurrency.py.)
    """
    find = text("""SELECT id FROM stock_balances WHERE item_id = :i AND warehouse_id = :w
                      AND location_id IS NOT DISTINCT FROM :l AND batch_id IS NOT DISTINCT FROM :b""")
    params = {"i": item_id, "w": warehouse_id, "l": location_id, "b": batch_id}
    bid = db.execute(find, params).scalar()
    if bid is None:
        bid = db.execute(pg_insert(StockBalance).values(item_id=item_id, warehouse_id=warehouse_id, location_id=location_id,
                                                        batch_id=batch_id)
                         .on_conflict_do_nothing(constraint="uq_stock_balances_item_id").returning(StockBalance.id)).scalar()
        if bid is None:  # a concurrent creator won; it committed (or we would still be waiting), so we can read it now
            bid = db.execute(find, params).scalar_one()
    return bid


def lock_balances(db: Session, ids: list[int]) -> dict[int, dict]:
    """Lock the given balances. FEFO order == id order for a single balance; for several, callers pass them through
    `fefo_sorted` first. Returns current (post-lock) values."""
    if not ids:
        return {}
    rows = db.execute(text("""
        SELECT sb.id, sb.item_id, sb.warehouse_id, sb.location_id, sb.batch_id, sb.qty_physical, sb.qty_reserved,
               sb.qty_blocked, sb.qty_available
          FROM stock_balances sb LEFT JOIN inventory_batches ib ON ib.id = sb.batch_id
         WHERE sb.id = ANY(:ids) ORDER BY """ + FEFO_ORDER + " FOR UPDATE OF sb"), {"ids": ids}).mappings().all()
    return {r["id"]: dict(r) for r in rows}


def fefo_candidates(db: Session, *, item_id: int, warehouse_id: int, batch_id: int | None = None) -> list[dict]:
    """Lock and return every sellable balance of an item in FEFO order. A row is only *locked* if it still matches the
    predicate after waiting for any concurrent writer (PostgreSQL re-evaluates it), so results are post-lock accurate."""
    rows = db.execute(text("""
        SELECT sb.id, sb.batch_id, sb.qty_physical, sb.qty_reserved, sb.qty_blocked, sb.qty_available
          FROM stock_balances sb LEFT JOIN inventory_batches ib ON ib.id = sb.batch_id
         WHERE sb.item_id = :item AND sb.warehouse_id = :wh AND sb.qty_available > 0
           AND (CAST(:batch AS bigint) IS NULL OR sb.batch_id = :batch)
           AND (sb.batch_id IS NULL OR (ib.qc_status = 'RELEASED' AND (ib.expiry_date IS NULL OR ib.expiry_date > business_today())))
         ORDER BY """ + FEFO_ORDER + " FOR UPDATE OF sb"), {"item": item_id, "wh": warehouse_id, "batch": batch_id}).mappings().all()
    return [dict(r) for r in rows]


def apply_delta(db: Session, balance_id: int, *, physical=Decimal(0), reserved=Decimal(0), blocked=Decimal(0)) -> None:
    """Atomic increment on a locked balance. The DB CHECK constraints are the last line of defence: a violation
    (negative or over-reserved stock) surfaces as a business conflict, never as corrupt data."""
    try:
        db.execute(update(StockBalance).where(StockBalance.id == balance_id).values(
            qty_physical=StockBalance.qty_physical + physical, qty_reserved=StockBalance.qty_reserved + reserved,
            qty_blocked=StockBalance.qty_blocked + blocked, updated_at=text("now()")))
        db.flush()
    except IntegrityError as e:
        if isinstance(e.orig, pgerr.CheckViolation):
            raise BusinessConflict("stock_constraint", "Operation would make stock negative or over-reserved",
                                   constraint=getattr(e.orig.diag, "constraint_name", None)) from e
        raise


def add_ledger(db: Session, *, balance: dict, qty_delta=Decimal(0), blocked_delta=Decimal(0), movement_type: str, source_type: str,
               source_id: int, source_line: int = 0, leg: int = 0, actor_id: int | None, reason: str | None = None,
               reverses_ledger_id: int | None = None) -> int:
    try:
        row = StockLedger(balance_id=balance["id"], item_id=balance["item_id"], warehouse_id=balance["warehouse_id"],
                          location_id=balance["location_id"], batch_id=balance["batch_id"], qty_delta=qty_delta,
                          blocked_delta=blocked_delta, movement_type=movement_type, source_type=source_type, source_id=source_id,
                          source_line=source_line, leg=leg, actor_id=actor_id, reason=reason, reverses_ledger_id=reverses_ledger_id,
                          request_id=request_id_var.get())
        db.add(row)
        db.flush()
    except IntegrityError as e:
        if isinstance(e.orig, pgerr.UniqueViolation):
            raise BusinessConflict("duplicate_posting", "This document line has already posted this stock movement",
                                   source=f"{source_type}:{source_id}:{source_line}") from e
        raise
    obs.STOCK_POSTINGS.labels(movement_type).inc()
    return row.id


def add_reservation(db: Session, **kw) -> int:
    r = StockReservation(request_id=request_id_var.get(), **kw)
    db.add(r)
    db.flush()
    return r.id


def get_balance_ids_for_reservations(db: Session, reservation_ids: list[int]) -> list[int]:
    return [r[0] for r in db.execute(text("SELECT DISTINCT balance_id FROM stock_reservations WHERE id = ANY(:ids)"),
                                     {"ids": reservation_ids})]


def active_reservations(db: Session, reservation_ids: list[int]) -> list[dict]:
    """Call AFTER the balances are locked: release/issue need the balance lock too, so status cannot change under us."""
    rows = db.execute(text("""SELECT id, balance_id, item_id, qty, status FROM stock_reservations
                              WHERE id = ANY(:ids) ORDER BY id FOR UPDATE"""), {"ids": reservation_ids}).mappings().all()
    return [dict(r) for r in rows]


def close_reservation(db: Session, reservation_id: int, status: str) -> None:
    db.execute(text("UPDATE stock_reservations SET status = :s, closed_at = now() WHERE id = :id AND status = 'ACTIVE'"),
               {"s": status, "id": reservation_id})
