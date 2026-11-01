"""QA #15: changing a road vehicle to a forklift replaces its plate; that needs an explicit confirmation first."""

import pytest

from app.models.vehicle import Vehicle
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService

EDIT = {"brand": "Iveco", "model": "Daily", "department": "Test department", "status": "active"}


async def _van(db, plate="QA1005BB"):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type="van", license_plate=plate, brand="Iveco", model="Daily", department="Test department"
        )
    )


async def _login(client):
    assert (await client.post("/login", data={"username": "testmanager", "password": "testpass123"})).status_code == 302


@pytest.mark.asyncio
async def test_changing_a_van_to_a_forklift_asks_before_dropping_the_plate(client, db_session, fleet_manager_user):
    van = await _van(db_session)
    await _login(client)
    asked = await client.post(f"/vehicles/{van.id}/edit", data={**EDIT, "vehicle_type": "forklift"})
    assert asked.status_code == 409
    assert "QA1005BB" in asked.text and "confirm_plate_drop" in asked.text  # names the plate, offers the confirm button
    assert 'value="forklift"' in asked.text  # the chosen type is kept in the form
    await db_session.refresh(van)
    assert van.license_plate == "QA1005BB" and van.vehicle_type.value == "van"  # nothing changed yet

    confirmed = await client.post(
        f"/vehicles/{van.id}/edit", data={**EDIT, "vehicle_type": "forklift", "confirm_plate_drop": "1"}
    )
    assert confirmed.status_code == 302
    await db_session.refresh(van)
    assert van.vehicle_type.value == "forklift" and van.license_plate.startswith("PRIVATE-")


@pytest.mark.asyncio
async def test_no_warning_when_no_plate_is_lost(client, db_session, fleet_manager_user):
    van = await _van(db_session, "QA2000AA")
    await _login(client)
    # road type to road type keeps the plate
    same = await client.post(f"/vehicles/{van.id}/edit", data={**EDIT, "vehicle_type": "passenger_car"})
    assert same.status_code == 302
    await db_session.refresh(van)
    assert van.license_plate == "QA2000AA"
    # an ordinary edit of a van asks nothing
    plain = await client.post(f"/vehicles/{van.id}/edit", data={**EDIT, "vehicle_type": "passenger_car", "color": "Blue"})
    assert plain.status_code == 302
    # a forklift edited again keeps its internal id and asks nothing
    lift = await VehicleService(db_session).create(
        VehicleCreate(vehicle_type="forklift", brand="Linde", model="H30", department="Test department")
    )
    again = await client.post(f"/vehicles/{lift.id}/edit", data={**EDIT, "brand": "Linde", "vehicle_type": "forklift"})
    assert again.status_code == 302
    assert isinstance(await db_session.get(Vehicle, lift.id), Vehicle)
