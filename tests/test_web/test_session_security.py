"""Sessions and tokens: fresh session on login, flags, and invalidation when the password changes."""

import re

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import create_app

CREDENTIALS = {"username": "testadmin", "password": "testpass123"}


def token_of(html: str) -> str:
    return re.search(r'<meta name="csrf-token" content="([^"]+)"', html).group(1)


@pytest.mark.asyncio
async def test_login_starts_a_fresh_session_with_a_new_csrf_token(client, admin_user):
    before = token_of((await client.get("/login")).text)
    await client.post("/login", data=CREDENTIALS)
    after = token_of((await client.get("/vehicles")).text)
    assert before != after


@pytest.mark.asyncio
async def test_session_cookie_flags(client, admin_user):
    cookie = (await client.post("/login", data=CREDENTIALS)).headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=43200" in cookie
    assert "secure" not in cookie  # development over http


@pytest.mark.asyncio
async def test_session_cookie_is_secure_in_production(monkeypatch, db_session):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as secure:
        cookie = (await secure.get("/login")).headers["set-cookie"].lower()
    assert "secure" in cookie


@pytest.mark.asyncio
async def test_changing_the_password_signs_out_other_browsers_and_api_tokens(app, client, admin_user):
    login = await client.post("/api/v1/auth/login", json=CREDENTIALS)
    old_access, old_refresh = login.json()["access_token"], login.json()["refresh_token"]
    old_headers = {"Authorization": f"Bearer {old_access}"}
    assert (await client.get("/api/v1/auth/me", headers=old_headers)).status_code == 200

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await other.post("/login", data=CREDENTIALS)
        assert (await other.get("/vehicles")).status_code == 200

        await client.post("/login", data=CREDENTIALS)
        done = await client.post(
            "/profile",
            data={
                "action": "change_password",
                "current_password": "testpass123",
                "new_password": "a-brand-new-password",
            },
        )
        assert done.status_code == 200
        assert (await client.get("/vehicles")).status_code == 200  # the browser that changed it stays signed in

        assert (await other.get("/vehicles")).status_code in (302, 303)  # the other one is signed out
        assert (await client.get("/api/v1/auth/me", headers=old_headers)).status_code == 401
        assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})).status_code == 401

    fresh = await client.post("/api/v1/auth/login", json={"username": "testadmin", "password": "a-brand-new-password"})
    assert fresh.status_code == 200
    new_headers = {"Authorization": f"Bearer {fresh.json()['access_token']}"}
    assert (await client.get("/api/v1/auth/me", headers=new_headers)).status_code == 200
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": fresh.json()["refresh_token"]})
    ).status_code == 200


@pytest.mark.asyncio
async def test_tokens_without_the_password_marker_are_refused(client, admin_user):
    from app.utils.security import create_access_token

    legacy = create_access_token(admin_user.id, admin_user.role.value)
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"})).status_code == 401
