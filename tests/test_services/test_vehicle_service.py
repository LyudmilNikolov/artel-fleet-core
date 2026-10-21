"""Tests for VehicleService business logic."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vehicle import Vehicle
from app.schemas.vehicle import VehicleCreate
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
