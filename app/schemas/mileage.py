from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.mileage import MileageSource


class MileageCreate(BaseModel):
    vehicle_id: UUID
    value: int = Field(..., ge=0, le=10000000)
    source: MileageSource = MileageSource.MANUAL
    notes: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def reject_bool(cls, value):
        if isinstance(value, bool):
            raise ValueError("errors.invalid_input")
        return value


class BulkMileageCreate(BaseModel):
    entries: list[MileageCreate]


class MileageRead(BaseModel):
    id: UUID
    vehicle_id: UUID
    recorded_by: UUID | None
    value: int
    source: MileageSource
    recorded_at: datetime
    notes: str | None
    is_correction: bool

    model_config = {"from_attributes": True}
