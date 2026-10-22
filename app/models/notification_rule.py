"""Reminder configuration: global settings, one rule per notification source, and who receives it."""

import uuid
from datetime import time

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class NotificationSettings(Base, UUIDPrimaryKey, TimestampMixin):
    """Global reminder settings. There is at most one row (``singleton`` is unique and always true)."""

    __tablename__ = "notification_settings"
    __table_args__ = (CheckConstraint("singleton", name="notification_settings_single_row"),)

    singleton: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), unique=True)
    send_time: Mapped[time] = mapped_column(Time, nullable=False, server_default=text("'08:00'"))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'Europe/Sofia'"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class NotificationRule(Base, UUIDPrimaryKey, TimestampMixin):
    """Reminder rule of one source (see ``app.notification_rules``). A missing row means the code defaults."""

    __tablename__ = "notification_rules"
    __table_args__ = (
        CheckConstraint(
            "repeat_every_days IS NULL OR repeat_every_days >= 1", name="notification_rules_repeat_positive"
        ),
        CheckConstraint(
            "stop_after_overdue_days IS NULL OR stop_after_overdue_days >= 1", name="notification_rules_stop_positive"
        ),
        CheckConstraint("cardinality(milestone_days) BETWEEN 1 AND 10", name="notification_rules_milestone_count"),
    )

    source: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    milestone_days: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    repeat_every_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_after_overdue_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    send_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    recipients = relationship(
        "NotificationRuleRecipient", back_populates="rule", cascade="all, delete-orphan", lazy="raise"
    )


class NotificationRuleRecipient(Base, UUIDPrimaryKey):
    """One recipient of a rule. ``kind`` is a plain string so new kinds (for example the vehicle's driver) need no
    migration: ``user`` (user_id), ``role`` (role), ``external_email`` / ``external_telegram`` (address)."""

    __tablename__ = "notification_rule_recipients"
    __table_args__ = (
        CheckConstraint(
            "(kind <> 'user' OR user_id IS NOT NULL) AND (kind <> 'role' OR role IS NOT NULL)"
            " AND (kind NOT IN ('external_email', 'external_telegram') OR address IS NOT NULL)",
            name="notification_rule_recipients_target",
        ),
        Index(
            "uq_notification_recipient_user",
            "rule_id",
            "user_id",
            unique=True,
            postgresql_where=text("kind = 'user'"),
        ),
        Index("uq_notification_recipient_role", "rule_id", "role", unique=True, postgresql_where=text("kind = 'role'")),
        Index(
            "uq_notification_recipient_address",
            "rule_id",
            "kind",
            "address",
            unique=True,
            postgresql_where=text("address IS NOT NULL"),
        ),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notification_rules.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)

    rule = relationship("NotificationRule", back_populates="recipients", lazy="raise")
