"""Stop / Resume, schedule and history on the item page, the reminders filter, and "send test" (phase 3)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.notification_dispatch import NotificationDelivery, NotificationStop
from app.schemas.compliance import ComplianceUpdate
from app.services.compliance_service import ComplianceService
from app.services.notification_rule_service import NotificationRuleService, RecipientInput, RuleInput
from app.services.validity import local_today
from app.utils.formatting import format_local_date
from tests.test_web.test_driver_access import make_driver, make_driver_login
from tests.test_web.test_notifications import SECRET_NUMBER, TODAY, login, vehicle, vehicle_doc


def item_url(doc):
    return f"/notifications/item/compliance_document/{doc.id}"


async def stops(db):
    return list(await db.scalars(select(NotificationStop)))


@pytest.mark.asyncio
async def test_manager_stops_and_resumes_reminders_for_a_document(client, db_session, fleet_manager_user):
    v = await vehicle(db_session, "CA8100AA")
    doc = await vehicle_doc(db_session, v, "casco", 7)
    await login(client, "testmanager")

    page = (await client.get(item_url(doc))).text
    assert "Stop reminders" in page and "Resume reminders" not in page

    response = await client.post(item_url(doc) + "/stop")
    assert response.status_code in (302, 303)
    assert response.headers["location"].startswith(item_url(doc)) and "done=stopped" in response.headers["location"]
    [stop] = await stops(db_session)
    assert stop.entity_id == doc.id and stop.stopped_by == fleet_manager_user.id and stop.resumed_at is None

    stopped = (await client.get(response.headers["location"])).text
    assert "Reminders stopped" in stopped and "Resume reminders" in stopped and "Stop reminders" not in stopped
    assert fleet_manager_user.full_name in stopped
    audit = list(await db_session.scalars(select(AuditLog).where(AuditLog.entity_type == "notification_stop")))
    assert len(audit) == 1 and audit[0].changes["action"] == "stop"

    resumed = await client.post(item_url(doc) + "/resume")
    assert "done=resumed" in resumed.headers["location"]
    again = (await client.get(item_url(doc))).text
    assert "Stop reminders" in again and "Resume reminders" not in again
    assert len(await stops(db_session)) == 1 and (await stops(db_session))[0].resumed_at is not None


@pytest.mark.asyncio
async def test_the_stop_keeps_the_inbox_filters_in_the_redirect(client, db_session, admin_user):
    v = await vehicle(db_session, "CA8200AA")
    doc = await vehicle_doc(db_session, v, "casco", 7)
    await login(client, "testadmin")
    response = await client.post(item_url(doc) + "/stop?status=expiring_soon&sort=end_date")
    location = response.headers["location"]
    assert "status=expiring_soon" in location and "sort=end_date" in location and "done=stopped" in location


@pytest.mark.asyncio
async def test_viewers_drivers_and_anonymous_users_cannot_stop(client, db_session, viewer_user):
    v = await vehicle(db_session, "CA8300AA")
    doc = await vehicle_doc(db_session, v, "casco", 7)
    anonymous = await client.post(item_url(doc) + "/stop")
    assert anonymous.status_code in (302, 303) and anonymous.headers["location"] == "/login"

    await login(client, "testviewer")
    assert "Stop reminders" not in (await client.get(item_url(doc))).text
    for action in ("stop", "resume"):
        response = await client.post(f"{item_url(doc)}/{action}")
        assert response.status_code in (302, 303) and response.headers["location"] == "/"
    await client.post("/logout")

    driver = await make_driver(db_session, "Шофьор Стоп")
    await make_driver_login(db_session, driver, "nostop")
    await login(client, "nostop", "driver-pass-1")
    response = await client.post(f"{item_url(doc)}/stop")
    assert response.headers["location"] == "/"
    assert await stops(db_session) == []


@pytest.mark.asyncio
async def test_stopping_something_that_is_not_an_attention_item_changes_nothing(client, db_session, admin_user):
    v = await vehicle(db_session, "CA8400AA")
    far = await vehicle_doc(db_session, v, "casco", 400)
    await login(client, "testadmin")
    response = await client.post(item_url(far) + "/stop")
    assert response.headers["location"] == "/notifications"
    assert await stops(db_session) == []


@pytest.mark.asyncio
async def test_a_changed_end_date_makes_the_item_stoppable_again(client, db_session, admin_user):
    v = await vehicle(db_session, "CA8500AA")
    doc = await vehicle_doc(db_session, v, "casco", 7)
    await login(client, "testadmin")
    await client.post(item_url(doc) + "/stop")
    assert "Resume reminders" in (await client.get(item_url(doc))).text
    old_end = doc.end_date
    await ComplianceService(db_session).update(doc.id, ComplianceUpdate(end_date=old_end + timedelta(days=2)))
    page = (await client.get(item_url(doc))).text
    assert "Stop reminders" in page and "Resume reminders" not in page


@pytest.mark.asyncio
async def test_inbox_filters_by_reminders_and_marks_stopped_rows(client, db_session, admin_user):
    v = await vehicle(db_session, "CA8600AA")
    stopped = await vehicle_doc(db_session, v, "casco", 4)
    await vehicle_doc(db_session, v, "civil_liability", 6)
    await login(client, "testadmin")
    await client.post(item_url(stopped) + "/stop")
    table = {"HX-Request": "true", "HX-Target": "attention-table"}

    async def rows(**params):
        return (await client.get("/notifications", params=params, headers=table)).text

    everything, only_stopped, only_active = (
        await rows(),
        await rows(reminders="stopped"),
        await rows(reminders="active"),
    )
    assert everything.count("data-attention-row") == 2 and everything.count("data-stopped") == 1
    assert only_stopped.count("data-attention-row") == 1 and "CASCO insurance" in only_stopped
    assert only_active.count("data-attention-row") == 1 and "Civil liability" in only_active
    assert (await rows(reminders="nonsense")).count("data-attention-row") == 2  # unknown values are ignored
    assert 'name="reminders"' in (await client.get("/notifications")).text


@pytest.mark.asyncio
async def test_item_page_shows_rule_next_reminder_and_history_to_managers_only(
    client, db_session, admin_user, viewer_user
):
    v = await vehicle(db_session, "CA8700AA")
    doc = await vehicle_doc(db_session, v, "casco", 12)
    svc = NotificationRuleService(db_session)
    await svc.save_rule(
        "vehicle:casco",
        RuleInput(
            True,
            True,
            False,
            [30, 15, 10],
            1,
            None,
            None,
            [RecipientInput("external_email", address="ops@example.com")],
        ),
        admin_user.id,
    )
    db_session.add(
        NotificationDelivery(
            entity_type="compliance_document",
            entity_id=doc.id,
            end_date=doc.end_date,
            channel="email",
            recipient="ops@example.com",
            point=15,
            status="sent",
        )
    )
    db_session.add(
        NotificationDelivery(
            entity_type="compliance_document",
            entity_id=doc.id,
            end_date=doc.end_date,
            channel="telegram",
            recipient="-100500",
            point=15,
            status="failed",
            error="send_failed",
        )
    )
    await db_session.flush()

    await login(client, "testadmin")
    page = (await client.get(item_url(doc))).text
    assert "30, 15, 10 days before expiry, then every day until renewed" in page
    assert "ops@example.com" in page and "Sent reminders" in page
    assert "Sent" in page and "Failed" in page and "-100500" in page
    next_date = format_local_date(doc.end_date - timedelta(days=10), "en")  # 12 left: the next point is 10
    assert 'data-next="date"' in page and next_date in page
    assert SECRET_NUMBER not in page and "send_failed" not in page
    await client.post("/logout")

    await login(client, "testviewer")
    viewer_page = (await client.get(item_url(doc))).text
    assert "ops@example.com" not in viewer_page and "Sent reminders" not in viewer_page
    assert "Next reminder" not in viewer_page


@pytest.mark.asyncio
async def test_next_reminder_states(client, db_session, admin_user):
    v = await vehicle(db_session, "CA8800AA")
    unconfigured = await vehicle_doc(db_session, v, "civil_liability", 20)
    due_now = await vehicle_doc(db_session, v, "casco", 9)
    await NotificationRuleService(db_session).save_rule(
        "vehicle:casco",
        RuleInput(True, True, False, [30, 15, 10], 1, None, None, [RecipientInput("role", role="admin")]),
        admin_user.id,
    )
    await login(client, "testadmin")
    assert "No reminders are sent for this document type" in (await client.get(item_url(unconfigured))).text
    page = (await client.get(item_url(due_now))).text
    assert 'data-next="today"' in page  # 9 days left, the point 9 has not been sent yet
    assert 'data-next="off"' in (await client.get(item_url(unconfigured))).text

    await client.post(item_url(due_now) + "/stop")
    assert 'data-next="stopped"' in (await client.get(item_url(due_now))).text


@pytest.mark.asyncio
async def test_send_test_messages_from_my_channels(client, db_session, monkeypatch, admin_user):
    from app.config import settings
    from app.utils.email import email_sender
    from app.utils.telegram import telegram_bot

    sent = {"email": [], "telegram": []}
    monkeypatch.setattr(
        email_sender, "send", lambda to, subject, html, text=None: sent["email"].append((to, subject)) or True
    )
    monkeypatch.setattr(
        telegram_bot, "send_message", lambda chat, text, parse_mode="HTML": sent["telegram"].append(chat) or True
    )
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:token")
    await login(client, "testadmin")
    channels = "/settings/notifications/channels"

    # no Telegram chat saved yet
    response = await client.post(f"{channels}/test/telegram")
    assert "test=telegram_no_target" in response.headers["location"]
    assert "Save your email or Telegram chat ID first" in (await client.get(response.headers["location"])).text

    await NotificationRuleService(db_session).save_channels(
        admin_user.id, email_enabled=True, telegram_enabled=True, telegram_chat_id="424242424"
    )
    email = await client.post(f"{channels}/test/email")
    assert (
        "test=email_ok" in email.headers["location"]
        and "Test email sent" in (await client.get(email.headers["location"])).text
    )
    telegram = await client.post(f"{channels}/test/telegram")
    assert "Test Telegram message sent" in (await client.get(telegram.headers["location"])).text
    assert sent["email"][0][0] == admin_user.email and sent["telegram"] == ["424242424"]
    assert "FleetCore" in sent["email"][0][1]  # the test user is English

    monkeypatch.setattr(email_sender, "send", lambda *a, **k: False)
    failed = await client.post(f"{channels}/test/email")
    assert "could not be sent" in (await client.get(failed.headers["location"])).text

    monkeypatch.setattr(settings, "SMTP_HOST", "")
    unconfigured = await client.post(f"{channels}/test/email")
    assert "not configured on the server" in (await client.get(unconfigured.headers["location"])).text

    junk = await client.post(f"{channels}/test/sms")
    assert junk.headers["location"] == channels
    page = (await client.get(channels)).text
    assert "Send test email" in page and "Send test Telegram message" in page


@pytest.mark.asyncio
@pytest.mark.parametrize("username", ["testviewer"])
async def test_viewers_cannot_send_test_messages(client, viewer_user, fleet_manager_user, username):
    """Managers send tests to their own address from My channels (QA #5); viewers have no channels page."""
    await login(client, username)
    response = await client.post("/settings/notifications/channels/test/email")
    assert response.status_code in (302, 303) and response.headers["location"] == "/"


@pytest.mark.asyncio
async def test_bulgarian_labels_on_the_item_page(client, db_session, admin_user):
    v = await vehicle(db_session, "CA8900AA")
    doc = await vehicle_doc(db_session, v, "casco", 7)
    await login(client, "testadmin")
    page = (await client.get(item_url(doc), params={"lang": "bg"})).text
    assert "Спри напомнянията" in page and "Следващо напомняне" in page
    assert local_today() == TODAY


@pytest.mark.asyncio
async def test_fleet_manager_sees_the_next_reminder_but_not_rules_recipients_or_history(
    client, db_session, admin_user, fleet_manager_user
):
    v = await vehicle(db_session, "CA9100AA")
    doc = await vehicle_doc(db_session, v, "casco", 12)
    await NotificationRuleService(db_session).save_rule(
        "vehicle:casco",
        RuleInput(True, True, False, [30, 15, 10], 1, None, None, [RecipientInput("external_email", address="ops@example.com")]),
        admin_user.id,
    )
    db_session.add(
        NotificationDelivery(
            entity_type="compliance_document",
            entity_id=doc.id,
            end_date=doc.end_date,
            channel="email",
            recipient="ops@example.com",
            point=15,
            status="sent",
        )
    )
    await db_session.flush()
    await login(client, "testmanager")
    page = (await client.get(item_url(doc))).text
    assert 'data-next="date"' in page and "Next reminder" in page  # they may still stop / resume reminders
    assert "Stop reminders" in page
    assert "ops@example.com" not in page and "Sent reminders" not in page
    assert "30, 15, 10 days before expiry" not in page and "Edit rule" not in page
    await client.post("/logout")
    await login(client, "testadmin")
    admin_page = (await client.get(item_url(doc))).text
    assert "ops@example.com" in admin_page and "Sent reminders" in admin_page and "30, 15, 10 days before expiry" in admin_page
