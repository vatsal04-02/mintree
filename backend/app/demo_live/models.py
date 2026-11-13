"""Control-plane tables for Demo Live Mode (migration 0027). They hold no business data and are empty in production."""
from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, SmallInteger, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DemoLiveState(Base):
    __tablename__ = "demo_live_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_demo_live_state_singleton"), CheckConstraint("speed IN (1, 3)", name="ck_demo_live_state_speed"))
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    paused: Mapped[bool] = mapped_column(Boolean, server_default=false())
    speed: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counters: Mapped[dict] = mapped_column(JSON, server_default="{}")
    anchor_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DemoLiveEvent(Base):
    __tablename__ = "demo_live_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(20), server_default="demo-live")
    permission: Mapped[str | None] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DemoLiveTrigger(Base):
    __tablename__ = "demo_live_triggers"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(30))
    requested_by: Mapped[int | None] = mapped_column(BigInteger)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(String(200))


class DemoScenario(Base):
    """State of the scripted order-to-cash scenario (migration 0029). `ctx` carries the ids the steps pass to each other; `log` is what the presenter sees."""
    __tablename__ = "demo_scenario"
    __table_args__ = (CheckConstraint("id = 1", name="ck_demo_scenario_singleton"), CheckConstraint("status IN ('idle','running','paused','done','failed')", name="ck_demo_scenario_status"),
                      CheckConstraint("speed IN (1, 3)", name="ck_demo_scenario_speed"))
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    status: Mapped[str] = mapped_column(String(10), server_default="idle")
    step: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    speed: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    ctx: Mapped[dict] = mapped_column(JSON, server_default="{}")
    log: Mapped[list] = mapped_column(JSON, server_default="[]")
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
