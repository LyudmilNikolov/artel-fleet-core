from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.mileage import MileageLog
from app.models.vehicle import Vehicle
from app.schemas.mileage import MileageCreate
from app.schemas.vehicle import VehicleCreate, VehicleUpdate
from app.services.errors import DomainError
from app.services.mileage_service import MileageService
from app.services.vehicle_service import VehicleService
from app.vehicle_rules import APPLICABLE, VehicleType


def payload(kind="passenger_car", **values):
    return VehicleCreate(
        **{
            "vehicle_type": kind,
            "license_plate": None if kind == "forklift" else "CA123",
            "brand": "B",
            "model": "M",
            "department": "Test department",
            **values,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(VehicleType))
async def test_six_types_minimal(db_session, kind):
    vehicle = await VehicleService(db_session).create(payload(kind))
    assert vehicle.vehicle_type == kind
    for name in set.union(*APPLICABLE.values()) - APPLICABLE[kind]:
        assert getattr(vehicle, name) is None
    assert vehicle.vin is None
    assert vehicle.current_mileage is None


@pytest.mark.asyncio
async def test_mileage_transition_history_and_null(db_session, admin_user):
    svc = VehicleService(db_session)
    vehicle = await svc.create(payload(current_mileage=100), admin_user.id)
    assert await db_session.scalar(select(func.count()).select_from(MileageLog)) == 1
    await svc.update(vehicle.id, VehicleUpdate(current_mileage=100), admin_user.id)
    assert await db_session.scalar(select(func.count()).select_from(MileageLog)) == 1
    with pytest.raises(DomainError, match="cannot_clear"):
        await svc.update(vehicle.id, VehicleUpdate(current_mileage=None), admin_user.id)
    with pytest.raises(DomainError, match="decreased"):
        await svc.update(vehicle.id, VehicleUpdate(current_mileage=99), admin_user.id)
    await svc.update(vehicle.id, VehicleUpdate(vehicle_type="forklift"), admin_user.id)
    private = vehicle.license_plate
    assert private.startswith("PRIVATE-") and len(private) == 16
    assert vehicle.current_mileage is None
    assert vehicle.fuel_type is None
    with pytest.raises(DomainError, match="unsupported"):
        await MileageService(db_session).add_reading(MileageCreate(vehicle_id=vehicle.id, value=200), admin_user.id)
    await svc.update(vehicle.id, VehicleUpdate(notes="stable"))
    assert vehicle.license_plate == private
    with pytest.raises(DomainError, match="readonly"):
        await svc.update(vehicle.id, VehicleUpdate(license_plate="CHANGED"))
    with pytest.raises(DomainError, match="road_plate"):
        await svc.update(vehicle.id, VehicleUpdate(vehicle_type="van"))
    await svc.update(vehicle.id, VehicleUpdate(vehicle_type="van", license_plate="NEW1"), admin_user.id)
    assert vehicle.current_mileage == 100
    await svc.update(vehicle.id, VehicleUpdate(vehicle_type="forklift"))
    assert vehicle.license_plate != private


@pytest.mark.asyncio
async def test_patch_clear_and_invalid_type_is_atomic(db_session):
    svc = VehicleService(db_session)
    vehicle = await svc.create(payload(color="blue", purchase_price="0.00"))
    await svc.update(vehicle.id, VehicleUpdate(color=None))
    assert vehicle.color is None
    assert vehicle.purchase_price == Decimal("0")
    with pytest.raises(DomainError):
        await svc.update(vehicle.id, VehicleUpdate(vehicle_type="tractor_unit", seats=4))
    assert vehicle.vehicle_type == "passenger_car"


@pytest.mark.asyncio
async def test_forklift_collision_retry(db_session, monkeypatch):
    svc = VehicleService(db_session)
    monkeypatch.setattr("app.services.vehicle_service.secrets.token_hex", lambda n: "1234abcd")
    first = await svc.create(payload("forklift"))
    with pytest.raises(DomainError, match="identifier_retry"):
        await svc.create(payload("forklift"))
    assert await db_session.get(Vehicle, first.id)
    assert await db_session.scalar(select(func.count()).select_from(Vehicle)) == 1


@pytest.mark.asyncio
async def test_departments_union_and_validation(db_session, monkeypatch):
    from app.config import settings

    svc = VehicleService(db_session)
    await svc.create(payload())
    monkeypatch.setattr(settings, "VEHICLE_DEPARTMENTS", ["Another", " Another ", ""])
    assert await svc.departments() == ["Another", "Test department"]
    with pytest.raises(DomainError, match="department"):
        await svc.create(payload(department="Invented", license_plate="NEW"))


@pytest.mark.asyncio
async def test_bulk_failure_rolls_back_readings(db_session, admin_user):
    svc = VehicleService(db_session)
    vehicle = await svc.create(payload(current_mileage=100), admin_user.id)
    with pytest.raises(DomainError):
        await MileageService(db_session).add_bulk(
            [
                MileageCreate(vehicle_id=vehicle.id, value=200),
                MileageCreate(vehicle_id=vehicle.id, value=150),
            ],
            admin_user.id,
        )
    await db_session.refresh(vehicle)
    assert vehicle.current_mileage == 100
    assert await db_session.scalar(select(func.count()).select_from(MileageLog)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(VehicleType))
async def test_every_applicable_field(db_session, kind):
    examples = {
        "fuel_type": "diesel",
        "transmission": "manual",
        "emission_standard": "Euro 6",
        "co2_emission_class": 5,
        "axle_count": 3,
        "engine_power_hp": "450.50",
        "engine_displacement_cc": 12000,
        "max_permissible_mass_kg": "40000.00",
        "fuel_tank_capacity_l": "800.50",
        "adblue_capacity_l": "100.00",
        "current_mileage": 0,
        "empty_weight_kg": "6000.00",
        "payload_capacity_kg": "20000.00",
        "supported_container_sizes": ["20ft", "40ft"],
        "cargo_length_m": "13.600",
        "cargo_width_m": "2.450",
        "cargo_height_m": "2.700",
        "seats": 2,
        "power_source": "electric",
        "lifting_capacity_kg": "2500.00",
        "max_lift_height_m": "4.500",
        "operating_hours": "0.0",
    }
    values = {name: examples[name] for name in APPLICABLE[kind]}
    svc = VehicleService(db_session)
    vehicle = await svc.create(payload(kind, **values))
    for field, expected in values.items():
        actual = getattr(vehicle, field)
        assert str(actual) == str(expected) or actual == expected
    await svc.update(vehicle.id, VehicleUpdate(**{name: None for name in values if name != "current_mileage"}))
    for name in values:
        if name != "current_mileage":
            assert getattr(vehicle, name) is None


@pytest.mark.asyncio
async def test_update_database_conflict_restores_object(db_session, monkeypatch):
    svc = VehicleService(db_session)
    one = await svc.create(payload())
    two = await svc.create(payload(license_plate="OTHER"))

    async def no_precheck(plate):
        return None

    monkeypatch.setattr(svc.repo, "get_by_plate", no_precheck)
    with pytest.raises(DomainError, match="plate_in_use"):
        await svc.update(two.id, VehicleUpdate(license_plate=one.license_plate, notes="must rollback"))
    assert two.license_plate == "OTHER"
    assert two.notes is None
    assert await db_session.scalar(select(func.count()).select_from(Vehicle)) == 2


@pytest.mark.asyncio
async def test_reports_with_nullable_year_and_vehicle_types(db_session):
    from io import BytesIO

    from openpyxl import load_workbook

    from app.schemas.report import TCOReportRow
    from app.services.report_service import ReportService

    await VehicleService(db_session).create(payload("forklift", purchase_price="0.30"))
    await VehicleService(db_session).create(payload(purchase_price="10.10"))
    service = ReportService(db_session)
    rows = await service.tco_report()
    assert len(rows) == 2
    assert all(row["year"] is None for row in rows)
    assert {TCOReportRow(**r).vehicle_type for r in rows} == {"forklift", "passenger_car"}
    for locale in ("en", "bg"):
        sheet = load_workbook(BytesIO(await service.export_tco_excel(locale=locale))).active
        values = [
            row
            for row in sheet
            if row[0].value in ("CA123", next(r["license_plate"] for r in rows if r["vehicle_type"] == "forklift"))
        ]
        assert len(values) == 2
        for row in values:
            assert row[4].value is None
            assert row[5].value in (0.3, 10.1)
            assert "EUR" in row[5].number_format


@pytest.mark.asyncio
async def test_initial_mileage_failure_rolls_back_vehicle(db_session, monkeypatch):
    async def fail(*args, **kwargs):
        raise DomainError("errors.invalid_input")

    monkeypatch.setattr(MileageService, "add_reading", fail)
    with pytest.raises(DomainError):
        await VehicleService(db_session).create(payload(current_mileage=100))
    assert await db_session.scalar(select(func.count()).select_from(Vehicle)) == 0
    assert await db_session.scalar(select(func.count()).select_from(MileageLog)) == 0
