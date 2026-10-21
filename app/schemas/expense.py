import datetime as dt
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

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
    fuel_liters: float | None = None
    fuel_price_per_liter: Decimal | None = Field(None, max_digits=8, decimal_places=2)
    fuel_type: str | None = None
    mileage_at_refuel: int | None = None


class ExpenseUpdate(BaseModel):
    category: ExpenseCategory | None = None
    amount: Decimal | None = Field(None, max_digits=12, decimal_places=2)
    currency: Currency = Currency.EUR
    date: dt.date | None = None
    description: str | None = None
    vendor: str | None = None
    fuel_liters: float | None = None
    fuel_price_per_liter: Decimal | None = Field(None, max_digits=8, decimal_places=2)
    fuel_type: str | None = None
    mileage_at_refuel: int | None = None


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
    fuel_liters: float | None
    fuel_price_per_liter: Decimal | None
    fuel_type: str | None
    mileage_at_refuel: int | None
    created_by: UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}
