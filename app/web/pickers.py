"""Data for the searchable vehicle picker shared by the maintenance and expense forms."""

from app.maintenance_rules import SERVICE_ITEMS, service_items_for
from app.models.vehicle import Vehicle
from app.vehicle_rules import (
    MILEAGE_TYPES,
    blocked_expense_categories,
    compliance_types_for,
    type_group_of,
    vehicle_display_label,
)


def vehicle_label(vehicle: Vehicle) -> str:
    return vehicle_display_label(vehicle)


def picker_state(vehicle: Vehicle | None) -> dict:
    """Initial state of the ``vehiclePicker`` Alpine component for an optional selected vehicle."""
    return {
        "vehicleId": str(vehicle.id) if vehicle else "",
        "label": vehicle_label(vehicle) if vehicle else "",
        "group": type_group_of(vehicle.vehicle_type) if vehicle else "",
        "tracked": bool(vehicle and vehicle.vehicle_type in MILEAGE_TYPES),
        "currentMileage": vehicle.current_mileage if vehicle else None,
        "docTypes": list(compliance_types_for(vehicle.vehicle_type)) if vehicle else [],
        "blocked": list(blocked_expense_categories(vehicle.vehicle_type)) if vehicle else [],
        # Service items that apply to the vehicle (none for trailers); all items while no vehicle is chosen.
        "serviceItems": list(service_items_for(vehicle.vehicle_type)) if vehicle else list(SERVICE_ITEMS),
        "allServiceItems": list(SERVICE_ITEMS),
    }


def option_rows(vehicles: list[Vehicle]) -> list[dict]:
    """Rows for ``components/vehicle_picker_options.html``."""
    return [
        {
            "vehicle": v,
            "label": vehicle_label(v),
            "group": type_group_of(v.vehicle_type),
            "tracked": v.vehicle_type in MILEAGE_TYPES,
            "doc_types": list(compliance_types_for(v.vehicle_type)),
            "blocked": list(blocked_expense_categories(v.vehicle_type)),
            "service_items": list(service_items_for(v.vehicle_type)),
        }
        for v in vehicles
    ]
