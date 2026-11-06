from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Stocktake(Base):
    __tablename__ = "stocktakes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    status: Mapped[str] = mapped_column(String(10), default="COUNTING", server_default="COUNTING")
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("status IN ('COUNTING','APPROVED','CANCELLED')", name="status_valid"),)


class StocktakeLine(Base):
    __tablename__ = "stocktake_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stocktake_id: Mapped[int] = mapped_column(ForeignKey("stocktakes.id", ondelete="CASCADE"))
    balance_id: Mapped[int] = mapped_column(ForeignKey("stock_balances.id"))
    system_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    counted_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    counted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("stocktake_id", "balance_id", name="uq_stocktake_lines_balance"), CheckConstraint("counted_qty IS NULL OR counted_qty >= 0", name="counted_non_negative"))


class OpeningStock(Base):
    """A go-live (or later migration) stock load: quantities with their cost, posted once, valued, and balanced against Owner's Equity (3000)."""

    __tablename__ = "opening_stock_postings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    qc_status: Mapped[str] = mapped_column(String(12))
    note: Mapped[str | None] = mapped_column(Text)
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    posted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("qc_status IN ('RELEASED','QUARANTINE')", name="qc_valid"),)
