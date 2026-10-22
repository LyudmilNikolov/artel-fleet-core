"""Fuel expenses: decimal liters, price per liter derived from amount / liters, localized messages."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.expense import Expense
from app.services.errors import DomainError
from app.services.expense_service import fuel_price_per_liter
from tests.test_web.test_expenses import _login, _vehicle


@pytest.mark.parametrize(
    ("amount", "liters", "expected"),
    [
        ("80", "40", "2.00"),
        ("50.50", "33.70", "1.50"),  # 1.4985... half-up to cents
        ("80.50", "80.50", "1.00"),
        ("0.01", "3", "0.00"),
        ("10.00", "6", "1.67"),
        ("1.00", "8", "0.13"),  # 0.125 rounds up
    ],
)
def test_price_per_liter_is_amount_over_liters(amount, liters, expected):
    assert fuel_price_per_liter(Decimal(amount), Decimal(liters)) == Decimal(expected)


def test_price_per_liter_needs_liters_and_fits_the_column():
    assert fuel_price_per_liter(Decimal("10"), None) is None
    with pytest.raises(DomainError):
        fuel_price_per_liter(Decimal("9999999999.99"), Decimal("0.01"))


async def _post_fuel(client, truck, **fields):
    data = {"vehicle_id": str(truck.id), "category": "fuel", "date": "2026-03-01", **fields}
    return await client.post("/expenses/new", data=data)


@pytest.mark.asyncio
async def test_create_derives_price_and_keeps_decimal_liters(client, db_session, fleet_manager_user):
    truck = await _vehicle(db_session)
    await _login(client)
    response = await _post_fuel(client, truck, amount="80", fuel_liters="40", fuel_price_per_liter="9.99")
    assert response.status_code == 303  # a submitted price is ignored
    response = await _post_fuel(client, truck, amount="50.5", fuel_liters="80.5")
    assert response.status_code == 303
    rows = (await db_session.scalars(select(Expense).order_by(Expense.amount))).all()
    assert [(r.amount, r.fuel_liters, r.fuel_price_per_liter) for r in rows] == [
        (Decimal("50.50"), Decimal("80.50"), Decimal("0.63")),
        (Decimal("80.00"), Decimal("40.00"), Decimal("2.00")),
    ]


@pytest.mark.asyncio
async def test_decimal_comma_is_accepted(client, db_session, fleet_manager_user):
    truck = await _vehicle(db_session)
    await _login(client)
    assert (await _post_fuel(client, truck, amount="50,5", fuel_liters="80,5")).status_code == 303
    expense = (await db_session.scalars(select(Expense))).one()
    assert (expense.amount, expense.fuel_liters) == (Decimal("50.50"), Decimal("80.50"))


@pytest.mark.asyncio
async def test_three_decimals_of_liters_are_rejected(client, db_session, fleet_manager_user):
    truck = await _vehicle(db_session)
    await _login(client)
    assert (await _post_fuel(client, truck, amount="50", fuel_liters="80.555")).status_code == 400
    assert (await _post_fuel(client, truck, amount="50", fuel_liters="0")).status_code == 400
    assert not (await db_session.scalars(select(Expense))).all()


@pytest.mark.asyncio
async def test_edit_recomputes_price_and_clears_it_without_liters(client, db_session, fleet_manager_user):
    truck = await _vehicle(db_session)
    await _login(client)
    await _post_fuel(client, truck, amount="80", fuel_liters="40")
    expense = (await db_session.scalars(select(Expense))).one()
    edit = f"/expenses/{expense.id}/edit"
    base = {"category": "fuel", "date": "2026-03-01"}

    assert (await client.post(edit, data={**base, "amount": "90", "fuel_liters": "40"})).status_code == 303
    await db_session.refresh(expense)
    assert expense.fuel_price_per_liter == Decimal("2.25")
    assert (await client.post(edit, data={**base, "amount": "90", "fuel_liters": "45.5"})).status_code == 303
    await db_session.refresh(expense)
    assert (expense.fuel_liters, expense.fuel_price_per_liter) == (Decimal("45.50"), Decimal("1.98"))
    assert (await client.post(edit, data={**base, "amount": "90"})).status_code == 303
    await db_session.refresh(expense)
    assert (expense.fuel_liters, expense.fuel_price_per_liter) == (None, None)


@pytest.mark.asyncio
async def test_form_is_localized_and_price_is_display_only(client, db_session, fleet_manager_user):
    truck = await _vehicle(db_session)
    await _login(client)
    bg = await client.get(f"/expenses/new?vehicle_id={truck.id}&category=fuel&lang=bg")
    assert bg.status_code == 200
    for text in ("Въведете сума", "Въведете литри", "Изчислява се автоматично"):
        assert text in bg.text
    assert 'name="fuel_price_per_liter"' not in bg.text  # not a submitted field
    en = await client.get(f"/expenses/new?vehicle_id={truck.id}&category=fuel&lang=en")
    assert "Enter the amount" in en.text and "Enter the liters" in en.text
    assert "Въведете сума" not in en.text


@pytest.mark.asyncio
async def test_api_derives_price_and_returns_exact_liters(client, manager_token):
    from tests.conftest import auth_header
    from tests.test_api.test_vehicles import VEHICLE_DATA

    headers = auth_header(manager_token)
    vehicle = await client.post("/api/v1/vehicles", json=VEHICLE_DATA, headers=headers)
    data = {"vehicle_id": vehicle.json()["id"], "category": "fuel", "date": "2026-01-01", "amount": "50.50"}
    created = await client.post("/api/v1/expenses", json={**data, "fuel_liters": "80.5"}, headers=headers)
    assert created.status_code == 201
    assert (created.json()["fuel_liters"], created.json()["fuel_price_per_liter"]) == ("80.50", "0.63")
    updated = await client.patch(
        f"/api/v1/expenses/{created.json()['id']}", json={"amount": "161.00"}, headers=headers
    )
    assert updated.json()["fuel_price_per_liter"] == "2.00"
    bad = await client.post("/api/v1/expenses", json={**data, "fuel_liters": "1.234"}, headers=headers)
    assert bad.status_code == 422
