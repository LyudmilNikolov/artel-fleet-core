from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.maintenance import MaintenanceStatus, MaintenanceType


class MaintenanceCreate(BaseModel):
    vehicle_id: UUID
    type: MaintenanceType
    title: str = Field(..., max_length=300)
    description: str | None = None
    status: MaintenanceStatus = MaintenanceStatus.SCHEDULED
    scheduled_date: date | None = None
    mileage_at_service: int | None = None
    next_service_mileage: int | None = None
    next_service_date: date | None = None
    cost: Decimal | None = Field(None, max_digits=12, decimal_places=2)
    service_provider: str | None = None
    performed_by: str | None = None


class MaintenanceUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: MaintenanceStatus | None = None
    scheduled_date: date | None = None
    completed_date: date | None = None
    mileage_at_service: int | None = None
    next_service_mileage: int | None = None
    next_service_date: date | None = None
    cost: Decimal | None = Field(None, max_digits=12, decimal_places=2)
    service_provider: str | None = None
    performed_by: str | None = None


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
