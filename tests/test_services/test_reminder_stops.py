"""Stop / Resume of reminders for one document: global, audited, reset by a changed end date."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.notification_dispatch import NotificationStop
from app.models.user import UserRole
from app.schemas.compliance import ComplianceCreate, ComplianceUpdate
from app.services.attention_service import AttentionService
from app.services.compliance_service import ComplianceService
from app.services.errors import DomainError
from app.services.reminder_stop_service import ReminderStopService
from tests.test_services.test_attention_service import TODAY, vehicle
from tests.test_services.test_notification_rule_service import make_user

TYPE = "compliance_document"


async def doc(db, days=10):
    v = await vehicle(db)
    end = TODAY + timedelta(days=days)
    return await ComplianceService(db, today=TODAY, window=30).create(
        ComplianceCreate(vehicle_id=v.id, document_type="casco", start_date=end - timedelta(days=365), end_date=end),
        None,
    )


async def audits(db):
    return list(await db.scalars(select(AuditLog).where(AuditLog.entity_type == "notification_stop")))


@pytest.mark.asyncio
async def test_stop_is_global_recorded_and_audited(db_session):
    manager = await make_user(db_session, "stopper")
    entry = await doc(db_session)
    svc = ReminderStopService(db_session)
    assert await svc.active(TYPE, entry.id, entry.end_date) is None

    info = await svc.stop(TYPE, entry.id, entry.end_date, manager)
    assert info.stopped_by_name == "Stopper" and info.stopped_at is not None
    active = await svc.active(TYPE, entry.id, entry.end_date)
    assert active is not None and active.stopped_by_name == "Stopper"
    [entry_log] = await audits(db_session)
    assert entry_log.user_id == manager.id and entry_log.entity_id == entry.id
    assert entry_log.changes["action"] == "stop" and entry_log.changes["end_date"] == str(entry.end_date)


@pytest.mark.asyncio
async def test_stopping_twice_keeps_one_stop(db_session):
    manager = await make_user(db_session, "twice")
    entry = await doc(db_session)
    svc = ReminderStopService(db_session)
    await svc.stop(TYPE, entry.id, entry.end_date, manager)
    await svc.stop(TYPE, entry.id, entry.end_date, manager)
    assert await db_session.scalar(select(func.count()).select_from(NotificationStop)) == 1
    assert len(await audits(db_session)) == 1


@pytest.mark.asyncio
async def test_resume_then_stop_again_keeps_history(db_session):
    manager = await make_user(db_session, "again")
    other = await make_user(db_session, "other")
    entry = await doc(db_session)
    svc = ReminderStopService(db_session)
    await svc.stop(TYPE, entry.id, entry.end_date, manager)
    assert await svc.resume(TYPE, entry.id, entry.end_date, other) is True
    assert await svc.active(TYPE, entry.id, entry.end_date) is None
    assert await svc.resume(TYPE, entry.id, entry.end_date, other) is False  # nothing to resume
    await svc.stop(TYPE, entry.id, entry.end_date, manager)
    assert await svc.active(TYPE, entry.id, entry.end_date) is not None
    rows = list(await db_session.scalars(select(NotificationStop).order_by(NotificationStop.stopped_at)))
    assert len(rows) == 2 and sum(r.resumed_at is not None for r in rows) == 1
    resumed = next(r for r in rows if r.resumed_at)
    assert resumed.resumed_by == other.id and resumed.stopped_by == manager.id
    assert [a.changes["action"] for a in sorted(await audits(db_session), key=lambda a: a.timestamp)].count(
        "resume"
    ) == 1


@pytest.mark.asyncio
async def test_a_changed_end_date_resets_the_stop(db_session):
    manager = await make_user(db_session, "reset")
    entry = await doc(db_session, days=10)
    svc = ReminderStopService(db_session)
    old_end = entry.end_date  # the returned entry is a live view: remember the date before changing it
    await svc.stop(TYPE, entry.id, old_end, manager)
    changed = await ComplianceService(db_session, today=TODAY, window=30).update(
        entry.id, ComplianceUpdate(end_date=old_end + timedelta(days=5))
    )
    assert changed.end_date == old_end + timedelta(days=5)
    assert await svc.active(TYPE, entry.id, changed.end_date) is None  # the old stop belongs to the old end date
    assert await svc.active(TYPE, entry.id, old_end) is not None
    await svc.stop(TYPE, entry.id, changed.end_date, manager)  # can be stopped again for the new date
    assert await db_session.scalar(select(func.count()).select_from(NotificationStop)) == 2


@pytest.mark.asyncio
async def test_only_admins_and_fleet_managers_may_stop_or_resume(db_session):
    entry = await doc(db_session)
    svc = ReminderStopService(db_session)
    for role in (UserRole.VIEWER, UserRole.DRIVER):
        user = await make_user(db_session, f"no_{role.value}", role=role)
        with pytest.raises(DomainError) as error:
            await svc.stop(TYPE, entry.id, entry.end_date, user)
        assert error.value.key == "errors.forbidden"
        with pytest.raises(DomainError):
            await svc.resume(TYPE, entry.id, entry.end_date, user)
    assert await audits(db_session) == []


@pytest.mark.asyncio
async def test_unknown_entity_type_is_rejected(db_session):
    manager = await make_user(db_session, "typo")
    entry = await doc(db_session)
    with pytest.raises(DomainError):
        await ReminderStopService(db_session).stop("vehicle", entry.id, entry.end_date, manager)


@pytest.mark.asyncio
async def test_attention_items_know_when_they_are_stopped(db_session):
    manager = await make_user(db_session, "marker")
    a, b = await doc(db_session, days=5), await doc(db_session, days=8)
    await ReminderStopService(db_session).stop(TYPE, a.id, a.end_date, manager)
    items = {i.entity_id: i for i in await AttentionService(db_session, today=TODAY).items()}
    assert items[a.id].stopped is True and items[b.id].stopped is False
    assert (await AttentionService(db_session, today=TODAY).find(TYPE, a.id)).stopped is True
