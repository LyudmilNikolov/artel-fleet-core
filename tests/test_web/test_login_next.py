"""QA #35: the username survives a wrong password, the requested page survives the login, the checkbox has a name."""

import pytest

from app.web.login_next import safe_next


@pytest.mark.parametrize(
    "value",
    [None, "", "dashboard", "//evil.example/x", "https://evil.example/", "/\\evil.example", "/ok\r\nSet-Cookie: x=1",
     "/login", "/login?next=/x", "/logout", "/static/x.js", "/reports/export/tco.xlsx", "javascript:alert(1)", "/" + "a" * 3000],
)
def test_safe_next_refuses_anything_but_a_plain_local_page(value):
    assert safe_next(value) == "/"
    assert safe_next(value, "/fallback") == "/fallback"


@pytest.mark.parametrize("value", ["/vehicles", "/vehicles?q=abc&page=2", "/expenses/new?vehicle_id=1", "/drivers/x#licences"])
def test_safe_next_keeps_local_pages(value):
    assert safe_next(value) == value


@pytest.mark.asyncio
async def test_signed_out_page_redirects_to_login_with_next_and_back_after_login(client, db_session, fleet_manager_user):
    redirect = await client.get("/vehicles?q=abc&page=2", follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "/login?next=%2Fvehicles%3Fq%3Dabc%26page%3D2"
    form = await client.get(redirect.headers["location"])
    assert '<input type="hidden" name="next" value="/vehicles?q=abc&amp;page=2">' in form.text
    done = await client.post(
        "/login",
        data={"username": "testmanager", "password": "testpass123", "next": "/vehicles?q=abc&page=2"},
        follow_redirects=False,
    )
    assert done.status_code == 302 and done.headers["location"] == "/vehicles?q=abc&page=2"


@pytest.mark.asyncio
async def test_login_ignores_a_foreign_or_loop_next(client, db_session, fleet_manager_user):
    for evil in ("https://evil.example/", "//evil.example", "/login", "/\\evil.example"):
        done = await client.post(
            "/login", data={"username": "testmanager", "password": "testpass123", "next": evil}, follow_redirects=False
        )
        assert done.status_code == 302 and done.headers["location"] == "/", evil
        await client.post("/logout")
    form = await client.get("/login?next=https://evil.example/")
    assert 'name="next"' not in form.text  # a bad next is not even carried through the form


@pytest.mark.asyncio
async def test_no_next_for_posts_htmx_api_and_login_itself(client, db_session):
    assert (await client.get("/login", follow_redirects=False)).status_code == 200
    htmx = await client.get("/vehicles", headers={"HX-Request": "true"}, follow_redirects=False)
    assert htmx.headers.get("location", "/login") == "/login"
    post = await client.post("/vehicles/new", follow_redirects=False)
    assert post.headers.get("location", "/login") in ("/login",)


@pytest.mark.asyncio
async def test_wrong_password_keeps_the_username_and_next_but_not_the_password(client, db_session, fleet_manager_user):
    response = await client.post(
        "/login", data={"username": "testmanager", "password": "wrong-password-1", "next": "/expenses"}
    )
    assert response.status_code == 401
    assert 'value="testmanager"' in response.text
    assert 'name="next" value="/expenses"' in response.text
    assert "wrong-password-1" not in response.text
    # a name with markup is escaped
    odd = await client.post("/login", data={"username": '"><script>x</script>', "password": "nope-nope-1"})
    assert "<script>x</script>" not in odd.text


@pytest.mark.asyncio
async def test_remember_me_checkbox_has_a_real_label(client):
    page = await client.get("/login?lang=en")
    assert 'for="remember"' in page.text and 'id="remember"' in page.text and "Remember me" in page.text
