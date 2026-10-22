from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.vehicle import VehicleStatus
from app.vehicle_rules import (
    CONTAINER_SIZES,
    CREATE_DEFAULTS,
    DECIMAL_BOUNDS,
    EMISSION_STANDARDS,
    INTEGER_BOUNDS,
    FuelType,
    PowerSource,
    TransmissionType,
    VehicleType,
    validate_applicability,
)


class VehicleFields(BaseModel):
    license_plate: str | None = Field(None, max_length=20)
    vin: str | None = Field(None, min_length=17, max_length=17)
    brand: str | None = Field(None, min_length=1, max_length=100)
    model: str | None = Field(None, min_length=1, max_length=100)
    vehicle_type: VehicleType | None = None
    department: str | None = Field(None, min_length=1, max_length=200)
    color: str | None = Field(None, max_length=50)
    notes: str | None = None
    fuel_type: FuelType | None = None
    transmission: TransmissionType | None = None
    power_source: PowerSource | None = None
    emission_standard: str | None = None
    supported_container_sizes: list[str] | None = None
    purchase_date: date | None = None
    assigned_driver_id: UUID | None = None
    status: VehicleStatus | None = None
    year: int | None = Field(None, ge=1900, le=2100)
    axle_count: int | None = Field(None, ge=1, le=20)
    seats: int | None = Field(None, ge=1, le=100)
    engine_displacement_cc: int | None = Field(None, ge=1, le=100000)
    current_mileage: int | None = Field(None, ge=0, le=10000000)
    co2_emission_class: int | None = Field(None, ge=1, le=5)
    engine_power_hp: Decimal | None = Field(None, gt=0, le=10000, max_digits=8, decimal_places=2, allow_inf_nan=False)
    max_permissible_mass_kg: Decimal | None = Field(
        None, gt=0, le=1000000, max_digits=12, decimal_places=2, allow_inf_nan=False
    )
    empty_weight_kg: Decimal | None = Field(
        None, gt=0, le=1000000, max_digits=12, decimal_places=2, allow_inf_nan=False
    )
    payload_capacity_kg: Decimal | None = Field(
        None, gt=0, le=1000000, max_digits=12, decimal_places=2, allow_inf_nan=False
    )
    lifting_capacity_kg: Decimal | None = Field(
        None, gt=0, le=1000000, max_digits=12, decimal_places=2, allow_inf_nan=False
    )
    fuel_tank_capacity_l: Decimal | None = Field(
        None, gt=0, le=10000, max_digits=10, decimal_places=2, allow_inf_nan=False
    )
    adblue_capacity_l: Decimal | None = Field(
        None, gt=0, le=10000, max_digits=10, decimal_places=2, allow_inf_nan=False
    )
    cargo_length_m: Decimal | None = Field(None, gt=0, le=100, max_digits=7, decimal_places=3, allow_inf_nan=False)
    cargo_width_m: Decimal | None = Field(None, gt=0, le=100, max_digits=7, decimal_places=3, allow_inf_nan=False)
    cargo_height_m: Decimal | None = Field(None, gt=0, le=100, max_digits=7, decimal_places=3, allow_inf_nan=False)
    max_lift_height_m: Decimal | None = Field(None, gt=0, le=100, max_digits=7, decimal_places=3, allow_inf_nan=False)
    operating_hours: Decimal | None = Field(
        None, ge=0, le=1000000, max_digits=10, decimal_places=1, allow_inf_nan=False
    )
    purchase_price: Decimal | None = Field(
        None, ge=0, le=9999999999.99, max_digits=12, decimal_places=2, allow_inf_nan=False
    )

    model_config = {"extra": "forbid", "from_attributes": True}

    @field_validator("*", mode="before")
    @classmethod
    def normalize(cls, value, info):
        if isinstance(value, bool) and info.field_name in INTEGER_BOUNDS.keys() | DECIMAL_BOUNDS.keys():
            raise ValueError("errors.invalid_input")
        if isinstance(value, str):
            value = value.strip() or None
            if value is not None and info.field_name in ("vin", "license_plate"):
                value = value.upper()
        return value

    @field_validator("supported_container_sizes")
    @classmethod
    def containers(cls, value):
        if value is None:
            return None
        if any(v not in CONTAINER_SIZES for v in value):
            raise ValueError("error.vehicle_container_size")
        return [v for v in CONTAINER_SIZES if v in value] or None

    @field_validator("emission_standard")
    @classmethod
    def emissions(cls, value):
        if value is not None and value not in EMISSION_STANDARDS:
            raise ValueError("errors.invalid_input")
        return value


class VehicleCreate(VehicleFields):
    vehicle_type: VehicleType
    brand: str = Field(..., min_length=1, max_length=100)
    model: str = Field(..., min_length=1, max_length=100)
    department: str = Field(..., min_length=1, max_length=200)
    status: VehicleStatus = VehicleStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def creation_defaults(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            for key, value in CREATE_DEFAULTS.get(data.get("vehicle_type"), {}).items():
                data.setdefault(key, value)
        return data

    @model_validator(mode="after")
    def applicable(self):
        validate_applicability(self.vehicle_type, self.model_dump())
        if self.vehicle_type == VehicleType.FORKLIFT:
            if self.license_plate:
                raise ValueError("error.forklift_identifier_readonly")
        elif not self.license_plate or self.license_plate.startswith("PRIVATE-"):
            raise ValueError("error.road_plate_required")
        return self


class VehicleUpdate(VehicleFields):
    @model_validator(mode="after")
    def required_fields(self):
        for field in ("brand", "model", "department", "vehicle_type", "status", "license_plate"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError("error.vehicle_required_field")
        return self


class VehicleRead(VehicleFields):
    id: UUID
    license_plate: str
    vehicle_type: VehicleType
    brand: str
    model: str
    department: str
    status: VehicleStatus
    created_at: datetime
    updated_at: datetime


class VehicleFilter(BaseModel):
    q: str | None = None
    status: VehicleStatus | None = None
    brand: str | None = None
    fuel_type: FuelType | None = None
    vehicle_type: VehicleType | None = None
    department: str | None = None
