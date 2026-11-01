"""QA #17: department names are shown translated; unknown (custom) departments are shown as typed."""

import pytest

from app.models.driver import Driver
from app.models.vehicle import Vehicle
from app.vehicle_rules import VehicleType
from app.utils.formatting import department_label


def test_known_departments_are_translated_and_others_kept():
    assert department_label("trucking", "bg") == "Транспорт"
    assert department_label("trucking", "en") == "Trucking"
    assert department_label("Supply Chain", "bg") == "Верига на доставките"  # case and spaces do not matter
    assert department_label("Workshop", "bg") == "Сервиз"
    assert department_label("Нещо си по мярка", "bg") == "Нещо си по мярка"  # custom: as typed
    assert department_label("Custom Dept", "en") == "Custom Dept"
    assert department_label(None, "bg") == "" and department_label("  ", "en") == ""


async def _login(client):
    assert (await client.post("/login", data={"username": "testmanager", "password": "testpass123"})).status_code == 302


@pytest.mark.asyncio
async def test_department_is_translated_on_vehicle_and_driver_pages(client, db_session, fleet_manager_user):
    vehicle = Vehicle(license_plate="QA7777ZZ", brand="Volvo", model="FH", department="trucking", vehicle_type=VehicleType.VAN)
    driver = Driver(full_name="QA Шофьор", department="trucking")
    db_session.add_all([vehicle, driver])
    await db_session.flush()
    await _login(client)
    bg = (await client.get(f"/vehicles/{vehicle.id}?lang=bg")).text
    assert "Транспорт" in bg and ">trucking<" not in bg
    en = (await client.get(f"/vehicles/{vehicle.id}?lang=en")).text
    assert "Trucking" in en and ">trucking<" not in en
    assert "Транспорт" in (await client.get(f"/drivers/{driver.id}?lang=bg")).text
    drivers = (await client.get("/drivers?lang=bg")).text
    assert "Транспорт" in drivers and ">trucking<" not in drivers


@pytest.mark.asyncio
async def test_form_dropdown_shows_the_label_but_posts_the_stored_value(client, db_session, fleet_manager_user):
    db_session.add(
        Vehicle(license_plate="QA7778ZZ", brand="Volvo", model="FH", department="trucking", vehicle_type=VehicleType.VAN)
    )
    await db_session.flush()
    await _login(client)
    form = (await client.get("/vehicles/new?lang=bg")).text
    assert '<option value="trucking" >Транспорт</option>' in form or '<option value="trucking">Транспорт</option>' in form
