"""Security headers and the country restriction."""

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, settings
from app.main import create_app
from app.security import allowed_countries, content_security_policy


@pytest.fixture
def make_client(monkeypatch):
    def factory(**changes):
        for name, value in changes.items():
            monkeypatch.setattr(settings, name, value)
        return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")

    return factory


@pytest.mark.asyncio
async def test_every_response_has_the_hardening_headers(make_client):
    async with make_client(CSP_MODE="enforce", APP_ENV="development") as client:
        for path in ("/login", "/does-not-exist", "/api/v1/health"):
            headers = (await client.get(path)).headers
            assert headers["x-content-type-options"] == "nosniff"
            assert headers["x-frame-options"] == "DENY"
            assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
            assert "camera=()" in headers["permissions-policy"]
            assert "frame-ancestors 'none'" in headers["content-security-policy"], path
            assert "strict-transport-security" not in headers  # only in production


@pytest.mark.asyncio
async def test_hsts_in_production_and_csp_modes(make_client):
    async with make_client(APP_ENV="production", CSP_MODE="report-only") as client:
        headers = (await client.get("/api/v1/health")).headers
        assert "max-age=31536000" in headers["strict-transport-security"]
        assert "content-security-policy-report-only" in headers and "content-security-policy" not in headers
    async with make_client(APP_ENV="development", CSP_MODE="off") as client:
        headers = (await client.get("/api/v1/health")).headers
        assert "content-security-policy" not in headers and "content-security-policy-report-only" not in headers


def test_csp_allows_only_self_and_the_file_host(monkeypatch):
    monkeypatch.setattr(settings, "MINIO_EXTERNAL_ENDPOINT", "files.example.com")
    monkeypatch.setattr(settings, "MINIO_EXTERNAL_USE_SSL", True)
    policy = content_security_policy()
    assert "default-src 'self'" in policy and "object-src 'none'" in policy and "form-action 'self'" in policy
    assert "img-src 'self' data: blob: https://files.example.com" in policy
    assert "frame-src 'self' https://files.example.com" in policy
    assert "connect-src 'self';" in policy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("country", "expected"),
    [("BG", 200), ("bg", 200), ("DE", 403), ("RU", 403), ("XX", 403), ("T1", 403), (None, 200)],
)
async def test_enforce_mode_allows_only_listed_countries(make_client, country, expected):
    async with make_client(GEO_MODE="enforce", ALLOWED_COUNTRIES="BG") as client:
        headers = {"CF-IPCountry": country} if country else {}
        response = await client.get("/login", headers=headers)
        assert response.status_code == expected
        if expected == 403:
            assert "not allowed" in response.text and response.headers["x-frame-options"] == "DENY"
            assert (await client.get("/api/v1/auth/me", headers=headers)).json() == {
                "detail": "Access from your country is not allowed"
            }


@pytest.mark.asyncio
async def test_health_check_is_always_reachable_and_the_list_can_have_several_countries(make_client):
    async with make_client(GEO_MODE="enforce", ALLOWED_COUNTRIES="bg, DE ") as client:
        assert (await client.get("/api/v1/health", headers={"CF-IPCountry": "US"})).status_code == 200
        assert (await client.get("/login", headers={"CF-IPCountry": "DE"})).status_code == 200
        assert (await client.get("/login", headers={"CF-IPCountry": "FR"})).status_code == 403
    assert allowed_countries() == {"BG", "DE"}


@pytest.mark.asyncio
async def test_log_mode_only_logs_and_off_ignores_the_header(make_client, caplog):
    caplog.set_level(logging.WARNING)
    async with make_client(GEO_MODE="log", ALLOWED_COUNTRIES="BG") as client:
        assert (await client.get("/login", headers={"CF-IPCountry": "DE"})).status_code == 200
        assert (await client.get("/login")).status_code == 200
    assert "would block DE" in caplog.text and "no CF-IPCountry header" in caplog.text
    async with make_client(GEO_MODE="off") as client:
        assert (await client.get("/login", headers={"CF-IPCountry": "DE"})).status_code == 200


def test_enforcing_with_an_empty_list_is_refused_in_production():
    base = dict(APP_ENV="production", SECRET_KEY="k" * 40, DATABASE_URL="postgresql+asyncpg://u:p@db/x")
    with pytest.raises(Exception):
        Settings(_env_file=None, GEO_MODE="enforce", ALLOWED_COUNTRIES=" ", **base)
    assert Settings(_env_file=None, GEO_MODE="enforce", ALLOWED_COUNTRIES="BG", **base).GEO_MODE == "enforce"
