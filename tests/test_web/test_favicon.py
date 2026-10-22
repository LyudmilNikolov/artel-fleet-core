"""The truck favicon is linked from every page layout and served at the default /favicon.ico path."""

import pytest


@pytest.mark.asyncio
async def test_favicon_files_and_links(client):
    for path, media in (
        ("/favicon.ico", "image/png"),
        ("/static/img/favicon.svg", "image/svg+xml"),
        ("/static/img/favicon-32.png", "image/png"),
        ("/static/img/favicon-180.png", "image/png"),
    ):
        response = await client.get(path)
        assert response.status_code == 200 and response.headers["content-type"].startswith(media), path
    login = (await client.get("/login")).text
    assert 'rel="icon" type="image/svg+xml" href="/static/img/favicon.svg"' in login
    assert 'rel="apple-touch-icon"' in login


@pytest.mark.asyncio
async def test_app_layout_links_the_favicon(client, admin_user):
    await client.post("/login", data={"username": "testadmin", "password": "testpass123"})
    page = (await client.get("/vehicles")).text
    assert 'href="/static/img/favicon.svg"' in page and 'href="/static/img/favicon-32.png"' in page


@pytest.mark.asyncio
async def test_app_name_follows_the_language(client, admin_user):
    await client.post("/login", data={"username": "testadmin", "password": "testpass123"})
    bg = (await client.get("/vehicles?lang=bg")).text
    assert ">Автопарк</span>" in bg and ">FleetCore</span>" not in bg and "<title>" in bg
    en = (await client.get("/vehicles?lang=en")).text
    assert ">FleetCore</span>" in en
    await client.post("/logout")
    assert "<h1 class=\"text-2xl font-bold text-gray-900 dark:text-white\">Автопарк</h1>" in (await client.get("/login?lang=bg")).text
