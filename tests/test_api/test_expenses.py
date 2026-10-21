"""EUR and exact cents at the actual HTTP boundary."""

import pytest

from tests.conftest import auth_header
from tests.test_api.test_vehicles import VEHICLE_DATA


@pytest.mark.asyncio
async def test_eur_create_update_and_report(client, manager_token):
    headers = auth_header(manager_token)
    response = await client.post("/api/v1/vehicles", json=VEHICLE_DATA, headers=headers)
    assert response.status_code == 201
    vehicle_id = response.json()["id"]
    data = dict(vehicle_id=vehicle_id, category="fuel", amount="0.10", date="2026-01-01")
    first = await client.post("/api/v1/expenses", json=data, headers=headers)
    assert first.status_code == 201
    assert first.json()["currency"] == "EUR"
    assert first.json()["amount"] == "0.10"
    second = await client.post("/api/v1/expenses", json={**data, "amount": "0.20"}, headers=headers)
    assert second.status_code == 201
    for currency in ["KZT", "RUB", "USD", "TRY", "eur", "GBP", None]:
        invalid = await client.post("/api/v1/expenses", json={**data, "currency": currency}, headers=headers)
        assert invalid.status_code == 422
        invalid = await client.patch(
            f"/api/v1/expenses/{first.json()['id']}", json={"currency": currency}, headers=headers
        )
        assert invalid.status_code == 422
    for amount in ["0.001", "10000000000.00"]:
        invalid = await client.post("/api/v1/expenses", json={**data, "amount": amount}, headers=headers)
        assert invalid.status_code == 422
    report = await client.get("/api/v1/reports/expense-analysis", headers=headers)
    assert report.status_code == 200
    assert report.json()["grand_total"] == "0.30"
