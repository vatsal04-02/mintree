from decimal import Decimal
from datetime import date, datetime

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Equipment(Base):
    __tablename__ = "equipment"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str | None] = mapped_column(String(60))
    location: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(18), default="OPERATIONAL", server_default="OPERATIONAL")
    calibration_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    calibration_due: Mapped[date | None] = mapped_column(Date)
    manufacturer: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(120))
    serial_no: Mapped[str | None] = mapped_column(String(80))
    purchase_date: Mapped[date | None] = mapped_column(Date)
    installation_date: Mapped[date | None] = mapped_column(Date)
    capacity: Mapped[str | None] = mapped_column(String(80))
    warranty_until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("status IN ('OPERATIONAL','UNDER_MAINTENANCE','BREAKDOWN','RETIRED')", name="status_valid"),)


class MaintenanceSchedule(Base):
    __tablename__ = "maintenance_schedules"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    title: Mapped[str] = mapped_column(String(160))
    frequency_days: Mapped[int] = mapped_column(Integer)
    next_due: Mapped[date] = mapped_column(Date)
    checklist: Mapped[list | None] = mapped_column(JSONB)                 # list of step texts copied onto each generated work order
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    __table_args__ = (CheckConstraint("frequency_days > 0", name="frequency_positive"),)


class MaintenanceWorkOrder(Base):
    __tablename__ = "maintenance_work_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    kind: Mapped[str] = mapped_column(String(12))                       # PREVENTIVE | BREAKDOWN
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance_schedules.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="OPEN", server_default="OPEN")
    priority: Mapped[str] = mapped_column(String(8), default="MEDIUM", server_default="MEDIUM")
    description: Mapped[str] = mapped_column(Text)
    production_impact: Mapped[str | None] = mapped_column(Text)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    root_cause: Mapped[str | None] = mapped_column(Text)
    repair_notes: Mapped[str | None] = mapped_column(Text)
    raised_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    checklist: Mapped[list | None] = mapped_column(JSONB)                 # [{"text", "done", "note"}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("kind IN ('PREVENTIVE','BREAKDOWN')", name="kind_valid"),
        CheckConstraint("status IN ('OPEN','ASSIGNED','IN_PROGRESS','DONE','CLOSED')", name="status_valid"),
        CheckConstraint("priority IN ('LOW','MEDIUM','HIGH','CRITICAL')", name="priority_valid"),
        # the scheduler can run any number of times: one work order per schedule per due date
        Index("uq_work_order_schedule_due", "schedule_id", "due_date", unique=True, postgresql_where=text("schedule_id IS NOT NULL")),
    )


class DowntimeRecord(Base):
    __tablename__ = "downtime_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance_work_orders.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(200))
    __table_args__ = (CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ordered"),
                      Index("uq_downtime_open_per_equipment", "equipment_id", unique=True, postgresql_where=text("ended_at IS NULL")))


class CalibrationRecord(Base):
    __tablename__ = "calibration_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    calibrated_on: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    result: Mapped[str] = mapped_column(String(10))
    certificate_ref: Mapped[str | None] = mapped_column(String(120))
    performed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (CheckConstraint("result IN ('PASS','FAIL')", name="result_valid"), CheckConstraint("due_date > calibrated_on", name="dates_ordered"))


class SparePartUsage(Base):
    __tablename__ = "spare_part_usage"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("maintenance_work_orders.id"))
    part_name: Mapped[str] = mapped_column(String(160))
    qty: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"),)
