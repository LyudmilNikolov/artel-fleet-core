"""Reminder rules, global settings and personal channels: defaults, validation, recipients, audit."""

from datetime import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.driver import Driver
from app.models.notification_rule import NotificationRule
from app.models.user import UserRole
from app.notification_rules import DEFAULT_MILESTONE_DAYS, SOURCES
from app.services.errors import DomainError
from app.services.notification_rule_service import NotificationRuleService, RecipientInput, RuleInput, preview_parts
from app.services.user_service import UserService

SOURCE = "vehicle:casco"


def rule_input(**kw) -> RuleInput:
    values = dict(
        enabled=True,
        email_enabled=True,
        telegram_enabled=False,
        milestone_days=[30, 15, 10],
        repeat_every_days=1,
        stop_after_overdue_days=None,
        send_time=None,
        recipients=[],
    )
    values.update(kw)
    return RuleInput(**values)


async def make_user(db, name, role=UserRole.FLEET_MANAGER, active=True):
    driver_id = None
    if role == UserRole.DRIVER:
        profile = Driver(id=uuid4(), full_name=name.title())
        db.add(profile)
        await db.flush()
        driver_id = profile.id
    user = await UserService(db).create(
        email=f"{name}@example.com",
        username=name,
        full_name=name.title(),
        password="secret-pass-1",
        role=role,
        language="bg",
        driver_id=driver_id,
    )
    user.is_active = active
    await db.flush()
    return user


async def audit_entries(db, entity_type):
    return list(await db.scalars(select(AuditLog).where(AuditLog.entity_type == entity_type)))


@pytest.mark.asyncio
async def test_every_source_has_code_defaults_without_stored_rows(db_session):
    rules = await NotificationRuleService(db_session).rules()
    assert [r.source for r in rules] == [s.code for s in SOURCES]
    for rule in rules:
        assert rule.milestone_days == DEFAULT_MILESTONE_DAYS == (30, 15, 10)
        assert rule.repeat_every_days == 1 and rule.stop_after_overdue_days is None and rule.send_time is None
        assert rule.enabled and not rule.email_enabled and not rule.telegram_enabled
        assert rule.recipients == () and rule.configured is False and rule.warnings == ()
    assert await db_session.scalar(select(NotificationRule.id).limit(1)) is None  # reading never writes


@pytest.mark.asyncio
async def test_save_rule_round_trip_sorts_milestones_and_replaces_recipients(db_session):
    manager = await make_user(db_session, "boss")
    svc = NotificationRuleService(db_session)
    saved = await svc.save_rule(
        SOURCE,
        rule_input(
            milestone_days=[10, 30, 15],
            repeat_every_days=2,
            stop_after_overdue_days=20,
            send_time=time(9, 30),
            telegram_enabled=True,
            recipients=[
                RecipientInput("user", user_id=manager.id),
                RecipientInput("role", role="viewer"),
                RecipientInput("external_email", address="Ops@Example.com"),
                RecipientInput("external_telegram", address="-1001234567890"),
            ],
        ),
        manager.id,
    )
    assert saved.configured and saved.milestone_days == (30, 15, 10)
    assert (saved.repeat_every_days, saved.stop_after_overdue_days, saved.send_time) == (2, 20, time(9, 30))
    assert {(r.kind, r.address or r.role or r.user_id) for r in saved.recipients} == {
        ("user", manager.id),
        ("role", "viewer"),
        ("external_email", "ops@example.com"),
        ("external_telegram", "-1001234567890"),
    }
    user_recipient = next(r for r in saved.recipients if r.kind == "user")
    assert user_recipient.label == "Boss"

    replaced = await svc.save_rule(SOURCE, rule_input(recipients=[RecipientInput("role", role="admin")]), manager.id)
    assert [(r.kind, r.role) for r in replaced.recipients] == [("role", "admin")]
    assert (await svc.rule(SOURCE)).recipients == replaced.recipients
    assert (await svc.rule("vehicle:tachograph")).configured is False  # other sources untouched


@pytest.mark.asyncio
async def test_no_repeat_and_empty_optionals(db_session):
    svc = NotificationRuleService(db_session)
    saved = await svc.save_rule(SOURCE, rule_input(repeat_every_days=None, milestone_days=[7]), None)
    assert saved.repeat_every_days is None and saved.milestone_days == (7,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "key"),
    [
        ({"milestone_days": []}, "error.notification_milestones_invalid"),
        ({"milestone_days": [30, 30]}, "error.notification_milestones_invalid"),
        ({"milestone_days": [0, 5]}, "error.notification_milestones_invalid"),
        ({"milestone_days": [-3]}, "error.notification_milestones_invalid"),
        ({"milestone_days": [366]}, "error.notification_milestones_invalid"),
        ({"milestone_days": list(range(1, 12))}, "error.notification_milestones_invalid"),
        ({"repeat_every_days": 0}, "error.notification_repeat_invalid"),
        ({"repeat_every_days": 366}, "error.notification_repeat_invalid"),
        ({"stop_after_overdue_days": 0}, "error.notification_stop_after_invalid"),
        ({"stop_after_overdue_days": 400}, "error.notification_stop_after_invalid"),
        (
            {"recipients": [RecipientInput("external_email", address="not-an-email")]},
            "error.notification_recipient_invalid",
        ),
        (
            {"recipients": [RecipientInput("external_telegram", address="hello world")]},
            "error.notification_recipient_invalid",
        ),
        ({"recipients": [RecipientInput("role", role="driver")]}, "error.notification_recipient_invalid"),
        ({"recipients": [RecipientInput("role", role="nonsense")]}, "error.notification_recipient_invalid"),
        ({"recipients": [RecipientInput("user", user_id=uuid4())]}, "error.notification_recipient_invalid"),
        ({"recipients": [RecipientInput("vehicle_driver")]}, "error.notification_recipient_invalid"),
    ],
)
async def test_validation_rejects_bad_input_and_saves_nothing(db_session, changes, key):
    svc = NotificationRuleService(db_session)
    with pytest.raises(DomainError) as error:
        await svc.save_rule(SOURCE, rule_input(**changes), None)
    assert error.value.key == key
    assert (await svc.rule(SOURCE)).configured is False
    assert await audit_entries(db_session, "notification_rule") == []


@pytest.mark.asyncio
async def test_unknown_source_is_rejected(db_session):
    svc = NotificationRuleService(db_session)
    with pytest.raises(DomainError) as error:
        await svc.save_rule("vehicle:nonsense", rule_input(), None)
    assert error.value.key == "error.notification_unknown_source"
    with pytest.raises(DomainError):
        await svc.rule("nope")


@pytest.mark.asyncio
async def test_recipient_users_must_be_active_and_not_drivers(db_session):
    svc = NotificationRuleService(db_session)
    inactive = await make_user(db_session, "gone", active=False)
    driver = await make_user(db_session, "wheel", role=UserRole.DRIVER)
    for user in (inactive, driver):
        with pytest.raises(DomainError) as error:
            await svc.save_rule(SOURCE, rule_input(recipients=[RecipientInput("user", user_id=user.id)]), None)
        assert error.value.key == "error.notification_recipient_invalid"


@pytest.mark.asyncio
async def test_duplicate_recipients_are_collapsed(db_session):
    svc = NotificationRuleService(db_session)
    manager = await make_user(db_session, "dup")
    saved = await svc.save_rule(
        SOURCE,
        rule_input(
            recipients=[
                RecipientInput("user", user_id=manager.id),
                RecipientInput("user", user_id=manager.id),
                RecipientInput("external_email", address="a@example.com"),
                RecipientInput("external_email", address="A@EXAMPLE.COM"),
                RecipientInput("role", role="admin"),
                RecipientInput("role", role="admin"),
            ]
        ),
        None,
    )
    assert len(saved.recipients) == 3


@pytest.mark.asyncio
async def test_warnings_for_rules_that_would_send_nothing(db_session):
    svc = NotificationRuleService(db_session)
    no_recipients = await svc.save_rule(SOURCE, rule_input(), None)
    assert no_recipients.warnings == ("notifications.warn_no_recipients",)
    no_channel = await svc.save_rule(
        "vehicle:casco", rule_input(email_enabled=False, recipients=[RecipientInput("role", role="admin")]), None
    )
    assert no_channel.warnings == ("notifications.warn_no_channel",)
    fine = await svc.save_rule(SOURCE, rule_input(recipients=[RecipientInput("role", role="admin")]), None)
    assert fine.warnings == ()
    off = await svc.save_rule(SOURCE, rule_input(enabled=False), None)
    assert off.warnings == ()  # a disabled rule is not a problem


@pytest.mark.asyncio
async def test_deleting_a_user_removes_them_from_rule_recipients(db_session):
    svc = NotificationRuleService(db_session)
    manager = await make_user(db_session, "leaver")
    await svc.save_rule(SOURCE, rule_input(recipients=[RecipientInput("user", user_id=manager.id)]), None)
    await db_session.delete(manager)
    await db_session.flush()
    db_session.expire_all()
    assert (await svc.rule(SOURCE)).recipients == ()


@pytest.mark.asyncio
async def test_rule_changes_are_audited_with_a_diff_and_no_op_saves_are_not(db_session):
    svc = NotificationRuleService(db_session)
    manager = await make_user(db_session, "auditor")
    await svc.save_rule(SOURCE, rule_input(), manager.id)
    created = await audit_entries(db_session, "notification_rule")
    assert len(created) == 1 and created[0].user_id == manager.id and created[0].action.value == "create"

    await svc.save_rule(SOURCE, rule_input(), manager.id)  # nothing changed
    assert len(await audit_entries(db_session, "notification_rule")) == 1

    await svc.save_rule(SOURCE, rule_input(milestone_days=[20, 5], repeat_every_days=3), manager.id)
    entries = await audit_entries(db_session, "notification_rule")
    update = next(e for e in entries if e.action.value == "update")
    assert update.changes["milestone_days"] == {"old": [30, 15, 10], "new": [20, 5]}
    assert update.changes["repeat_every_days"] == {"old": 1, "new": 3}
    assert update.changes["_label"] == SOURCE
    assert "email_enabled" not in update.changes


@pytest.mark.asyncio
async def test_recipient_changes_appear_in_the_audit_diff_without_ids(db_session):
    svc = NotificationRuleService(db_session)
    await svc.save_rule(SOURCE, rule_input(), None)
    await svc.save_rule(
        SOURCE, rule_input(recipients=[RecipientInput("external_email", address="ops@example.com")]), None
    )
    update = next(e for e in await audit_entries(db_session, "notification_rule") if e.action.value == "update")
    assert update.changes["recipients"]["new"] == ["external_email:ops@example.com"]
    assert update.changes["recipients"]["old"] == []


@pytest.mark.asyncio
async def test_apply_to_group_copies_one_rule_to_every_source_of_that_owner(db_session):
    svc = NotificationRuleService(db_session)
    manager = await make_user(db_session, "copier")
    await svc.save_rule(
        SOURCE,
        rule_input(
            milestone_days=[45, 20], repeat_every_days=None, recipients=[RecipientInput("user", user_id=manager.id)]
        ),
        manager.id,
    )
    changed = await svc.apply_to_group("vehicle", SOURCE, manager.id)
    assert changed == 3  # the other three vehicle sources
    for source in ("vehicle:civil_liability", "vehicle:technical_inspection", "vehicle:tachograph"):
        copy = await svc.rule(source)
        assert copy.milestone_days == (45, 20) and copy.repeat_every_days is None and copy.email_enabled
        assert [r.user_id for r in copy.recipients] == [manager.id]
    assert (await svc.rule("driver:id_card")).configured is False
    assert await svc.apply_to_group("vehicle", SOURCE, manager.id) == 0  # already identical
    with pytest.raises(DomainError):
        await svc.apply_to_group("vehicle", "driver:id_card", manager.id)  # source of another owner
    with pytest.raises(DomainError):
        await svc.apply_to_group("fleet", SOURCE, manager.id)


@pytest.mark.asyncio
async def test_global_settings_default_update_validation_and_audit(db_session):
    svc = NotificationRuleService(db_session)
    manager = await make_user(db_session, "clock")
    current = await svc.settings()
    assert (current.send_time, current.timezone, current.configured) == (time(8, 0), "Europe/Sofia", False)

    updated = await svc.update_settings(send_time=time(7, 45), timezone="Europe/London", user_id=manager.id)
    assert (updated.send_time, updated.timezone, updated.configured) == (time(7, 45), "Europe/London", True)
    assert (await svc.settings()).send_time == time(7, 45)
    entry = (await audit_entries(db_session, "notification_settings"))[0]
    assert entry.changes["send_time"] == {"old": "08:00", "new": "07:45"}

    again = await svc.update_settings(send_time=time(6, 0), timezone="Europe/Sofia", user_id=manager.id)
    assert again.send_time == time(6, 0)  # still one row
    with pytest.raises(DomainError) as error:
        await svc.update_settings(send_time=time(8, 0), timezone="Mars/Olympus", user_id=manager.id)
    assert error.value.key == "error.notification_timezone_invalid"
    assert (await svc.settings()).send_time == time(6, 0)


@pytest.mark.asyncio
async def test_personal_channels_default_save_and_validate(db_session):
    svc = NotificationRuleService(db_session)
    user = await make_user(db_session, "me")
    default = await svc.channels(user.id)
    assert (default.email_enabled, default.telegram_enabled, default.telegram_chat_id) == (True, False, None)

    saved = await svc.save_channels(user.id, email_enabled=False, telegram_enabled=True, telegram_chat_id=" 123456789 ")
    assert (saved.email_enabled, saved.telegram_enabled, saved.telegram_chat_id) == (False, True, "123456789")
    assert (await svc.channels(user.id)).telegram_chat_id == "123456789"

    with pytest.raises(DomainError) as error:
        await svc.save_channels(user.id, email_enabled=True, telegram_enabled=True, telegram_chat_id="")
    assert error.value.key == "error.notification_chat_id_required"
    with pytest.raises(DomainError) as error:
        await svc.save_channels(user.id, email_enabled=True, telegram_enabled=False, telegram_chat_id="bad id!")
    assert error.value.key == "error.notification_chat_id_invalid"
    cleared = await svc.save_channels(user.id, email_enabled=True, telegram_enabled=False, telegram_chat_id="")
    assert cleared.telegram_chat_id is None


def test_preview_parts_describe_the_schedule():
    assert preview_parts((30, 15, 10), 1, None) == {"days": "30, 15, 10", "repeat": 1, "stop_after": None}
    assert preview_parts((7,), None, 14) == {"days": "7", "repeat": None, "stop_after": 14}
