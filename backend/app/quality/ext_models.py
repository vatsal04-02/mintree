"""Sampling plans (how many units to test from a lot) and Certificates of Analysis (immutable result snapshots of a released inspection)."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.mixins import AuditFieldsMixin

SAMPLING_METHODS = ("ALL", "FIXED", "PERCENT", "SQRT_PLUS_ONE")


class SamplingPlan(AuditFieldsMixin, Base):
    __tablename__ = "sampling_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(14))
    fixed_qty: Mapped[int | None] = mapped_column(Integer)
    percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    min_samples: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    max_samples: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    __table_args__ = (
        CheckConstraint("method IN ('ALL','FIXED','PERCENT','SQRT_PLUS_ONE')", name="method_valid"),
        CheckConstraint("(method <> 'FIXED') OR (fixed_qty IS NOT NULL AND fixed_qty > 0)", name="fixed_needs_qty"),
        CheckConstraint("(method <> 'PERCENT') OR (percent IS NOT NULL AND percent > 0 AND percent <= 100)", name="percent_needs_value"),
        CheckConstraint("min_samples >= 1 AND (max_samples IS NULL OR max_samples >= min_samples)", name="bounds_valid"),
    )


class CertificateOfAnalysis(Base):
    __tablename__ = "certificates_of_analysis"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), unique=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("inventory_batches.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    status: Mapped[str] = mapped_column(String(8), default="ISSUED", server_default="ISSUED")
    snapshot: Mapped[dict] = mapped_column(JSONB)            # item, batch, spec version, tests with limits, values, pass/fail as at issue: a frozen document, not a live view
    issued_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("status IN ('ISSUED','REVOKED')", name="status_valid"),
                      CheckConstraint("(status = 'REVOKED') = (revoked_at IS NOT NULL AND revoke_reason IS NOT NULL AND revoked_by IS NOT NULL)", name="revocation_recorded"))
