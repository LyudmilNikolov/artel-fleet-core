from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.driver import DriverStatus


class DriverCreate(BaseModel):
    full_name: str = Field(..., max_length=255)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=255)
    hire_date: date | None = None
    department: str | None = None
    status: DriverStatus = DriverStatus.ACTIVE
    user_id: UUID | None = None
    notes: str | None = None


class DriverUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    hire_date: date | None = None
    department: str | None = None
    status: DriverStatus | None = None
    notes: str | None = None


class DriverDocumentAlerts(BaseModel):
    """Current documents of a driver that are expired or expiring soon."""

    expired: int = 0
    expiring_soon: int = 0


class DriverRead(BaseModel):
    id: UUID
    user_id: UUID | None
    full_name: str
    phone: str | None
    email: str | None
    hire_date: date | None
    department: str | None
    status: DriverStatus
    notes: str | None
    created_at: datetime
    updated_at: datetime
    document_alerts: DriverDocumentAlerts | None = None

    model_config = {"from_attributes": True}
