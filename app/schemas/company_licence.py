"""Schemas for the company licence (international haulage licence of the company)."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CompanyLicenceInput(BaseModel):
    """Create, edit and renew all take the same fields; the number, holder and both dates are required."""

    model_config = ConfigDict(extra="forbid")

    licence_number: str = Field(max_length=60)
    holder: str = Field(max_length=300)
    start_date: date
    end_date: date
    issued_by: str | None = Field(None, max_length=300)
    notes: str | None = None

    @field_validator("licence_number", "holder", "issued_by", "notes", mode="before")
    @classmethod
    def _trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("licence_number", "holder")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("error.company_licence_text_required")
        return value

    @field_validator("issued_by", "notes")
    @classmethod
    def _blank_to_none(cls, value):
        return value or None

    @model_validator(mode="after")
    def _period(self):
        if self.end_date < self.start_date:
            raise ValueError("error.compliance_end_before_start")
        return self
