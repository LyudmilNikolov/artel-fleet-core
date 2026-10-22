"""Admin user management in the web UI: create, edit, reset password, deactivate, guard rails."""

import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.user import User, UserRole

NEW = {
    "username": "newdriver",
    "full_name": "New Driver",
    "email": "newdriver@example.com",
    "role": "fleet_manager",
    "language": "en",
    "password": "secret-123",
    "password_confirm": "secret-123",
}


async def login(client, username="testadmin", password="testpass123"):
    return await client.post("/login", data={"username": username, "password": password})


async def count_users(db):
    return await db.scalar(select(func.count()).select_from(User))


async def user_named(db, username):
    return await db.scalar(select(User).where(User.username == username))


@pytest.mark.asyncio
async def test_list_has_create_and_edit_links(client, db_session, admin_user):
    await db_session.commit()
    await login(client)
    page = await client.get("/settings/users")
    assert page.status_code == 200
    assert 'href="/settings/users/new"' in page.text and f'href="/settings/users/{admin_user.id}/edit"' in page.text
    assert "Administrator" in page.text  # role shown with its translated label


@pytest.mark.asyncio
async def test_form_renders_clean_options_and_headings(client, db_session, admin_user):
    await db_session.commit()
    await login(client)
    await client.get("/set-language/en")
    page = await client.get("/settings/users/new")
    assert '<option value="bg"' in page.text and '<option value="en"' in page.text
    assert "CODE" not in page.text  # a language dict leaked into the select once
    for role in ("admin", "fleet_manager", "driver", "viewer"):
        assert f'<option value="{role}"' in page.text
    assert "settings.account_section" not in page.text and "Account" in page.text  # translated headings
    assert 'class="fc-btn-primary inline-flex' in (await client.get("/settings/users")).text


@pytest.mark.asyncio
async def test_admin_creates_user_who_can_log_in(client, db_session, admin_user):
    await db_session.commit()
    await login(client)
    assert (await client.get("/settings/users/new")).status_code == 200
    created = await client.post("/settings/users/new", data=NEW, follow_redirects=False)
    assert created.status_code == 303 and created.headers["location"] == "/settings/users"
    user = await user_named(db_session, "newdriver")
    assert user.role == UserRole.FLEET_MANAGER and user.language == "en" and user.is_active
    assert user.hashed_password != NEW["password"]
    # audit: who created the account, and no password material
    entry = await db_session.scalar(select(AuditLog).where(AuditLog.entity_id == user.id))
    assert entry.user_id == admin_user.id and entry.changes["hashed_password"] == "***"
    await client.post("/logout")
    assert (await login(client, "newdriver", "secret-123")).status_code == 302


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"password": "abc", "password_confirm": "abc"}, "10 to 128"),
        ({"password_confirm": "different"}, "do not match"),
        ({"email": "not-an-email"}, "valid email"),
        ({"username": "ab"}, "3 to 100"),
        ({"full_name": " "}, "Full name is required"),
        ({"role": "boss"}, "Invalid input"),
        ({"username": "testadmin"}, "already taken"),
        ({"email": "testadmin@test.com"}, "already registered"),
    ],
)
async def test_create_validation_errors(client, db_session, admin_user, override, message):
    await db_session.commit()
    await login(client)
    await client.get("/set-language/en")
    before = await count_users(db_session)
    response = await client.post("/settings/users/new", data={**NEW, **override})
    assert response.status_code == 422 and message in response.text
    assert await count_users(db_session) == before
    # the form never echoes the password back
    assert "secret-123" not in response.text


@pytest.mark.asyncio
async def test_edit_changes_profile_and_password_is_optional(client, db_session, admin_user, viewer_user):
    await db_session.commit()
    await login(client)
    form = {
        "full_name": "Renamed",
        "email": "renamed@example.com",
        "role": "fleet_manager",
        "language": "bg",
        "is_active": "on",
    }
    assert (await client.get(f"/settings/users/{viewer_user.id}/edit")).status_code == 200
    assert (await client.post(f"/settings/users/{viewer_user.id}/edit", data=form)).status_code == 303
    await db_session.refresh(viewer_user)
    assert (viewer_user.full_name, viewer_user.role, viewer_user.language) == ("Renamed", UserRole.FLEET_MANAGER, "bg")
    await client.post("/logout")
    assert (await login(client, "testviewer")).status_code == 302  # empty password kept the old one
    await client.post("/logout")
    await login(client)
    reset = {**form, "password": "brand-new-1", "password_confirm": "brand-new-1"}
    assert (await client.post(f"/settings/users/{viewer_user.id}/edit", data=reset)).status_code == 303
    await client.post("/logout")
    assert (await login(client, "testviewer")).status_code == 401
    assert (await login(client, "testviewer", "brand-new-1")).status_code == 302


@pytest.mark.asyncio
async def test_deactivated_user_cannot_log_in(client, db_session, admin_user, viewer_user):
    await db_session.commit()
    await login(client)
    form = {"full_name": "V", "email": viewer_user.email, "role": "viewer", "language": "en"}  # no is_active -> off
    assert (await client.post(f"/settings/users/{viewer_user.id}/edit", data=form)).status_code == 303
    await client.post("/logout")
    assert (await login(client, "testviewer")).status_code == 401


@pytest.mark.asyncio
async def test_admin_cannot_lock_themselves_out_or_steal_an_email(client, db_session, admin_user, viewer_user):
    await db_session.commit()
    await login(client)
    await client.get("/set-language/en")
    base = {"full_name": "Admin", "email": admin_user.email, "role": "admin", "language": "en", "is_active": "on"}
    demote = await client.post(f"/settings/users/{admin_user.id}/edit", data={**base, "role": "viewer"})
    assert demote.status_code == 422 and "cannot change your own role" in demote.text
    deactivate = await client.post(
        f"/settings/users/{admin_user.id}/edit", data={k: v for k, v in base.items() if k != "is_active"}
    )
    assert deactivate.status_code == 422
    taken = await client.post(f"/settings/users/{admin_user.id}/edit", data={**base, "email": viewer_user.email})
    assert taken.status_code == 422 and "already registered" in taken.text
    await db_session.refresh(admin_user)
    assert admin_user.role == UserRole.ADMIN and admin_user.is_active


@pytest.mark.asyncio
async def test_only_admins_manage_users(client, db_session, fleet_manager_user):
    await db_session.commit()
    await login(client, "testmanager")
    for method, path in (
        ("get", "/settings/users/new"),
        ("post", "/settings/users/new"),
        ("get", f"/settings/users/{fleet_manager_user.id}/edit"),
    ):
        response = await getattr(client, method)(path, follow_redirects=False)
        assert response.status_code in (302, 303) and response.headers["location"] == "/", path


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["bg", "en"])
async def test_password_fields_have_show_hide_toggle(client, db_session, admin_user, lang):
    """Login, profile and the user form offer a show / hide button on every password field."""
    await db_session.commit()
    await client.get("/set-language/" + lang)
    login_page = await client.get("/login")
    assert login_page.text.count('class="fc-password-toggle"') == 1
    assert "auth.show_password" not in login_page.text  # label is translated
    await login(client)
    await client.get("/set-language/" + lang)
    for path, fields in (("/settings/users/new", 2), (f"/settings/users/{admin_user.id}/edit", 2), ("/profile", 2)):
        page = await client.get(path)
        assert page.text.count('class="fc-password-toggle"') == fields, path
        assert page.text.count('class="fc-password-wrap"') == fields, path
    assert ("Show password" if lang == "en" else "Покажи паролата") in (await client.get("/profile")).text


def visible_text(html: str) -> str:
    """Text a person would read on the page (no tags, scripts or attribute values)."""
    from html.parser import HTMLParser

    class Collector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.skip, self.parts = 0, []

        def handle_starttag(self, tag, attrs):
            self.skip += tag in ("script", "style")

        def handle_endtag(self, tag):
            self.skip -= tag in ("script", "style")

        def handle_data(self, data):
            if not self.skip and data.strip():
                self.parts.append(data.strip())

    collector = Collector()
    collector.feed(html)
    return " | ".join(collector.parts)


@pytest.mark.asyncio
async def test_user_forms_show_no_stray_markup_or_script_text(client, db_session, admin_user):
    """Regression: a broken x-init attribute once leaked `fill()); $watch(...)` text into the form."""
    await db_session.commit()
    await login(client)
    await client.get("/set-language/en")
    for path in ("/settings/users/new", f"/settings/users/{admin_user.id}/edit"):
        text = visible_text((await client.get(path)).text)
        for fragment in ("fill()", "$watch", "$data", "=>", "x-init", "'>"):
            assert fragment not in text, (path, fragment)
        assert "Access" in text and "Role" in text
