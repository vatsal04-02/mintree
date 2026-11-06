from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

QTY = Numeric(18, 3)
MONEY = Numeric(18, 2)


class Rfq(Base):
    __tablename__ = "rfqs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    pr_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_requests.id"))
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", server_default="DRAFT")
    due_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    awarded_quotation_id: Mapped[int | None] = mapped_column(BigInteger)
    awarded_po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"))
    award_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["RfqItem"]] = relationship(order_by="RfqItem.line_no", lazy="selectin")
    suppliers: Mapped[list["RfqSupplier"]] = relationship(lazy="selectin")
    __table_args__ = (CheckConstraint("status IN ('DRAFT','SENT','AWARDED','CANCELLED')", name="status_valid"),)


class RfqItem(Base):
    __tablename__ = "rfq_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    __table_args__ = (UniqueConstraint("rfq_id", "line_no"), UniqueConstraint("rfq_id", "item_id", name="uq_rfq_items_item"), CheckConstraint("qty > 0", name="qty_positive"))


class RfqSupplier(Base):
    __tablename__ = "rfq_suppliers"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id", ondelete="CASCADE"))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    __table_args__ = (UniqueConstraint("rfq_id", "supplier_id", name="uq_rfq_suppliers"),)


class Quotation(Base):
    """One quotation per invited supplier per RFQ (unique). Prices are what the supplier actually quoted; comparison uses these values only."""

    __tablename__ = "quotations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id"))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    status: Mapped[str] = mapped_column(String(10), default="SUBMITTED", server_default="SUBMITTED")
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    valid_until: Mapped[date | None] = mapped_column(Date)
    payment_terms_days: Mapped[int | None] = mapped_column(Integer)
    freight: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["QuotationItem"]] = relationship(lazy="selectin")
    __table_args__ = (UniqueConstraint("rfq_id", "supplier_id", name="uq_quotations_supplier"),
                      CheckConstraint("status IN ('SUBMITTED','AWARDED','REJECTED')", name="status_valid"),
                      CheckConstraint("freight >= 0", name="freight_non_negative"))


class QuotationItem(Base):
    __tablename__ = "quotation_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(ForeignKey("quotations.id", ondelete="CASCADE"))
    rfq_item_id: Mapped[int] = mapped_column(ForeignKey("rfq_items.id"))
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, server_default="0")
    __table_args__ = (UniqueConstraint("quotation_id", "rfq_item_id", name="uq_quotation_items_line"), CheckConstraint("unit_price >= 0", name="price_non_negative"))
