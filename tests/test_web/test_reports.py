"""Report pages: the Maintenance report exists, shared filters, validation and exports."""

from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.models.expense import Expense, ExpenseCategory
from app.models.maintenance import MaintenanceRecord, MaintenanceStatus, MaintenanceType
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService

PAGES = ["/reports/tco", "/reports/fuel", "/reports/expenses", "/reports/maintenance"]


async def vehicle(db, kind="tractor_unit", plate=None):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else plate or "CA" + uuid4().hex[:6].upper(),
            brand="Volvo",
            model="FH",
            department="Test department",
            current_mileage=1000 if kind in ("tractor_unit", "van", "passenger_car") else None,
        )
    )


async def login(client, username="testadmin"):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["bg", "en"])
async def test_maintenance_report_page_and_export_exist(client, db_session, admin_user, lang):
    """Regression: the reports hub links to /reports/maintenance, which used to be a 404."""
    truck = await vehicle(db_session)
    db_session.add(
        MaintenanceRecord(
            vehicle_id=truck.id,
            type=MaintenanceType.REPAIR,
            title="Brake job",
            status=MaintenanceStatus.COMPLETED,
            scheduled_date=date(2026, 1, 5),
            completed_date=date(2026, 1, 6),
            cost=Decimal("12.50"),
        )
    )
    await db_session.commit()
    await login(client)
    await client.get("/set-language/" + lang)
    page = await client.get("/reports/maintenance")
    assert page.status_code == 200 and "Brake job" in page.text and truck.license_plate in page.text
    assert f'<html lang="{lang}"' in page.text and "/reports/export/maintenance.xlsx" in page.text
    export = await client.get("/reports/export/maintenance.xlsx")
    assert export.status_code == 200
    rows = list(load_workbook(BytesIO(export.content)).active.values)
    assert any(r[4] == "Brake job" and r[8] == 12.5 for r in rows)


@pytest.mark.asyncio
async def test_reports_require_login(client):
    for path in [*PAGES, "/reports/export/maintenance.xlsx", "/reports/vehicle-options"]:
        response = await client.get(path, follow_redirects=False)
        assert response.status_code == 302 and response.headers["location"] == "/login", path


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize(
    "query",
    [
        "start_date=bad",
        "end_date=2026-13-45",
        "start_date=2026-05-01&end_date=2026-01-01",
        "vehicle_id=zzz",
        "vehicle_type=boat",
    ],
)
async def test_invalid_filters_are_a_localized_422_not_a_500(client, db_session, admin_user, path, query):
    await db_session.commit()
    await login(client)
    await client.get("/set-language/en")
    response = await client.get(f"{path}?{query}")
    assert response.status_code == 422
    assert 'role="alert"' in response.text
    assert "Invalid" in response.text or "cannot be before" in response.text or "Unknown" in response.text
    assert (await client.get(f"/reports/export/tco.xlsx?{query}")).status_code == 422


@pytest.mark.asyncio
async def test_invalid_maintenance_status_is_422(client, db_session, admin_user):
    await db_session.commit()
    await login(client)
    assert (await client.get("/reports/maintenance?status=nope")).status_code == 422


@pytest.mark.asyncio
async def test_expense_report_filters_by_category(client, db_session, admin_user):
    truck = await vehicle(db_session, "tractor_unit", "CB3333CC")
    for category, amount in ((ExpenseCategory.TOLL, "12.34"), (ExpenseCategory.PARKING, "56.78")):
        db_session.add(Expense(vehicle_id=truck.id, category=category, amount=Decimal(amount), date=date(2026, 1, 1)))
    await db_session.commit()
    await login(client)
    await client.get("/set-language/en")
    page = await client.get("/reports/expenses?category=toll")
    assert page.status_code == 200
    assert 'name="category"' in page.text and '<option value="toll" selected' in page.text
    assert "12,34" in page.text or "12.34" in page.text
    assert "56,78" not in page.text and "56.78" not in page.text
    assert "/reports/export/expenses.csv?category=toll" in page.text
    export = (await client.get("/reports/export/expenses.csv?category=toll")).text
    assert "12.34" in export and "56.78" not in export
    assert (await client.get("/reports/expenses?category=nope")).status_code == 422
    # other report pages have no category select
    assert 'name="category"' not in (await client.get("/reports/tco")).text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PAGES)
async def test_filters_apply_and_export_links_carry_them(client, db_session, admin_user, path):
    truck = await vehicle(db_session, "tractor_unit", "CB1111AA")
    van = await vehicle(db_session, "van", "CB2222BB")
    db_session.add(
        Expense(
            vehicle_id=truck.id,
            category=ExpenseCategory.FUEL,
            amount=Decimal("10"),
            date=date(2026, 1, 1),
            fuel_liters=5,
        )
    )
    await db_session.commit()
    await login(client)
    query = f"vehicle_id={truck.id}&vehicle_type=tractor_unit&start_date=2026-01-01"
    page = await client.get(f"{path}?{query}")
    assert page.status_code == 200
    export_href = next(
        part.split('"')[0] for part in page.text.split('href="') if part.startswith("/reports/export/")
    ).replace("&amp;", "&")
    for fragment in (f"vehicle_id={truck.id}", "vehicle_type=tractor_unit", "start_date=2026-01-01"):
        assert fragment in export_href
    assert (await client.get(export_href)).status_code == 200
    assert van.license_plate not in page.text
    # picker shows the chosen vehicle
    assert "CB1111AA" in page.text


@pytest.mark.asyncio
async def test_vehicle_outside_group_shows_note_and_empty_table(client, db_session, admin_user):
    truck = await vehicle(db_session, "tractor_unit", "CB1111AA")
    await db_session.commit()
    await login(client)
    await client.get("/set-language/en")
    page = await client.get(f"/reports/tco?vehicle_id={truck.id}&vehicle_type=van")
    assert page.status_code == 200
    assert "not in the chosen vehicle type" in page.text and "CB1111AA</span>" not in page.text.split("<tbody")[1]


@pytest.mark.asyncio
async def test_fuel_report_trailers_empty_state(client, db_session, admin_user):
    await vehicle(db_session, "container_semi_trailer", "CB3333CC")
    await db_session.commit()
    await login(client)
    await client.get("/set-language/en")
    page = await client.get("/reports/fuel?vehicle_type=trailer")
    assert page.status_code == 200 and "Trailers have no fuel expenses" in page.text


@pytest.mark.asyncio
async def test_maintenance_pagination_keeps_filters(client, db_session, admin_user):
    truck = await vehicle(db_session)
    for i in range(3):
        db_session.add(
            MaintenanceRecord(
                vehicle_id=truck.id,
                type=MaintenanceType.REPAIR,
                title=f"Job {i}",
                status=MaintenanceStatus.COMPLETED,
                scheduled_date=date(2026, 1, 1 + i),
                completed_date=date(2026, 1, 1 + i),
                cost=Decimal("1.00"),
            )
        )
    await db_session.commit()
    await login(client)
    page = await client.get(f"/reports/maintenance?size=2&status=completed&vehicle_id={truck.id}")
    assert page.status_code == 200
    links = [
        part.split('"')[0] for part in page.text.split('href="') if part.startswith("http") and "page=" in part[:200]
    ]
    assert links and all("status=completed" in link and f"vehicle_id={truck.id}" in link for link in links)
    # totals cover the whole filtered set, not just the page
    assert "3,00" in page.text or "3.00" in page.text


@pytest.mark.asyncio
async def test_vehicle_options_narrow_by_type(client, db_session, admin_user):
    await vehicle(db_session, "container_semi_trailer", "CB4444DD")
    await vehicle(db_session, "van", "CB5555EE")
    forklift = await vehicle(db_session, "forklift")
    await db_session.commit()
    await login(client)
    trailers = await client.get("/reports/vehicle-options?vehicle_type=trailer")
    assert "CB4444DD" in trailers.text and "CB5555EE" not in trailers.text
    assert 'data-group="trailer"' in trailers.text
    everything = await client.get("/reports/vehicle-options")
    assert "CB4444DD" in everything.text and "CB5555EE" in everything.text
    # forklifts carry their generated identifier, shown after brand and model
    assert forklift.license_plate.startswith("PRIVATE-") and forklift.license_plate in everything.text
    assert "CB5555" in (await client.get("/reports/vehicle-options?q=cb-5555-ee")).text
