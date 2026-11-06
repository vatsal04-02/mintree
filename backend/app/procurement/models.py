from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

QTY = Numeric(18, 3)
MONEY = Numeric(18, 2)
PR_STATUSES = ("DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CLOSED")
PO_STATUSES = ("DRAFT", "PENDING_APPROVAL", "APPROVED", "PARTIALLY_RECEIVED", "RECEIVED", "CLOSED", "CANCELLED")


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    status: Mapped[str] = mapped_column(String(12), default="DRAFT", server_default="DRAFT")
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    required_by: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["PurchaseRequestItem"]] = relationship(order_by="PurchaseRequestItem.line_no", lazy="selectin")
    __table_args__ = (CheckConstraint("status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CLOSED')", name="status_valid"),)


class PurchaseRequestItem(Base):
    __tablename__ = "purchase_request_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pr_id: Mapped[int] = mapped_column(ForeignKey("purchase_requests.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    __table_args__ = (UniqueConstraint("pr_id", "line_no"), CheckConstraint("qty > 0", name="qty_positive"))


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    pr_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_requests.id"))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", server_default="DRAFT")
    expected_date: Mapped[date | None] = mapped_column(Date)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")  # optimistic concurrency token
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    items: Mapped[list["PurchaseOrderItem"]] = relationship(order_by="PurchaseOrderItem.line_no", lazy="selectin")
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','PENDING_APPROVAL','APPROVED','PARTIALLY_RECEIVED','RECEIVED','CLOSED','CANCELLED')",
                        name="status_valid"),
        CheckConstraint("total_amount >= 0", name="total_non_negative"),
        Index("ix_purchase_orders_supplier", "supplier_id", "status"),
        Index("ix_purchase_orders_status", "status", "id"),
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty_ordered: Mapped[Decimal] = mapped_column(QTY)
    qty_received: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")  # projection, maintained with receipts
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, server_default="0")
    __table_args__ = (
        UniqueConstraint("po_id", "line_no"),
        CheckConstraint("qty_ordered > 0", name="ordered_positive"),
        CheckConstraint("qty_received >= 0", name="received_non_negative"),
        CheckConstraint("qty_received <= qty_ordered", name="no_over_receipt"),   # final guard against over-receipt
        CheckConstraint("unit_price >= 0", name="price_non_negative"),
    )


class ApprovalPolicy(Base):
    """Configurable approval limits: a user holding `role_code` may approve `doc_type` up to `max_amount`."""

    __tablename__ = "approval_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(40))
    role_code: Mapped[str] = mapped_column(String(50))
    max_amount: Mapped[Decimal | None] = mapped_column(MONEY)   # NULL = unlimited
    allow_self_approval: Mapped[bool] = mapped_column(default=False, server_default="false")
    __table_args__ = (UniqueConstraint("doc_type", "role_code"),)


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    delivery_note_no: Mapped[str] = mapped_column(String(60))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(10), default="POSTED", server_default="POSTED")
    posted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    items: Mapped[list["GoodsReceiptItem"]] = relationship(order_by="GoodsReceiptItem.line_no", lazy="selectin")
    __table_args__ = (
        # Business-level duplicate guard: the same physical delivery cannot be booked twice, whatever the idempotency key.
        UniqueConstraint("supplier_id", "delivery_note_no", name="uq_goods_receipts_delivery_note"),
        CheckConstraint("status IN ('POSTED','REVERSED')", name="status_valid"),
        Index("ix_goods_receipts_po", "po_id"),
    )


class GoodsReceiptItem(Base):
    __tablename__ = "goods_receipt_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    gr_id: Mapped[int] = mapped_column(ForeignKey("goods_receipts.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    po_item_id: Mapped[int] = mapped_column(ForeignKey("purchase_order_items.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("storage_locations.id"))
    __table_args__ = (UniqueConstraint("gr_id", "line_no"), CheckConstraint("qty > 0", name="qty_positive"),
                      Index("ix_goods_receipt_items_po_item", "po_item_id"))
