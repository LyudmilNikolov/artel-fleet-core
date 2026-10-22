"""Optional browser check of the searchable vehicle picker; runs when Playwright Chromium is installed."""

import mimetypes
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.compliance import ComplianceDocument
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService

playwright = pytest.importorskip("playwright.async_api")


@pytest.mark.asyncio
async def test_vehicle_picker_filters_types_and_saves(client, db_session, fleet_manager_user):
    service = VehicleService(db_session)
    van = await service.create(
        VehicleCreate(
            vehicle_type="van", license_plate="CB5555BB", brand="Ford", model="Transit", department="Test department"
        )
    )
    await service.create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CB6666BB",
            brand="MAN",
            model="TGX",
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
            elif path.startswith("/compliance"):
                headers = {k: v for k, v in request.headers.items() if k.lower().startswith(("hx-", "content-type"))}
                response = await client.request(
                    request.method, path, content=request.post_data_buffer, headers=headers, follow_redirects=False
                )
                await route.fulfill(status=response.status_code, body=response.content, headers=dict(response.headers))
            else:
                await route.fulfill(body="")

        await page.route("http://test/**", serve)
        await page.goto("http://test/compliance/new")
        await page.wait_for_function("window.Alpine !== undefined && window.htmx !== undefined")
        await playwright.expect(page.locator("#document_type")).to_be_disabled()
        await page.locator("#vehicle-search").fill("5555")
        option = page.locator("#vehicle-options button", has_text="CB5555BB")
        await playwright.expect(option).to_be_visible()
        await playwright.expect(page.locator("#vehicle-options button", has_text="CB6666BB")).to_have_count(0)
        await option.click()
        await playwright.expect(page.locator("#document_type")).to_be_enabled()
        values = await page.locator("#document_type option").evaluate_all("os => os.map(o => o.value).filter(Boolean)")
        assert values == ["civil_liability", "casco", "technical_inspection"]
        await page.locator("#document_type").select_option("casco")
        await page.evaluate(
            "() => { document.querySelector('#start_date')._flatpickr.setDate('2026-01-01');"
            " document.querySelector('#end_date')._flatpickr.setDate('2026-12-31'); }"
        )
        await page.locator("#amount").fill("310.00")
        await page.locator("button[type=submit]").click()
        await page.wait_for_url("http://test/compliance/*")
        await browser.close()
    assert errors == []
    saved = (await db_session.scalars(select(ComplianceDocument))).one()
    assert (saved.vehicle_id, saved.document_type, str(saved.amount)) == (van.id, "casco", "310.00")
