"""Web forms validate currency even though the control is fixed to EUR."""

import pytest


@pytest.mark.asyncio
async def test_web_expense_currency_rejected(client, admin_user, db_session):
    # Commit only the fixture savepoint; the outer test transaction still rolls back.
    await db_session.commit()
    login = await client.post("/login", data={"username": admin_user.username, "password": "testpass123"})
    assert login.status_code == 302
    for currency in ["KZT", "RUB", "USD", "TRY", "eur", "GBP", ""]:
        response = await client.post(
            "/expenses/new",
            data={
                "vehicle_id": "00000000-0000-0000-0000-000000000001",
                "category": "fuel",
                "amount": "0.10",
                "date": "2026-01-01",
                "currency": currency,
            },
        )
        assert response.status_code == 400
        assert "ValidationError" not in response.text
        assert "EUR" in response.text
