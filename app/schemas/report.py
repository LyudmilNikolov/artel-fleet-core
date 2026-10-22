"""Report amounts serialize as exact decimal strings through Pydantic."""

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.maintenance import MaintenanceStatus
from app.services.errors import DomainError
from app.vehicle_rules import VehicleType, vehicle_type_filter


class ReportFilters(BaseModel):
    """Filters shared by every report (web, API and exports)."""

    model_config = ConfigDict(frozen=True)

    start_date: dt.date | None = None
    end_date: dt.date | None = None
    vehicle_id: UUID | None = None
    vehicle_type: str | None = None  # list-filter group: tractor_unit, trailer, van, passenger_car, forklift

    @property
    def vehicle_types(self) -> list[VehicleType] | None:
        """Stored vehicle types matched by the group filter; None for no filter."""
        return vehicle_type_filter(self.vehicle_type)

    @property
    def is_empty(self) -> bool:
        return not self.to_query()

    def to_query(self) -> dict[str, str]:
        """Non-empty filters as query parameters (export links, pagination)."""
        values = {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "vehicle_id": self.vehicle_id,
            "vehicle_type": self.vehicle_type,
            "status": getattr(self, "status", None),
        }
        return {k: str(getattr(v, "value", v)) for k, v in values.items() if v}


class MaintenanceReportFilters(ReportFilters):
    """Maintenance history adds a status filter."""

    status: MaintenanceStatus | None = None


def parse_report_filters(raw: Mapping[str, str | None], cls: type[ReportFilters] = ReportFilters) -> ReportFilters:
    """Build report filters from raw query strings; raises DomainError with a specific localized key."""

    def clean(name: str) -> str | None:
        value = raw.get(name)
        return value.strip() if value and value.strip() else None

    values: dict = {}
    for name in ("start_date", "end_date"):
        if clean(name):
            try:
                values[name] = dt.date.fromisoformat(clean(name))
            except ValueError:
                raise DomainError("error.report_invalid_date") from None
    if values.get("start_date") and values.get("end_date") and values["end_date"] < values["start_date"]:
        raise DomainError("error.report_date_order")
    if clean("vehicle_id"):
        try:
            values["vehicle_id"] = UUID(clean("vehicle_id"))
        except ValueError:
            raise DomainError("error.report_invalid_vehicle") from None
    if clean("vehicle_type"):
        try:
            vehicle_type_filter(clean("vehicle_type"))
        except ValueError:
            raise DomainError("error.report_unknown_vehicle_type") from None
        values["vehicle_type"] = clean("vehicle_type")
    if issubclass(cls, MaintenanceReportFilters) and clean("status"):
        try:
            values["status"] = MaintenanceStatus(clean("status"))
        except ValueError:
            raise DomainError("error.report_invalid_status") from None
    return cls(**values)


class TCOReportRow(BaseModel):
    id: str
    license_plate: str
    vehicle_type: str
    brand: str
    model: str
    year: int | None
    purchase_price: Decimal | None
    total_expenses: Decimal
    tco: Decimal


class FuelReportRow(BaseModel):
    id: str
    license_plate: str
    vehicle_type: str
    brand: str
    model: str
    total_liters: float
    total_cost: Decimal
    refuel_count: int
    avg_price: Decimal
    l_per_100km: Decimal | None = None


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
    vehicle_type: str
    brand: str
    model: str
    type: str
    title: str
    status: str
    scheduled_date: str
    completed_date: str
    cost: Decimal | None
    service_provider: str
