"""Authoritative vehicle choices, applicability and storage limits."""

from enum import StrEnum


class VehicleType(StrEnum):
    TRACTOR_UNIT = "tractor_unit"
    CONTAINER_SEMI_TRAILER = "container_semi_trailer"
    CURTAIN_SIDE_SEMI_TRAILER = "curtain_side_semi_trailer"
    VAN = "van"
    PASSENGER_CAR = "passenger_car"
    FORKLIFT = "forklift"


class FuelType(StrEnum):
    GASOLINE = "gasoline"
    DIESEL = "diesel"
    GAS = "gas"
    ELECTRIC = "electric"
    HYBRID = "hybrid"
    LNG = "lng"
    CNG = "cng"


class TransmissionType(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class PowerSource(StrEnum):
    DIESEL = "diesel"
    LPG = "lpg"
    ELECTRIC = "electric"


CONTAINER_SIZES = ("20ft", "40ft", "45ft", "2x20ft")
EMISSION_STANDARDS = ("Euro 3", "Euro 4", "Euro 5", "EEV", "Euro 6")
APPLICABLE = {
    "tractor_unit": {
        "fuel_type",
        "transmission",
        "emission_standard",
        "co2_emission_class",
        "axle_count",
        "engine_power_hp",
        "engine_displacement_cc",
        "max_permissible_mass_kg",
        "fuel_tank_capacity_l",
        "adblue_capacity_l",
        "current_mileage",
    },
    "container_semi_trailer": {
        "axle_count",
        "max_permissible_mass_kg",
        "empty_weight_kg",
        "payload_capacity_kg",
        "supported_container_sizes",
    },
    "curtain_side_semi_trailer": {
        "axle_count",
        "max_permissible_mass_kg",
        "empty_weight_kg",
        "payload_capacity_kg",
        "cargo_length_m",
        "cargo_width_m",
        "cargo_height_m",
    },
    "van": {
        "fuel_type",
        "transmission",
        "engine_power_hp",
        "engine_displacement_cc",
        "max_permissible_mass_kg",
        "payload_capacity_kg",
        "seats",
        "current_mileage",
    },
    "passenger_car": {
        "fuel_type",
        "transmission",
        "engine_power_hp",
        "engine_displacement_cc",
        "seats",
        "current_mileage",
    },
    "forklift": {"power_source", "lifting_capacity_kg", "max_lift_height_m", "operating_hours"},
}
TECHNICAL_FIELDS = set.union(*APPLICABLE.values())
MILEAGE_TYPES = {t for t, fields in APPLICABLE.items() if "current_mileage" in fields}
FUEL_CHOICES = {
    "tractor_unit": ("diesel", "lng", "cng", "electric"),
    "van": ("gasoline", "diesel", "gas", "electric", "hybrid"),
    "passenger_car": ("gasoline", "diesel", "gas", "electric", "hybrid"),
}
CREATE_DEFAULTS = {
    "tractor_unit": {"fuel_type": "diesel", "transmission": "automatic", "axle_count": 2},
    "van": {"transmission": "automatic"},
    "passenger_car": {"transmission": "automatic"},
    "forklift": {"power_source": "diesel"},
}
# field: minimum, maximum; all integer values are exact counts.
INTEGER_BOUNDS = {
    "year": (1900, 2100),
    "axle_count": (1, 20),
    "seats": (1, 100),
    "engine_displacement_cc": (1, 100000),
    "current_mileage": (0, 10000000),
    "co2_emission_class": (1, 5),
}
# field: precision, scale, maximum, zero allowed
DECIMAL_BOUNDS = {
    "engine_power_hp": (8, 2, 10000, False),
    **{
        f: (12, 2, 1000000, False)
        for f in ("max_permissible_mass_kg", "empty_weight_kg", "payload_capacity_kg", "lifting_capacity_kg")
    },
    **{f: (10, 2, 10000, False) for f in ("fuel_tank_capacity_l", "adblue_capacity_l")},
    **{f: (7, 3, 100, False) for f in ("cargo_length_m", "cargo_width_m", "cargo_height_m", "max_lift_height_m")},
    "operating_hours": (10, 1, 1000000, True),
    "purchase_price": (12, 2, 9999999999.99, True),
}


def validate_applicability(vehicle_type, values):
    for field in TECHNICAL_FIELDS - APPLICABLE[vehicle_type]:
        if values.get(field) is not None:
            raise ValueError("error.vehicle_incompatible_field")
    if values.get("fuel_type") is not None and values["fuel_type"] not in FUEL_CHOICES.get(vehicle_type, ()):
        raise ValueError("error.vehicle_incompatible_fuel")


class ComplianceType(StrEnum):
    """Per-vehicle compliance documents with a validity period (Документи и срокове)."""

    CIVIL_LIABILITY = "civil_liability"
    CASCO = "casco"
    TECHNICAL_INSPECTION = "technical_inspection"
    TACHOGRAPH = "tachograph"


# Applicable compliance document types per vehicle type, in display order.
# Types are stored as plain strings: extending this mapping (plus bg/en labels
# under compliance_type.*) needs no migration.
_ROAD_COMPLIANCE = ("civil_liability", "casco", "technical_inspection")
COMPLIANCE_TYPES = {
    "tractor_unit": (*_ROAD_COMPLIANCE, "tachograph"),
    "container_semi_trailer": _ROAD_COMPLIANCE,
    "curtain_side_semi_trailer": _ROAD_COMPLIANCE,
    "van": _ROAD_COMPLIANCE,
    "passenger_car": _ROAD_COMPLIANCE,
    "forklift": (),
}
COMPLIANCE_VEHICLE_TYPES = {t for t, types in COMPLIANCE_TYPES.items() if types}


def compliance_types_for(vehicle_type) -> tuple[str, ...]:
    """Return the compliance document types that apply to a vehicle type."""
    return COMPLIANCE_TYPES.get(str(getattr(vehicle_type, "value", vehicle_type)), ())


def validate_compliance_type(vehicle_type, compliance_type) -> None:
    """Raise ValueError when a compliance document type does not apply to the vehicle type."""
    if compliance_type not in set(ComplianceType):
        raise ValueError("error.compliance_type_unknown")
    if compliance_type not in compliance_types_for(vehicle_type):
        raise ValueError("error.compliance_type_not_applicable")


# Expense categories that make no sense for a vehicle type (trailers are towed: no fuel, no toll).
EXPENSE_CATEGORY_EXCLUSIONS = {
    "container_semi_trailer": ("fuel", "toll"),
    "curtain_side_semi_trailer": ("fuel", "toll"),
}
# Categories kept in the database for existing records but no longer offered. Insurance is tracked
# through the document categories (civil liability, CASCO) instead.
RETIRED_EXPENSE_CATEGORIES = ("insurance",)


def blocked_expense_categories(vehicle_type) -> tuple[str, ...]:
    """Expense categories that do not apply to a vehicle type."""
    return EXPENSE_CATEGORY_EXCLUSIONS.get(str(getattr(vehicle_type, "value", vehicle_type)), ())


def validate_expense_category(vehicle_type, category) -> None:
    """Raise ValueError when an expense category is retired or does not apply to the vehicle type."""
    code = str(getattr(category, "value", category))
    if code in RETIRED_EXPENSE_CATEGORIES:
        raise ValueError("error.expense_category_retired")
    if code in blocked_expense_categories(vehicle_type):
        raise ValueError("error.expense_category_not_applicable")
