"""Schemas for per-driver tracked documents (Лицензи и документи)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.driver_document_rules import DriverDocumentType, normalize_categories

DriverDocumentStatus = Literal["valid", "expiring_soon", "expired", "no_expiry"]


class _Fields(BaseModel):
    document_number: str | None = Field(None, max_length=100)
    categories: str | None = Field(None, max_length=50)
    notes: str | None = None

    @field_validator("document_number", "categories", "notes", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("categories", mode="after")
    @classmethod
    def _normalize_categories(cls, value):
        return normalize_categories(value)


def _check_period(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("error.driver_document_end_before_start")


class DriverDocumentCreate(_Fields):
    driver_id: UUID
    document_type: DriverDocumentType
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _period(self):
        _check_period(self.start_date, self.end_date)
        return self


class DriverDocumentRenew(_Fields):
    """A new validity period for the same driver and type; the source entry is untouched.

    Number and categories not sent are inherited from the source entry.
    """

    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _period(self):
        _check_period(self.start_date, self.end_date)
        return self


class DriverDocumentUpdate(_Fields):
    """PATCH: omitted fields are kept; explicit null clears optional fields. Driver and type are fixed."""

    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _period(self):
        _check_period(self.start_date, self.end_date)
        return self


class DriverDocumentRead(BaseModel):
    id: UUID
    driver_id: UUID
    document_type: str
    document_number: str | None
    categories: str | None
    start_date: date | None
    end_date: date | None
    notes: str | None
    renewed_from_id: UUID | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    status: DriverDocumentStatus
    days_left: int | None
    is_current: bool

    model_config = {"from_attributes": True}


class DriverDocumentTypeOption(BaseModel):
    code: str
    label: str
    number_required: bool
    has_categories: bool
