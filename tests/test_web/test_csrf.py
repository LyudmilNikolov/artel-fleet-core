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


@pytest.fixture
async def manager_browser(app, fleet_manager_user):
    """A real-browser-like client: no test helper adds the CSRF header, so only the hidden form field can carry it."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as raw:
        assert (await raw.post("/login", data={"username": "testmanager", "password": "testpass123"})).status_code == 302
        yield raw


VEHICLE_FORM = {
    "vehicle_type": "van",
    "license_plate": "CB8595CB",
    "brand": "Renault",
    "model": "Premium",
    "department": "Test department",
    "year": "2011",
    "fuel_type": "diesel",
    "transmission": "automatic",
}


@pytest.mark.asyncio
async def test_the_hidden_token_field_is_not_passed_on_to_strict_validators(manager_browser):
    """Regression: forms carry a csrf_token field; handlers hand the form to schemas that forbid unknown fields."""
    token = page_token((await manager_browser.get("/vehicles/new")).text)
    created = await manager_browser.post("/vehicles/new", data={**VEHICLE_FORM, "csrf_token": token})
    assert created.status_code == 302, created.text  # not a 422 "invalid input"
    driver = await manager_browser.post("/drivers/new", data={"full_name": "Иван Иванов", "csrf_token": token})
    assert driver.status_code in (302, 303), driver.text


@pytest.mark.asyncio
async def test_a_header_plus_a_form_field_also_works_and_validation_errors_stay_specific(manager_browser):
    token = page_token((await manager_browser.get("/vehicles/new")).text)
    both = await manager_browser.post(
        "/vehicles/new", data={**VEHICLE_FORM, "license_plate": "CB1111CB", "csrf_token": token}, headers={"X-CSRF-Token": token}
    )
    assert both.status_code == 302  # an HTMX form post sends the header and the field together
    bad = await manager_browser.post("/vehicles/new", data={**VEHICLE_FORM, "year": "bad", "csrf_token": token})
    assert bad.status_code == 422 and "bad" in bad.text  # a real validation error, shown with the entered value


@pytest.mark.asyncio
async def test_dependency_strips_the_field_from_urlencoded_and_multipart_bodies():
    from starlette.requests import Request

    from app.web.csrf import csrf_protect

    async def run(content_type: str, body: bytes):
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/x",
                "query_string": b"",
                "headers": [(b"content-type", content_type.encode()), (b"x-csrf-token", b"tok")],
                "session": {"user_id": "u", "csrf": "tok"},
            },
            receive,
        )
        await csrf_protect(request)
        return await request.form()

    form = await run("application/x-www-form-urlencoded", b"a=1&csrf_token=tok&b=2")
    assert dict(form) == {"a": "1", "b": "2"}
    boundary = "xyz"
    multipart = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="csrf_token"\r\n\r\ntok\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="entity_id"\r\n\r\n42\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\nContent-Type: application/pdf\r\n\r\n%PDF-1.4\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    form = await run(f"multipart/form-data; boundary={boundary}", multipart)
    assert set(form.keys()) == {"entity_id", "file"} and form["file"].filename == "a.pdf"


@pytest.mark.asyncio
async def test_login_form_works_when_a_session_is_already_signed_in(browser):
    """A stale login tab (or Back button) submits an old token while the browser is already logged in."""
    response = await browser.post(
        "/login", data={"username": "testadmin", "password": "testpass123", "csrf_token": "stale"}
    )
    assert response.status_code == 302
    assert (await browser.get("/vehicles")).status_code == 200
