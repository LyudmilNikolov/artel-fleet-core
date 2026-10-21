"""Schemas for per-vehicle compliance documents (Документи и срокове)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.vehicle_rules import ComplianceType

ComplianceStatus = Literal["valid", "expiring_soon", "expired"]
Amount = Decimal | None


def _amount_field():
    return Field(None, ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)


class _TextFields(BaseModel):
    provider: str | None = Field(None, max_length=300)
    policy_number: str | None = Field(None, max_length=100)
    notes: str | None = None

    @field_validator("provider", "policy_number", "notes", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


def _check_period(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("error.compliance_end_before_start")


class ComplianceCreate(_TextFields):
    vehicle_id: UUID
    document_type: ComplianceType
    start_date: date
    end_date: date
    amount: Amount = _amount_field()

    @model_validator(mode="after")
    def _period(self):
        _check_period(self.start_date, self.end_date)
        return self


class ComplianceRenew(_TextFields):
    """A new validity period for the same vehicle and type; the source entry is untouched."""

    start_date: date
    end_date: date
    amount: Amount = _amount_field()

    @model_validator(mode="after")
    def _period(self):
        _check_period(self.start_date, self.end_date)
        return self


class ComplianceUpdate(_TextFields):
    """PATCH: omitted fields are kept; explicit null clears optional fields. Vehicle and type are fixed."""

    start_date: date | None = None
    end_date: date | None = None
    amount: Amount = _amount_field()

    @model_validator(mode="after")
    def _required_dates(self):
        for name in ("start_date", "end_date"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError("error.compliance_date_required")
        _check_period(self.start_date, self.end_date)
        return self


class ComplianceRead(BaseModel):
    id: UUID
    vehicle_id: UUID
    document_type: str
    start_date: date
    end_date: date
    amount: Decimal | None
    provider: str | None
    policy_number: str | None
    notes: str | None
    renewed_from_id: UUID | None
    expense_id: UUID | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    status: ComplianceStatus
    days_left: int
    is_current: bool
    applicable: bool

    model_config = {"from_attributes": True}


class ComplianceTypeOption(BaseModel):
    code: str
    label: str
