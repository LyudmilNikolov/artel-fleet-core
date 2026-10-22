"""Web UI for planned service items: form fields, detail state, due page, vehicle tab, dashboard, roles."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.maintenance import MaintenanceRecord
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService

TODAY = datetime.now(UTC).date()


async def make_vehicle(db, mileage=100000, plate=None, kind="van"):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=plate or "CA" + uuid4().hex[:6].upper(),
            brand="Iveco",
            model="Daily",
            department="Test department",
            current_mileage=mileage if kind == "van" else None,
        )
    )


async def login(client, username):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


def item_labels(page_text: str) -> dict:
    """The service item labels the form hands to its Alpine dropdown."""
    import html
    import json
    import re

    return json.loads(html.unescape(re.search(r"itemLabels: (\{[^}]*\})", page_text).group(1)))


def planned(vehicle, **kw):
    return {
        "vehicle_id": str(vehicle.id),
        "type": "scheduled_service",
        "title": "Смяна на масло двигател",
        "status": "completed",
        "completed_date": str(TODAY - timedelta(days=10)),
        "mileage_at_service": "100000",
        "service_item": "engine_oil",
        "next_service_mileage": "160000",
        "next_service_date": str(TODAY + timedelta(days=300)),
        **kw,
    }


@pytest.mark.asyncio
async def test_form_offers_item_and_next_service_fields(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    page = await client.get("/maintenance/new")
    assert page.status_code == 200
    for name in ("service_item", "next_service_mileage", "next_service_date"):
        assert f'name="{name}"' in page.text
    for label in ("Engine oil", "Gearbox oil", "Differential oil", "Oil filter", "Coolant"):
        assert label in page.text
    assert "Next service at (km)" in page.text and "Next service date" in page.text
    assert 'x-model="type"' in page.text and "type: \"scheduled_service\"" in page.text  # shown for planned services only


@pytest.mark.asyncio
async def test_create_shows_next_due_and_state_on_the_detail_page(client, db_session, fleet_manager_user):
    vehicle = await make_vehicle(db_session)
    await login(client, "testmanager")
    created = await client.post("/maintenance/new", data=planned(vehicle))
    assert created.status_code == 303, created.text
    detail = await client.get(created.headers["location"])
    assert "Engine oil" in detail.text and "160,000 km" in detail.text and str(TODAY + timedelta(days=300)) in detail.text
    assert "Next due" in detail.text and "On schedule" in detail.text

    edit = await client.get(created.headers["location"] + "/edit")
    assert 'value="160000"' in edit.text
    assert 'serviceItem: "engine_oil"' in edit.text or "serviceItem: &#34;engine_oil&#34;" in edit.text


@pytest.mark.asyncio
async def test_validation_messages(client, db_session, fleet_manager_user):
    vehicle = await make_vehicle(db_session)
    await login(client, "testmanager")
    bad_km = await client.post("/maintenance/new", data=planned(vehicle, next_service_mileage="90000"))
    assert bad_km.status_code == 400 and "next service km must be higher" in bad_km.text
    bad_item = await client.post("/maintenance/new", data=planned(vehicle, service_item="windshield"))
    assert bad_item.status_code == 400
    wrong_type = await client.post("/maintenance/new", data=planned(vehicle, type="repair"))
    assert wrong_type.status_code == 400 and "only be set for a planned service" in wrong_type.text
    assert (await db_session.scalars(select(MaintenanceRecord))).first() is None


@pytest.mark.asyncio
async def test_editing_type_away_from_planned_keeps_due_values(client, db_session, fleet_manager_user):
    vehicle = await make_vehicle(db_session)
    await login(client, "testmanager")
    url = (await client.post("/maintenance/new", data=planned(vehicle))).headers["location"]
    changed = await client.post(
        url + "/edit",
        data={"type": "repair", "title": "Ремонт", "status": "completed", "completed_date": str(TODAY), "mileage_at_service": "100000"},
    )
    assert changed.status_code == 303, changed.text
    record = await db_session.scalar(select(MaintenanceRecord))
    await db_session.refresh(record)
    assert record.service_item is None and record.next_service_mileage == 160000 and record.type.value == "repair"
    assert "Next due" not in (await client.get(url)).text


@pytest.mark.asyncio
async def test_due_page_vehicle_tab_dashboard_and_list_link(client, db_session, fleet_manager_user):
    late = await make_vehicle(db_session, plate="CA1111LT")
    fine = await make_vehicle(db_session, plate="CA2222OK")
    await login(client, "testmanager")
    await client.post(
        "/maintenance/new",
        data=planned(late, service_item="differential_oil", next_service_mileage="", next_service_date=str(TODAY - timedelta(days=3))),
    )
    await client.post("/maintenance/new", data=planned(late, service_item="engine_oil", next_service_mileage="100400", next_service_date=""))
    await client.post("/maintenance/new", data=planned(fine, service_item="gearbox_oil"))

    page = await client.get("/maintenance/due")
    assert page.status_code == 200
    assert page.text.index("CA1111LT") < page.text.index("CA2222OK")  # most urgent first
    assert "Differential oil" in page.text and "Overdue" in page.text and "Due soon" in page.text and "On schedule" in page.text
    assert "3 days overdue" in page.text and "400 km" in page.text

    overdue = (await client.get("/maintenance/due", params={"status": "overdue"})).text
    assert "Differential oil" in overdue and "Gearbox oil" not in overdue and "Engine oil" not in overdue
    assert "Gearbox oil" in (await client.get("/maintenance/due", params={"status": "ok"})).text
    assert (await client.get("/maintenance/due", params={"status": "bogus"})).status_code == 200  # ignored

    tab = await client.get(f"/vehicles/{late.id}/tab/maintenance")
    assert "Service due" in tab.text and "Differential oil" in tab.text and "Engine oil" in tab.text
    assert "Gearbox oil" not in tab.text

    widget = (await client.get("/widgets/attention-needed")).text
    assert "Overdue services (oils etc.)" in widget and "/maintenance/due?status=overdue" in widget
    assert "Services due soon (oils etc.)" in widget and "/maintenance/due?status=due_soon" in widget

    listing = (await client.get("/maintenance")).text
    assert 'href="/maintenance/due"' in listing and "Differential oil" in listing


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_write(client, db_session, viewer_user):
    vehicle = await make_vehicle(db_session)
    await login(client, "testviewer")
    assert (await client.get("/maintenance/due")).status_code == 200
    denied = await client.post("/maintenance/new", data=planned(vehicle))
    assert denied.status_code in (302, 303) and denied.headers["location"] == "/"
    assert (await db_session.scalars(select(MaintenanceRecord))).first() is None


@pytest.mark.asyncio
async def test_bulgarian_labels(client, db_session, fleet_manager_user):
    vehicle = await make_vehicle(db_session)
    await login(client, "testmanager")
    await client.post("/maintenance/new", data=planned(vehicle, service_item="differential_oil"))
    await client.get("/set-language/bg")
    page = (await client.get("/maintenance/due")).text
    assert "Масло диференциал" in page and "Предстоящи обслужвания" in page and "В срок" in page
    form = (await client.get("/maintenance/new")).text
    assert "Следващо обслужване при (км)" in form and item_labels(form)["engine_oil"] == "Масло двигател"
    assert "Маслен филтър" in form  # the filters are checklist options


@pytest.mark.asyncio
async def test_filters_are_a_checklist_under_engine_oil(client, db_session, fleet_manager_user):
    import re

    vehicle = await make_vehicle(db_session)
    await login(client, "testmanager")
    form = (await client.get("/maintenance/new", params={"vehicle_id": str(vehicle.id)})).text
    assert "Also replaced during this service" in form
    boxes = re.findall(r'<input type="checkbox" name="service_checklist" value="(\w+)"', form)
    assert boxes == ["oil_filter", "air_filter", "fuel_filter"]
    assert "serviceItem === 'engine_oil'" in form  # shown only for an engine oil service
    labels = item_labels(form)
    assert labels["engine_oil"] == "Engine oil" and not {"oil_filter", "air_filter", "fuel_filter"} & set(labels)

    created = await client.post(
        "/maintenance/new", data=planned(vehicle, service_checklist=["fuel_filter", "oil_filter"])
    )
    assert created.status_code == 303, created.text
    detail = await client.get(created.headers["location"])
    assert "Also replaced during this service" in detail.text and "Oil filter, Fuel filter" in detail.text

    edit = (await client.get(created.headers["location"] + "/edit")).text
    assert re.findall(r'name="service_checklist" value="(\w+)"[^>]*', edit)  # options present
    assert "checks: [&#34;oil_filter&#34;, &#34;fuel_filter&#34;]" in edit or 'checks: ["oil_filter", "fuel_filter"]' in edit

    # another item keeps no checklist; unticking everything clears it
    url = created.headers["location"]
    await client.post(url + "/edit", data={**planned(vehicle), "vehicle_id": None, "service_item": "gearbox_oil"} | {"type": "scheduled_service"})
    record = await db_session.scalar(select(MaintenanceRecord))
    await db_session.refresh(record)
    assert record.service_item == "gearbox_oil" and record.service_checklist is None

    bad = await client.post("/maintenance/new", data=planned(vehicle, service_item="gearbox_oil", service_checklist=["oil_filter"]))
    assert bad.status_code == 400 and "do not apply to the selected service item" in bad.text


@pytest.mark.asyncio
async def test_trailers_do_not_offer_service_items(client, db_session, fleet_manager_user):
    trailer = await make_vehicle(db_session, plate="CA7777TR", kind="curtain_side_semi_trailer")
    van = await make_vehicle(db_session, plate="CA8888VN")
    await login(client, "testmanager")

    trailer_form = (await client.get("/maintenance/new", params={"vehicle_id": str(trailer.id)})).text
    assert '&#34;serviceItems&#34;: []' in trailer_form or '"serviceItems": []' in trailer_form
    van_form = (await client.get("/maintenance/new", params={"vehicle_id": str(van.id)})).text
    assert "engine_oil" in van_form and '"serviceItems": []' not in van_form and '&#34;serviceItems&#34;: []' not in van_form

    options = (await client.get("/maintenance/vehicle-options", params={"q": "CA7777"})).text
    assert "data-service-items='[]'" in options
    assert 'data-service-items=\'["engine_oil"' in (await client.get("/maintenance/vehicle-options", params={"q": "CA8888"})).text

    rejected = await client.post("/maintenance/new", data=planned(trailer, mileage_at_service="", next_service_mileage=""))
    assert rejected.status_code == 400 and "does not apply to the selected vehicle" in rejected.text
    # a planned service without an item is fine for a trailer
    ok = await client.post(
        "/maintenance/new",
        data=planned(trailer, service_item="", mileage_at_service="", next_service_mileage="", title="Годишен преглед"),
    )
    assert ok.status_code == 303, ok.text
