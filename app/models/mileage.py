import enum
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Identity, Index, Integer, Text, false, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, UUIDPrimaryKey


class MileageSource(str, enum.Enum):
    MANUAL = "manual"
    OBD = "obd"
    GPS = "gps"


class MileageLog(Base, UUIDPrimaryKey):
    __tablename__ = "mileage_logs"
    __table_args__ = (Index("ix_mileage_logs_vehicle_seq", "vehicle_id", "seq"),)

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[MileageSource] = mapped_column(
        Enum(MileageSource, name="mileage_source"),
        default=MileageSource.MANUAL,
        nullable=False,
    )
    recorded_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Insertion order independent of timestamps (many rows can share one transaction time).
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    # A deliberate lower reading: it starts a new baseline; earlier readings stay as history.
    is_correction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())

    # Relationships
    vehicle = relationship("Vehicle", foreign_keys=[vehicle_id])
    recorder = relationship("User", foreign_keys=[recorded_by])
