import enum
import uuid
from decimal import Decimal

from sqlalchemy import Date, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class MaintenanceType(str, enum.Enum):
    SCHEDULED_SERVICE = "scheduled_service"
    REPAIR = "repair"
    INSPECTION = "inspection"
    TIRE_CHANGE = "tire_change"
    BODY_REPAIR = "body_repair"
    RECALL = "recall"


class MaintenanceStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MaintenanceRecord(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "maintenance_records"

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[MaintenanceType] = mapped_column(Enum(MaintenanceType, name="maintenance_type"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What a planned service covers (engine oil, ...); only for scheduled_service. See app/maintenance_rules.py.
    service_item: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Extra work ticked off during the service (e.g. oil / air / fuel filter with an engine oil change).
    service_checklist: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[MaintenanceStatus] = mapped_column(
        Enum(MaintenanceStatus, name="maintenance_status"), default=MaintenanceStatus.SCHEDULED, nullable=False
    )
    scheduled_date: Mapped[str | None] = mapped_column(Date, nullable=True, index=True)
    completed_date: Mapped[str | None] = mapped_column(Date, nullable=True)
    mileage_at_service: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_service_mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_service_date: Mapped[str | None] = mapped_column(Date, nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    service_provider: Mapped[str | None] = mapped_column(String(300), nullable=True)
    performed_by: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    vehicle = relationship("Vehicle", foreign_keys=[vehicle_id], lazy="joined")
    creator = relationship("User", foreign_keys=[created_by])
