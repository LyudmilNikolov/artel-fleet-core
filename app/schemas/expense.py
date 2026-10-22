import datetime as dt
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.expense import Currency, ExpenseCategory


class ExpenseCreate(BaseModel):
    vehicle_id: UUID
    driver_id: UUID | None = None
    category: ExpenseCategory
    amount: Decimal = Field(..., ge=0, max_digits=12, decimal_places=2)
    currency: Currency = Currency.EUR
    date: dt.date
    description: str | None = None
    vendor: str | None = None
    fuel_liters: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2, allow_inf_nan=False)
    # Derived from amount / liters whenever liters are given; a value sent with liters is ignored.
    fuel_price_per_liter: Decimal | None = Field(None, max_digits=8, decimal_places=2)
    fuel_type: str | None = None
    mileage_at_refuel: int | None = Field(None, ge=0, le=10000000)
    # Only for document categories (civil liability, CASCO, inspection, tachograph): when given, the
    # linked Documents & Deadlines entry is created too. Both dates are needed together.
    valid_from: dt.date | None = None
    valid_until: dt.date | None = None
    policy_number: str | None = Field(None, max_length=100)
    # Confirms that a mileage below the vehicle's current one may overwrite it (adds a correction reading).
    overwrite_mileage: bool = False


class ExpenseUpdate(BaseModel):
    category: ExpenseCategory | None = None
    amount: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    currency: Currency = Currency.EUR
    date: dt.date | None = None
    description: str | None = None
    vendor: str | None = None
    fuel_liters: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2, allow_inf_nan=False)
    # Derived from amount / liters whenever liters are given; a value sent with liters is ignored.
    fuel_price_per_liter: Decimal | None = Field(None, max_digits=8, decimal_places=2)
    fuel_type: str | None = None
    mileage_at_refuel: int | None = Field(None, ge=0, le=10000000)
    overwrite_mileage: bool = False

    @model_validator(mode="after")
    def _required_fields(self):
        for name in ("category", "amount", "date"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError("errors.invalid_input")
        return self


class ExpenseRead(BaseModel):
    id: UUID
    vehicle_id: UUID
    driver_id: UUID | None
    category: ExpenseCategory
    amount: Decimal
    currency: Currency
    date: dt.date
    description: str | None
    vendor: str | None
    fuel_liters: Decimal | None
    fuel_price_per_liter: Decimal | None
    fuel_type: str | None
    mileage_at_refuel: int | None
    compliance_document_id: UUID | None
    created_by: UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}
