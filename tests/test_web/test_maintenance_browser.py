"""Optional browser check of the maintenance picker and create-as-completed; needs Playwright Chromium."""

import mimetypes
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.maintenance import MaintenanceRecord
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService

playwright = pytest.importorskip("playwright.async_api")


@pytest.mark.asyncio
async def test_picker_and_completed_date_field(client, db_session, fleet_manager_user):
    service = VehicleService(db_session)
    van = await service.create(
        VehicleCreate(
            vehicle_type="van", license_plate="CB3131ZZ", brand="Ford", model="Transit", department="Test department"
        )
    )
    await service.create(
        VehicleCreate(
            vehicle_type="passenger_car",
            license_plate="CB4141ZZ",
            brand="Opel",
            model="Astra",
            department="Test department",
        )
    )
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
            elif path.startswith("/maintenance"):
                headers = {k: v for k, v in request.headers.items() if k.lower().startswith(("hx-", "content-type"))}
                response = await client.request(
                    request.method, path, content=request.post_data_buffer, headers=headers, follow_redirects=False
                )
                await route.fulfill(status=response.status_code, body=response.content, headers=dict(response.headers))
            else:
                await route.fulfill(body="")

        await page.route("http://test/**", serve)
        await page.goto("http://test/maintenance/new")
        await page.wait_for_function("window.Alpine !== undefined && window.htmx !== undefined")
        await playwright.expect(page.locator("#completed_date")).not_to_be_visible()
        await page.locator("#vehicle-search").fill("3131")
        option = page.locator("#vehicle-options button", has_text="CB3131ZZ")
        await playwright.expect(option).to_be_visible()
        await playwright.expect(page.locator("#vehicle-options button", has_text="CB4141ZZ")).to_have_count(0)
        await option.click()
        await page.locator("#title").fill("Annual service")
        await page.locator("#status").select_option("completed")
        await playwright.expect(page.locator("#completed_date")).to_be_attached()
        await page.locator("button[type=submit]").click()
        await page.wait_for_url("http://test/maintenance/*")
        await browser.close()
    assert errors == []
    saved = (await db_session.scalars(select(MaintenanceRecord))).one()
    assert saved.vehicle_id == van.id and saved.status.value == "completed" and saved.completed_date is not None


@pytest.mark.asyncio
async def test_lower_mileage_popup_confirm_saves(client, db_session, fleet_manager_user):
    truck = await VehicleService(db_session).create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CB7070TT",
            brand="Volvo",
            model="FH",
            department="Test department",
            current_mileage=1000,
        )
    )
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
            elif path.startswith("/maintenance"):
                headers = {k: v for k, v in request.headers.items() if k.lower().startswith(("hx-", "content-type"))}
                response = await client.request(
                    request.method, path, content=request.post_data_buffer, headers=headers, follow_redirects=False
                )
                await route.fulfill(status=response.status_code, body=response.content, headers=dict(response.headers))
            else:
                await route.fulfill(body="")

        await page.route("http://test/**", serve)
        await page.goto("http://test/maintenance/new")
        await page.wait_for_function("window.Alpine !== undefined && window.htmx !== undefined")
        await playwright.expect(page.locator("#mileage_at_service")).not_to_be_visible()
        await page.locator("#vehicle-search").fill("7070")
        await page.locator("#vehicle-options button", has_text="CB7070TT").click()
        await playwright.expect(page.locator("#mileage_at_service")).to_be_visible()
        await playwright.expect(page.get_by_text("Current vehicle mileage:")).to_be_visible()
        await page.locator("#title").fill("Brakes")
        await page.locator("#mileage_at_service").fill("900")
        await page.locator("button.fc-btn-primary").click()
        await playwright.expect(page.get_by_text("Mileage is lower than the current one")).to_be_visible()
        await page.get_by_role("button", name="Cancel").first.click()
        await playwright.expect(page.get_by_text("Mileage is lower than the current one")).not_to_be_visible()
        await playwright.expect(page.locator("#mileage_at_service")).to_have_value("900")
        await page.locator("button.fc-btn-primary").click()
        await page.get_by_role("button", name="Yes, overwrite and save").click()
        await page.wait_for_url("http://test/maintenance/*")
        await browser.close()
    assert errors == []
    await db_session.refresh(truck)
    assert truck.current_mileage == 900
