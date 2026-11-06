"""Bookkeeping for the demo seeder (scripts/demo). Empty in every non-demo database; it holds no business data."""
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DemoSeedStep(Base):
    """One row per completed seeding step (and a `__marker__` row proving the database was initialised as a demo database)."""

    __tablename__ = "demo_seed_steps"
    step_key: Mapped[str] = mapped_column(String(60), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    detail: Mapped[dict | None] = mapped_column(JSONB)       # ids produced by the step, so later steps and `verify` can find them after a resume
