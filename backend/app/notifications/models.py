from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserNotification(Base):
    """Per-user notification centre entry, written in the same transaction as the business change that causes it
    (so a rolled-back change never notifies). Distinct from `notifications`, the event-consumer effect table."""

    __tablename__ = "user_notifications"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(200))         # in-app route, e.g. /procurement/purchase-orders/12
    priority: Mapped[str] = mapped_column(String(8), default="NORMAL", server_default="NORMAL")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_user_notifications_user", "user_id", "read_at", "id"),)
