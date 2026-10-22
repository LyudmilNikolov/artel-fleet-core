import pytest
from pydantic import ValidationError

from app.schemas.vehicle import VehicleCreate, VehicleUpdate


def common(**kwargs):
    return dict(
        vehicle_type="tractor_unit",
        license_plate=" ca123 ",
        brand=" Volvo ",
        model="FH",
        department="Operations",
        **kwargs,
    )


def test_creation_defaults_and_normalization():
    v = VehicleCreate(**common())
    assert v.license_plate == "CA123"
    assert v.brand == "Volvo"
    assert v.fuel_type == "diesel"
    assert v.transmission == "automatic"
    assert v.axle_count == 2
    assert v.vin is None


def test_explicit_null_does_not_default():
    assert VehicleCreate(**common(fuel_type=None)).fuel_type is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("axle_count", 2.5),
        ("year", True),
        ("engine_power_hp", "1.001"),
        ("current_mileage", -1),
        ("purchase_price", "NaN"),
    ],
)
def test_numeric_guards(field, value):
    with pytest.raises(ValidationError):
        VehicleCreate(**common(**{field: value}))


def test_incompatible_and_removed_fields():
    for fields in ({"seats": 2}, {"body_type": "truck"}, {"engine_volume": 3}):
        with pytest.raises(ValidationError):
            VehicleCreate(**common(**fields))


def test_update_omission_and_null():
    assert VehicleUpdate().model_dump(exclude_unset=True) == {}
    assert VehicleUpdate(vin="   ").model_dump(exclude_unset=True) == {"vin": None}
    with pytest.raises(ValidationError):
        VehicleUpdate(brand=None)


def test_container_canonical_order():
    v = VehicleCreate(
        vehicle_type="container_semi_trailer",
        license_plate="A1",
        brand="B",
        model="M",
        department="Operations",
        supported_container_sizes=["45ft", "20ft", "45ft"],
    )
    assert v.supported_container_sizes == ["20ft", "45ft"]


@pytest.mark.parametrize(
    "kind", ["tractor_unit", "container_semi_trailer", "curtain_side_semi_trailer", "van", "passenger_car", "forklift"]
)
def test_required_and_optional_strings(kind):
    values = {
        "vehicle_type": kind,
        "license_plate": None if kind == "forklift" else "TEST",
        "brand": "B",
        "model": "M",
        "department": "D",
        "vin": " abcdefghijklmnopq ",
    }
    assert VehicleCreate(**values).vin == "ABCDEFGHIJKLMNOPQ"
    for name in ["brand", "model", "department"]:
        with pytest.raises(ValidationError):
            VehicleCreate(**{**values, name: "  "})
    with pytest.raises(ValidationError):
        VehicleUpdate(vin="SHORT")


@pytest.mark.parametrize(
    "field,value",
    [
        ("seats", True),
        ("engine_displacement_cc", 3.1),
        ("operating_hours", "Infinity"),
        ("purchase_price", "-0.01"),
        ("cargo_length_m", "100.001"),
        ("co2_emission_class", 6),
    ],
)
def test_patch_numeric_guards(field, value):
    with pytest.raises(ValidationError):
        VehicleUpdate(**{field: value})


def test_seed_examples_cover_exact_types():
    from app.vehicle_rules import VehicleType
    from scripts.seed_data import VEHICLES_DATA

    assert set(VEHICLES_DATA) == {v.value for v in VehicleType}
    for kind, examples in VEHICLES_DATA.items():
        for brand, model in examples:
            value = VehicleCreate(
                vehicle_type=kind,
                license_plate=None if kind == "forklift" else "SEED",
                brand=brand,
                model=model,
                department="Configured",
            )
            assert value.brand == brand
