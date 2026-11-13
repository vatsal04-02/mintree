from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

QTY = Numeric(18, 3)
MONEY = Numeric(18, 2)
SO_STATUSES = ("DRAFT", "CONFIRMED", "ALLOCATED", "PACKED", "DISPATCHED", "DELIVERED", "CANCELLED")


class SalesOrder(Base):
    __tablename__ = "sales_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    channel: Mapped[str] = mapped_column(String(12), default="B2B", server_default="B2B")
    external_ref: Mapped[str | None] = mapped_column(String(80))          # marketplace / customer PO number
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", server_default="DRAFT")
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    shipping_address: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    tax_total: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    total: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    cartons: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    short_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))     # remaining quantity was cancelled by a decision (close short)
    short_close_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["SalesOrderItem"]] = relationship(order_by="SalesOrderItem.line_no", lazy="selectin")
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','CONFIRMED','ALLOCATED','PACKED','PARTIALLY_DISPATCHED','DISPATCHED','DELIVERED','CANCELLED')", name="status_valid"),
        CheckConstraint("channel IN ('B2B','DISTRIBUTOR','BRAND','MARKETPLACE','MANUAL')", name="channel_valid"),
        # the same external order (marketplace / customer PO) can never create two sales orders
        Index("uq_sales_orders_external", "channel", "external_ref", unique=True, postgresql_where=text("external_ref IS NOT NULL")),
        Index("ix_sales_orders_customer", "customer_id", "status"),
    )


class SalesOrderItem(Base):
    __tablename__ = "sales_order_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, server_default="0")
    qty_shipped: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")
    qty_returned: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")
    __table_args__ = (UniqueConstraint("so_id", "line_no", name="uq_sales_order_items_line"), CheckConstraint("qty > 0", name="qty_positive"),
                      CheckConstraint("qty_shipped <= qty AND qty_returned <= qty_shipped", name="shipped_returned_within"),
                      Index("ix_sales_order_items_item", "item_id"))


class SalesInvoice(Base):
    __tablename__ = "sales_invoices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)          # gapless (locked counter)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"))               # an order may have several invoices (one per shipment)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"), unique=True)   # the shipment this invoice bills (NULL only on legacy rows)
    invoice_date: Mapped[date] = mapped_column(Date)
    subtotal: Mapped[Decimal] = mapped_column(MONEY)
    tax_total: Mapped[Decimal] = mapped_column(MONEY)
    total: Mapped[Decimal] = mapped_column(MONEY)
    amount_paid: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    credited_amount: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(10), default="OPEN", server_default="OPEN")
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_sales_invoices_order", "so_id"), CheckConstraint("total = subtotal + tax_total", name="total_consistent"),
                      CheckConstraint("amount_paid >= 0 AND credited_amount >= 0 AND amount_paid + credited_amount <= total", name="settled_within_total"),
                      CheckConstraint("status IN ('OPEN','PARTIAL','PAID')", name="status_valid"),
                      Index("ix_sales_invoices_customer", "customer_id", text("invoice_date DESC")), Index("ix_sales_invoices_open", "invoice_date", postgresql_where=text("status <> 'PAID'")))


class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"))                 # an order may ship in several shipments
    shipment_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1")   # 1, 2, 3 ... within the order
    courier: Mapped[str] = mapped_column(String(30))
    courier_partner_id: Mapped[int | None] = mapped_column(ForeignKey("courier_partners.id"))
    status: Mapped[str] = mapped_column(String(18), default="PENDING_BOOKING", server_default="PENDING_BOOKING")
    awb: Mapped[str | None] = mapped_column(String(60), unique=True)
    tracking_url: Mapped[str | None] = mapped_column(String(300))
    booking_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # booking lease (a dead worker's claim expires)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipping_charge: Mapped[Decimal | None] = mapped_column(MONEY)              # courier freight actually charged (recorded for margin; not posted)
    pickup_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manifest_no: Mapped[str | None] = mapped_column(String(40))
    handed_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))      # scanned onto the courier at handover
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lines: Mapped[list["ShipmentLine"]] = relationship(lazy="selectin")
    __table_args__ = (CheckConstraint("status IN ('PENDING_BOOKING','BOOKING','BOOKED','PICKED_UP','IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERED','FAILED_DELIVERY','RTO')", name="status_valid"),
                      UniqueConstraint("so_id", "shipment_no", name="uq_shipments_order_no"), Index("ix_shipments_order", "so_id"))


class ShipmentLine(Base):
    """What physically left, from which batch, at what cost: the forward half of genealogy and the base for returns."""

    __tablename__ = "shipment_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    so_item_id: Mapped[int] = mapped_column(ForeignKey("sales_order_items.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    balance_id: Mapped[int] = mapped_column(ForeignKey("stock_balances.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    net_amount: Mapped[Decimal | None] = mapped_column(MONEY)      # invoiced taxable value of this line (NULL on legacy rows)
    tax_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"), Index("ix_shipment_lines_batch", "batch_id"),
                      Index("ix_shipment_lines_shipment", "shipment_id"), Index("ix_shipment_lines_so_item", "so_item_id"))


class CourierEvent(Base):
    __tablename__ = "courier_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    external_event_id: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(24))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("shipment_id", "external_event_id", name="uq_courier_events_ext"),)   # no duplicate shipment events


class SalesReturn(Base):
    __tablename__ = "sales_returns"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"))
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"))     # a return covers lines of exactly one shipment (and so one invoice)
    status: Mapped[str] = mapped_column(String(10), default="REQUESTED", server_default="REQUESTED")
    reason: Mapped[str] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(String(10))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    credit_note_id: Mapped[int | None] = mapped_column(BigInteger)
    reverse_pickup_status: Mapped[str | None] = mapped_column(String(12))        # REQUESTING | REQUESTED | FAILED
    reverse_awb: Mapped[str | None] = mapped_column(String(60))
    reverse_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lines: Mapped[list["SalesReturnLine"]] = relationship(lazy="selectin")
    __table_args__ = (CheckConstraint("status IN ('REQUESTED','RECEIVED','DECIDED')", name="status_valid"),
                      CheckConstraint("decision IS NULL OR decision IN ('RESTOCK','REJECT')", name="decision_valid"))


class SalesReturnLine(Base):
    __tablename__ = "sales_return_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("sales_returns.id"))
    shipment_line_id: Mapped[int] = mapped_column(ForeignKey("shipment_lines.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"), UniqueConstraint("return_id", "shipment_line_id", name="uq_return_line"))


class CreditNote(Base):
    __tablename__ = "credit_notes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)          # gapless
    invoice_id: Mapped[int] = mapped_column(ForeignKey("sales_invoices.id"))
    return_id: Mapped[int] = mapped_column(ForeignKey("sales_returns.id"), unique=True)
    subtotal: Mapped[Decimal] = mapped_column(MONEY)
    tax_total: Mapped[Decimal] = mapped_column(MONEY)
    total: Mapped[Decimal] = mapped_column(MONEY)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("total = subtotal + tax_total AND total > 0", name="consistent"),)


class PickScan(Base):
    """One scan during picking: which batch, how much, by whom. Verification compares scanned quantity per batch with the reservation that allocation made."""

    __tablename__ = "pick_scans"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    scanned_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"), Index("ix_pick_scans_order", "so_id"))
