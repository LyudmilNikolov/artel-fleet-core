"""Low-severity QA fixes: #18 mileage tab, #20 password hint, #34 negative amount, #41 empty toll preview."""

from uuid import uuid4

import pytest

from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService
from tests.test_services.test_toll_import import csv_bytes, line
from tests.test_web.test_toll_import import upload


async def _login(client, username="testmanager"):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


async def _vehicle(db, kind, **extra):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else "CA" + uuid4().hex[:6].upper(),
            brand="Linde",
            model="X1",
            department="Test department",
            **extra,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, has_mileage", [("tractor_unit", True), ("van", True), ("forklift", False),
                                                ("container_semi_trailer", False), ("curtain_side_semi_trailer", False)])
async def test_mileage_tab_only_for_vehicles_that_track_mileage(client, db_session, fleet_manager_user, kind, has_mileage):
    """QA #18: no (empty) mileage tab on trailers and forklifts."""
    vehicle = await _vehicle(db_session, kind)
    await _login(client)
    page = (await client.get(f"/vehicles/{vehicle.id}")).text
    assert (f"/vehicles/{vehicle.id}/tab/mileage" in page) is has_mileage
    assert ("tab = 'mileage'" in page) is has_mileage
    assert "tab = 'maintenance'" in page  # the other tabs are untouched


@pytest.mark.asyncio
async def test_password_hint_and_fields_match_the_server_rule(client, db_session, admin_user):
    """QA #20: the rule is 10-128 characters; the hint and the inputs said 6."""
    from app.utils.password_policy import MAX_LENGTH, MIN_LENGTH

    assert (MIN_LENGTH, MAX_LENGTH) == (10, 128)
    await _login(client, "testadmin")
    form = (await client.get("/settings/users/new?lang=en")).text
    assert "At least 10 characters" in form and "6 characters" not in form
    assert 'minlength="10"' in form and 'minlength="6"' not in form
    bg = (await client.get("/settings/users/new?lang=bg")).text
    assert "Поне 10 символа" in bg and "Поне 6 символа" not in bg
    profile = (await client.get("/profile")).text
    assert 'minlength="10"' in profile and 'minlength="6"' not in profile
    # the server really refuses 6 characters
    refused = await client.post(
        "/settings/users/new",
        data={"username": "short6", "email": "s6@test.com", "full_name": "S", "role": "viewer",
              "password": "abc123", "password_confirm": "abc123"},
    )
    assert refused.status_code in (400, 422)


@pytest.mark.asyncio
async def test_negative_amount_says_it_cannot_be_negative(client, db_session, fleet_manager_user):
    """QA #34: '-50' used to answer 'Enter an amount with up to 2 decimals'."""
    vehicle = await _vehicle(db_session, "tractor_unit")
    await _login(client)
    page = (await client.get("/expenses/new?lang=en")).text
    assert "data-msg-negative" in page and "Amount cannot be negative" in page  # the browser-side message
    posted = await client.post(
        "/expenses/new", data={"vehicle_id": str(vehicle.id), "category": "fuel", "amount": "-50",
                               "date": "2026-03-01", "fuel_liters": "10"}
    )
    assert posted.status_code == 400 and "Cannot be negative" in posted.text
    assert "up to 2 decimals" not in posted.text.split('<form')[0]  # not in the banner / field message


@pytest.mark.asyncio
async def test_toll_preview_with_nothing_new_has_no_import_prompt(client, db_session, fleet_manager_user):
    """QA #41: a preview with 0 new rows told the user to check the numbers and import, with no import button."""
    await _vehicle(db_session, "tractor_unit", current_mileage=1000)
    await db_session.commit()
    await _login(client)
    data = csv_bytes(line("1", "CO7930CX"))
    first = await upload(client, data)
    await client.post(first.headers["location"] + "/apply")
    second = await upload(client, data)  # the same file again: every row is already imported
    page = (await client.get(second.headers["location"] + "?lang=en")).text
    assert "Nothing new to import" in page
    assert "then import" not in page and "/apply" not in page
