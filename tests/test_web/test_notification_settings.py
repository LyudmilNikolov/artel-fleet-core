"""Rules page, global settings, personal channels and the sidebar section (phase 2 of notifications)."""

from datetime import time

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.notification_rule import NotificationRule
from app.services.notification_rule_service import NotificationRuleService
from tests.test_web.test_driver_access import make_driver, make_driver_login

RULES = "/settings/notifications/rules"


async def login(client, username, password="testpass123"):
    response = await client.post("/login", data={"username": username, "password": password})
    assert response.status_code == 302


def form(**kw):
    data = {
        "enabled": "on",
        "email_enabled": "on",
        "milestones": "30,15,10",
        "repeat_every_days": "1",
        "stop_after_overdue_days": "",
        "send_time": "",
    }
    data.update(kw)
    return data


async def stored(db):
    return {r.source: r for r in await db.scalars(select(NotificationRule))}


@pytest.mark.asyncio
async def test_sidebar_section_per_role(client, admin_user, fleet_manager_user, viewer_user):
    await login(client, "testadmin")
    admin_page = (await client.get("/vehicles")).text
    for href in ("/notifications", RULES, "/settings/notifications/settings", "/settings/notifications/channels"):
        assert f'href="{href}"' in admin_page, href
    await client.get("/logout")

    await login(client, "testmanager")
    manager_page = (await client.get("/vehicles")).text
    assert f'href="{RULES}"' in manager_page and 'href="/settings/notifications/channels"' in manager_page
    assert 'href="/settings/notifications/settings"' not in manager_page
    await client.get("/logout")

    await login(client, "testviewer")
    viewer_page = (await client.get("/vehicles")).text
    assert f'href="{RULES}"' not in viewer_page and 'href="/settings/notifications/channels"' not in viewer_page


@pytest.mark.asyncio
async def test_rules_page_lists_every_source_with_defaults(client, admin_user):
    await login(client, "testadmin")
    response = await client.get(RULES)
    assert response.status_code == 200
    page = response.text
    for label in ("CASCO insurance", "Tachograph inspection", "ID card", "ADR certificate (dangerous goods)"):
        assert label in page
    assert page.count("data-rule-card") == 11
    assert "30, 15, 10 days before expiry, then every day until renewed" in page
    assert "Not set up yet: nothing is sent" in page
    assert "Apply to all vehicle documents" in page and "Apply to all driver documents" in page
    assert "data-rules-save" in page and 'name="milestones"' in page


@pytest.mark.asyncio
async def test_manager_sees_rules_read_only_and_cannot_save(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    page = (await client.get(RULES)).text
    assert "Only an administrator can change rules" in page
    assert "data-rules-save" not in page and "<fieldset disabled" in page
    response = await client.post(f"{RULES}/vehicle:casco", data=form())
    assert response.status_code in (302, 303) and response.headers["location"] == "/"
    assert await stored(db_session) == {}
    apply_response = await client.post(f"{RULES}/apply", data={"owner": "vehicle", "from_source": "vehicle:casco"})
    assert apply_response.headers["location"] == "/"


@pytest.mark.asyncio
async def test_viewer_and_driver_cannot_open_settings_pages(client, db_session, viewer_user):
    await login(client, "testviewer")
    for path in (RULES, "/settings/notifications/settings", "/settings/notifications/channels"):
        response = await client.get(path)
        assert response.status_code in (302, 303) and response.headers["location"] == "/", path
    await client.get("/logout")
    driver = await make_driver(db_session, "Шофьор Иван")
    await make_driver_login(db_session, driver, "wheel")
    await login(client, "wheel", "driver-pass-1")
    response = await client.get(RULES)
    assert response.status_code in (302, 303) and response.headers["location"].startswith("/drivers/")


@pytest.mark.asyncio
async def test_admin_saves_a_rule_with_all_recipient_kinds(client, db_session, admin_user, fleet_manager_user):
    await login(client, "testadmin")
    response = await client.post(
        f"{RULES}/vehicle:casco",
        data=form(
            telegram_enabled="on",
            milestones="10, 30,15",
            repeat_every_days="2",
            stop_after_overdue_days="14",
            send_time="09:30",
            recipient_users=[str(fleet_manager_user.id)],
            recipient_roles=["admin", "viewer"],
            recipient_emails="Ops@Example.com\nfleet@example.com; ops@example.com",
            recipient_telegram="-1001234567890",
        ),
    )
    assert response.status_code in (302, 303), response.text
    assert response.headers["location"].startswith(RULES) and "saved=" in response.headers["location"]

    rule = await NotificationRuleService(db_session).rule("vehicle:casco")
    assert rule.milestone_days == (30, 15, 10) and rule.repeat_every_days == 2
    assert rule.stop_after_overdue_days == 14 and rule.send_time == time(9, 30)
    assert rule.email_enabled and rule.telegram_enabled and rule.enabled
    kinds = sorted((r.kind, r.address or r.role or str(r.user_id)) for r in rule.recipients)
    assert kinds == sorted(
        [
            ("user", str(fleet_manager_user.id)),
            ("role", "admin"),
            ("role", "viewer"),
            ("external_email", "ops@example.com"),
            ("external_email", "fleet@example.com"),
            ("external_telegram", "-1001234567890"),
        ]
    )
    page = (await client.get(RULES)).text
    assert "every 2 days until renewed" in page and "stops 14 days after expiry" in page
    assert "ops@example.com" in page
    audit = list(await db_session.scalars(select(AuditLog).where(AuditLog.entity_type == "notification_rule")))
    assert len(audit) == 1 and audit[0].user_id == admin_user.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"milestones": ""},
        {"milestones": "30,abc"},
        {"milestones": "30,30"},
        {"repeat_every_days": "0"},
        {"repeat_every_days": "x"},
        {"stop_after_overdue_days": "-2"},
        {"send_time": "25:99"},
        {"send_time": "soon"},
        {"recipient_emails": "nobody"},
        {"recipient_roles": ["driver"]},
        {"recipient_users": ["not-a-uuid"]},
    ],
)
async def test_invalid_rule_form_shows_an_error_keeps_input_and_saves_nothing(client, db_session, admin_user, bad):
    await login(client, "testadmin")
    response = await client.post(f"{RULES}/vehicle:casco", data=form(**bad))
    assert response.status_code == 422
    assert 'role="alert"' in response.text and "data-rule-card" in response.text
    assert await stored(db_session) == {}


@pytest.mark.asyncio
async def test_error_is_shown_in_bulgarian_and_keeps_what_was_typed(client, admin_user):
    await login(client, "testadmin")
    response = await client.post(f"{RULES}/vehicle:casco", data=form(milestones="7,x"))
    assert response.status_code == 422 and "7,x" in response.text
    await client.get("/set-language/bg")
    response = await client.post(f"{RULES}/vehicle:casco", data=form(repeat_every_days="0"))
    assert "Интервалът на повторение" in response.text


@pytest.mark.asyncio
async def test_unknown_source_redirects(client, admin_user):
    await login(client, "testadmin")
    response = await client.post(f"{RULES}/vehicle:nonsense", data=form())
    assert response.status_code in (302, 303) and response.headers["location"].startswith(RULES)


@pytest.mark.asyncio
async def test_apply_to_all_vehicle_documents(client, db_session, admin_user):
    await login(client, "testadmin")
    await client.post(f"{RULES}/vehicle:casco", data=form(milestones="45,20", recipient_roles=["admin"]))
    response = await client.post(f"{RULES}/apply", data={"owner": "vehicle", "from_source": "vehicle:casco"})
    assert response.status_code in (302, 303) and "applied=3" in response.headers["location"]
    rows = await stored(db_session)
    assert set(rows) == {
        "vehicle:casco",
        "vehicle:civil_liability",
        "vehicle:technical_inspection",
        "vehicle:tachograph",
    }
    assert "3 rules updated" in (await client.get(response.headers["location"])).text
    bad = await client.post(f"{RULES}/apply", data={"owner": "vehicle", "from_source": "driver:id_card"})
    assert bad.headers["location"].startswith(RULES) and "applied" not in bad.headers["location"]


@pytest.mark.asyncio
async def test_rules_without_recipients_or_channels_show_a_warning(client, admin_user):
    await login(client, "testadmin")
    await client.post(f"{RULES}/driver:id_card", data=form())
    assert "No recipients: nothing will be sent" in (await client.get(RULES)).text
    await client.post(f"{RULES}/driver:id_card", data=form(email_enabled="", recipient_roles=["admin"]))
    assert "No channel selected: nothing will be sent" in (await client.get(RULES)).text


@pytest.mark.asyncio
async def test_settings_page_admin_only_saves_and_hides_credentials(client, monkeypatch, db_session, admin_user):
    from app.config import settings

    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123456:SUPER-SECRET-TOKEN")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "smtp-secret-pw")
    await login(client, "testadmin")
    page = (await client.get("/settings/notifications/settings")).text
    assert 'value="08:00"' in page and "Europe/Sofia" in page
    assert "Email (SMTP) is not configured" in page and "The Telegram bot is configured" in page
    assert "SUPER-SECRET-TOKEN" not in page and "smtp-secret-pw" not in page

    response = await client.post(
        "/settings/notifications/settings", data={"send_time": "07:15", "timezone": "Europe/London"}
    )
    assert response.status_code in (302, 303)
    current = await NotificationRuleService(db_session).settings()
    assert (current.send_time, current.timezone) == (time(7, 15), "Europe/London")

    for bad in ({"send_time": "99:00", "timezone": "Europe/Sofia"}, {"send_time": "08:00", "timezone": "Mars/Base"}):
        failed = await client.post("/settings/notifications/settings", data=bad)
        assert failed.status_code == 422 and 'role="alert"' in failed.text
    assert (await NotificationRuleService(db_session).settings()).send_time == time(7, 15)


@pytest.mark.asyncio
async def test_manager_cannot_open_global_settings(client, fleet_manager_user):
    await login(client, "testmanager")
    for method in (client.get, client.post):
        response = await method("/settings/notifications/settings")
        assert response.status_code in (302, 303) and response.headers["location"] == "/"


@pytest.mark.asyncio
async def test_my_channels_page_saves_and_validates(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    page = (await client.get("/settings/notifications/channels")).text
    assert fleet_manager_user.email in page and 'name="telegram_chat_id"' in page

    response = await client.post(
        "/settings/notifications/channels",
        data={"email_enabled": "on", "telegram_enabled": "on", "telegram_chat_id": "987654321"},
    )
    assert response.status_code in (302, 303)
    channels = await NotificationRuleService(db_session).channels(fleet_manager_user.id)
    assert (channels.email_enabled, channels.telegram_enabled, channels.telegram_chat_id) == (True, True, "987654321")

    missing = await client.post(
        "/settings/notifications/channels", data={"telegram_enabled": "on", "telegram_chat_id": ""}
    )
    assert missing.status_code == 422 and "Enter your Telegram chat ID" in missing.text
    junk = await client.post("/settings/notifications/channels", data={"telegram_chat_id": "x y"})
    assert junk.status_code == 422
    still = await NotificationRuleService(db_session).channels(fleet_manager_user.id)
    assert still.telegram_chat_id == "987654321"


@pytest.mark.asyncio
async def test_pages_render_in_both_languages(client, admin_user):
    await login(client, "testadmin")
    for path in (RULES, "/settings/notifications/settings", "/settings/notifications/channels"):
        bg = (await client.get(path, params={"lang": "bg"})).text
        en = (await client.get(path, params={"lang": "en"})).text
        assert bg != en and "notifications." not in bg and "notifications." not in en, path
    assert "Правила за напомняне" in (await client.get(RULES, params={"lang": "bg"})).text
    assert (
        "30, 15, 10 дни преди изтичане, след това всеки ден до подновяване"
        in (await client.get(RULES, params={"lang": "bg"})).text
    )


@pytest.mark.asyncio
async def test_old_notifications_url_redirect_target_still_exists(client, admin_user):
    await login(client, "testadmin")
    assert (await client.get("/settings/notifications")).headers["location"] == RULES
