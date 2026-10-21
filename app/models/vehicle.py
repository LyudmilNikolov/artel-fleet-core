import enum
import uuid
from decimal import Decimal

from sqlalchemy import Date, Enum, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class BodyType(str, enum.Enum):
    SEDAN = "sedan"
    SUV = "suv"
    TRUCK = "truck"
    VAN = "van"
    BUS = "bus"
    MINIVAN = "minivan"
    PICKUP = "pickup"


class FuelType(str, enum.Enum):
    GASOLINE = "gasoline"
    DIESEL = "diesel"
    GAS = "gas"
    ELECTRIC = "electric"
    HYBRID = "hybrid"


class TransmissionType(str, enum.Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    CVT = "cvt"
    ROBOT = "robot"


class VehicleStatus(str, enum.Enum):
    ACTIVE = "active"
    IN_MAINTENANCE = "in_maintenance"
    DECOMMISSIONED = "decommissioned"
    RESERVED = "reserved"


class Vehicle(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "vehicles"

    license_plate: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    vin: Mapped[str] = mapped_column(String(17), unique=True, nullable=False, index=True)
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    body_type: Mapped[BodyType] = mapped_column(Enum(BodyType, name="body_type"), nullable=False)
    fuel_type: Mapped[FuelType] = mapped_column(Enum(FuelType, name="fuel_type"), nullable=False)
    engine_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    transmission: Mapped[TransmissionType] = mapped_column(
        Enum(TransmissionType, name="transmission_type"), nullable=False
    )
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purchase_date: Mapped[str | None] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    current_mileage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[VehicleStatus] = mapped_column(
        Enum(VehicleStatus, name="vehicle_status"),
        default=VehicleStatus.ACTIVE,
        nullable=False,
    )
    assigned_driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    assigned_driver = relationship("Driver", back_populates="assigned_vehicles", foreign_keys=[assigned_driver_id])

    def __repr__(self) -> str:
        return f"<Vehicle {self.license_plate} {self.brand} {self.model}>"
