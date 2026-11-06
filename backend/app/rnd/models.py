from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func,
                        text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

QTY4 = Numeric(18, 4)


class Formulation(Base):
    __tablename__ = "formulations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    product_item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))      # the BULK / FG item this formula makes
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    versions: Mapped[list["FormulationVersion"]] = relationship(order_by="FormulationVersion.version_no", lazy="selectin")


class FormulationVersion(Base):
    """APPROVED versions are immutable (DB trigger): content never changes silently; changes make a new version."""

    __tablename__ = "formulation_versions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    formulation_id: Mapped[int] = mapped_column(ForeignKey("formulations.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", server_default="DRAFT")
    batch_size: Mapped[Decimal] = mapped_column(QTY4)                         # output quantity the ingredient list yields
    process_instructions: Mapped[str | None] = mapped_column(Text)
    revision_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_date: Mapped[date | None] = mapped_column(Date)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ingredients: Mapped[list["FormulationIngredient"]] = relationship(order_by="FormulationIngredient.line_no", lazy="selectin")
    __table_args__ = (
        UniqueConstraint("formulation_id", "version_no"),
        CheckConstraint("status IN ('DRAFT','SUBMITTED','APPROVED','OBSOLETE')", name="status_valid"),
        CheckConstraint("batch_size > 0", name="batch_size_positive"),
        CheckConstraint("status <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)", name="approval_recorded"),
        # one open (draft/submitted) version per formulation at a time, one approved (current) version at a time
        Index("uq_formulation_open_version", "formulation_id", unique=True, postgresql_where=text("status IN ('DRAFT','SUBMITTED')")),
        Index("uq_formulation_current_version", "formulation_id", unique=True, postgresql_where=text("status = 'APPROVED'")),
    )


class FormulationIngredient(Base):
    __tablename__ = "formulation_ingredients"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("formulation_versions.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY4)                                  # per batch_size
    percentage: Mapped[Decimal] = mapped_column(Numeric(9, 5))
    __table_args__ = (UniqueConstraint("version_id", "line_no", name="uq_formulation_ingredients_line"),
                      UniqueConstraint("version_id", "item_id", name="uq_formulation_ingredients_item"),
                      CheckConstraint("qty > 0 AND percentage > 0 AND percentage <= 100", name="positive"))


class LabTrial(Base):
    __tablename__ = "lab_trials"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("formulation_versions.id"))
    trial_no: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[Decimal] = mapped_column(QTY4)
    status: Mapped[str] = mapped_column(String(12), default="PLANNED", server_default="PLANNED")
    observations: Mapped[str | None] = mapped_column(Text)
    stability_note: Mapped[str | None] = mapped_column(Text)
    researcher_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    concluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("version_id", "trial_no"), CheckConstraint("quantity > 0", name="qty_positive"),
                      CheckConstraint("status IN ('PLANNED','IN_PROGRESS','PASSED','FAILED')", name="status_valid"))
