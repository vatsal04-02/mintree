from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint,
                        func, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

NUM = Numeric(18, 4)


class QualitySpec(Base):
    __tablename__ = "quality_specs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    stage: Mapped[str] = mapped_column(String(12))              # INCOMING | IN_PROCESS | FINISHED
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE", server_default="ACTIVE")
    sampling_plan_id: Mapped[int | None] = mapped_column(ForeignKey("sampling_plans.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    tests: Mapped[list["QualitySpecTest"]] = relationship(order_by="QualitySpecTest.id", lazy="selectin")
    __table_args__ = (UniqueConstraint("item_id", "stage", "version_no"),
                      CheckConstraint("stage IN ('INCOMING','IN_PROCESS','FINISHED')", name="stage_valid"),
                      CheckConstraint("status IN ('ACTIVE','OBSOLETE')", name="status_valid"),
                      Index("uq_quality_spec_active", "item_id", "stage", unique=True, postgresql_where=text("status = 'ACTIVE'")))


class QualitySpecTest(Base):
    __tablename__ = "quality_spec_tests"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("quality_specs.id"))
    test_name: Mapped[str] = mapped_column(String(80))
    method: Mapped[str | None] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(20))
    min_value: Mapped[Decimal | None] = mapped_column(NUM)
    max_value: Mapped[Decimal | None] = mapped_column(NUM)
    is_pass_fail: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")   # visual/organoleptic tests
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    __table_args__ = (UniqueConstraint("spec_id", "test_name"),
                      CheckConstraint("min_value IS NULL OR max_value IS NULL OR min_value <= max_value", name="limits_ordered"),
                      CheckConstraint("is_pass_fail OR min_value IS NOT NULL OR max_value IS NOT NULL", name="has_acceptance_criteria"))


class Inspection(Base):
    __tablename__ = "inspections"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    stage: Mapped[str] = mapped_column(String(12))
    batch_id: Mapped[int] = mapped_column(ForeignKey("inventory_batches.id"))
    spec_id: Mapped[int] = mapped_column(ForeignKey("quality_specs.id"))
    status: Mapped[str] = mapped_column(String(12), default="PENDING", server_default="PENDING")
    decision: Mapped[str | None] = mapped_column(String(10))
    lot_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))              # lot size when the inspection was opened
    sample_size: Mapped[int | None] = mapped_column(Integer)                      # units to test, from the spec's sampling plan
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    request_id: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (CheckConstraint("status IN ('PENDING','IN_PROGRESS','DECIDED')", name="status_valid"),
                      CheckConstraint("decision IS NULL OR decision IN ('RELEASE','REJECT','HOLD')", name="decision_valid"),
                      CheckConstraint("(status = 'DECIDED') = (decision IS NOT NULL)", name="decided_consistent"),
                      Index("uq_inspection_open_per_batch_stage", "batch_id", "stage", unique=True, postgresql_where=text("status <> 'DECIDED'")),
                      Index("ix_inspections_batch", "batch_id", text("created_at DESC")))


class InspectionResult(Base):
    """Append-only: a retest adds a row; earlier results are retained (the latest per test counts)."""

    __tablename__ = "inspection_results"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"))
    test_name: Mapped[str] = mapped_column(String(80))
    value: Mapped[Decimal | None] = mapped_column(NUM)
    passed: Mapped[bool] = mapped_column(Boolean)               # computed server-side from the spec, never client-supplied
    observation: Mapped[str | None] = mapped_column(Text)
    tested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    tested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_inspection_results_insp", "inspection_id", "test_name", "id"),)


class Deviation(Base):
    __tablename__ = "deviations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(14), default="OPEN", server_default="OPEN")
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_batches.id"))
    root_cause: Mapped[str | None] = mapped_column(Text)
    raised_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actions: Mapped[list["CapaAction"]] = relationship(order_by="CapaAction.id", lazy="selectin")
    __table_args__ = (CheckConstraint("classification IN ('MINOR','MAJOR','CRITICAL')", name="class_valid"),
                      CheckConstraint("status IN ('OPEN','INVESTIGATION','CLOSED')", name="status_valid"),
                      CheckConstraint("status <> 'CLOSED' OR (root_cause IS NOT NULL AND closed_by IS NOT NULL)", name="closure_recorded"))


class CapaAction(Base):
    __tablename__ = "capa_actions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    deviation_id: Mapped[int] = mapped_column(ForeignKey("deviations.id"))
    kind: Mapped[str] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(Text)
    responsible_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="OPEN", server_default="OPEN")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (CheckConstraint("kind IN ('CORRECTIVE','PREVENTIVE')", name="kind_valid"),
                      CheckConstraint("status IN ('OPEN','DONE','VERIFIED')", name="status_valid"))
