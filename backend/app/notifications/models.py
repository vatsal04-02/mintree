from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
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


class NotificationPreference(Base):
    """A user's choice to mute a notification kind on a channel. Absence of a row = enabled. HIGH/CRITICAL priorities are never muted (see notify())."""

    __tablename__ = "notification_preferences"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(60), primary_key=True)
    channel: Mapped[str] = mapped_column(String(8), primary_key=True, default="IN_APP", server_default="IN_APP")
    enabled: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("channel IN ('IN_APP')", name="channel_supported"),)    # no e-mail/SMS/push integration exists: do not store preferences for channels that would never deliver
