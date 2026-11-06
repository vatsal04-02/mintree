from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

MONEY = Numeric(18, 2)
QTY = Numeric(18, 3)


class CustomerContact(Base):
    __tablename__ = "customer_contacts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str | None] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(254))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    __table_args__ = (Index("uq_customer_primary_contact", "customer_id", unique=True, postgresql_where="is_primary"),)    # at most one primary per customer


class CustomerPrice(Base):
    """Customer-specific price for an item for a validity window. Windows for the same customer+item may not overlap (enforced by an exclusion constraint in the migration)."""

    __tablename__ = "customer_prices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    price: Mapped[Decimal] = mapped_column(MONEY)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (CheckConstraint("price >= 0", name="price_non_negative"), CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="dates_ordered"),
                      Index("ix_customer_prices_lookup", "customer_id", "item_id", "valid_from"))


class FollowUp(Base):
    __tablename__ = "followups"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subject: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="OPEN", server_default="OPEN")
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    assigned_to: Mapped[int] = mapped_column(ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("status IN ('OPEN','DONE','CANCELLED')", name="status_valid"),
                      CheckConstraint("(lead_id IS NOT NULL) <> (customer_id IS NOT NULL)", name="one_subject"),
                      CheckConstraint("status <> 'DONE' OR outcome IS NOT NULL", name="outcome_required"),
                      Index("ix_followups_due", "assigned_to", "status", "due_date"))


class SalesQuotation(Base):
    __tablename__ = "sales_quotations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", server_default="DRAFT")
    valid_until: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    tax_total: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    total: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    sales_order_id: Mapped[int | None] = mapped_column(ForeignKey("sales_orders.id"), unique=True)     # a quotation converts to at most one order
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["SalesQuotationItem"]] = relationship(order_by="SalesQuotationItem.line_no", lazy="selectin")
    __table_args__ = (CheckConstraint("status IN ('DRAFT','SENT','ACCEPTED','REJECTED','CONVERTED')", name="status_valid"),)


class SalesQuotationItem(Base):
    __tablename__ = "sales_quotation_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(ForeignKey("sales_quotations.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, server_default="0")
    price_source: Mapped[str] = mapped_column(String(16))                 # CUSTOMER_LIST | MANUAL
    __table_args__ = (UniqueConstraint("quotation_id", "line_no"), UniqueConstraint("quotation_id", "item_id", name="uq_sales_quotation_items_item"),
                      CheckConstraint("qty > 0 AND unit_price >= 0", name="positive"), CheckConstraint("price_source IN ('CUSTOMER_LIST','MANUAL')", name="source_valid"))
