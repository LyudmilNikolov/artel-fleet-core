import pytest

from tests.conftest import auth_header


@pytest.mark.asyncio
async def test_form_nulls_and_error_redisplay(client, manager_token):
    await client.post("/login", data={"username": "testmanager", "password": "testpass123"})
    headers = auth_header(manager_token)
    response = await client.get("/vehicles/new", headers=headers)
    assert response.status_code == 200
    assert "vehicle_type" in response.text
    response = await client.post(
        "/vehicles/new",
        headers=headers,
        data={
            "vehicle_type": "van",
            "license_plate": "TEST",
            "brand": "Keep this",
            "model": "M",
            "department": "Test department",
            "year": "bad",
            "fuel_type": "",
            "transmission": "",
        },
    )
    assert response.status_code == 422
    assert "Keep this" in response.text
    assert "bad" in response.text
    # Number controls sanitize invalid strings in browsers; error redisplay must
    # use a text control so the original entry remains visible and editable.
    import re

    year_input = re.search(r'<input[^>]*id="year"[^>]*>', response.text).group()
    assert 'type="text"' in year_input
    response = await client.post(
        "/vehicles/new",
        headers=headers,
        data={
            "vehicle_type": "van",
            "license_plate": "TEST",
            "brand": "B",
            "model": "M",
            "department": "Test department",
            "year": "",
            "fuel_type": "",
            "transmission": "",
        },
    )
    assert response.status_code == 302
    detail = await client.get(response.headers["location"], headers=headers)
    assert detail.status_code == 200
    assert "None" not in detail.text
    edit = await client.get(response.headers["location"] + "/edit", headers=headers)
    assert edit.status_code == 200


@pytest.mark.asyncio
async def test_empty_department_configuration(client, manager_token, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "VEHICLE_DEPARTMENTS", [])
    await client.post("/login", data={"username": "testmanager", "password": "testpass123"})
    response = await client.get("/vehicles/new", headers=auth_header(manager_token))
    assert response.status_code == 200
    assert "disabled" in response.text
    response = await client.get("/api/v1/vehicles/departments", headers=auth_header(manager_token))
    assert response.json() == []
