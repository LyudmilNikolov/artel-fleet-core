"""REST API for maintenance records: create (including as completed), complete, delete, RBAC."""

from datetime import date
from uuid import uuid4

import pytest

from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService
from tests.conftest import auth_header

BASE = "/api/v1/maintenance"


async def make_vehicle(db):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type="van",
            license_plate="CA" + uuid4().hex[:6].upper(),
            brand="Ford",
            model="Transit",
            department="Test department",
        )
    )


def body(vehicle_id, **kw):
    return {"vehicle_id": str(vehicle_id), "type": "repair", "title": "Brake pads", **kw}


@pytest.mark.asyncio
async def test_create_complete_edit_delete(client, db_session, manager_token):
    vehicle = await make_vehicle(db_session)
    headers = auth_header(manager_token)
    created = (await client.post(BASE, json=body(vehicle.id), headers=headers)).json()
    assert created["status"] == "scheduled" and created["completed_date"] is None

    done = await client.patch(f"{BASE}/{created['id']}/complete", headers=headers)
    assert done.status_code == 200
    assert done.json()["status"] == "completed" and done.json()["completed_date"] == str(date.today())

    reopened = await client.patch(f"{BASE}/{created['id']}", json={"status": "scheduled"}, headers=headers)
    assert reopened.json()["completed_date"] is None
    assert (await client.patch(f"{BASE}/{created['id']}", json={"title": None}, headers=headers)).status_code == 422

    assert (await client.delete(f"{BASE}/{created['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"{BASE}/{created['id']}", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_create_as_completed_and_unknown_vehicle(client, db_session, manager_token):
    vehicle = await make_vehicle(db_session)
    headers = auth_header(manager_token)
    response = await client.post(BASE, json=body(vehicle.id, status="completed", cost="99.90"), headers=headers)
    assert response.status_code == 201
    assert response.json()["status"] == "completed" and response.json()["completed_date"] == str(date.today())
    response = await client.post(
        BASE, json=body(vehicle.id, status="completed", completed_date="2026-02-03"), headers=headers
    )
    assert response.json()["completed_date"] == "2026-02-03"
    assert (await client.post(BASE, json=body(uuid4()), headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_viewer_cannot_write(client, db_session, manager_token, viewer_token):
    vehicle = await make_vehicle(db_session)
    created = (await client.post(BASE, json=body(vehicle.id), headers=auth_header(manager_token))).json()
    headers = auth_header(viewer_token)
    assert (await client.get(f"{BASE}/{created['id']}", headers=headers)).status_code == 200
    assert (await client.post(BASE, json=body(vehicle.id), headers=headers)).status_code == 403
    assert (await client.patch(f"{BASE}/{created['id']}/complete", headers=headers)).status_code == 403
    assert (await client.delete(f"{BASE}/{created['id']}", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_mileage_confirmation_flow(client, db_session, manager_token):
    vehicle = await VehicleService(db_session).create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CA" + uuid4().hex[:6].upper(),
            brand="DAF",
            model="XF",
            department="Test department",
            current_mileage=1000,
        )
    )
    headers = auth_header(manager_token)
    higher = await client.post(BASE, json=body(vehicle.id, mileage_at_service=1200), headers=headers)
    assert higher.status_code == 201 and higher.json()["mileage_at_service"] == 1200

    lower = await client.post(BASE, json=body(vehicle.id, mileage_at_service=900), headers=headers)
    assert lower.status_code == 409 and "1200" in lower.json()["detail"] and "900" in lower.json()["detail"]
    confirmed = await client.post(
        BASE, json=body(vehicle.id, mileage_at_service=900, overwrite_mileage=True), headers=headers
    )
    assert confirmed.status_code == 201

    history = (await client.get(f"/api/v1/mileage/vehicle/{vehicle.id}", headers=headers)).json()
    assert [(h["value"], h["is_correction"]) for h in history] == [(900, True), (1200, False), (1000, False)]

    assert (await client.post(BASE, json=body(vehicle.id, mileage_at_service=-1), headers=headers)).status_code == 422
    forklift = await VehicleService(db_session).create(
        VehicleCreate(vehicle_type="forklift", brand="Hyster", model="H3", department="Test department")
    )
    response = await client.post(BASE, json=body(forklift.id, mileage_at_service=10), headers=headers)
    assert response.status_code == 400
