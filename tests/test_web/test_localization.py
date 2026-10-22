"""Exercise signed sessions and request boundaries without a development database."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import create_app
from app.models.user import UserRole
from app.repositories.user_repo import UserRepository
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService


@pytest.fixture
async def locale_client(monkeypatch):
    user = SimpleNamespace(id=uuid4(), language="en", is_active=True, role=UserRole.ADMIN)

    async def update(instance, **changes):
        for key, value in changes.items():
            setattr(instance, key, value)
        return instance

    monkeypatch.setattr(AuthService, "authenticate", AsyncMock(return_value=(user, "access", "refresh")))
    monkeypatch.setattr(UserRepository, "get_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(UserRepository, "update", AsyncMock(side_effect=update))
    monkeypatch.setattr(AuditService, "log", AsyncMock())  # no database in these tests
    app = create_app()

    async def no_db():
        yield None

    app.dependency_overrides[get_db] = no_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, user


@pytest.mark.asyncio
async def test_browser_defaults_and_explicit_english_survives_login_logout(locale_client):
    client, user = locale_client
    response = await client.get("/login", headers={"Accept-Language": "en-GB"})
    assert '<html lang="bg"' in response.text
    await client.get("/set-language/en?next=/login")
    assert '<html lang="en"' in (await client.get("/login")).text
    await client.post("/login", data={"username": "test", "password": "pass"})
    assert user.language == "en"
    await client.get("/logout")
    assert '<html lang="en"' in (await client.get("/login")).text


@pytest.mark.asyncio
async def test_saved_english_without_session_selection(locale_client):
    client, _ = locale_client
    await client.post("/login", data={"username": "test", "password": "pass"})
    await client.get("/logout")
    assert '<html lang="en"' in (await client.get("/login")).text


@pytest.mark.asyncio
async def test_explicit_anonymous_bulgarian_updates_saved_user(locale_client):
    client, user = locale_client
    await client.get("/set-language/bg")
    await client.post("/login", data={"username": "test", "password": "pass"})
    assert user.language == "bg"


@pytest.mark.asyncio
async def test_query_override_is_local_and_validation_is_translated(locale_client):
    client, _ = locale_client
    assert '<html lang="en"' in (await client.get("/login?lang=en")).text
    assert '<html lang="bg"' in (await client.get("/login")).text
    bg = await client.post("/login", data={})
    en = await client.post("/login?lang=en", data={})
    assert bg.status_code == en.status_code == 422
    assert bg.json()["detail"][0]["msg"] == "Полето е задължително"
    assert en.json()["detail"][0]["msg"] == "Field required"
    assert bg.json()["detail"][0]["loc"] == ["body", "username"]


@pytest.mark.asyncio
async def test_anonymous_logout_does_not_override_saved_english(locale_client):
    client, user = locale_client
    await client.get("/logout")
    await client.post("/login", data={"username": "test", "password": "pass"})
    assert user.language == "en"
    await client.get("/logout")
    assert '<html lang="en"' in (await client.get("/login")).text


@pytest.mark.asyncio
async def test_stale_anonymous_session_then_saved_english_login(locale_client):
    import base64
    import json

    from itsdangerous import TimestampSigner

    from app.config import settings

    client, user = locale_client
    session = base64.b64encode(json.dumps({"lang": "ru"}).encode())
    cookie = TimestampSigner(str(settings.SECRET_KEY)).sign(session).decode()
    client.cookies.set("fleetcore_session", cookie)
    page = await client.get("/login")
    assert '<html lang="bg"' in page.text
    await client.post("/login", data={"username": "test", "password": "pass"})
    assert user.language == "en"
    await client.get("/logout")
    assert '<html lang="en"' in (await client.get("/login")).text


@pytest.mark.asyncio
async def test_interleaved_clients_keep_independent_locale_and_validation(locale_client):
    client, _ = locale_client
    async with AsyncClient(transport=client._transport, base_url="http://test") as other:
        await client.get("/set-language/en")
        await other.get("/set-language/bg")
        for _ in range(2):
            assert '<html lang="en"' in (await client.get("/login")).text
            assert '<html lang="bg"' in (await other.get("/login")).text
            english = await client.post("/api/v1/auth/login", json={}, headers={"Accept-Language": "en-GB"})
            bulgarian = await other.post("/api/v1/auth/login", json={}, headers={"Accept-Language": "bg-BG"})
            assert english.status_code == bulgarian.status_code == 422
            assert english.json()["detail"][0]["loc"] == bulgarian.json()["detail"][0]["loc"]
            assert english.json()["detail"][0]["msg"] != bulgarian.json()["detail"][0]["msg"]
