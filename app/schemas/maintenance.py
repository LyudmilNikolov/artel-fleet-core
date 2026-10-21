from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.maintenance import MaintenanceStatus, MaintenanceType


class MaintenanceCreate(BaseModel):
    vehicle_id: UUID
    type: MaintenanceType
    title: str = Field(..., max_length=300)
    description: str | None = None
    status: MaintenanceStatus = MaintenanceStatus.SCHEDULED
    scheduled_date: date | None = None
    completed_date: date | None = None
    mileage_at_service: int | None = Field(None, ge=0, le=10000000)
    next_service_mileage: int | None = None
    next_service_date: date | None = None
    cost: Decimal | None = Field(None, max_digits=12, decimal_places=2)
    service_provider: str | None = None
    performed_by: str | None = None
    # Confirms that a mileage below the vehicle's current one may overwrite it (adds a correction reading).
    overwrite_mileage: bool = False


class MaintenanceUpdate(BaseModel):
    """PATCH: omitted fields are kept. The vehicle cannot change; title, type and status cannot be null."""

    type: MaintenanceType | None = None
    title: str | None = Field(None, max_length=300)
    description: str | None = None
    status: MaintenanceStatus | None = None
    scheduled_date: date | None = None
    completed_date: date | None = None
    mileage_at_service: int | None = Field(None, ge=0, le=10000000)
    next_service_mileage: int | None = None
    next_service_date: date | None = None
    cost: Decimal | None = Field(None, max_digits=12, decimal_places=2)
    service_provider: str | None = None
    performed_by: str | None = None
    overwrite_mileage: bool = False

    @model_validator(mode="after")
    def _required_fields(self):
        for name in ("type", "title", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError("errors.invalid_input")
        return self


class MaintenanceRead(BaseModel):
    id: UUID
    vehicle_id: UUID
    type: MaintenanceType
    title: str
    description: str | None
    status: MaintenanceStatus
    scheduled_date: date | None
    completed_date: date | None
    mileage_at_service: int | None
    next_service_mileage: int | None
    next_service_date: date | None
    cost: Decimal | None
    service_provider: str | None
    performed_by: str | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
