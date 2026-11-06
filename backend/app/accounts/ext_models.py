from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

MONEY = Numeric(18, 2)


class BankAccount(Base):
    """A bank/cash account. `ledger_account_code` is the GL account whose journal lines are reconciled against its statement."""

    __tablename__ = "bank_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    bank_name: Mapped[str | None] = mapped_column(String(120))
    account_last4: Mapped[str | None] = mapped_column(String(4))               # never the full account number
    ledger_account_code: Mapped[str] = mapped_column(String(20), unique=True)
    opening_balance: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")     # statement balance before the first imported transaction
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")


class BankTransaction(Base):
    """One statement line. amount > 0 = money in (a DEBIT to the ledger account), < 0 = money out. unique(account, statement, line) makes re-importing a file a no-op;
    unique(journal_line_id) means a ledger line is matched to at most one statement line."""

    __tablename__ = "bank_transactions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"))
    statement_ref: Mapped[str] = mapped_column(String(64))
    line_no: Mapped[int] = mapped_column(Integer)
    txn_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(300))
    reference: Mapped[str | None] = mapped_column(String(80))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(10), default="UNMATCHED", server_default="UNMATCHED")
    journal_line_id: Mapped[int | None] = mapped_column(ForeignKey("journal_lines.id"), unique=True)
    matched_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("bank_account_id", "statement_ref", "line_no", name="uq_bank_transactions_line"),
                      CheckConstraint("status IN ('UNMATCHED','MATCHED','IGNORED')", name="status_valid"),
                      CheckConstraint("amount <> 0", name="amount_nonzero"),
                      CheckConstraint("(status = 'MATCHED') = (journal_line_id IS NOT NULL)", name="matched_has_line"),
                      Index("ix_bank_transactions_status", "bank_account_id", "status", "txn_date"))


class DebitNote(Base):
    """Supplier debit note: reduces what we owe on a supplier invoice (price correction, quality claim). Gapless per fiscal year."""

    __tablename__ = "debit_notes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    supplier_invoice_id: Mapped[int] = mapped_column(ForeignKey("supplier_invoices.id"))
    reason_type: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    subtotal: Mapped[Decimal] = mapped_column(MONEY)
    tax_total: Mapped[Decimal] = mapped_column(MONEY)
    total: Mapped[Decimal] = mapped_column(MONEY)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("reason_type IN ('PRICE_ADJUSTMENT','QUALITY_CLAIM','SHORT_SUPPLY')", name="reason_type_valid"),
                      CheckConstraint("subtotal > 0 AND tax_total >= 0 AND total = subtotal + tax_total", name="amounts_consistent"))
