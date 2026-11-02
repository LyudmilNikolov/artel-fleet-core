"""QA #36 / #37: refused pages say so, and finished actions confirm, with one shared one-time message."""

import pytest

from app.web.flash import KEEP
from tests.test_web.test_driver_access import get, login, make_driver, make_driver_login
from tests.test_web.test_expenses import _login, _vehicle

class _NoStorage:
    def delete_file(self, key):
        pass


DENIED_EN = "Access denied: your role does not allow that page or action."
DENIED_BG = "Достъпът е отказан"


@pytest.mark.asyncio
async def test_viewer_sent_away_from_settings_is_told_once(client, db_session, viewer_user):
    await _login(client, "testviewer")
    redirect = await client.get("/settings/users", follow_redirects=False)
    assert redirect.status_code == 302 and redirect.headers["location"] == "/"
    home = await client.get("/")
    assert DENIED_EN in home.text
    assert DENIED_EN not in (await client.get("/")).text  # shown once


@pytest.mark.asyncio
async def test_message_follows_the_language_it_is_read_in(client, db_session, viewer_user):
    await _login(client, "testviewer")
    await client.get("/settings/users", follow_redirects=False)
    assert DENIED_BG in (await client.get("/?lang=bg")).text


@pytest.mark.asyncio
async def test_fleet_manager_sent_away_from_admin_pages_is_told(client, db_session, fleet_manager_user):
    await _login(client)
    assert (await client.get("/settings/users", follow_redirects=False)).status_code == 302
    assert DENIED_EN in (await client.get("/")).text


@pytest.mark.asyncio
async def test_driver_forbidden_page_redirects_home_with_message_but_landing_page_does_not(client, db_session):
    driver = await make_driver(db_session, "Flash Driver")
    await make_driver_login(db_session, driver, "flashdrv")
    await login(client, "flashdrv")
    first = await get(client, "/vehicles")  # not a driver page
    assert first.status_code == 302
    home = await client.get(first.headers["location"])
    assert DENIED_EN in home.text or DENIED_BG in home.text
    # opening the dashboard itself (the usual landing page) is not a refused request
    await get(client, "/")
    landing = await client.get((await get(client, "/")).headers["location"])
    assert DENIED_EN not in landing.text and DENIED_BG not in landing.text


@pytest.mark.asyncio
async def test_viewer_cannot_open_the_expense_form_and_is_told(client, db_session, viewer_user):
    await _login(client, "testviewer")
    assert (await client.get("/expenses/new", follow_redirects=False)).status_code == 302
    assert DENIED_EN in (await client.get("/")).text


@pytest.mark.asyncio
async def test_deleting_a_vehicle_confirms_it(client, db_session, admin_user, monkeypatch):
    monkeypatch.setattr("app.services.vehicle_service.get_s3_client", lambda: _NoStorage())
    await _login(client, "testadmin")
    truck = await _vehicle(db_session)
    await db_session.commit()
    done = await client.post(f"/vehicles/{truck.id}/delete", follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/vehicles"
    assert "Vehicle deleted." in (await client.get("/vehicles")).text
    missing = await client.post(f"/vehicles/{truck.id}/delete", follow_redirects=False)
    assert "The vehicle could not be deleted." in (await client.get(missing.headers["location"])).text


@pytest.mark.asyncio
async def test_queue_is_capped_and_deduplicated(client, db_session, viewer_user):
    await _login(client, "testviewer")
    for _ in range(KEEP + 3):
        await client.get("/settings/users", follow_redirects=False)
    assert (await client.get("/")).text.count(DENIED_EN) == 1
