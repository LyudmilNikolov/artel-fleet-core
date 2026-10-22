"""Tests for reports API endpoints."""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from tests.conftest import auth_header


@pytest.mark.asyncio
async def test_fleet_utilization(client: AsyncClient, admin_token: str):
    """GET /api/v1/reports/fleet-utilization returns 200 with utilization data."""
    response = await client.get(
        "/api/v1/reports/fleet-utilization",
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "active" in data
    assert "utilization_rate" in data


@pytest.mark.asyncio
async def test_tco_report(client: AsyncClient, admin_token: str):
    """GET /api/v1/reports/tco returns 200 with a list."""
    response = await client.get(
        "/api/v1/reports/tco",
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_reports_require_auth(client: AsyncClient):
    """GET /api/v1/reports/tco without token returns 401."""
    response = await client.get("/api/v1/reports/tco")
    assert response.status_code == 401 or response.status_code == 403


@pytest.mark.asyncio
async def test_new_filters_are_optional_and_validated(client: AsyncClient, admin_token: str):
    """Old calls keep working; new query parameters are optional and invalid input is a localized 422."""
    headers = auth_header(admin_token)
    for path in ("tco", "fuel-consumption", "expense-analysis", "maintenance-history"):
        assert (await client.get(f"/api/v1/reports/{path}", headers=headers)).status_code == 200
        ok = await client.get(
            f"/api/v1/reports/{path}?start_date=2026-01-01&vehicle_type=trailer&vehicle_id={uuid4()}", headers=headers
        )
        assert ok.status_code == 200, path
        bad = await client.get(
            f"/api/v1/reports/{path}?vehicle_type=boat", headers={**headers, "Accept-Language": "en"}
        )
        assert bad.status_code == 422 and bad.json()["detail"] == "Unknown vehicle type."
    reversed_dates = await client.get(
        "/api/v1/reports/tco?start_date=2026-05-01&end_date=2026-01-01", headers={**headers, "Accept-Language": "en"}
    )
    assert reversed_dates.status_code == 422 and "cannot be before" in reversed_dates.json()["detail"]
    assert (await client.get("/api/v1/reports/maintenance-history?status=nope", headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_report_rbac_matrix(client: AsyncClient, viewer_token: str, manager_token: str):
    """Data endpoints are open to any authenticated user; exports need admin or fleet manager."""
    viewer, manager = auth_header(viewer_token), auth_header(manager_token)
    assert (await client.get("/api/v1/reports/maintenance-history", headers=viewer)).status_code == 200
    for export in ("tco.xlsx", "fuel.xlsx", "maintenance.xlsx", "expenses.csv"):
        assert (await client.get(f"/api/v1/reports/export/{export}", headers=viewer)).status_code == 403
        assert (await client.get(f"/api/v1/reports/export/{export}", headers=manager)).status_code == 200
