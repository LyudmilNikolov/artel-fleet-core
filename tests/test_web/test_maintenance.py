"""Web UI for maintenance: searchable vehicle picker, create (also as completed), edit, complete, delete."""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.maintenance import MaintenanceRecord, MaintenanceStatus
from app.schemas.maintenance import MaintenanceCreate
from app.schemas.vehicle import VehicleCreate
from app.services.maintenance_service import MaintenanceService
from app.services.vehicle_service import VehicleService


async def make_vehicle(db, kind="van", plate=None):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else plate or "CA" + uuid4().hex[:6].upper(),
            brand="Iveco",
            model="Daily",
            department="Test department",
        )
    )


async def login(client, username):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


async def records(db):
    return list((await db.scalars(select(MaintenanceRecord))).all())


@pytest.mark.asyncio
async def test_form_uses_searchable_picker_not_select(client, db_session, fleet_manager_user):
    van = await make_vehicle(db_session, plate="CB8888XX")
    forklift = await make_vehicle(db_session, "forklift")
    await login(client, "testmanager")
    form = await client.get("/maintenance/new")
    assert form.status_code == 200
    assert 'hx-get="/maintenance/vehicle-options"' in form.text
    assert '<select name="vehicle_id"' not in form.text
    assert 'name="status"' in form.text and 'name="completed_date"' in form.text
    options = await client.get("/maintenance/vehicle-options", params={"q": "8888"})
    assert "CB8888XX" in options.text and str(van.id) in options.text
    # Every vehicle type can be serviced, including forklifts.
    assert forklift.license_plate in (await client.get("/maintenance/vehicle-options")).text
    assert "No vehicles found" in (await client.get("/maintenance/vehicle-options", params={"q": "zzzz"})).text
    preselected = await client.get("/maintenance/new", params={"vehicle_id": str(van.id)})
    assert "CB8888XX" in preselected.text


@pytest.mark.asyncio
async def test_create_scheduled_and_completed(client, db_session, fleet_manager_user):
    van = await make_vehicle(db_session, plate="CB1212YY")
    await login(client, "testmanager")
    data = {"vehicle_id": str(van.id), "type": "repair", "title": "Oil change", "status": "scheduled", "cost": "45.50"}
    response = await client.post("/maintenance/new", data=data)
    assert response.status_code == 303
    detail = await client.get(response.headers["location"])
    assert "CB1212YY" in detail.text and "Oil change" in detail.text
    assert "Mark as completed" in detail.text and "/edit" in detail.text and "/delete" in detail.text

    response = await client.post(
        "/maintenance/new",
        data={**data, "title": "Done already", "status": "completed", "completed_date": "2026-05-04"},
    )
    assert response.status_code == 303
    done = await client.get(response.headers["location"])
    assert "Completed" in done.text and "2026-05-04" in done.text and "Mark as completed" not in done.text

    response = await client.post("/maintenance/new", data={**data, "title": "No date", "status": "completed"})
    created = next(r for r in await records(db_session) if r.title == "No date")
    assert created.status == MaintenanceStatus.COMPLETED and created.completed_date == date.today()

    listing = await client.get("/maintenance")
    assert "CB1212YY" in listing.text and "Iveco Daily" in listing.text


@pytest.mark.asyncio
async def test_invalid_submissions_keep_input(client, db_session, fleet_manager_user):
    van = await make_vehicle(db_session)
    await login(client, "testmanager")
    response = await client.post("/maintenance/new", data={"type": "repair", "title": "Keep me", "status": "scheduled"})
    assert response.status_code == 400 and "Choose a vehicle" in response.text and "Keep me" in response.text
    response = await client.post(
        "/maintenance/new",
        data={"vehicle_id": str(van.id), "type": "repair", "title": "Keep me", "status": "scheduled", "cost": "1.005"},
    )
    assert response.status_code == 400 and 'value="1.005"' in response.text and "Keep me" in response.text
    response = await client.post(
        "/maintenance/new", data={"vehicle_id": str(uuid4()), "type": "repair", "title": "T", "status": "scheduled"}
    )
    assert response.status_code == 400 and "Vehicle not found" in response.text
    assert await records(db_session) == []


@pytest.mark.asyncio
async def test_complete_edit_delete(client, db_session, fleet_manager_user):
    van = await make_vehicle(db_session)
    service = MaintenanceService(db_session)
    record = await service.create(MaintenanceCreate(vehicle_id=van.id, type="repair", title="Tires"), None)
    await login(client, "testmanager")

    response = await client.post(f"/maintenance/{record.id}/complete", data={"next": "/maintenance"})
    assert response.status_code == 303 and response.headers["location"] == "/maintenance"
    await db_session.refresh(record)
    assert record.status == MaintenanceStatus.COMPLETED and record.completed_date == date.today()
    response = await client.post(f"/maintenance/{record.id}/complete", data={"next": "//evil.example"})
    assert response.headers["location"] == f"/maintenance/{record.id}"

    edit = await client.get(f"/maintenance/{record.id}/edit")
    assert edit.status_code == 200 and 'name="vehicle_id"' not in edit.text and 'value="Tires"' in edit.text
    response = await client.post(
        f"/maintenance/{record.id}/edit",
        data={
            "type": "inspection",
            "title": "Tires v2",
            "status": "in_progress",
            "cost": "",
            "scheduled_date": "2026-09-01",
        },
    )
    assert response.status_code == 303
    await db_session.refresh(record)
    assert (record.title, record.status.value, record.type.value) == ("Tires v2", "in_progress", "inspection")
    assert record.completed_date is None and str(record.scheduled_date) == "2026-09-01"
    response = await client.post(
        f"/maintenance/{record.id}/edit", data={"type": "repair", "title": "", "status": "scheduled"}
    )
    assert response.status_code == 400

    response = await client.post(f"/maintenance/{record.id}/delete")
    assert response.status_code == 303 and response.headers["location"] == "/maintenance"
    assert await records(db_session) == []
    assert await db_session.scalar(select(func.count()).select_from(MaintenanceRecord)) == 0


@pytest.mark.asyncio
async def test_viewer_is_read_only(client, db_session, viewer_user, fleet_manager_user):
    van = await make_vehicle(db_session)
    record = await MaintenanceService(db_session).create(
        MaintenanceCreate(vehicle_id=van.id, type="repair", title="Tires"), None
    )
    await login(client, "testviewer")
    listing = await client.get("/maintenance")
    assert listing.status_code == 200 and 'href="/maintenance/new"' not in listing.text
    detail = await client.get(f"/maintenance/{record.id}")
    assert detail.status_code == 200 and "/complete" not in detail.text and "/delete" not in detail.text
    for path in ("/maintenance/new", f"/maintenance/{record.id}/edit"):
        response = await client.get(path)
        assert response.status_code == 302 and response.headers["location"] == "/", path
    data = {"vehicle_id": str(van.id), "type": "repair", "title": "Sneaky", "status": "scheduled"}
    assert (await client.post("/maintenance/new", data=data)).status_code == 302
    assert (await client.post(f"/maintenance/{record.id}/complete")).status_code == 302
    assert (await client.post(f"/maintenance/{record.id}/delete")).status_code == 302
    assert [r.title for r in await records(db_session)] == ["Tires"]
    assert record.status == MaintenanceStatus.SCHEDULED


@pytest.mark.asyncio
async def test_bulgarian_labels(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    await client.get("/set-language/bg")
    form = await client.get("/maintenance/new")
    assert "Търсене по рег. номер" in form.text and "Добавяне на обслужване" in form.text


@pytest.mark.asyncio
async def test_mileage_field_and_confirmation_popup(client, db_session, fleet_manager_user):
    truck = await VehicleService(db_session).create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CB5050TT",
            brand="Volvo",
            model="FH",
            department="Test department",
            current_mileage=1000,
        )
    )
    forklift = await make_vehicle(db_session, "forklift")
    await login(client, "testmanager")

    options = await client.get("/maintenance/vehicle-options", params={"q": "5050"})
    assert 'data-tracked="1"' in options.text and 'data-current="1000"' in options.text
    assert (
        'data-tracked="0"'
        in (await client.get("/maintenance/vehicle-options", params={"q": forklift.license_plate})).text
    )
    form = await client.get("/maintenance/new", params={"vehicle_id": str(truck.id)})
    assert 'name="mileage_at_service"' in form.text and "Current vehicle mileage:" in form.text and "1,000" in form.text

    data = {"vehicle_id": str(truck.id), "type": "repair", "title": "Gearbox", "status": "completed"}
    response = await client.post("/maintenance/new", data={**data, "mileage_at_service": "1200"})
    assert response.status_code == 303
    await db_session.refresh(truck)
    assert truck.current_mileage == 1200

    response = await client.post("/maintenance/new", data={**data, "title": "Lower", "mileage_at_service": "900"})
    assert response.status_code == 409
    assert "Mileage is lower than the current one" in response.text
    assert "Previously the vehicle mileage (1,200 km) was greater than the one entered now (900 km)" in response.text
    assert 'name="confirm_mileage_overwrite" value="1"' in response.text
    assert 'value="Lower"' in response.text and 'value="900"' in response.text
    assert [r.title for r in await records(db_session)] == ["Gearbox"]
    await db_session.refresh(truck)
    assert truck.current_mileage == 1200

    response = await client.post(
        "/maintenance/new",
        data={**data, "title": "Lower", "mileage_at_service": "900", "confirm_mileage_overwrite": "1"},
    )
    assert response.status_code == 303
    await db_session.refresh(truck)
    assert truck.current_mileage == 900
    tab = await client.get(f"/vehicles/{truck.id}/tab/mileage")
    assert "Correction" in tab.text and "900 km" in tab.text and "1,200 km" in tab.text

    # Editing: lower value on an existing record asks again.
    record = next(r for r in await records(db_session) if r.title == "Gearbox")
    response = await client.post(
        f"/maintenance/{record.id}/edit",
        data={"type": "repair", "title": "Gearbox", "status": "completed", "mileage_at_service": "800"},
    )
    assert response.status_code == 409 and "Mileage is lower than the current one" in response.text
    assert "Само" not in response.text
    await client.get("/set-language/bg")
    response = await client.post("/maintenance/new", data={**data, "title": "L2", "mileage_at_service": "700"})
    assert "Пробегът е по-малък от текущия" in response.text and "Да, презапиши и запази" in response.text

    # Untracked vehicles do not offer or accept mileage.
    response = await client.post(
        "/maintenance/new",
        data={
            "vehicle_id": str(forklift.id),
            "type": "repair",
            "title": "F",
            "status": "scheduled",
            "mileage_at_service": "5",
        },
    )
    assert response.status_code == 400
