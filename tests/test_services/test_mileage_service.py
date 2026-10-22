import pytest
from sqlalchemy import func, select

from app.models.mileage import MileageLog
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService
from app.vehicle_rules import MILEAGE_TYPES, VehicleType
from tests.conftest import auth_header


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(VehicleType))
async def test_direct_mileage_endpoints_enforce_supported_types(client, db_session, manager_token, kind):
    vehicle = await VehicleService(db_session).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else "DIRECT",
            brand="B",
            model="M",
            department="Test department",
        )
    )
    entry = {"vehicle_id": str(vehicle.id), "value": 0}
    expected = 201 if kind in MILEAGE_TYPES else 400
    headers = auth_header(manager_token)
    response = await client.post("/api/v1/mileage", json=entry, headers=headers)
    assert response.status_code == expected
    response = await client.post("/api/v1/mileage/bulk", json={"entries": [entry]}, headers=headers)
    assert response.status_code == expected
    count = await db_session.scalar(
        select(func.count()).select_from(MileageLog).where(MileageLog.vehicle_id == vehicle.id)
    )
    assert count == (1 if kind in MILEAGE_TYPES else 0)
