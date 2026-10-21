from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.contract import ContractStatus, ContractType, PaymentFrequency


class ContractCreate(BaseModel):
    vehicle_id: UUID
    type: ContractType
    contractor: str = Field(..., max_length=300)
    contract_number: str | None = None
    start_date: date
    end_date: date
    amount: Decimal | None = Field(None, max_digits=12, decimal_places=2)
    payment_frequency: PaymentFrequency = PaymentFrequency.ONE_TIME
    status: ContractStatus = ContractStatus.ACTIVE
    auto_renew: bool = False
    notes: str | None = None


class ContractUpdate(BaseModel):
    contractor: str | None = None
    contract_number: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    amount: Decimal | None = Field(None, max_digits=12, decimal_places=2)
    payment_frequency: PaymentFrequency | None = None
    status: ContractStatus | None = None
    auto_renew: bool | None = None
    notes: str | None = None


class ContractRead(BaseModel):
    id: UUID
    vehicle_id: UUID
    type: ContractType
    contractor: str
    contract_number: str | None
    start_date: date
    end_date: date
    amount: Decimal | None
    payment_frequency: PaymentFrequency
    status: ContractStatus
    auto_renew: bool
    notes: str | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
