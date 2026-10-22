"""ReminderDispatcher with fake senders and a fake clock: once per local day, schedule, recipients, idempotency."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from app.models.compliance import ComplianceDocument
from app.models.driver_document import DriverDocument
from app.models.notification import NotificationPreference
from app.models.notification_dispatch import NotificationDelivery, NotificationRun
from app.models.user import UserRole
from app.schemas.compliance import ComplianceCreate, ComplianceRenew, ComplianceUpdate
from app.schemas.driver import DriverCreate
from app.schemas.driver_document import DriverDocumentCreate
from app.services.compliance_service import ComplianceService
from app.services.driver_document_service import DriverDocumentService
from app.services.driver_service import DriverService
from app.services.notification_rule_service import NotificationRuleService, RecipientInput, RuleInput
from app.services.reminder_dispatcher import ReminderDispatcher
from app.services.reminder_stop_service import ReminderStopService
from tests.test_services.test_attention_service import TODAY, vehicle
from tests.test_services.test_notification_rule_service import make_user

BASE = "https://fleet.example"
SECRET = "SECRET-POLICY-98765"
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


class Outbox:
    def __init__(self):
        self.emails: list[tuple] = []
        self.telegrams: list[tuple] = []
        self.sleeps: list[float] = []
        self.fail_email = False
        self.fail_telegram = False

    def email(self, to, subject, html, text):
        self.emails.append((to, subject, html, text))
        return not self.fail_email

    def telegram(self, chat_id, text):
        self.telegrams.append((chat_id, text))
        return not self.fail_telegram

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def at(day=0, hour=12, minute=0) -> datetime:
    """A UTC moment `day` days after TODAY (12:00 UTC is after 08:00 in Sofia all year)."""
    return datetime(TODAY.year, TODAY.month, TODAY.day, hour, minute, tzinfo=UTC) + timedelta(days=day)


def dispatcher(db, out, **kw):
    options = dict(
        send_email=out.email,
        send_telegram=out.telegram,
        sleep=out.sleep,
        base_url=BASE,
        email_configured=lambda: True,
        telegram_configured=lambda: True,
    )
    options.update(kw)
    return ReminderDispatcher(db, **options)


def rule_input(recipients, **kw) -> RuleInput:
    values = dict(
        enabled=True,
        email_enabled=True,
        telegram_enabled=False,
        milestone_days=[30, 15, 10],
        repeat_every_days=1,
        stop_after_overdue_days=None,
        send_time=None,
        recipients=recipients,
    )
    values.update(kw)
    return RuleInput(**values)


async def configure(db, source="vehicle:casco", recipients=None, **kw):
    """A rule that mails the user "boss" (English) unless told otherwise; returns that user."""
    boss = await make_user(db, "boss")
    boss.language = "en"
    await db.flush()
    recipients = [RecipientInput("user", user_id=boss.id)] if recipients is None else recipients
    await NotificationRuleService(db).save_rule(source, rule_input(recipients, **kw), boss.id)
    return boss


async def vdoc(db, plate="CA1111AA", kind="casco", days=30, **kw):
    v = await vehicle(db, plate)
    end = TODAY + timedelta(days=days)
    entry = await ComplianceService(db, today=TODAY, window=30).create(
        ComplianceCreate(vehicle_id=v.id, document_type=kind, start_date=end - timedelta(days=365), end_date=end, **kw),
        None,
    )
    await backdate(db, ComplianceDocument, entry)
    return entry


async def backdate(db, model, entry, created=LONG_AGO):
    """Pretend the entry was created long ago, so the first-seen rule does not skip its early points."""
    await db.execute(update(model).where(model.id == entry.id).values(created_at=created))
    await db.flush()


async def deliveries(db):
    return list(await db.scalars(select(NotificationDelivery).order_by(NotificationDelivery.created_at)))


@pytest.mark.asyncio
async def test_nothing_is_sent_before_the_send_time_and_only_once_per_local_day(db_session):
    await configure(db_session)
    await vdoc(db_session, days=30)
    out = Outbox()

    early = await dispatcher(db_session, out).run(at(0, 4, 59))  # 07:59 in Sofia
    assert out.emails == [] and early.sent == 0
    assert await db_session.scalar(select(NotificationRun)) is None

    first = await dispatcher(db_session, out).run(at(0, 5, 0))  # 08:00 in Sofia
    assert len(out.emails) == 1 and first.sent == 1 and first.sources == ["vehicle:casco"]
    run = await db_session.scalar(select(NotificationRun))
    assert run.source == "vehicle:casco" and run.run_date == TODAY and run.finished_at is not None
    assert (run.planned, run.sent, run.failed) == (1, 1, 0)

    await dispatcher(db_session, out).run(at(0, 5, 15))
    await dispatcher(db_session, out).run(at(0, 20, 0))
    assert len(out.emails) == 1


@pytest.mark.asyncio
async def test_message_content_link_language_and_no_document_numbers(db_session):
    boss = await configure(db_session)
    entry = await vdoc(db_session, "CA2222BB", days=30, policy_number=SECRET)
    out = Outbox()
    await dispatcher(db_session, out).run(at())
    [(to, subject, html, text)] = out.emails
    assert to == boss.email
    assert "CASCO insurance" in subject and "CA2222BB" in subject and "30 days left" in subject
    link = f"{BASE}/notifications/item/compliance_document/{entry.id}"
    assert link in html and link in text
    assert SECRET not in subject + html + text
    assert (TODAY + timedelta(days=30)).strftime("%d/%m/%Y") in text  # English date format for an English recipient


@pytest.mark.asyncio
async def test_each_recipient_gets_their_own_language_and_externals_the_default(db_session):
    boss = await configure(db_session)
    ivan = await make_user(db_session, "ivan")  # Bulgarian
    await NotificationRuleService(db_session).save_rule(
        "vehicle:casco",
        rule_input(
            [
                RecipientInput("user", user_id=boss.id),
                RecipientInput("user", user_id=ivan.id),
                RecipientInput("external_email", address="office@example.com"),
            ]
        ),
        boss.id,
    )
    await vdoc(db_session, days=30)
    out = Outbox()
    await dispatcher(db_session, out).run(at())
    by_address = {to: subject for to, subject, _, _ in out.emails}
    assert set(by_address) == {boss.email, ivan.email, "office@example.com"}
    assert "CASCO insurance" in by_address[boss.email] and "30 days left" in by_address[boss.email]
    assert "Каско" in by_address[ivan.email] and "остават 30 дни" in by_address[ivan.email]
    assert "Каско" in by_address["office@example.com"]  # default language is Bulgarian


@pytest.mark.asyncio
async def test_milestones_then_daily_and_missed_days_send_only_the_latest(db_session):
    await configure(db_session)
    await vdoc(db_session, days=16)  # days left on day d: 16 - d
    out = Outbox()
    per_day = []
    for day in range(0, 9):
        before = len(out.emails)
        await dispatcher(db_session, out).run(at(day))
        per_day.append(len(out.emails) - before)
    # day0: 16 left -> milestone 30 (first_seen long ago); day1: 15; days 2-4: nothing; day5: 11 left -> 15 sent,
    # so 10 at day6; then daily 9, 8
    assert per_day == [1, 1, 0, 0, 0, 0, 1, 1, 1]


@pytest.mark.asyncio
async def test_downtime_sends_one_message_for_the_latest_point(db_session):
    await configure(db_session)
    await vdoc(db_session, days=16)
    out = Outbox()
    await dispatcher(db_session, out).run(at(7))  # nothing was sent for a week; 9 days are left now
    assert len(out.emails) == 1 and "9 days left" in out.emails[0][1]
    assert [d.point for d in await deliveries(db_session)] == [9]


@pytest.mark.asyncio
async def test_rerun_is_idempotent_even_without_the_run_record(db_session):
    await configure(db_session)
    await vdoc(db_session, days=30)
    out = Outbox()
    await dispatcher(db_session, out).run(at())
    await db_session.execute(delete(NotificationRun))
    await db_session.flush()
    again = await dispatcher(db_session, out).run(at(0, 13))
    assert len(out.emails) == 1 and again.sent == 0 and again.planned == 0


@pytest.mark.asyncio
async def test_failed_sends_are_recorded_and_retried_at_the_next_run(db_session):
    boss = await configure(db_session, milestone_days=[10], repeat_every_days=None)
    await vdoc(db_session, days=10)
    out = Outbox()
    out.fail_email = True
    failed = await dispatcher(db_session, out).run(at())
    assert failed.failed == 1 and failed.sent == 0
    [row] = await deliveries(db_session)
    assert (row.status, row.error, row.point, row.channel, row.recipient) == (
        "failed",
        "send_failed",
        10,
        "email",
        boss.email,
    )

    out.fail_email = False
    retried = await dispatcher(db_session, out).run(at(1))  # 9 days left: the latest reached point is still 10
    assert retried.sent == 1
    [row] = await deliveries(db_session)
    assert row.status == "sent" and row.error is None and row.sent_at is not None and row.user_id == boss.id
    await dispatcher(db_session, out).run(at(2))
    assert len(out.emails) == 2  # one failed attempt, one success, nothing more


@pytest.mark.asyncio
async def test_recipients_are_resolved_and_de_duplicated(db_session):
    boss = await configure(db_session)
    await NotificationRuleService(db_session).save_rule(
        "vehicle:casco",
        rule_input(
            [
                RecipientInput("user", user_id=boss.id),
                RecipientInput("role", role="fleet_manager"),  # includes the boss again
                RecipientInput("external_email", address="BOSS@example.com"),
                RecipientInput("external_email", address="other@example.com"),
            ]
        ),
        boss.id,
    )
    inactive = await make_user(db_session, "gone", active=False)
    assert inactive.is_active is False
    await vdoc(db_session, days=30)
    out = Outbox()
    await dispatcher(db_session, out).run(at())
    assert sorted(to for to, *_ in out.emails) == ["boss@example.com", "other@example.com"]


@pytest.mark.asyncio
async def test_roles_expand_to_active_users_and_never_to_drivers(db_session):
    admin = await make_user(db_session, "chief", role=UserRole.ADMIN)
    viewer = await make_user(db_session, "watcher", role=UserRole.VIEWER)
    await make_user(db_session, "retired", role=UserRole.VIEWER, active=False)
    await make_user(db_session, "wheel", role=UserRole.DRIVER)
    await NotificationRuleService(db_session).save_rule(
        "vehicle:casco",
        rule_input([RecipientInput("role", role="admin"), RecipientInput("role", role="viewer")]),
        admin.id,
    )
    await vdoc(db_session, days=30)
    out = Outbox()
    await dispatcher(db_session, out).run(at())
    assert sorted(to for to, *_ in out.emails) == sorted([admin.email, viewer.email])


@pytest.mark.asyncio
async def test_personal_channel_preferences_are_respected(db_session):
    boss = await configure(db_session, telegram_enabled=True)
    db_session.add(
        NotificationPreference(
            user_id=boss.id, email_enabled=False, telegram_enabled=True, telegram_chat_id="555000111"
        )
    )
    await db_session.flush()
    await vdoc(db_session, days=30)
    out = Outbox()
    await dispatcher(db_session, out).run(at())
    assert out.emails == [] and [chat for chat, _ in out.telegrams] == ["555000111"]


@pytest.mark.asyncio
async def test_telegram_messages_are_escaped_paced_and_numberless(db_session):
    boss = await configure(
        db_session,
        source="driver:id_card",
        email_enabled=False,
        telegram_enabled=True,
        recipients=[RecipientInput("external_telegram", address="-1001234567890")],
    )
    driver_a = await DriverService(db_session).create(DriverCreate(full_name="<b>O'Neil</b> & Co"))
    driver_b = await DriverService(db_session).create(DriverCreate(full_name="Петър Петров"))
    for driver in (driver_a, driver_b):
        created = await DriverDocumentService(db_session, today=TODAY, window=30).create(
            DriverDocumentCreate(
                driver_id=driver.id,
                document_type="id_card",
                document_number=SECRET,
                end_date=TODAY + timedelta(days=30),
            ),
            None,
        )
        await backdate(db_session, DriverDocument, created)
    out = Outbox()
    result = await dispatcher(db_session, out).run(at())
    assert boss.email and result.sent == 2 and len(out.telegrams) == 2
    joined = "\n".join(text for _, text in out.telegrams)
    assert "&lt;b&gt;O&#x27;Neil&lt;/b&gt; &amp; Co" in joined and "<b>O'Neil" not in joined
    assert SECRET not in joined and f"{BASE}/notifications/item/driver_document/" in joined
    assert len(out.sleeps) == 1 and out.sleeps[0] >= 1  # one pause between two sends to the same chat


@pytest.mark.asyncio
async def test_email_is_html_escaped(db_session):
    boss = await configure(db_session, source="driver:id_card")
    driver = await DriverService(db_session).create(DriverCreate(full_name="<script>alert(1)</script>"))
    created = await DriverDocumentService(db_session, today=TODAY, window=30).create(
        DriverDocumentCreate(
            driver_id=driver.id, document_type="id_card", document_number="1", end_date=TODAY + timedelta(days=30)
        ),
        None,
    )
    await backdate(db_session, DriverDocument, created)
    out = Outbox()
    await dispatcher(db_session, out).run(at())
    [(_, _, html, _)] = out.emails
    assert boss.email and "<script>" not in html and "&lt;script&gt;" in html


@pytest.mark.asyncio
async def test_stop_prevents_every_channel_and_resume_or_a_new_end_date_start_again(db_session):
    boss = await configure(db_session, telegram_enabled=True)
    db_session.add(NotificationPreference(user_id=boss.id, telegram_enabled=True, telegram_chat_id="777888999"))
    await db_session.flush()
    entry = await vdoc(db_session, days=30)
    await ReminderStopService(db_session).stop("compliance_document", entry.id, entry.end_date, boss)
    out = Outbox()
    result = await dispatcher(db_session, out).run(at())
    assert out.emails == [] and out.telegrams == [] and result.sent == 0

    await ReminderStopService(db_session).resume("compliance_document", entry.id, entry.end_date, boss)
    await dispatcher(db_session, out).run(at(1))  # next day's run
    assert len(out.emails) == 1 and len(out.telegrams) == 1

    # stopped again, then the end date is corrected: the stop no longer applies
    old_end = entry.end_date  # the returned entry is a live view: remember the date before changing it
    await ReminderStopService(db_session).stop("compliance_document", entry.id, old_end, boss)
    moved = await ComplianceService(db_session, today=TODAY, window=30).update(
        entry.id, ComplianceUpdate(end_date=old_end - timedelta(days=3))
    )
    assert moved.end_date == old_end - timedelta(days=3)
    await dispatcher(db_session, out).run(at(2))
    assert len(out.emails) == 2  # the new end date has its own reminders (25 days left: milestone 30)


@pytest.mark.asyncio
async def test_disabled_rules_missing_channels_and_unconfigured_sources_send_nothing(db_session):
    boss = await configure(db_session, enabled=False)
    await vdoc(db_session, days=30)
    await vdoc(db_session, "CA3333CC", "civil_liability", days=30)  # no rule at all
    await NotificationRuleService(db_session).save_rule(
        "vehicle:technical_inspection",
        rule_input([RecipientInput("user", user_id=boss.id)], email_enabled=False),
        boss.id,
    )
    await vdoc(db_session, "CA4444DD", "technical_inspection", days=30)
    out = Outbox()
    result = await dispatcher(db_session, out).run(at())
    assert out.emails == [] and out.telegrams == [] and result.sources == []


@pytest.mark.asyncio
async def test_unconfigured_channels_are_skipped_without_failures_and_caught_up_later(db_session):
    await configure(db_session)
    await vdoc(db_session, days=30)
    out = Outbox()
    skipped = await dispatcher(db_session, out, email_configured=lambda: False).run(at())
    assert skipped.sent == 0 and skipped.failed == 0 and out.emails == [] and await deliveries(db_session) == []
    await dispatcher(db_session, out).run(at(1))  # SMTP configured now: the latest unsent point goes out
    assert len(out.emails) == 1


@pytest.mark.asyncio
async def test_send_time_default_custom_and_per_rule_override(db_session):
    boss = await configure(db_session)  # casco: default time
    await NotificationRuleService(db_session).save_rule(
        "vehicle:civil_liability",
        rule_input([RecipientInput("user", user_id=boss.id)], send_time=datetime(2026, 1, 1, 12, 0).time()),
        boss.id,
    )
    await vdoc(db_session, "CA5555EE", "casco", days=30)
    await vdoc(db_session, "CA6666FF", "civil_liability", days=30)
    out = Outbox()
    await dispatcher(db_session, out).run(at(0, 5, 0))  # 08:00 Sofia
    assert len(out.emails) == 1 and "CASCO" in out.emails[0][1]
    await dispatcher(db_session, out).run(at(0, 8, 59))  # 11:59 Sofia: the override is not due yet
    assert len(out.emails) == 1
    await dispatcher(db_session, out).run(at(0, 9, 0))  # 12:00 Sofia
    assert len(out.emails) == 2 and "Civil liability" in out.emails[1][1]


@pytest.mark.asyncio
async def test_custom_global_send_time_and_timezone(db_session):
    boss = await configure(db_session)
    await vdoc(db_session, days=30)
    svc = NotificationRuleService(db_session)
    await svc.update_settings(send_time=datetime(2026, 1, 1, 10, 30).time(), timezone="Europe/Sofia", user_id=boss.id)
    out = Outbox()
    await dispatcher(db_session, out).run(at(0, 7, 29))  # 10:29 Sofia
    assert out.emails == []
    await dispatcher(db_session, out).run(at(0, 7, 30))
    assert len(out.emails) == 1

    await svc.update_settings(send_time=datetime(2026, 1, 1, 8, 0).time(), timezone="UTC", user_id=boss.id)
    await vdoc(db_session, "CA7777GG", days=30)
    await dispatcher(db_session, out).run(at(1, 7, 59))  # 07:59 UTC
    assert len(out.emails) == 1
    await dispatcher(db_session, out).run(at(1, 8, 0))  # 08:00 UTC
    assert len(out.emails) >= 2


@pytest.mark.asyncio
async def test_daylight_saving_change_moves_the_utc_moment_not_the_local_time(db_session):
    await configure(db_session)
    await vdoc(db_session, days=19)  # 16 days left on 2026-10-24, 15 on 2026-10-25
    out = Outbox()
    last_summer_day = datetime(2026, 10, 24, 5, 0, tzinfo=UTC)  # 08:00 EEST
    await dispatcher(db_session, out).run(last_summer_day)  # 16 days left -> milestone 30 is due
    assert len(out.emails) == 1
    first_winter_day = date(2026, 10, 25)
    assert first_winter_day == TODAY + timedelta(days=4)
    await dispatcher(db_session, out).run(datetime(2026, 10, 25, 5, 59, tzinfo=UTC))  # 07:59 EET
    assert len(out.emails) == 1
    await dispatcher(db_session, out).run(datetime(2026, 10, 25, 6, 0, tzinfo=UTC))  # 08:00 EET
    assert len(out.emails) == 2  # 15 days left: milestone 15


@pytest.mark.asyncio
async def test_a_document_created_late_starts_at_its_next_point(db_session):
    await configure(db_session)
    entry = await vdoc(db_session, days=12)
    await backdate(db_session, ComplianceDocument, entry, created=at(0, 1, 0))  # created today, 12 days left
    out = Outbox()
    await dispatcher(db_session, out).run(at(0))
    assert out.emails == []
    await dispatcher(db_session, out).run(at(1))
    assert out.emails == []
    await dispatcher(db_session, out).run(at(2))  # 10 days left
    assert len(out.emails) == 1 and "10 days left" in out.emails[0][1]


@pytest.mark.asyncio
async def test_a_renewed_document_restarts_its_reminders(db_session):
    await configure(db_session)
    entry = await vdoc(db_session, days=10)
    out = Outbox()
    await dispatcher(db_session, out).run(at())
    assert len(out.emails) == 1
    renewed = await ComplianceService(db_session, today=TODAY, window=30).renew(
        entry.id, ComplianceRenew(start_date=TODAY, end_date=TODAY + timedelta(days=31)), None
    )
    await backdate(db_session, ComplianceDocument, renewed)
    await dispatcher(db_session, out).run(at(1))  # the new entry has 30 days left: milestone 30 is due
    assert len(out.emails) == 2 and "30" in out.emails[1][1]


@pytest.mark.asyncio
async def test_fresh_pending_claims_are_respected_and_stale_ones_retried(db_session):
    boss = await configure(db_session)
    entry = await vdoc(db_session, days=30)
    claim = NotificationDelivery(
        entity_type="compliance_document",
        entity_id=entry.id,
        end_date=entry.end_date,
        channel="email",
        recipient=boss.email,
        user_id=boss.id,
        point=30,
        status="pending",
    )
    db_session.add(claim)
    await db_session.flush()
    out = Outbox()
    await dispatcher(db_session, out).run(at())
    assert out.emails == []  # another run is sending it right now

    await db_session.execute(update(NotificationDelivery).values(updated_at=datetime.now(UTC) - timedelta(hours=2)))
    await db_session.execute(delete(NotificationRun))
    await db_session.flush()
    await dispatcher(db_session, out).run(at(0, 13))
    assert len(out.emails) == 1  # the crashed run's claim is taken over
    assert (await deliveries(db_session))[0].status == "sent"
