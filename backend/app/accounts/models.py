"""Double-entry accounting. Journals are immutable and balanced by a DEFERRED constraint trigger (see migration): an
unbalanced entry cannot commit, whatever the application does."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

MONEY = Numeric(18, 2)
QTY = Numeric(18, 3)
ACCOUNT_TYPES = ("ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE")


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(10))
    is_postable: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    __table_args__ = (CheckConstraint("type IN ('ASSET','LIABILITY','EQUITY','REVENUE','EXPENSE')", name="type_valid"),)


class AccountingPeriod(Base):
    __tablename__ = "accounting_periods"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    start_date: Mapped[date] = mapped_column(Date, unique=True)
    end_date: Mapped[date] = mapped_column(Date, unique=True)
    status: Mapped[str] = mapped_column(String(8), default="OPEN", server_default="OPEN")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("end_date >= start_date", name="dates_ordered"),
                      CheckConstraint("status IN ('OPEN','CLOSED')", name="status_valid"))


class JournalEntry(Base):
    """Append-only (trigger). A correction is a new entry with reverses_entry_id."""

    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    entry_date: Mapped[date] = mapped_column(Date)
    period_id: Mapped[int] = mapped_column(ForeignKey("accounting_periods.id"))
    description: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[int] = mapped_column(BigInteger)
    source_event: Mapped[str] = mapped_column(String(30), default="post", server_default="post")
    total_debit: Mapped[Decimal] = mapped_column(MONEY)
    total_credit: Mapped[Decimal] = mapped_column(MONEY)
    reverses_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"), unique=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lines: Mapped[list["JournalLine"]] = relationship(order_by="JournalLine.line_no", lazy="selectin")
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "source_event", name="uq_journal_entries_source"),  # a document posts once
        CheckConstraint("total_debit = total_credit AND total_debit > 0", name="balanced"),
        Index("ix_journal_entries_period", "period_id", "entry_date"),
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    debit: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    credit: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    narration: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        UniqueConstraint("entry_id", "line_no"),
        CheckConstraint("debit >= 0 AND credit >= 0 AND (debit = 0 OR credit = 0) AND debit + credit > 0", name="one_sided_positive"),
        Index("ix_journal_lines_account", "account_id", "entry_id"),
    )


class DocumentCounter(Base):
    """Gapless statutory numbering (invoices): a locked counter row, incremented in the document's own transaction, so a
    rollback frees the number. Deliberately a hot row -- taken LAST in a transaction to keep the hold short."""

    __tablename__ = "document_counters"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)    # e.g. "sales_invoice:2026-27"
    last_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ItemValuation(Base):
    """Moving-average valuation per item. GL inventory accounts must equal the sum of `value` (integrity-checked)."""

    __tablename__ = "item_valuation"
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    qty: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")
    value: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    __table_args__ = (CheckConstraint("qty >= 0 AND value >= 0", name="non_negative"),
                      CheckConstraint("(qty = 0) = (value = 0) OR qty > 0", name="value_needs_qty"))


class SupplierInvoice(Base):
    __tablename__ = "supplier_invoices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    supplier_invoice_no: Mapped[str] = mapped_column(String(60))
    invoice_date: Mapped[date] = mapped_column(Date)
    subtotal: Mapped[Decimal] = mapped_column(MONEY)
    tax_total: Mapped[Decimal] = mapped_column(MONEY)
    total: Mapped[Decimal] = mapped_column(MONEY)
    amount_paid: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    credited_amount: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")     # debit notes raised against this invoice
    status: Mapped[str] = mapped_column(String(10), default="OPEN", server_default="OPEN")
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lines: Mapped[list["SupplierInvoiceLine"]] = relationship(order_by="SupplierInvoiceLine.line_no", lazy="selectin")
    __table_args__ = (
        UniqueConstraint("supplier_id", "supplier_invoice_no", name="uq_supplier_invoices_supplier_no"),   # same bill never booked twice
        CheckConstraint("total = subtotal + tax_total", name="total_consistent"),
        CheckConstraint("amount_paid >= 0 AND credited_amount >= 0 AND amount_paid + credited_amount <= total", name="settled_within_total"),
        CheckConstraint("status IN ('OPEN','PARTIAL','PAID')", name="status_valid"),
    )


class SupplierInvoiceLine(Base):
    __tablename__ = "supplier_invoice_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("supplier_invoices.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    po_item_id: Mapped[int] = mapped_column(ForeignKey("purchase_order_items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, server_default="0")
    __table_args__ = (UniqueConstraint("invoice_id", "line_no"), CheckConstraint("qty > 0", name="qty_positive"))


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    direction: Mapped[str] = mapped_column(String(10))            # PAID (to supplier) | RECEIVED (from customer)
    party_type: Mapped[str] = mapped_column(String(10))           # supplier | customer
    party_id: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    payment_date: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(20))
    bank_account_code: Mapped[str] = mapped_column(String(20))
    reference: Mapped[str | None] = mapped_column(String(80))
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),
                      CheckConstraint("direction IN ('PAID','RECEIVED')", name="direction_valid"))


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"))
    invoice_type: Mapped[str] = mapped_column(String(20))         # supplier_invoice | sales_invoice
    invoice_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    __table_args__ = (UniqueConstraint("payment_id", "invoice_type", "invoice_id"), CheckConstraint("amount > 0", name="amount_positive"),
                      Index("ix_payment_allocations_invoice", "invoice_type", "invoice_id"))
