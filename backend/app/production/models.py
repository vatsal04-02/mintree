from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

QTY = Numeric(18, 3)
QTY4 = Numeric(18, 4)
MONEY = Numeric(18, 2)


class Bom(Base):
    __tablename__ = "boms"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    output_item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    output_qty: Mapped[Decimal] = mapped_column(QTY4)
    formulation_version_id: Mapped[int | None] = mapped_column(ForeignKey("formulation_versions.id"))
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", server_default="DRAFT")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lines: Mapped[list["BomLine"]] = relationship(order_by="BomLine.line_no", lazy="selectin")
    __table_args__ = (UniqueConstraint("output_item_id", "version_no"), CheckConstraint("status IN ('DRAFT','ACTIVE','OBSOLETE')", name="status_valid"),
                      CheckConstraint("output_qty > 0", name="output_positive"),
                      CheckConstraint("status <> 'ACTIVE' OR approved_by IS NOT NULL", name="active_is_approved"),
                      Index("uq_bom_active", "output_item_id", unique=True, postgresql_where=text("status = 'ACTIVE'")))


class BomLine(Base):
    __tablename__ = "bom_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY4)
    __table_args__ = (UniqueConstraint("bom_id", "line_no", name="uq_bom_lines_line"), UniqueConstraint("bom_id", "item_id", name="uq_bom_lines_item"),
                      CheckConstraint("qty > 0", name="qty_positive"))


class ProductionOrder(Base):
    __tablename__ = "production_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))                          # pinned: exact BOM used
    formulation_version_id: Mapped[int | None] = mapped_column(ForeignKey("formulation_versions.id"))   # pinned: exact formula used
    output_item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    planned_qty: Mapped[Decimal] = mapped_column(QTY)
    planned_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="PLANNED", server_default="PLANNED")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    actual_qty: Mapped[Decimal | None] = mapped_column(QTY)
    yield_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    wip_cost: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")      # cost currently sitting in WIP
    input_cost: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    output_batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_id: Mapped[str | None] = mapped_column(String(64))
    materials: Mapped[list["ProductionMaterial"]] = relationship(order_by="ProductionMaterial.line_no", lazy="selectin")
    stages: Mapped[list["ProductionStage"]] = relationship(order_by="ProductionStage.seq", lazy="selectin")
    __table_args__ = (CheckConstraint("status IN ('PLANNED','RELEASED','IN_PROGRESS','COMPLETED','CANCELLED')", name="status_valid"),
                      CheckConstraint("planned_qty > 0", name="planned_positive"), CheckConstraint("wip_cost >= 0", name="wip_non_negative"),
                      Index("ix_production_orders_status", "status", "id"))


class ProductionMaterial(Base):
    __tablename__ = "production_materials"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("production_orders.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    required_qty: Mapped[Decimal] = mapped_column(QTY)
    issued_qty: Mapped[Decimal] = mapped_column(QTY, default=0, server_default="0")
    __table_args__ = (UniqueConstraint("order_id", "line_no", name="uq_production_materials_line"), UniqueConstraint("order_id", "item_id", name="uq_production_materials_item"),
                      CheckConstraint("issued_qty >= 0 AND issued_qty <= required_qty", name="issued_within_required"))


class ProductionStage(Base):
    __tablename__ = "production_stages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("production_orders.id"))
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12), default="PENDING", server_default="PENDING")
    equipment_id: Mapped[int | None] = mapped_column(BigInteger)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parameters: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("order_id", "seq", name="uq_production_stages_seq"), CheckConstraint("status IN ('PENDING','IN_PROGRESS','DONE')", name="status_valid"))


class ProductionConsumption(Base):
    """Batch genealogy: which exact lots (and cost) went into which order."""

    __tablename__ = "production_consumptions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("production_orders.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    cost: Mapped[Decimal] = mapped_column(MONEY)
    __table_args__ = (Index("ix_production_consumptions_batch", "batch_id"), Index("ix_production_consumptions_order", "order_id"), CheckConstraint("qty > 0", name="qty_positive"))


class WastageRecord(Base):
    __tablename__ = "wastage_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("production_orders.id"))
    category: Mapped[str] = mapped_column(String(30))
    qty: Mapped[Decimal] = mapped_column(QTY)
    reason: Mapped[str] = mapped_column(Text)
    responsible_department: Mapped[str | None] = mapped_column(String(60))
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"),)


class ProcessCheck(Base):
    """In-process quality reading taken during a stage. Append-only (trigger); `passed` is computed from the limits when given, otherwise stated by the inspector.
    A stage cannot be completed while the latest reading of any parameter in it has failed."""

    __tablename__ = "process_checks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("production_orders.id"))
    stage_seq: Mapped[int] = mapped_column(Integer)
    parameter: Mapped[str] = mapped_column(String(80))
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    passed: Mapped[bool] = mapped_column(Boolean)
    observation: Mapped[str | None] = mapped_column(Text)
    corrective_action: Mapped[str | None] = mapped_column(Text)
    inspector_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("passed OR corrective_action IS NOT NULL", name="failure_needs_corrective_action"),
                      Index("ix_process_checks_order", "order_id", "stage_seq", "parameter", "id"))
