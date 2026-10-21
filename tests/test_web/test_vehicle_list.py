"""Vehicle list filters: live search by plate, Truck / Trailer (both kinds) / ... and status."""

import mimetypes
from pathlib import Path

import pytest

from app.models.vehicle import VehicleStatus
from app.schemas.vehicle import VehicleCreate, VehicleUpdate
from app.services.vehicle_service import VehicleService

playwright = pytest.importorskip("playwright.async_api")

FLEET = [
    ("tractor_unit", "CA 1234 AB", "Volvo", "FH"),
    ("tractor_unit", "PB-7777-XX", "MAN", "TGX"),
    ("container_semi_trailer", "CB 5555 TR", "Schmitz", "Cargobull"),
    ("curtain_side_semi_trailer", "CB 6666 TR", "Krone", "Profi"),
    ("van", "CC 9999 VV", "Ford", "Transit"),
]


async def make_fleet(db):
    service = VehicleService(db)
    vehicles = {}
    for kind, plate, brand, model in FLEET:
        vehicles[plate] = await service.create(
            VehicleCreate(
                vehicle_type=kind, license_plate=plate, brand=brand, model=model, department="Test department"
            )
        )
    return vehicles


async def login(client, username="testmanager"):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


def shown(response):
    return {plate for _, plate, _, _ in FLEET if plate in response.text}


@pytest.mark.asyncio
async def test_search_matches_plate_with_or_without_spaces_and_dashes(client, db_session, fleet_manager_user):
    await make_fleet(db_session)
    await login(client)
    assert shown(await client.get("/vehicles", params={"q": "ca1234"})) == {"CA 1234 AB"}
    assert shown(await client.get("/vehicles", params={"q": "CA 1234"})) == {"CA 1234 AB"}
    assert shown(await client.get("/vehicles", params={"q": "pb7777"})) == {"PB-7777-XX"}
    assert shown(await client.get("/vehicles", params={"q": "pb-7777"})) == {"PB-7777-XX"}
    assert shown(await client.get("/vehicles", params={"q": "transit"})) == {"CC 9999 VV"}  # make/model still work
    assert shown(await client.get("/vehicles", params={"q": "  "})) == {p for _, p, _, _ in FLEET}
    assert shown(await client.get("/vehicles", params={"q": "nothing-like-this"})) == set()


@pytest.mark.asyncio
async def test_type_filter_groups_both_trailer_kinds(client, db_session, fleet_manager_user):
    await make_fleet(db_session)
    await login(client)
    assert shown(await client.get("/vehicles", params={"vehicle_type": "tractor_unit"})) == {"CA 1234 AB", "PB-7777-XX"}
    assert shown(await client.get("/vehicles", params={"vehicle_type": "trailer"})) == {"CB 5555 TR", "CB 6666 TR"}
    assert shown(await client.get("/vehicles", params={"vehicle_type": "van"})) == {"CC 9999 VV"}
    both = await client.get("/vehicles", params={"vehicle_type": "trailer", "q": "6666"})
    assert shown(both) == {"CB 6666 TR"}
    assert (await client.get("/vehicles", params={"vehicle_type": "spaceship"})).status_code == 422


@pytest.mark.asyncio
async def test_status_filter_and_old_links_still_work(client, db_session, fleet_manager_user):
    vehicles = await make_fleet(db_session)
    await VehicleService(db_session).update(
        vehicles["CC 9999 VV"].id, VehicleUpdate(status=VehicleStatus.IN_MAINTENANCE)
    )
    await login(client)
    assert shown(await client.get("/vehicles", params={"status": "in_maintenance"})) == {"CC 9999 VV"}
    # Department, fuel type and brand are no longer in the filter bar, but old bookmarks keep working.
    assert shown(await client.get("/vehicles", params={"brand": "Volvo"})) == {"CA 1234 AB"}
    assert shown(await client.get("/vehicles", params={"department": "Test department"})) == {p for _, p, _, _ in FLEET}


@pytest.mark.asyncio
async def test_filter_bar_controls_and_partial(client, db_session, fleet_manager_user, viewer_user):
    await make_fleet(db_session)
    await login(client)
    page = await client.get("/vehicles")
    for hidden in ('name="department"', 'name="fuel_type"', 'name="brand"'):
        assert hidden not in page.text
    assert 'name="q"' in page.text and 'name="vehicle_type"' in page.text and 'name="status"' in page.text
    assert 'value="trailer"' in page.text and 'value="container_semi_trailer"' not in page.text
    assert "Add vehicle" in page.text and 'href="/vehicles/new"' in page.text and "All types" in page.text

    partial = await client.get(
        "/vehicles", params={"q": "7777"}, headers={"HX-Request": "true", "HX-Target": "vehicle-results"}
    )
    assert "<html" not in partial.text and "PB-7777-XX" in partial.text and "CA 1234 AB" not in partial.text
    full = await client.get("/vehicles", params={"q": "7777"}, headers={"HX-Request": "true", "HX-Target": "other"})
    assert "<html" in full.text

    await login(client, "testviewer")
    assert 'href="/vehicles/new"' not in (await client.get("/vehicles")).text
    await client.get("/set-language/bg")
    assert "Всички видове" in (await client.get("/vehicles")).text


@pytest.mark.asyncio
async def test_filters_apply_live_in_the_browser(client, db_session, fleet_manager_user):
    await make_fleet(db_session)
    assert (
        await client.post("/login", data={"username": fleet_manager_user.username, "password": "testpass123"})
    ).status_code == 302
    async with playwright.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except playwright.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Install Playwright Chromium to run browser checks")
            raise
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def serve(route):
            request = route.request
            path = request.url.removeprefix("http://test")
            if path.startswith("/static/"):
                file = Path(path.split("?")[0].lstrip("/"))
                await route.fulfill(
                    body=file.read_bytes(),
                    content_type=mimetypes.guess_type(str(file))[0] or "application/octet-stream",
                )
            elif path.startswith("/vehicles"):
                headers = {k: v for k, v in request.headers.items() if k.lower().startswith("hx-")}
                response = await client.request(request.method, path, headers=headers, follow_redirects=False)
                await route.fulfill(status=response.status_code, body=response.content, headers=dict(response.headers))
            else:
                await route.fulfill(body="")

        await page.route("http://test/**", serve)
        await page.goto("http://test/vehicles")
        await page.wait_for_function("window.Alpine !== undefined && window.htmx !== undefined")
        rows = page.locator("#vehicle-results tbody tr")
        await playwright.expect(rows).to_have_count(5)
        await playwright.expect(page.get_by_role("link", name="Reset")).not_to_be_visible()

        await page.locator("#vehicle-search").press_sequentially("cb55")  # typing, no Enter, no button
        await playwright.expect(rows).to_have_count(1)
        await playwright.expect(rows.first).to_contain_text("CB 5555 TR")
        await playwright.expect(page.get_by_role("link", name="Reset")).to_be_visible()

        await page.locator("#vehicle-search").fill("")
        await page.locator("#vehicle-type").select_option("trailer")
        await playwright.expect(rows).to_have_count(2)
        await page.locator("#vehicle-type").select_option("")
        await page.locator("#vehicle-status").select_option("decommissioned")
        await playwright.expect(page.locator("#vehicle-results")).to_contain_text("No data")
        assert "vehicle_type" in page.url or "status=decommissioned" in page.url  # address bar follows the filters
        await browser.close()
    assert errors == []
