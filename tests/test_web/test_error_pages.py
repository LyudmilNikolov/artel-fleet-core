"""QA #16: browsers get an app-styled 404 / 422 page; API and non-browser clients keep JSON."""

import pytest

HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


async def _login(client):
    assert (await client.post("/login", data={"username": "testmanager", "password": "testpass123"})).status_code == 302


@pytest.mark.asyncio
async def test_unknown_page_is_a_styled_404_with_navigation(client, db_session, fleet_manager_user):
    await _login(client)
    page = await client.get("/no-such-page", headers=HTML)
    assert page.status_code == 404
    assert "text/html" in page.headers["content-type"]
    assert "Page not found" in page.text and 'href="/vehicles"' in page.text  # layout with the sidebar
    assert '"detail"' not in page.text
    bg = await client.get("/no-such-page?lang=bg", headers=HTML)
    assert bg.status_code == 404 and "Страницата не е намерена" in bg.text


@pytest.mark.asyncio
async def test_malformed_id_is_a_styled_422(client, db_session, fleet_manager_user):
    await _login(client)
    for path in ("/vehicles/not-a-uuid", "/maintenance/abc"):
        page = await client.get(path, headers=HTML)
        assert page.status_code == 422, path
        assert "text/html" in page.headers["content-type"] and "This request is not valid" in page.text, path
        assert "uuid_parsing" not in page.text and '"detail"' not in page.text and 'href="/vehicles"' in page.text


@pytest.mark.asyncio
async def test_logged_out_visitor_gets_a_minimal_page_with_a_way_in(client):
    page = await client.get("/no-such-page?lang=en", headers=HTML)
    assert page.status_code == 404 and "Page not found" in page.text
    assert 'href="/login"' in page.text and 'href="/vehicles"' not in page.text  # no app navigation


@pytest.mark.asyncio
async def test_api_and_non_browser_requests_keep_json(client, db_session, fleet_manager_user, manager_token):
    from tests.conftest import auth_header

    await _login(client)
    plain = await client.get("/no-such-page")  # no Accept: text/html
    assert plain.status_code == 404 and set(plain.json()) == {"detail"}
    api = await client.get("/api/v1/vehicles/not-a-uuid", headers={**HTML, **auth_header(manager_token)})
    assert api.status_code == 422 and "detail" in api.json()
    missing = await client.get("/api/v1/no-such-route", headers={**HTML, **auth_header(manager_token)})
    assert missing.status_code == 404 and set(missing.json()) == {"detail"}
    htmx = await client.get("/no-such-page", headers={**HTML, "HX-Request": "true"})
    assert htmx.status_code == 404 and "text/html" not in htmx.headers["content-type"]  # fragments are not pages
