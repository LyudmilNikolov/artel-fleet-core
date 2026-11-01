"""QA #6: numbers follow the UI language: Bulgarian '1 234,5' / '72,9 %', English '1,234.5' / '72.9%'."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.expense import ExpenseCategory
from app.schemas.expense import ExpenseCreate
from app.schemas.vehicle import VehicleCreate
from app.services.expense_service import ExpenseService
from app.services.vehicle_service import VehicleService
from app.utils.formatting import format_number, format_percent

NB = " "


def test_bulgarian_and_english_number_formats():
    assert format_number(120000, "bg") == f"120{NB}000"
    assert format_number(1234, "bg") == f"1{NB}234"  # grouped from four digits, as in the QA expectation
    assert format_number(Decimal("2151.40"), "bg", 1) == f"2{NB}151,4"
    assert format_number(Decimal("150"), "bg", 2) == "150,00"
    assert format_number(Decimal("1.665"), "bg", 2) == "1,67"  # half-up
    assert format_number(-1500, "bg") == f"-1{NB}500"
    assert format_number(120000, "en") == "120,000"
    assert format_number(Decimal("2151.40"), "en", 1) == "2,151.4"
    assert format_number(None, "bg") == "—"
    assert format_percent(Decimal("72.857"), "bg") == f"72,9{NB}%"
    assert format_percent(Decimal("72.857"), "en") == "72.9%"


async def _login(client, username="testmanager"):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


async def _world(db, manager):
    vehicle = await VehicleService(db).create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CA" + uuid4().hex[:6].upper(),
            brand="Volvo",
            model="FH",
            department="Test department",
            current_mileage=120000,
        )
    )
    expense = await ExpenseService(db).create(
        ExpenseCreate(
            vehicle_id=vehicle.id,
            category=ExpenseCategory.FUEL,
            amount=Decimal("3600.00"),
            date=date(2026, 3, 1),
            fuel_liters=Decimal("2151.40"),
            mileage_at_refuel=120500,
        ),
        manager.id,
    )
    await db.flush()
    return vehicle, expense


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lang, km, liters, litres_1dp, price, share",
    [
        ("bg", f"120{NB}500", f"2{NB}151,40", f"2{NB}151,4", "1,67 EUR", f"100,0{NB}%"),
        ("en", "120,500", "2,151.40", "2,151.4", "1.67 EUR", "100.0%"),
    ],
)
async def test_pages_use_the_language_number_format(
    client, db_session, fleet_manager_user, lang, km, liters, litres_1dp, price, share
):
    vehicle, expense = await _world(db_session, fleet_manager_user)
    await _login(client)
    q = {"lang": lang}
    listing = (await client.get("/vehicles", params=q)).text
    assert km in listing  # vehicle list shows the mileage the fuel entry raised it to
    detail = (await client.get(f"/expenses/{expense.id}", params=q)).text
    assert f"{km} km" in detail and liters in detail and price in detail
    fuel = (await client.get("/reports/fuel", params=q)).text
    assert litres_1dp in fuel
    spend = (await client.get("/reports/expenses", params=q)).text
    assert share in spend
    tab = (await client.get(f"/vehicles/{vehicle.id}/tab/mileage", params=q)).text
    assert f"{km} km" in tab
    # the raw English grouping must not leak into the Bulgarian pages
    if lang == "bg":
        assert "120,500" not in listing + detail + tab and "2,151" not in detail + fuel
