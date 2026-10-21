"""Optional browser checks; run when Playwright and Chromium are installed."""

import mimetypes
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.async_api")


@pytest.mark.asyncio
async def test_vehicle_browser_defaults_switching_and_raw_errors(client, fleet_manager_user):
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
                file = Path(path.lstrip("/"))
                await route.fulfill(
                    body=file.read_bytes(),
                    content_type=mimetypes.guess_type(str(file))[0] or "application/octet-stream",
                )
            elif path.startswith("/vehicles"):
                response = await client.request(
                    request.method,
                    path,
                    content=request.post_data_buffer,
                    headers={"content-type": request.headers.get("content-type", "text/html")},
                    follow_redirects=False,
                )
                await route.fulfill(status=response.status_code, body=response.content, headers=dict(response.headers))
            else:
                await route.fulfill(body="")

        await page.route("http://test/**", serve)
        await page.goto("http://test/vehicles/new")
        await page.wait_for_function("window.Alpine !== undefined")
        await playwright.expect(page.locator("#vehicle_type")).to_have_value("tractor_unit")
        await playwright.expect(page.locator("#fuel_type")).to_have_value("diesel")
        await playwright.expect(page.locator("#transmission")).to_have_value("automatic")
        await playwright.expect(page.locator("#axle_count")).to_have_value("2")
        await page.locator("#engine_power_hp").fill("400.50")
        await page.locator("#vehicle_group").select_option("forklift")
        await playwright.expect(page.locator("#license_plate")).to_be_disabled()
        await playwright.expect(page.locator("#power_source")).to_have_value("diesel")
        await playwright.expect(page.locator("#engine_power_hp")).to_be_disabled()
        await page.locator("#vehicle_group").select_option("tractor_unit")
        await playwright.expect(page.locator("#engine_power_hp")).to_have_value("400.50")
        await page.locator("#vehicle_group").select_option("trailer")
        await playwright.expect(page.locator("#trailer_kind")).to_be_visible()
        await playwright.expect(page.locator("#vehicle_type")).to_have_value("container_semi_trailer")
        await page.locator('input[value="40ft"]').check()
        await page.locator("#vehicle_group").select_option("van")
        await playwright.expect(page.locator("#fuel_type")).to_have_value("")
        await playwright.expect(page.locator("#seats")).to_be_visible()
        await playwright.expect(page.locator('input[value="40ft"]')).to_be_disabled()
        await page.locator("#vehicle_group").select_option("passenger_car")
        await playwright.expect(page.locator("#max_permissible_mass_kg")).to_be_disabled()
        await page.locator("#vehicle_group").select_option("trailer")
        await page.locator("#trailer_kind").select_option("curtain_side_semi_trailer")
        await playwright.expect(page.locator("#vehicle_type")).to_have_value("curtain_side_semi_trailer")
        await playwright.expect(page.locator("#cargo_length_m")).to_be_visible()

        # Render a server-rejected numeric payload and execute Alpine against it.
        response = await client.post(
            "/vehicles/new",
            data={
                "vehicle_type": "van",
                "license_plate": "RAW",
                "brand": "Keep",
                "model": "M",
                "department": "Test department",
                "year": "bad",
                "engine_power_hp": "also bad",
                "fuel_type": "",
                "transmission": "",
            },
        )
        assert response.status_code == 422
        await page.route(
            "http://test/rejected", lambda route: route.fulfill(body=response.content, content_type="text/html")
        )
        await page.goto("http://test/rejected")
        await page.wait_for_function("window.Alpine !== undefined")
        await playwright.expect(page.locator("#year")).to_have_value("bad")
        await playwright.expect(page.locator("#engine_power_hp")).to_have_value("also bad")
        await playwright.expect(page.locator("#fuel_type")).to_have_value("")
        await playwright.expect(page.locator("#transmission")).to_have_value("")
        assert not errors, errors
        await browser.close()
