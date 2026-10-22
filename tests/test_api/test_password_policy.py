"""The 10-character password policy applies on every path that sets a password."""

import pytest
from httpx import AsyncClient

from app.models.user import UserRole
from app.services.auth_service import AuthService
from app.services.errors import DomainError
from tests.conftest import auth_header

USER = {"email": "p@test.com", "username": "polis", "full_name": "P", "role": "viewer", "language": "en"}


@pytest.mark.asyncio
async def test_api_user_creation_needs_ten_characters(client: AsyncClient, admin_token: str):
    short = await client.post("/api/v1/users", json={**USER, "password": "123456789"}, headers=auth_header(admin_token))
    assert short.status_code == 422
    ok = await client.post("/api/v1/users", json={**USER, "password": "1234567890"}, headers=auth_header(admin_token))
    assert ok.status_code == 201


@pytest.mark.asyncio
async def test_change_password_needs_ten_characters(client: AsyncClient, admin_token: str):
    body = {"current_password": "testpass123", "new_password": "short-pw9"}
    response = await client.post("/api/v1/auth/change-password", json=body, headers=auth_header(admin_token))
    assert response.status_code == 422
    body["new_password"] = "a-long-enough-password"
    done = await client.post("/api/v1/auth/change-password", json=body, headers=auth_header(admin_token))
    assert done.status_code == 204


@pytest.mark.asyncio
async def test_service_level_register_and_change_password_enforce_the_policy(db_session, admin_user):
    service = AuthService(db_session)
    with pytest.raises(DomainError):
        await service.register(
            email="a@b.co", username="newname", full_name="N", password="tooshort", role=UserRole.VIEWER
        )
    with pytest.raises(DomainError):
        await service.register(
            email="a@b.co", username="samename-1", full_name="N", password="samename-1", role=UserRole.VIEWER
        )
    with pytest.raises(DomainError):
        await service.change_password(admin_user.id, "testpass123", "short")
    await service.register(
        email="a@b.co", username="newname", full_name="N", password="long-enough-pw", role=UserRole.VIEWER
    )


@pytest.mark.asyncio
async def test_web_user_form_rejects_short_passwords(client: AsyncClient, admin_user):
    await client.post("/login", data={"username": "testadmin", "password": "testpass123"})
    form = {"username": "webuser", "email": "w@test.com", "full_name": "W", "role": "viewer", "language": "bg"}
    short = await client.post(
        "/settings/users/new", data={**form, "password": "short123", "password_confirm": "short123"}
    )
    assert short.status_code == 422 and "10" in short.text
    ok = await client.post(
        "/settings/users/new", data={**form, "password": "long-enough-1", "password_confirm": "long-enough-1"}
    )
    assert ok.status_code in (302, 303)
