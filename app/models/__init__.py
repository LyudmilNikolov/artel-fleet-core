import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


# Import all models here so Alembic can discover them
from app.models.user import User  # noqa: E402, F401
from app.models.driver import Driver  # noqa: E402, F401
from app.models.driver_document import DriverDocument  # noqa: E402, F401
from app.models.vehicle import Vehicle  # noqa: E402, F401
from app.models.mileage import MileageLog  # noqa: E402, F401
from app.models.document import Document  # noqa: E402, F401
from app.models.maintenance import MaintenanceRecord  # noqa: E402, F401
from app.models.expense import Expense  # noqa: E402, F401
from app.models.compliance import ComplianceDocument  # noqa: E402, F401
from app.models.audit_log import AuditLog  # noqa: E402, F401
from app.models.notification import Notification, NotificationPreference  # noqa: E402, F401
