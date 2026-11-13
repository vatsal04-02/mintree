"""Tables of the tier-1 operations features (migration 0031): batch recall, material returns, rework and GST e-invoice records.
See quality/recall.py, production/rework.py and accounts/einvoice.py for the rules that use them."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

QTY = Numeric(18, 3)
MONEY = Numeric(18, 2)


class RecallCase(Base):
    __tablename__ = "recall_cases"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    root_batch_id: Mapped[int] = mapped_column(ForeignKey("inventory_batches.id"))
    reason: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10), default="MAJOR", server_default="MAJOR")
    status: Mapped[str] = mapped_column(String(10), default="OPEN", server_default="OPEN")
    summary: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("status IN ('OPEN','CLOSED')", name="status_valid"), CheckConstraint("severity IN ('CRITICAL','MAJOR','MINOR')", name="severity_valid"),
                      Index("ix_recall_cases_status", "status", "id"))


class RecallBatch(Base):
    __tablename__ = "recall_batches"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("recall_cases.id", ondelete="CASCADE"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("inventory_batches.id"))
    role: Mapped[str] = mapped_column(String(10))
    prior_status: Mapped[str] = mapped_column(String(12))
    held_by_recall: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    qty_physical: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")
    qty_reserved: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("role IN ('SUSPECT','AFFECTED')", name="role_valid"), UniqueConstraint("case_id", "batch_id", name="uq_recall_batches_case_batch"),
                      Index("ix_recall_batches_batch", "batch_id"))


class RecallCustomer(Base):
    __tablename__ = "recall_customers"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("recall_cases.id", ondelete="CASCADE"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    so_id: Mapped[int | None] = mapped_column(ForeignKey("sales_orders.id"))
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("inventory_batches.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    disposition: Mapped[str] = mapped_column(String(10), default="PENDING", server_default="PENDING")
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("disposition IN ('PENDING','NOTIFIED','RETURNED','CLEARED')", name="disposition_valid"), Index("ix_recall_customers_case", "case_id"))


class MaterialReturn(Base):
    __tablename__ = "material_returns"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("production_orders.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    cost: Mapped[Decimal] = mapped_column(MONEY)
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"), Index("ix_material_returns_order", "order_id"))


class ReworkOrder(Base):
    __tablename__ = "rework_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    source_batch_id: Mapped[int] = mapped_column(ForeignKey("inventory_batches.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    input_qty: Mapped[Decimal] = mapped_column(QTY)
    input_cost: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    reason: Mapped[str] = mapped_column(Text)
    process: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="STARTED", server_default="STARTED")
    output_batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    output_qty: Mapped[Decimal | None] = mapped_column(QTY)
    loss_qty: Mapped[Decimal | None] = mapped_column(QTY)
    added_cost: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("status IN ('STARTED','COMPLETED','CANCELLED')", name="status_valid"), CheckConstraint("input_qty > 0", name="input_positive"),
                      Index("ix_rework_orders_source", "source_batch_id"), Index("ix_rework_orders_output", "output_batch_id"))


class EInvoice(Base):
    __tablename__ = "einvoices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("sales_invoices.id"), unique=True)
    status: Mapped[str] = mapped_column(String(12), default="GENERATED", server_default="GENERATED")
    mode: Mapped[str] = mapped_column(String(8), default="SANDBOX", server_default="SANDBOX")
    provider: Mapped[str] = mapped_column(String(30))
    irn: Mapped[str | None] = mapped_column(String(64))
    ack_no: Mapped[str | None] = mapped_column(String(30))
    ack_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_qr: Mapped[str | None] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    ewb_no: Mapped[str | None] = mapped_column(String(20))
    ewb_valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("status IN ('GENERATED','CANCELLED','FAILED')", name="status_valid"), CheckConstraint("mode IN ('SANDBOX','LIVE')", name="mode_valid"),
                      Index("uq_einvoices_irn", "irn", unique=True, postgresql_where=text("irn IS NOT NULL")))
