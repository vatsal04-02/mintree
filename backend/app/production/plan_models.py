"""Work centres (where production happens, with daily capacity) and production plans (what to make, when, where) that convert into production orders."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.mixins import AuditFieldsMixin

QTY = Numeric(18, 3)
WORK_CENTRE_KINDS = ("MIXING", "FILLING", "PACKING", "LAB", "STORAGE", "OTHER")


class WorkCentre(AuditFieldsMixin, Base):
    __tablename__ = "work_centres"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    capacity_per_day: Mapped[Decimal | None] = mapped_column(QTY)          # output units per day; NULL = not capacity-planned
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    __table_args__ = (CheckConstraint("kind IN ('MIXING','FILLING','PACKING','LAB','STORAGE','OTHER')", name="kind_valid"),
                      CheckConstraint("capacity_per_day IS NULL OR capacity_per_day > 0", name="capacity_positive"))


class ProductionPlan(Base):
    __tablename__ = "production_plans"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", server_default="DRAFT")
    overload_accepted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))     # set when a plan was confirmed over a work centre's capacity
    overload_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lines: Mapped[list["ProductionPlanLine"]] = relationship(order_by="ProductionPlanLine.line_no", lazy="selectin")
    __table_args__ = (CheckConstraint("status IN ('DRAFT','CONFIRMED','CLOSED','CANCELLED')", name="status_valid"),
                      CheckConstraint("period_end >= period_start", name="period_valid"))


class ProductionPlanLine(Base):
    __tablename__ = "production_plan_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("production_plans.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    planned_qty: Mapped[Decimal] = mapped_column(QTY)
    planned_date: Mapped[date] = mapped_column(Date)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    work_centre_id: Mapped[int | None] = mapped_column(ForeignKey("work_centres.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("production_orders.id"), unique=True)      # set when converted: a line yields at most one order
    __table_args__ = (UniqueConstraint("plan_id", "line_no", name="uq_production_plan_lines_no"), CheckConstraint("planned_qty > 0", name="qty_positive"),
                      Index("ix_production_plan_lines_wc_date", "work_centre_id", "planned_date"))
