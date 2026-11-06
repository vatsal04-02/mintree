"""Physical shipping detail: packages in a shipment, delivery attempts, proof of delivery."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ATTEMPT_OUTCOMES = ("DELIVERED", "FAILED", "REFUSED", "RESCHEDULED")


class ShipmentPackage(Base):
    __tablename__ = "shipment_packages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"))
    package_no: Mapped[int] = mapped_column(Integer)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    length_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    width_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    contents: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (UniqueConstraint("shipment_id", "package_no", name="uq_shipment_packages_no"),
                      CheckConstraint("weight_kg IS NULL OR weight_kg > 0", name="weight_positive"),
                      CheckConstraint("(length_cm IS NULL OR length_cm > 0) AND (width_cm IS NULL OR width_cm > 0) AND (height_cm IS NULL OR height_cm > 0)", name="dims_positive"))


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(8), default="MANUAL", server_default="MANUAL")     # MANUAL (a user recorded it) | COURIER (webhook)
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("shipment_id", "attempt_no", name="uq_delivery_attempts_no"),
                      CheckConstraint("outcome IN ('DELIVERED','FAILED','REFUSED','RESCHEDULED')", name="outcome_valid"),
                      CheckConstraint("source IN ('MANUAL','COURIER')", name="source_valid"),
                      CheckConstraint("outcome = 'DELIVERED' OR reason IS NOT NULL", name="failure_needs_reason"),
                      Index("ix_delivery_attempts_shipment", "shipment_id", "attempt_no"))


class ProofOfDelivery(Base):
    __tablename__ = "proofs_of_delivery"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), unique=True)
    delivery_attempt_id: Mapped[int] = mapped_column(ForeignKey("delivery_attempts.id"), unique=True)
    received_by: Mapped[str] = mapped_column(String(120))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))            # signed slip / photo kept in document storage
    remarks: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
