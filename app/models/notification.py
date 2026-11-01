"""Notification and notification preferences models."""

import enum

from sqlalchemy import UUID, Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class NotificationType(str, enum.Enum):
    MAINTENANCE_REMINDER = "maintenance_reminder"
    CONTRACT_EXPIRY = "contract_expiry"
    LICENSE_EXPIRY = "license_expiry"
    MEDICAL_EXPIRY = "medical_expiry"
    MILEAGE_ALERT = "mileage_alert"
    BUDGET_ALERT = "budget_alert"
    SYSTEM = "system"


class Notification(UUIDPrimaryKey, Base):
    __tablename__ = "notifications"

    user_id: Mapped[str] = mapped_column(UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[NotificationType] = mapped_column(Enum(NotificationType), nullable=False)
    # Translation keys and values, so the message can be shown in the reader's current language. Rows without
    # them (older ones) are shown with ``title`` / ``message`` as stored.
    title_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(UUID, nullable=True)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationPreference(UUIDPrimaryKey, Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    preferences: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
