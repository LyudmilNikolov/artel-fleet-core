"""CSRF: a logged-in browser must send the session token on every state-changing web request."""

import re

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def browser(app, admin_user):
    """A client WITHOUT the test helper that adds the CSRF header: it behaves like a forged cross-site request."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as raw:
        assert (await raw.post("/login", data={"username": "testadmin", "password": "testpass123"})).status_code == 302
        yield raw


def page_token(html: str) -> str:
    return re.search(r'<meta name="csrf-token" content="([^"]+)"', html).group(1)


@pytest.mark.asyncio
async def test_post_without_a_token_is_refused_and_does_nothing(browser):
    response = await browser.post("/profile", data={"action": "update_profile", "language": "en"})
    assert response.status_code == 403
    assert (await browser.get("/profile")).status_code == 200  # still logged in, nothing changed


@pytest.mark.asyncio
async def test_wrong_token_is_refused(browser):
    assert (
        await browser.post("/profile", data={"action": "update_profile", "language": "en", "csrf_token": "nope"})
    ).status_code == 403
    assert (
        await browser.post("/profile", data={"action": "update_profile"}, headers={"X-CSRF-Token": "nope"})
    ).status_code == 403


@pytest.mark.asyncio
async def test_the_page_token_works_as_a_form_field_and_as_a_header(browser):
    token = page_token((await browser.get("/profile")).text)
    as_field = await browser.post("/profile", data={"action": "update_profile", "language": "en", "csrf_token": token})
    assert as_field.status_code == 200
    as_header = await browser.post(
        "/profile", data={"action": "update_profile", "language": "bg"}, headers={"X-CSRF-Token": token}
    )
    assert as_header.status_code == 200


@pytest.mark.asyncio
async def test_htmx_style_post_and_logout_need_the_token(browser):
    token = page_token((await browser.get("/notifications")).text)
    assert (await browser.post("/notifications/mark-all-read")).status_code == 403
    assert (
        await browser.post("/notifications/mark-all-read", headers={"X-CSRF-Token": token, "HX-Request": "true"})
    ).status_code == 200
    assert (await browser.post("/logout")).status_code == 403
    out = await browser.post("/logout", headers={"X-CSRF-Token": token})
    assert out.status_code == 302 and (await browser.get("/vehicles")).status_code in (302, 303)
    assert (await browser.get("/logout")).status_code == 405  # logout is a POST only


@pytest.mark.asyncio
async def test_anonymous_login_and_the_bearer_api_are_not_affected(app, admin_user, admin_token):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as raw:
        assert (
            await raw.post("/login", data={"username": "testadmin", "password": "wrong-password-1"})
        ).status_code == 401
        api = await raw.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
        assert api.status_code == 200
        changed = await raw.post(
            "/api/v1/auth/change-password",
            json={"current_password": "testpass123", "new_password": "another-long-password"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert changed.status_code == 204


@pytest.mark.asyncio
async def test_pages_carry_the_token_and_the_script_sends_it(client, admin_user):
    login_page = (await client.get("/login")).text
    assert page_token(login_page)
    await client.post("/login", data={"username": "testadmin", "password": "testpass123"})
    page = (await client.get("/vehicles")).text
    assert page_token(page) and '<form method="POST" action="/logout">' in page
    script = (await client.get("/static/js/app.js")).text
    assert "X-CSRF-Token" in script and "htmx:configRequest" in script and 'name="csrf_token"' in script
