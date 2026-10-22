"""Web UI for Documents & Deadlines: forms, picker, renewal, vehicle tab, RBAC, locales."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.compliance import ComplianceDocument
from app.schemas.compliance import ComplianceCreate, ComplianceRenew
from app.schemas.vehicle import VehicleCreate
from app.services.compliance_service import ComplianceService
from app.services.vehicle_service import VehicleService


async def make_vehicle(db, kind="tractor_unit", plate=None):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else plate or "CA" + uuid4().hex[:6].upper(),
            brand="Scania",
            model="R450",
            department="Test department",
        )
    )


async def login(client, username):
    response = await client.post("/login", data={"username": username, "password": "testpass123"})
    assert response.status_code == 302


async def count(db):
    return await db.scalar(select(func.count()).select_from(ComplianceDocument))


@pytest.mark.asyncio
async def test_manager_create_renew_edit_flow(client, db_session, fleet_manager_user):
    vehicle = await make_vehicle(db_session, plate="CA4321KT")
    forklift = await make_vehicle(db_session, "forklift")
    await login(client, "testmanager")

    form = await client.get("/compliance/new")
    assert form.status_code == 200 and 'hx-get="/compliance/vehicle-options"' in form.text

    options = await client.get("/compliance/vehicle-options", params={"q": "4321"})
    assert "CA4321KT" in options.text and str(vehicle.id) in options.text
    assert "tachograph" in options.text  # allowed types travel with the option
    everything = await client.get("/compliance/vehicle-options")
    assert forklift.license_plate not in everything.text

    preselected = await client.get("/compliance/new", params={"vehicle_id": str(vehicle.id), "document_type": "casco"})
    assert "CA4321KT" in preselected.text and '"selectedType": "casco"' in preselected.text

    start, end = date(2025, 11, 1), date(2026, 10, 31)
    response = await client.post(
        "/compliance/new",
        data={
            "vehicle_id": str(vehicle.id),
            "document_type": "civil_liability",
            "start_date": str(start),
            "end_date": str(end),
            "amount": "199.90",
            "provider": "Армеец",
            "policy_number": "",
            "notes": "",
        },
    )
    assert response.status_code == 302, response.text
    detail_url = response.headers["location"]
    detail = await client.get(detail_url)
    assert detail.status_code == 200
    assert "Civil liability insurance" in detail.text and "Армеец" in detail.text
    assert "/documents/gallery/compliance_document/" in detail.text
    assert 'value="insurance"' in detail.text  # upload defaults to the insurance file category

    renew = await client.get(detail_url + "/renew")
    assert renew.status_code == 200
    assert 'value="2026-11-01"' in renew.text and 'value="2027-10-31"' in renew.text
    assert 'value="Армеец"' in renew.text
    response = await client.post(
        detail_url + "/renew",
        data={"start_date": "2026-10-25", "end_date": "2027-10-24", "provider": "ДЗИ", "amount": ""},
    )
    assert response.status_code == 302
    assert response.headers["location"] != detail_url
    assert await count(db_session) == 2
    old = await client.get(detail_url)
    assert "Replaced" in old.text and "Renewed by" in old.text

    edit = await client.get(detail_url + "/edit")
    assert edit.status_code == 200 and 'name="vehicle_id"' not in edit.text
    response = await client.post(
        detail_url + "/edit",
        data={"start_date": str(start), "end_date": "2026-11-15", "provider": "Армеец", "policy_number": "X"},
    )
    assert response.status_code == 302
    assert "2026-11-15" in (await client.get(detail_url)).text


@pytest.mark.asyncio
async def test_invalid_submission_redisplays(client, db_session, fleet_manager_user):
    van = await make_vehicle(db_session, "van")
    await login(client, "testmanager")
    data = {
        "vehicle_id": str(van.id),
        "document_type": "tachograph",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "provider": "Keep me",
    }
    response = await client.post("/compliance/new", data=data)
    assert response.status_code == 422
    assert "does not apply to the selected vehicle" in response.text and "Keep me" in response.text
    data.update(document_type="casco", end_date="2025-12-31")
    response = await client.post("/compliance/new", data=data)
    assert response.status_code == 422 and "on or after the start date" in response.text
    data.update(end_date="2026-12-31", amount="1.005")
    response = await client.post("/compliance/new", data=data)
    assert response.status_code == 422 and 'value="1.005"' in response.text
    assert await count(db_session) == 0


@pytest.mark.asyncio
async def test_vehicle_tab(client, db_session, fleet_manager_user):
    tractor = await make_vehicle(db_session)
    forklift = await make_vehicle(db_session, "forklift")
    today = date.today()
    await ComplianceService(db_session).create(
        ComplianceCreate(
            vehicle_id=tractor.id,
            document_type="technical_inspection",
            start_date=today - timedelta(days=360),
            end_date=today + timedelta(days=5),
        ),
        None,
    )
    await login(client, "testmanager")
    detail = await client.get(f"/vehicles/{tractor.id}")
    assert f"/vehicles/{tractor.id}/tab/compliance" in detail.text and "Documents &amp; Deadlines" in detail.text
    tab = await client.get(f"/vehicles/{tractor.id}/tab/compliance")
    assert tab.status_code == 200
    assert "Expiring soon" in tab.text and "Missing" in tab.text and "Tachograph inspection" in tab.text
    assert f"/compliance/new?vehicle_id={tractor.id}&document_type=tachograph" in tab.text
    assert "/renew" in tab.text
    tab = await client.get(f"/vehicles/{forklift.id}/tab/compliance")
    assert "No documents apply to this vehicle type." in tab.text


@pytest.mark.asyncio
async def test_list_filters_and_partial(client, db_session, fleet_manager_user):
    vehicle = await make_vehicle(db_session, plate="CB1111AA")
    other = await make_vehicle(db_session, plate="CB2222AA")
    today = date.today()
    service = ComplianceService(db_session)
    for v, end in ((vehicle, today - timedelta(days=3)), (other, today + timedelta(days=200))):
        await service.create(
            ComplianceCreate(
                vehicle_id=v.id, document_type="casco", start_date=end - timedelta(days=364), end_date=end
            ),
            None,
        )
    await login(client, "testmanager")
    page = await client.get("/compliance", params={"status": "expired"})
    assert "CB1111AA" in page.text and "CB2222AA" not in page.text
    partial = await client.get(
        "/compliance", params={"q": "2222"}, headers={"HX-Request": "true", "HX-Target": "compliance-table"}
    )
    assert "<html" not in partial.text and "CB2222AA" in partial.text and "CB1111AA" not in partial.text


@pytest.mark.asyncio
async def test_viewer_is_read_only(client, db_session, viewer_user, fleet_manager_user):
    vehicle = await make_vehicle(db_session)
    created = await ComplianceService(db_session).create(
        ComplianceCreate(
            vehicle_id=vehicle.id, document_type="casco", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        ),
        None,
    )
    await login(client, "testviewer")
    listing = await client.get("/compliance")
    assert listing.status_code == 200 and 'href="/compliance/new"' not in listing.text
    detail = await client.get(f"/compliance/{created.id}")
    assert detail.status_code == 200 and "/renew" not in detail.text and "/delete" not in detail.text
    for path in ("/compliance/new", f"/compliance/{created.id}/edit", f"/compliance/{created.id}/renew"):
        response = await client.get(path)
        assert response.status_code == 302 and response.headers["location"] == "/", path
    data = {
        "vehicle_id": str(vehicle.id),
        "document_type": "casco",
        "start_date": "2027-01-01",
        "end_date": "2027-12-31",
    }
    assert (await client.post("/compliance/new", data=data)).status_code == 302
    assert (await client.post(f"/compliance/{created.id}/delete")).status_code == 302
    assert await count(db_session) == 1
    upload = await client.post(
        "/documents/upload",
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        data={"entity_type": "compliance_document", "entity_id": str(created.id), "doc_type": "insurance"},
    )
    assert upload.status_code == 403


@pytest.mark.asyncio
async def test_bulgarian_labels(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    await client.get("/set-language/bg")
    page = await client.get("/compliance")
    assert "Документи и срокове" in page.text and "Договори" not in page.text
    form = await client.get("/compliance/new")
    assert "Валиден до" in form.text and "Търсене по рег. номер" in form.text


@pytest.mark.asyncio
async def test_delete_dialog_and_scopes(client, db_session, fleet_manager_user):
    vehicle = await make_vehicle(db_session)
    service = ComplianceService(db_session)
    first = await service.create(
        ComplianceCreate(
            vehicle_id=vehicle.id, document_type="casco", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
        ),
        None,
    )
    await login(client, "testmanager")
    # A single entry keeps the plain confirmation.
    single = await client.get(f"/compliance/{first.id}")
    assert 'name="scope"' not in single.text and "confirm(" in single.text

    second = await service.renew(
        first.id, ComplianceRenew(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)), None
    )
    page = await client.get(f"/compliance/{second.id}")
    assert 'name="scope" value="entry"' in page.text and 'name="scope" value="all"' in page.text
    assert "Only this entry" in page.text and "All entries (2)" in page.text
    await client.get("/set-language/bg")
    assert "Само този запис" in (await client.get(f"/compliance/{second.id}")).text

    response = await client.post(f"/compliance/{second.id}/delete", data={"scope": "entry"})
    assert response.status_code == 303 and await count(db_session) == 1
    second = await service.renew(
        first.id, ComplianceRenew(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)), None
    )
    response = await client.post(f"/compliance/{second.id}/delete", data={"scope": "all"})
    assert response.status_code == 303 and response.headers["location"] == f"/vehicles/{vehicle.id}"
    assert await count(db_session) == 0
