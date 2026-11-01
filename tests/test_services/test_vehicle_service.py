"""Tests for VehicleService business logic."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vehicle import Vehicle
from app.schemas.vehicle import VehicleCreate, VehicleUpdate
from app.services.vehicle_service import VehicleService

VEHICLE_CREATE_DATA = {
    "license_plate": "456 DEF 02",
    "vin": "WVWZZZ3CZWE123456",
    "brand": "Toyota",
    "model": "Camry",
    "year": 2023,
    "color": "White",
    "vehicle_type": "passenger_car",
    "department": "Test department",
    "fuel_type": "gasoline",
    "transmission": "automatic",
    "seats": 5,
    "status": "active",
}


@pytest.mark.asyncio
async def test_create_vehicle_service(db_session: AsyncSession):
    """VehicleService.create creates a vehicle and returns it."""
    service = VehicleService(db_session)
    data = VehicleCreate(**VEHICLE_CREATE_DATA)
    vehicle = await service.create(data)

    assert isinstance(vehicle, Vehicle)
    assert vehicle.license_plate == "456 DEF 02"
    assert vehicle.vin == "WVWZZZ3CZWE123456"
    assert vehicle.brand == "Toyota"
    assert vehicle.model == "Camry"
    assert vehicle.year == 2023
    assert vehicle.id is not None


@pytest.mark.asyncio
async def test_duplicate_plate_raises(db_session: AsyncSession):
    """Creating a vehicle with a duplicate license plate raises ValueError."""
    service = VehicleService(db_session)
    data = VehicleCreate(**VEHICLE_CREATE_DATA)

    # Create the first vehicle
    await service.create(data)

    # Attempt to create a second vehicle with the same plate but different VIN
    duplicate_data = VehicleCreate(**{**VEHICLE_CREATE_DATA, "vin": "WVWZZZ3CZWE999999"})
    with pytest.raises(ValueError, match="error.plate_in_use"):
        await service.create(duplicate_data)


LATIN_PLATE = "CB2607HB"
CYRILLIC_TWIN = "СВ2607НВ"  # Cyrillic С, В, Н, В


def _plate_data(plate: str, vin: str | None = None) -> dict:
    return {**VEHICLE_CREATE_DATA, "license_plate": plate, "vin": vin}


def test_plate_is_normalised_to_latin_on_the_schema():
    """QA #2: look-alike Cyrillic letters are stored as their Latin twins; other text is untouched."""
    assert VehicleCreate(**_plate_data(CYRILLIC_TWIN)).license_plate == LATIN_PLATE
    assert VehicleCreate(**_plate_data("ср 1234 ок")).license_plate == "CP 1234 OK"
    assert VehicleCreate(**_plate_data("Ж1234Ж")).license_plate == "Ж1234Ж"  # no Latin twin: kept


@pytest.mark.asyncio
async def test_cyrillic_lookalike_plate_is_a_duplicate_on_create(db_session: AsyncSession):
    service = VehicleService(db_session)
    await service.create(VehicleCreate(**_plate_data(LATIN_PLATE)))
    with pytest.raises(ValueError, match="error.plate_in_use"):
        await service.create(VehicleCreate(**_plate_data(CYRILLIC_TWIN)))
    with pytest.raises(ValueError, match="error.plate_in_use"):
        await service.create(VehicleCreate(**_plate_data(LATIN_PLATE.lower())))


@pytest.mark.asyncio
async def test_cyrillic_lookalike_plate_is_a_duplicate_on_update(db_session: AsyncSession):
    service = VehicleService(db_session)
    await service.create(VehicleCreate(**_plate_data(LATIN_PLATE)))
    other = await service.create(VehicleCreate(**_plate_data("CA1111AA")))
    with pytest.raises(ValueError, match="error.plate_in_use"):
        await service.update(other.id, VehicleUpdate(license_plate=CYRILLIC_TWIN))


@pytest.mark.asyncio
async def test_lookup_matches_existing_rows_stored_with_cyrillic_letters(db_session: AsyncSession):
    """Rows saved before the fix may hold Cyrillic letters; they must still collide with the Latin spelling."""
    legacy = Vehicle(
        license_plate=CYRILLIC_TWIN,
        brand="Renault",
        model="T",
        department="Test department",
        vehicle_type="tractor_unit",
    )
    db_session.add(legacy)
    await db_session.flush()
    service = VehicleService(db_session)
    assert (await service.repo.get_by_plate(LATIN_PLATE)).id == legacy.id
    assert (await service.repo.get_by_plate(CYRILLIC_TWIN)).id == legacy.id
    with pytest.raises(ValueError, match="error.plate_in_use"):
        await service.create(VehicleCreate(**_plate_data(LATIN_PLATE)))
    found, _total = await service.repo.search(q="CB 2607")
    assert [v.id for v in found] == [legacy.id]  # typed with Latin letters and a space, stored Cyrillic
    # editing the legacy row itself is not a duplicate of itself
    await service.update(legacy.id, VehicleUpdate(notes="x"))
