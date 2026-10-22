"""Tests for vehicle API endpoints."""

import pytest
from httpx import AsyncClient

from tests.conftest import auth_header

VEHICLE_DATA = {
    "license_plate": "123 ABC 01",
    "vin": "JTDKN3DU5A0123456",
    "brand": "Toyota",
    "model": "Camry",
    "year": 2023,
    "color": "White",
    "vehicle_type": "passenger_car",
    "department": "Test department",
    "fuel_type": "gasoline",
    "transmission": "automatic",
    "seats": 5,
    "status": "active",
}


@pytest.mark.asyncio
async def test_list_vehicles(client: AsyncClient, admin_token: str):
    """GET /api/v1/vehicles returns 200 with paginated response containing total and items."""
    response = await client.get(
        "/api/v1/vehicles",
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)
    assert isinstance(data["total"], int)


@pytest.mark.asyncio
async def test_create_vehicle(client: AsyncClient, manager_token: str):
    """POST /api/v1/vehicles with valid data and manager token returns 201."""
    response = await client.post(
        "/api/v1/vehicles",
        json=VEHICLE_DATA,
        headers=auth_header(manager_token),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["license_plate"] == "123 ABC 01"
    assert data["vin"] == "JTDKN3DU5A0123456"
    assert data["brand"] == "Toyota"
    assert data["model"] == "Camry"
    assert data["year"] == 2023
    assert data["status"] == "active"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_vehicle_unauthorized(client: AsyncClient):
    """POST /api/v1/vehicles without token returns 401."""
    response = await client.post(
        "/api/v1/vehicles",
        json=VEHICLE_DATA,
    )
    assert response.status_code == 401 or response.status_code == 403


@pytest.mark.asyncio
async def test_get_vehicle_by_id(client: AsyncClient, manager_token: str):
    """Create a vehicle then GET /api/v1/vehicles/{id} returns 200 with vehicle data."""
    # Create the vehicle first
    create_response = await client.post(
        "/api/v1/vehicles",
        json=VEHICLE_DATA,
        headers=auth_header(manager_token),
    )
    assert create_response.status_code == 201
    vehicle_id = create_response.json()["id"]

    # Retrieve by ID
    response = await client.get(
        f"/api/v1/vehicles/{vehicle_id}",
        headers=auth_header(manager_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == vehicle_id
    assert data["license_plate"] == "123 ABC 01"
    assert data["brand"] == "Toyota"


@pytest.mark.asyncio
async def test_create_vehicle_duplicate_plate(client: AsyncClient, manager_token: str):
    """Creating two vehicles with the same license plate returns 400."""
    # Create first vehicle
    response1 = await client.post(
        "/api/v1/vehicles",
        json=VEHICLE_DATA,
        headers=auth_header(manager_token),
    )
    assert response1.status_code == 201

    # Attempt to create second vehicle with the same plate but different VIN
    duplicate_data = {**VEHICLE_DATA, "vin": "JTDKN3DU5A9999999"}
    response2 = await client.post(
        "/api/v1/vehicles",
        json=duplicate_data,
        headers=auth_header(manager_token),
    )
    assert response2.status_code == 400


@pytest.mark.asyncio
async def test_initial_and_edited_mileage_serialize_vehicle_timestamps(client, manager_token):
    """Mileage updates must not leave server timestamps requiring implicit async I/O."""
    headers = auth_header(manager_token)
    response = await client.post("/api/v1/vehicles", json={**VEHICLE_DATA, "current_mileage": 10}, headers=headers)
    assert response.status_code == 201
    data = response.json()
    assert data["current_mileage"] == 10
    assert data["updated_at"]
    response = await client.patch(f"/api/v1/vehicles/{data['id']}", json={"current_mileage": 20}, headers=headers)
    assert response.status_code == 200
    assert response.json()["current_mileage"] == 20
    assert response.json()["updated_at"]
