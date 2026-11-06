"""Inventory: batches, balances (current-state projection), ledger (append-only truth), reservations.

Invariants (also enforced by DB constraints, so application bugs cannot violate them):
  * physical >= 0, reserved >= 0, blocked >= 0, and reserved + blocked <= physical  (=> available >= 0)
  * balance.physical == SUM(ledger.qty_delta); balance.blocked == SUM(ledger.blocked_delta);
    balance.reserved == SUM(active reservations)       (verified by scripts/check_integrity.py)
  * ledger rows are never updated or deleted; corrections are new REVERSAL/ADJUSTMENT rows.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, Computed, Date, DateTime, ForeignKey, Index, Integer, Numeric, SmallInteger,
                        String, Text, UniqueConstraint, func, text)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

QTY = Numeric(18, 3)
QC_STATUSES = ("QUARANTINE", "RELEASED", "HOLD", "REJECTED")
MOVEMENTS = ("RECEIPT", "ISSUE", "TRANSFER_OUT", "TRANSFER_IN", "ADJUSTMENT", "REVERSAL", "QC_BLOCK", "QC_RELEASE",
             "PRODUCTION_ISSUE", "PRODUCTION_OUTPUT", "DISPATCH", "RETURN")


class InventoryBatch(Base):
    __tablename__ = "inventory_batches"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    batch_no: Mapped[str] = mapped_column(String(40))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    supplier_lot_no: Mapped[str | None] = mapped_column(String(60))
    mfg_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    qc_status: Mapped[str] = mapped_column(String(12), default="QUARANTINE", server_default="QUARANTINE")
    source_type: Mapped[str | None] = mapped_column(String(40))   # traceability: which document created the batch
    source_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    qc_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    qc_decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        UniqueConstraint("item_id", "batch_no"),
        CheckConstraint("qc_status IN ('QUARANTINE','RELEASED','HOLD','REJECTED')", name="qc_status_valid"),
        CheckConstraint("expiry_date IS NULL OR mfg_date IS NULL OR expiry_date > mfg_date", name="dates_ordered"),
        Index("ix_inventory_batches_source", "source_type", "source_id"),
        Index("ix_inventory_batches_expiry", "expiry_date"),
    )


class StockBalance(Base):
    __tablename__ = "stock_balances"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("storage_locations.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    qty_physical: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")
    qty_reserved: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")
    qty_blocked: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")
    qty_available: Mapped[Decimal] = mapped_column(QTY, Computed("qty_physical - qty_reserved - qty_blocked", persisted=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        # NULLS NOT DISTINCT: one balance row per (item, warehouse, location, batch) even when location/batch are NULL.
        UniqueConstraint("item_id", "warehouse_id", "location_id", "batch_id", postgresql_nulls_not_distinct=True),
        CheckConstraint("qty_physical >= 0", name="physical_non_negative"),
        CheckConstraint("qty_reserved >= 0", name="reserved_non_negative"),
        CheckConstraint("qty_blocked >= 0", name="blocked_non_negative"),
        CheckConstraint("qty_reserved + qty_blocked <= qty_physical", name="available_non_negative"),
        Index("ix_stock_balances_item_wh", "item_id", "warehouse_id"),
        Index("ix_stock_balances_batch", "batch_id"),
    )


class StockLedger(Base):
    """Append-only. UPDATE/DELETE/TRUNCATE are blocked by triggers (see migration)."""

    __tablename__ = "stock_ledger"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    balance_id: Mapped[int] = mapped_column(ForeignKey("stock_balances.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("storage_locations.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    qty_delta: Mapped[Decimal] = mapped_column(QTY, default=0)
    blocked_delta: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")
    movement_type: Mapped[str] = mapped_column(String(20))
    source_type: Mapped[str] = mapped_column(String(40))          # e.g. goods_receipt, adjustment, transfer
    source_id: Mapped[int] = mapped_column(BigInteger)
    source_line: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    leg: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    reverses_ledger_id: Mapped[int | None] = mapped_column(ForeignKey("stock_ledger.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        # Final duplicate-posting guard: a document line can post each movement type/leg exactly once.
        UniqueConstraint("source_type", "source_id", "source_line", "leg", "movement_type", name="uq_stock_ledger_source"),
        UniqueConstraint("reverses_ledger_id", name="uq_stock_ledger_reverses"),  # a movement can be reversed once
        CheckConstraint("movement_type IN ('RECEIPT','ISSUE','TRANSFER_OUT','TRANSFER_IN','ADJUSTMENT','REVERSAL','QC_BLOCK',"
                        "'QC_RELEASE','PRODUCTION_ISSUE','PRODUCTION_OUTPUT','DISPATCH','RETURN')", name="movement_valid"),
        CheckConstraint("qty_delta <> 0 OR blocked_delta <> 0", name="not_empty"),
        Index("ix_stock_ledger_balance", "balance_id", "id"),
        Index("ix_stock_ledger_item_posted", "item_id", "posted_at"),
        Index("ix_stock_ledger_batch", "batch_id", "id"),
        Index("ix_stock_ledger_source", "source_type", "source_id"),
    )


class StockReservation(Base):
    __tablename__ = "stock_reservations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    balance_id: Mapped[int] = mapped_column(ForeignKey("stock_balances.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE", server_default="ACTIVE")
    reference_type: Mapped[str] = mapped_column(String(40))       # e.g. sales_order, production_order
    reference_id: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_id: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("status IN ('ACTIVE','RELEASED','CONSUMED')", name="status_valid"),
        Index("ix_stock_reservations_active", "balance_id", postgresql_where=text("status = 'ACTIVE'")),
        Index("ix_stock_reservations_ref", "reference_type", "reference_id"),
    )
