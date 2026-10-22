"""Service items of a planned service (engine oil, gearbox oil, ...) and their due-state rules.

Items are stored as plain strings (``maintenance_records.service_item``). Adding an item means adding it to
``ServiceItem`` and ``SERVICE_ITEMS`` plus ``service_item.<code>`` labels in both catalogs; no migration.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class ServiceItem(StrEnum):
    """What a planned service covers, so the next due km / date can be tracked per item."""

    ENGINE_OIL = "engine_oil"  # Масло двигател
    GEARBOX_OIL = "gearbox_oil"  # Масло скоростна кутия
    DIFFERENTIAL_OIL = "differential_oil"  # Масло диференциал
    BRAKE_FLUID = "brake_fluid"
    COOLANT = "coolant"


class ServiceCheck(StrEnum):
    """Extra work ticked off during a service (a checklist, not a tracked item of its own)."""

    OIL_FILTER = "oil_filter"
    AIR_FILTER = "air_filter"
    FUEL_FILTER = "fuel_filter"


# Display order.
SERVICE_ITEMS: tuple[str, ...] = tuple(item.value for item in ServiceItem)
SERVICE_CHECKS: tuple[str, ...] = tuple(check.value for check in ServiceCheck)

# Checklist offered once a service item is chosen. Items without an entry have no checklist.
SERVICE_CHECKLIST: dict[str, tuple[str, ...]] = {
    "engine_oil": SERVICE_CHECKS,
}

# Towed trailers have no engine, gearbox, differential or oil-filled parts to service here.
NO_SERVICE_ITEM_VEHICLE_TYPES = frozenset({"container_semi_trailer", "curtain_side_semi_trailer"})


def service_items_for(vehicle_type) -> tuple[str, ...]:
    """Service items that apply to a vehicle type (none for trailers); all items when the type is unknown."""
    if vehicle_type is None:
        return SERVICE_ITEMS
    return () if str(getattr(vehicle_type, "value", vehicle_type)) in NO_SERVICE_ITEM_VEHICLE_TYPES else SERVICE_ITEMS


def normalize_checklist(values) -> list[str] | None:
    """Unique checks in display order; blank becomes None."""
    chosen = {str(getattr(v, "value", v)) for v in (values or [])}
    return [c for c in SERVICE_CHECKS if c in chosen] or None


def validate_service_selection(vehicle_type, service_item, checklist) -> None:
    """Raise ValueError (an error key) when the item or checklist does not fit the vehicle type / item."""
    if service_item is not None and service_item not in service_items_for(vehicle_type):
        raise ValueError("error.maintenance_service_item_not_for_vehicle")
    if checklist:
        allowed = SERVICE_CHECKLIST.get(service_item or "", ())
        if any(c not in allowed for c in checklist):
            raise ValueError("error.maintenance_checklist_not_allowed")


DUE_STATUSES = ("overdue", "due_soon", "ok", "none")


@dataclass(frozen=True)
class ServiceDue:
    """Computed, never-stored state of a service item's next due km / date."""

    status: str  # overdue | due_soon | ok | none (no next km/date recorded)
    km_left: int | None  # negative when passed; None when not measurable
    days_left: int | None  # negative when passed


def service_due(
    next_km: int | None,
    next_date: date | None,
    current_km: int | None,
    today: date,
    km_window: int,
    day_window: int,
) -> ServiceDue:
    """Overdue when the due date has passed or the odometer reached the due km; due soon inside the windows.

    The km part needs the vehicle's current mileage (not tracked for trailers and forklifts) and is
    ignored without it. A due date of today counts as due soon (it is still the last day).
    """
    km_left = next_km - current_km if next_km is not None and current_km is not None else None
    days_left = (next_date - today).days if next_date is not None else None
    if km_left is None and days_left is None:
        return ServiceDue("none", None, None)
    if (km_left is not None and km_left <= 0) or (days_left is not None and days_left < 0):
        return ServiceDue("overdue", km_left, days_left)
    if (km_left is not None and km_left <= km_window) or (days_left is not None and days_left <= day_window):
        return ServiceDue("due_soon", km_left, days_left)
    return ServiceDue("ok", km_left, days_left)
