"""Report amounts serialize as exact decimal strings through Pydantic."""

from decimal import Decimal

from pydantic import BaseModel


class TCOReportRow(BaseModel):
    id: str
    license_plate: str
    brand: str
    model: str
    year: int
    purchase_price: Decimal | None
    total_expenses: Decimal
    tco: Decimal


class FuelReportRow(BaseModel):
    id: str
    license_plate: str
    brand: str
    model: str
    total_liters: float
    total_cost: Decimal
    refuel_count: int
    avg_price: Decimal


class CategoryTotal(BaseModel):
    category: str
    total: Decimal
    count: int


class MonthlyTotal(BaseModel):
    month: str
    total: Decimal
    count: int


class ExpenseAnalysis(BaseModel):
    by_category: list[CategoryTotal]
    monthly: list[MonthlyTotal]
    grand_total: Decimal
    total_count: int


class MaintenanceReportRow(BaseModel):
    id: str
    license_plate: str
    brand: str
    model: str
    type: str
    title: str
    status: str
    scheduled_date: str
    completed_date: str
    cost: Decimal | None
    service_provider: str
