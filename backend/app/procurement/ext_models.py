"""Purchase returns (stock goes back to the supplier) and purchase-order amendments (controlled, approved, versioned changes)."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

QTY = Numeric(18, 3)
MONEY = Numeric(18, 2)
RETURN_REASONS = ("QC_REJECTED", "DAMAGED", "WRONG_ITEM", "EXCESS", "OTHER")


class PurchaseReturn(Base):
    __tablename__ = "purchase_returns"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    goods_receipt_id: Mapped[int] = mapped_column(ForeignKey("goods_receipts.id"))
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    reason_type: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(Text)
    total_value: Mapped[Decimal] = mapped_column(MONEY)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    posted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lines: Mapped[list["PurchaseReturnLine"]] = relationship(order_by="PurchaseReturnLine.line_no", lazy="selectin")
    __table_args__ = (CheckConstraint("reason_type IN ('QC_REJECTED','DAMAGED','WRONG_ITEM','EXCESS','OTHER')", name="reason_type_valid"),
                      CheckConstraint("total_value >= 0", name="value_non_negative"),
                      Index("ix_purchase_returns_gr", "goods_receipt_id"))


class PurchaseReturnLine(Base):
    __tablename__ = "purchase_return_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("purchase_returns.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    gr_item_id: Mapped[int] = mapped_column(ForeignKey("goods_receipt_items.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)      # PO price snapshot used to reverse the receipt accrual
    value: Mapped[Decimal] = mapped_column(MONEY)
    __table_args__ = (UniqueConstraint("return_id", "line_no"), CheckConstraint("qty > 0 AND value >= 0", name="qty_positive"),
                      Index("ix_purchase_return_lines_gr_item", "gr_item_id"))


class PoAmendment(Base):
    """A requested change to an approved PO. Nothing on the PO changes until a (different) authorised user approves it."""

    __tablename__ = "po_amendments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    amendment_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="PENDING", server_default="PENDING")
    reason: Mapped[str] = mapped_column(Text)
    po_version_at_request: Mapped[int] = mapped_column(Integer)
    total_before: Mapped[Decimal] = mapped_column(MONEY)
    total_after: Mapped[Decimal] = mapped_column(MONEY)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    lines: Mapped[list["PoAmendmentLine"]] = relationship(order_by="PoAmendmentLine.id", lazy="selectin")
    __table_args__ = (UniqueConstraint("po_id", "amendment_no"),
                      CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="status_valid"),
                      CheckConstraint("(status = 'PENDING') = (decided_by IS NULL)", name="decision_consistent"),
                      Index("uq_po_amendments_one_pending", "po_id", unique=True, postgresql_where=text("status = 'PENDING'")))


class PoAmendmentLine(Base):
    __tablename__ = "po_amendment_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    amendment_id: Mapped[int] = mapped_column(ForeignKey("po_amendments.id", ondelete="CASCADE"))
    po_item_id: Mapped[int] = mapped_column(ForeignKey("purchase_order_items.id"))
    old_qty: Mapped[Decimal] = mapped_column(QTY)
    new_qty: Mapped[Decimal] = mapped_column(QTY)
    old_unit_price: Mapped[Decimal] = mapped_column(MONEY)
    new_unit_price: Mapped[Decimal] = mapped_column(MONEY)
    __table_args__ = (UniqueConstraint("amendment_id", "po_item_id"), CheckConstraint("new_qty > 0 AND new_unit_price >= 0", name="new_values_valid"))
