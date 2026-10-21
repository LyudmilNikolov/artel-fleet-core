from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.vehicle import BodyType, FuelType, TransmissionType, VehicleStatus


class VehicleCreate(BaseModel):
    license_plate: str = Field(..., max_length=20)
    vin: str = Field(..., min_length=17, max_length=17)
    brand: str = Field(..., max_length=100)
    model: str = Field(..., max_length=100)
    year: int = Field(..., ge=1900, le=2100)
    color: str | None = None
    body_type: BodyType
    fuel_type: FuelType
    engine_volume: float | None = None
    transmission: TransmissionType
    seats: int | None = Field(None, ge=1, le=100)
    purchase_date: date | None = None
    purchase_price: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    status: VehicleStatus = VehicleStatus.ACTIVE
    department: str | None = None
    notes: str | None = None

    @field_validator("vin")
    @classmethod
    def validate_vin(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("license_plate")
    @classmethod
    def validate_plate(cls, v: str) -> str:
        return v.upper().strip()


class VehicleUpdate(BaseModel):
    license_plate: str | None = None
    vin: str | None = None
    brand: str | None = None
    model: str | None = None
    year: int | None = Field(None, ge=1900, le=2100)
    color: str | None = None
    body_type: BodyType | None = None
    fuel_type: FuelType | None = None
    engine_volume: float | None = None
    transmission: TransmissionType | None = None
    seats: int | None = None
    purchase_date: date | None = None
    purchase_price: Decimal | None = Field(None, max_digits=12, decimal_places=2)
    status: VehicleStatus | None = None
    assigned_driver_id: UUID | None = None
    department: str | None = None
    notes: str | None = None


class VehicleRead(BaseModel):
    id: UUID
    license_plate: str
    vin: str
    brand: str
    model: str
    year: int
    color: str | None
    body_type: BodyType
    fuel_type: FuelType
    engine_volume: float | None
    transmission: TransmissionType
    seats: int | None
    purchase_date: date | None
    purchase_price: Decimal | None
    current_mileage: int
    status: VehicleStatus
    assigned_driver_id: UUID | None
    department: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VehicleFilter(BaseModel):
    q: str | None = None
    status: VehicleStatus | None = None
    brand: str | None = None
    fuel_type: FuelType | None = None
    body_type: BodyType | None = None
    department: str | None = None
