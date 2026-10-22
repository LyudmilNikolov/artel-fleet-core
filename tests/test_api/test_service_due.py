"""API: service item and next due values on maintenance records, and the service-due listing."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.schemas.vehicle import VehicleCreate
from app.services.validity import local_today
from app.services.vehicle_service import VehicleService
from tests.conftest import auth_header

TODAY = local_today()


async def make_vehicle(db, mileage=100000):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CA" + uuid4().hex[:6].upper(),
            brand="MAN",
            model="TGX",
            department="Test department",
            current_mileage=mileage,
        )
    )


def body(vehicle, **kw):
    return {
        "vehicle_id": str(vehicle.id),
        "type": "scheduled_service",
        "title": "Смяна на масло",
        "status": "completed",
        "completed_date": str(TODAY - timedelta(days=5)),
        "mileage_at_service": 100000,
        "service_item": "differential_oil",
        "next_service_mileage": 160000,
        "next_service_date": str(TODAY + timedelta(days=20)),
        **kw,
    }


@pytest.mark.asyncio
async def test_create_update_and_list_service_due(client, db_session, manager_token, viewer_token):
    vehicle = await make_vehicle(db_session)
    h = auth_header(manager_token)
    created = await client.post("/api/v1/maintenance", json=body(vehicle), headers=h)
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["service_item"] == "differential_oil" and item["next_service_mileage"] == 160000

    due = await client.get("/api/v1/maintenance/service-due", headers=auth_header(viewer_token))
    assert due.status_code == 200
    row = due.json()[0]
    assert row["record_id"] == item["id"] and row["service_item"] == "differential_oil"
    assert row["status"] == "due_soon" and row["days_left"] == 20 and row["km_left"] == 60000
    assert row["current_mileage"] == 100000 and row["license_plate"] == vehicle.license_plate

    assert (await client.get("/api/v1/maintenance/service-due", params={"status": "overdue"}, headers=h)).json() == []
    assert (await client.get("/api/v1/maintenance/service-due", params={"status": "bogus"}, headers=h)).status_code == 422
    assert (await client.get("/api/v1/maintenance/service-due", params={"vehicle_id": str(uuid4())}, headers=h)).json() == []

    patched = await client.patch(
        f"/api/v1/maintenance/{item['id']}", json={"next_service_date": str(TODAY - timedelta(days=1))}, headers=h
    )
    assert patched.status_code == 200
    assert (await client.get("/api/v1/maintenance/service-due", headers=h)).json()[0]["status"] == "overdue"
    assert (await client.get(f"/api/v1/maintenance/{item['id']}", headers=h)).json()["service_item"] == "differential_oil"


@pytest.mark.asyncio
async def test_api_validation(client, db_session, manager_token):
    vehicle = await make_vehicle(db_session)
    h = auth_header(manager_token)
    assert (await client.post("/api/v1/maintenance", json=body(vehicle, service_item="windshield"), headers=h)).status_code == 422
    not_planned = await client.post("/api/v1/maintenance", json=body(vehicle, type="repair"), headers=h)
    assert not_planned.status_code == 400 and "planned service" in not_planned.json()["detail"]
    low = await client.post("/api/v1/maintenance", json=body(vehicle, next_service_mileage=90000), headers=h)
    assert low.status_code == 400 and "next service km" in low.json()["detail"]
    assert (await client.post("/api/v1/maintenance", json=body(vehicle, next_service_mileage=-1), headers=h)).status_code == 422


@pytest.mark.asyncio
async def test_checklist_and_trailer_rules(client, db_session, manager_token):
    vehicle = await make_vehicle(db_session)
    h = auth_header(manager_token)
    created = await client.post(
        "/api/v1/maintenance",
        json=body(vehicle, service_item="engine_oil", service_checklist=["fuel_filter", "oil_filter"]),
        headers=h,
    )
    assert created.status_code == 201, created.text
    assert created.json()["service_checklist"] == ["oil_filter", "fuel_filter"]
    wrong = await client.post(
        "/api/v1/maintenance", json=body(vehicle, service_item="gearbox_oil", service_checklist=["oil_filter"]), headers=h
    )
    assert wrong.status_code == 400 and "do not apply" in wrong.json()["detail"]
    assert (
        await client.post("/api/v1/maintenance", json=body(vehicle, service_item="oil_filter"), headers=h)
    ).status_code == 422  # filters are no longer service items

    trailer = await VehicleService(db_session).create(
        VehicleCreate(
            vehicle_type="container_semi_trailer",
            license_plate="CA5555TR",
            brand="Schmitz",
            model="S",
            department="Test department",
        )
    )
    refused = await client.post("/api/v1/maintenance", json=body(trailer, mileage_at_service=None, next_service_mileage=None), headers=h)
    assert refused.status_code == 400 and "does not apply to the selected vehicle" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_list_filters(client, db_session, manager_token):
    from app.models.maintenance import MaintenanceRecord  # noqa: F401

    van = await make_vehicle(db_session)  # a tractor unit
    h = auth_header(manager_token)
    base = {"vehicle_id": str(van.id), "type": "repair", "title": "Job"}
    late = (await client.post("/api/v1/maintenance", json={**base, "title": "Late", "scheduled_date": str(TODAY - timedelta(days=2))}, headers=h)).json()
    soon = (await client.post("/api/v1/maintenance", json={**base, "title": "Soon", "scheduled_date": str(TODAY + timedelta(days=3))}, headers=h)).json()
    done = (await client.post("/api/v1/maintenance", json={**base, "title": "Done", "status": "completed"}, headers=h)).json()

    def ids(response):
        return [i["id"] for i in response.json()["items"]]

    assert ids(await client.get("/api/v1/maintenance", params={"when": "overdue"}, headers=h)) == [late["id"]]
    assert ids(await client.get("/api/v1/maintenance", params={"when": "upcoming"}, headers=h)) == [soon["id"]]
    assert ids(await client.get("/api/v1/maintenance", params={"status": "pending"}, headers=h)) == [late["id"], soon["id"]]
    assert ids(await client.get("/api/v1/maintenance", params={"status": "completed"}, headers=h)) == [done["id"]]
    assert ids(await client.get("/api/v1/maintenance", params={"q": van.license_plate, "vehicle_type": "van"}, headers=h)) == []
    assert len(ids(await client.get("/api/v1/maintenance", params={"q": van.license_plate}, headers=h))) == 3
    assert (await client.get("/api/v1/maintenance", params={"when": "bogus"}, headers=h)).status_code == 422
    assert (await client.get("/api/v1/maintenance", params={"vehicle_type": "bogus"}, headers=h)).status_code == 422
    assert (await client.get("/api/v1/expenses", params={"vehicle_type": "bogus"}, headers=h)).status_code == 422
    assert (await client.get("/api/v1/expenses", params={"q": "no-such-vehicle"}, headers=h)).json()["total"] == 0
