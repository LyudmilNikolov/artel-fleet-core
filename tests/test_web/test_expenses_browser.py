"""Optional browser checks of the expense form; need Playwright Chromium."""

import mimetypes
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.compliance import ComplianceDocument
from app.models.expense import Expense
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService

playwright = pytest.importorskip("playwright.async_api")


async def _page(pw, client):
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
                body=file.read_bytes(), content_type=mimetypes.guess_type(str(file))[0] or "application/octet-stream"
            )
        elif path.startswith(("/expenses", "/compliance")):
            headers = {k: v for k, v in request.headers.items() if k.lower().startswith(("hx-", "content-type"))}
            response = await client.request(
                request.method, path, content=request.post_data_buffer, headers=headers, follow_redirects=False
            )
            await route.fulfill(status=response.status_code, body=response.content, headers=dict(response.headers))
        else:
            await route.fulfill(body="")

    await page.route("http://test/**", serve)
    return browser, page, errors


@pytest.mark.asyncio
async def test_document_categories_follow_the_vehicle_and_create_both_records(client, db_session, fleet_manager_user):
    service = VehicleService(db_session)
    van = await service.create(
        VehicleCreate(
            vehicle_type="van", license_plate="CB1818VV", brand="Ford", model="Transit", department="Test department"
        )
    )
    truck = await service.create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CB1919TT",
            brand="MAN",
            model="TGX",
            department="Test department",
        )
    )
    assert (
        await client.post("/login", data={"username": fleet_manager_user.username, "password": "testpass123"})
    ).status_code == 302
    async with playwright.async_playwright() as pw:
        browser, page, errors = await _page(pw, client)
        await page.goto("http://test/expenses/new")
        await page.wait_for_function("window.Alpine !== undefined && window.htmx !== undefined")
        await playwright.expect(page.locator("label[for=valid_from]")).not_to_be_visible()
        await page.locator("#vehicle-search").fill("1818")
        await page.locator("#vehicle-options button", has_text="CB1818VV").click()
        await playwright.expect(page.locator("#category option[value=tachograph]")).to_be_disabled()
        await playwright.expect(page.locator("#category option[value=casco]")).to_be_enabled()
        await page.get_by_role("button", name="Change").click()
        await page.locator("#vehicle-search").fill("1919")
        await page.locator("#vehicle-options button", has_text="CB1919TT").click()
        await playwright.expect(page.locator("#category option[value=tachograph]")).to_be_enabled()
        await page.locator("#category").select_option("tachograph")
        await playwright.expect(page.locator("label[for=valid_from]")).to_be_visible()
        await page.locator("#amount").fill("95.00")
        # Type like a user does: the visible date boxes are the date picker's copies of the fields.
        for field, value in (("date", "01/04/2026"), ("valid_from", "01/04/2026"), ("valid_until", "31/03/2028")):
            visible = page.locator(f"#{field} + input")
            await playwright.expect(visible).to_be_enabled()
            await visible.fill(value)
            await visible.press("Enter")
        await playwright.expect(page.locator("#policy_number")).to_be_enabled()
        await page.locator("#policy_number").fill("T-100")
        await page.locator("button.fc-btn-primary").click()
        await page.wait_for_url("http://test/expenses/*")
        await browser.close()
    assert errors == []
    expense = (await db_session.scalars(select(Expense))).one()
    document = (await db_session.scalars(select(ComplianceDocument))).one()
    assert expense.vehicle_id == truck.id and document.expense_id == expense.id
    assert document.document_type == "tachograph" and str(document.amount) == "95.00"
    assert (str(document.start_date), str(document.end_date), document.policy_number) == (
        "2026-04-01",
        "2028-03-31",
        "T-100",
    )
    assert van.id != truck.id


@pytest.mark.asyncio
async def test_expense_lower_mileage_popup(client, db_session, fleet_manager_user):
    truck = await VehicleService(db_session).create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CB2020TT",
            brand="MAN",
            model="TGX",
            department="Test department",
            current_mileage=5000,
        )
    )
    assert (
        await client.post("/login", data={"username": fleet_manager_user.username, "password": "testpass123"})
    ).status_code == 302
    async with playwright.async_playwright() as pw:
        browser, page, errors = await _page(pw, client)
        await page.goto("http://test/expenses/new")
        await page.wait_for_function("window.Alpine !== undefined && window.htmx !== undefined")
        await page.locator("#vehicle-search").fill("2020")
        await page.locator("#vehicle-options button", has_text="CB2020TT").click()
        await playwright.expect(page.get_by_text("Current vehicle mileage:")).to_be_visible()
        await page.locator("#amount").fill("30.00")
        await page.evaluate("() => document.querySelector('#date')._flatpickr.setDate('2026-04-01')")
        await page.locator("#mileage_at_refuel").fill("4800")
        await page.locator("button.fc-btn-primary").click()
        await playwright.expect(page.get_by_text("Mileage is lower than the current one")).to_be_visible()
        await page.get_by_role("button", name="Yes, overwrite and save").click()
        await page.wait_for_url("http://test/expenses/*")
        await browser.close()
    assert errors == []
    await db_session.refresh(truck)
    assert truck.current_mileage == 4800


@pytest.mark.asyncio
async def test_trailer_has_no_fuel_or_toll_and_validity_fields_stay_editable(client, db_session, fleet_manager_user):
    service = VehicleService(db_session)
    await service.create(
        VehicleCreate(
            vehicle_type="container_semi_trailer",
            license_plate="CB3030TR",
            brand="Schmitz",
            model="Cargobull",
            department="Test department",
        )
    )
    await service.create(
        VehicleCreate(
            vehicle_type="van", license_plate="CB3131VV", brand="Ford", model="Transit", department="Test department"
        )
    )
    assert (
        await client.post("/login", data={"username": fleet_manager_user.username, "password": "testpass123"})
    ).status_code == 302
    async with playwright.async_playwright() as pw:
        browser, page, errors = await _page(pw, client)
        await page.goto("http://test/expenses/new")
        await page.wait_for_function("window.Alpine !== undefined && window.htmx !== undefined")
        await playwright.expect(page.locator("#category option[value=insurance]")).to_have_count(0)
        await playwright.expect(page.locator("#currency")).to_have_count(0)
        await page.locator("#vehicle-search").fill("3131")
        await page.locator("#vehicle-options button", has_text="CB3131VV").click()
        await playwright.expect(page.locator("#category option[value=fuel]")).to_be_enabled()
        await page.locator("#category").select_option("fuel")
        await page.get_by_role("button", name="Change").click()
        await page.locator("#vehicle-search").fill("3030")
        await page.locator("#vehicle-options button", has_text="CB3030TR").click()
        await playwright.expect(page.locator("#category option[value=fuel]")).to_be_disabled()
        await playwright.expect(page.locator("#category option[value=toll]")).to_be_disabled()
        await playwright.expect(page.locator("#category option[value=parts]")).to_be_enabled()
        await playwright.expect(page.locator("#category")).to_have_value("other")  # fuel was reset
        # Validity fields are usable whichever order things are chosen in.
        await page.locator("#category").select_option("casco")
        for field in ("valid_from", "valid_until"):
            await playwright.expect(page.locator(f"#{field} + input")).to_be_enabled()
        await playwright.expect(page.locator("#policy_number")).to_be_enabled()
        await browser.close()
    assert errors == []
