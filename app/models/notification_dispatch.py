"""Reminder dispatch state: per-document stops, the delivery log (also the idempotency guard) and daily runs."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class NotificationStop(Base, UUIDPrimaryKey):
    """Reminders for one document were stopped (for everyone, on every channel).

    ``end_date`` is a snapshot: the stop only applies while the document still has that end date, so a renewal or
    an edited end date starts reminders again by itself. A resumed stop keeps its row (``resumed_at``).
    """

    __tablename__ = "notification_stops"
    __table_args__ = (
        Index(
            "uq_notification_stops_active",
            "entity_type",
            "entity_id",
            "end_date",
            unique=True,
            postgresql_where=text("resumed_at IS NULL"),
        ),
        Index("ix_notification_stops_entity", "entity_type", "entity_id"),
    )

    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    stopped_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    stopped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resumed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationDelivery(Base, UUIDPrimaryKey, TimestampMixin):
    """One reminder to one recipient on one channel for one schedule point of one document entry.

    The unique key makes sending idempotent: a row is claimed (``pending``) before the message goes out and ends as
    ``sent`` or ``failed``. A failed row is retried at the next run; a stale ``pending`` row (a crashed run) too.
    ``error`` is a short code, never an exception text (those can contain credentials).
    """

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "end_date",
            "channel",
            "recipient",
            "point",
            name="uq_notification_deliveries_point",
        ),
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="notification_deliveries_status"),
        Index("ix_notification_deliveries_entity", "entity_type", "entity_id"),
    )

    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)  # email address or Telegram chat id
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    point: Mapped[int] = mapped_column(Integer, nullable=False)  # days left the schedule point stands for
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationRun(Base, UUIDPrimaryKey):
    """The daily send of one source finished for a local date (guards "once per day")."""

    __tablename__ = "notification_runs"
    __table_args__ = (UniqueConstraint("run_date", "source", name="uq_notification_runs_day_source"),)

    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sent: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
