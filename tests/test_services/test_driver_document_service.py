"""DriverDocumentService rules on PostgreSQL: type rules, computed status, current entry, renewal, deletion."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentType, EntityType
from app.models.driver import DriverStatus
from app.models.driver_document import DriverDocument
from app.schemas.driver import DriverCreate, DriverUpdate
from app.schemas.driver_document import DriverDocumentCreate, DriverDocumentRenew, DriverDocumentUpdate
from app.services.dashboard_service import DashboardService
from app.services.driver_document_service import DriverDocumentService
from app.services.driver_service import DriverService
from app.services.errors import DomainError

TODAY = date(2026, 10, 21)


class FakeS3:
    def __init__(self):
        self.deleted = []

    def delete_file(self, key):
        self.deleted.append(key)


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr("app.services.driver_document_service.get_s3_client", lambda: fake)
    monkeypatch.setattr("app.services.driver_service.get_s3_client", lambda: fake)
    return fake


async def driver(db, name="Иван Петров", status=DriverStatus.ACTIVE):
    return await DriverService(db).create(DriverCreate(full_name=name, status=status))


def entry(driver_id, document_type="id_card", number="123456789", start=None, end=TODAY + timedelta(days=200), **kw):
    return DriverDocumentCreate(
        driver_id=driver_id, document_type=document_type, document_number=number, start_date=start, end_date=end, **kw
    )


def service(db):
    return DriverDocumentService(db, today=TODAY, window=30)


def scan(entity_type, entity_id, key):
    return Document(
        entity_type=entity_type,
        entity_id=entity_id,
        type=DocumentType.SCAN,
        filename="scan.pdf",
        s3_key=key,
        mime_type="application/pdf",
        size_bytes=10,
    )


@pytest.mark.asyncio
async def test_status_is_computed_never_stored(db_session):
    d = await driver(db_session)
    svc = service(db_session)
    valid = await svc.create(entry(d.id, end=TODAY + timedelta(days=200)), None)
    soon = await svc.create(entry(d.id, "driving_licence", end=TODAY + timedelta(days=30)), None)
    gone = await svc.create(entry(d.id, "tachograph_card", end=TODAY - timedelta(days=1)), None)
    forever = await svc.create(entry(d.id, "psycho_test", number=None, end=None), None)
    assert (valid.status, soon.status, gone.status, forever.status) == (
        "valid",
        "expiring_soon",
        "expired",
        "no_expiry",
    )
    assert valid.days_left == 200 and gone.days_left == -1 and forever.days_left is None
    assert not hasattr(DriverDocument, "status")
    # the same entry changes status when "today" moves, without any write
    later = DriverDocumentService(db_session, today=TODAY + timedelta(days=201), window=30)
    assert (await later.get(valid.id)).status == "expired"


@pytest.mark.asyncio
async def test_per_type_rules(db_session):
    d = await driver(db_session)
    svc = service(db_session)
    with pytest.raises(DomainError, match="number_required"):
        await svc.create(entry(d.id, "driving_licence", number=None), None)
    with pytest.raises(DomainError, match="categories_not_allowed"):
        await svc.create(entry(d.id, "id_card", categories="B"), None)
    with pytest.raises(ValidationError):
        entry(d.id, document_type="passport")
    licence = await svc.create(entry(d.id, "driving_licence", categories="b, c;ce"), None)
    assert licence.categories == "B, C, CE"
    optional = await svc.create(entry(d.id, "professional_competence", number=None), None)
    assert optional.document_number is None
    with pytest.raises(DomainError, match="driver_not_found"):
        await svc.create(entry(uuid4()), None)
    with pytest.raises(ValidationError):
        entry(d.id, start=TODAY, end=TODAY - timedelta(days=1))


@pytest.mark.asyncio
async def test_dates_are_optional(db_session):
    d = await driver(db_session)
    created = await service(db_session).create(entry(d.id, "psycho_test", number=None, start=None, end=None), None)
    assert created.start_date is None and created.end_date is None and created.status == "no_expiry"


@pytest.mark.asyncio
async def test_current_entry_is_latest_end_date_and_history_never_warns(db_session):
    d = await driver(db_session)
    svc = service(db_session)
    old = await svc.create(entry(d.id, end=TODAY - timedelta(days=400)), None)
    new = await svc.create(entry(d.id, end=TODAY + timedelta(days=300)), None)
    summary = await svc.summary_for_driver(d)
    slot = dict(summary.slots)["id_card"]
    assert slot.id == new.id
    assert {e.id: e.is_current for e in summary.entries} == {new.id: True, old.id: False}
    # the expired old entry is history: no warning for the driver
    assert await svc.attention_counts() == {"expired": 0, "expiring_soon": 0}
    assert await svc.alert_counts_by_driver([d.id]) == {}


@pytest.mark.asyncio
async def test_no_expiry_counts_as_latest_and_never_warns(db_session):
    d = await driver(db_session)
    svc = service(db_session)
    await svc.create(entry(d.id, end=TODAY - timedelta(days=5)), None)
    forever = await svc.create(entry(d.id, end=None), None)
    assert dict((await svc.summary_for_driver(d)).slots)["id_card"].id == forever.id
    assert await svc.attention_counts() == {"expired": 0, "expiring_soon": 0}
    listed, _ = await svc.list_entries(status="no_expiry")
    assert [v.id for v in listed] == [forever.id]


@pytest.mark.asyncio
async def test_attention_counts_only_current_entries_of_active_drivers(db_session):
    active = await driver(db_session, "Active")
    gone = await driver(db_session, "Terminated", DriverStatus.TERMINATED)
    svc = service(db_session)
    await svc.create(entry(active.id, end=TODAY - timedelta(days=1)), None)
    await svc.create(entry(active.id, "driving_licence", end=TODAY + timedelta(days=10)), None)
    await svc.create(entry(active.id, "tachograph_card", end=TODAY + timedelta(days=100)), None)
    await svc.create(entry(gone.id, end=TODAY - timedelta(days=1)), None)
    assert await svc.attention_counts() == {"expired": 1, "expiring_soon": 1}
    assert await svc.alert_counts_by_driver([active.id, gone.id]) == {
        active.id: {"expired": 1, "expiring_soon": 1},
        gone.id: {"expired": 1, "expiring_soon": 0},
    }
    stats = await DashboardService(db_session).attention_needed()
    assert stats["expired_driver_documents"] >= 0 and "expiring_driver_documents" in stats
    assert "expiring_licenses" not in stats and "expiring_medical" not in stats


@pytest.mark.asyncio
async def test_list_drivers_by_document_status(db_session):
    a = await driver(db_session, "Has expired")
    b = await driver(db_session, "All good")
    svc = DriverDocumentService(db_session)  # real today for DriverService filters
    real = date.today()
    await svc.create(entry(a.id, end=real - timedelta(days=2)), None)
    await svc.create(entry(b.id, end=real + timedelta(days=400)), None)
    drivers, total = await DriverService(db_session).list_drivers(doc_status="expired")
    assert [x.id for x in drivers] == [a.id] and total == 1
    with pytest.raises(DomainError):
        await DriverService(db_session).list_drivers(doc_status="valid")


@pytest.mark.asyncio
async def test_renew_keeps_history_and_inherits_number(db_session):
    d = await driver(db_session)
    svc = service(db_session)
    source = await svc.create(
        entry(d.id, "driving_licence", number="L-1", categories="B, C", start=TODAY - timedelta(days=3000), end=TODAY - timedelta(days=5)),
        None,
    )
    renewed = await svc.renew(
        source.id, DriverDocumentRenew(start_date=TODAY, end_date=TODAY + timedelta(days=3650)), None
    )
    assert renewed.renewed_from_id == source.id and renewed.document_type == "driving_licence"
    assert renewed.document_number == "L-1" and renewed.categories == "B, C"  # inherited when omitted
    again = await svc.get(source.id)
    assert again.end_date == TODAY - timedelta(days=5) and not again.is_current and renewed.is_current
    assert (await svc.successor(source.id)).id == renewed.id
    # explicit values win; a required number cannot be blanked on renewal
    with pytest.raises(DomainError, match="number_required"):
        await svc.renew(renewed.id, DriverDocumentRenew(document_number=None, end_date=TODAY + timedelta(days=9999)), None)
    changed = await svc.renew(renewed.id, DriverDocumentRenew(document_number="L-2", categories="B"), None)
    assert changed.document_number == "L-2" and changed.categories == "B"


@pytest.mark.asyncio
async def test_renewing_a_no_expiry_document_hands_over_to_the_successor(db_session):
    d = await driver(db_session)
    svc = service(db_session)
    source = await svc.create(entry(d.id, "id_card", end=None), None)
    renewed = await svc.renew(source.id, DriverDocumentRenew(end_date=TODAY + timedelta(days=100)), None)
    assert renewed.is_current and not (await svc.get(source.id)).is_current


def test_renewal_proposal_handles_missing_dates(db_session):
    svc = service(db_session)

    class Src:
        document_number, categories, notes = "1", None, None

    dated = Src()
    dated.start_date, dated.end_date = date(2025, 11, 1), date(2026, 10, 31)
    assert svc.renewal_proposal(dated)["end_date"] == date(2027, 10, 31)
    open_end = Src()
    open_end.start_date, open_end.end_date = None, date(2026, 10, 31)
    assert svc.renewal_proposal(open_end)["start_date"] == date(2026, 11, 1)
    assert svc.renewal_proposal(open_end)["end_date"] is None
    none = Src()
    none.start_date = none.end_date = None
    assert svc.renewal_proposal(none)["start_date"] == TODAY


@pytest.mark.asyncio
async def test_patch_semantics(db_session):
    d = await driver(db_session)
    svc = service(db_session)
    e = await svc.create(entry(d.id, "driving_licence", categories="B", notes="keep", start=TODAY - timedelta(days=10)), None)
    kept = await svc.update(e.id, DriverDocumentUpdate(notes="changed"))
    assert kept.categories == "B" and kept.document_number == "123456789" and kept.notes == "changed"
    cleared = await svc.update(e.id, DriverDocumentUpdate(end_date=None, categories=None))
    assert cleared.end_date is None and cleared.categories is None and cleared.status == "no_expiry"
    with pytest.raises(DomainError, match="number_required"):
        await svc.update(e.id, DriverDocumentUpdate(document_number=None))
    with pytest.raises(DomainError, match="end_before_start"):
        await svc.update(e.id, DriverDocumentUpdate(end_date=TODAY - timedelta(days=20)))
    with pytest.raises(DomainError, match="not_found"):
        await svc.update(uuid4(), DriverDocumentUpdate(notes="x"))
    assert (await svc.get(e.id)).document_type == "driving_licence"  # type is fixed


@pytest.mark.asyncio
async def test_delete_scopes_and_attachments(db_session, s3):
    d = await driver(db_session)
    svc = service(db_session)
    old = await svc.create(entry(d.id, end=TODAY - timedelta(days=400)), None)
    new = await svc.renew(old.id, DriverDocumentRenew(end_date=TODAY + timedelta(days=300)), None)
    db_session.add_all([scan(EntityType.DRIVER_DOCUMENT, new.id, "k-new"), scan(EntityType.DRIVER_DOCUMENT, old.id, "k-old")])
    await db_session.flush()
    assert await svc.history_count(new.id) == 2
    assert await svc.delete(new.id) == 1
    assert s3.deleted == ["k-new"]
    assert (await svc.get(old.id)).is_current  # previous entry becomes current again
    refreshed = await svc.renew(old.id, DriverDocumentRenew(end_date=TODAY + timedelta(days=50)), None)
    assert await svc.delete(refreshed.id, "all") == 2
    assert "k-old" in s3.deleted
    assert await db_session.scalar(select(func.count()).select_from(DriverDocument)) == 0
    with pytest.raises(DomainError):
        await svc.delete(uuid4())
    with pytest.raises(DomainError):
        await svc.delete(old.id, "everything")


@pytest.mark.asyncio
async def test_delete_clears_renewed_from_of_survivors(db_session, s3):
    d = await driver(db_session)
    svc = service(db_session)
    first = await svc.create(entry(d.id, end=TODAY - timedelta(days=400)), None)
    second = await svc.renew(first.id, DriverDocumentRenew(end_date=TODAY + timedelta(days=300)), None)
    await svc.delete(first.id)
    assert (await svc.get(second.id)).renewed_from_id is None


@pytest.mark.asyncio
async def test_deleting_a_driver_removes_entries_and_all_attachments(db_session, s3):
    d = await driver(db_session)
    other = await driver(db_session, "Other")
    svc = service(db_session)
    e1 = await svc.create(entry(d.id), None)
    e2 = await svc.create(entry(d.id, "driving_licence"), None)
    keep = await svc.create(entry(other.id), None)
    db_session.add_all(
        [
            scan(EntityType.DRIVER, d.id, "k-driver"),
            scan(EntityType.DRIVER_DOCUMENT, e1.id, "k-e1"),
            scan(EntityType.DRIVER_DOCUMENT, e2.id, "k-e2"),
            scan(EntityType.DRIVER_DOCUMENT, keep.id, "k-keep"),
        ]
    )
    await db_session.flush()
    await DriverService(db_session).delete(d.id)
    assert sorted(s3.deleted) == ["k-driver", "k-e1", "k-e2"]
    assert await DriverService(db_session).get_by_id(d.id) is None
    remaining = (await db_session.scalars(select(DriverDocument.id))).all()
    assert remaining == [keep.id]
    assert [x.s3_key for x in (await db_session.scalars(select(Document))).all()] == ["k-keep"]
    with pytest.raises(DomainError, match="driver_not_found"):
        await DriverService(db_session).delete(d.id)


@pytest.mark.asyncio
async def test_driver_update_ignores_removed_fields(db_session):
    d = await driver(db_session)
    updated = await DriverService(db_session).update(d.id, DriverUpdate(phone="+359888", employee_id="X1"))
    assert updated.phone == "+359888" and not hasattr(updated, "employee_id")


@pytest.mark.asyncio
async def test_audit_log_masks_document_numbers(db_session):
    d = await driver(db_session)
    svc = service(db_session)
    user_id = None
    e = await svc.create(entry(d.id, "driving_licence", number="987654321"), user_id)
    await svc.update(e.id, DriverDocumentUpdate(document_number="123654321"), user_id)  # only the prefix changes
    renewed = await svc.renew(e.id, DriverDocumentRenew(end_date=TODAY + timedelta(days=500)), user_id)
    await svc.delete(renewed.id, "entry", user_id)
    logs = (await db_session.scalars(select(AuditLog).where(AuditLog.entity_type == "driver_document"))).all()
    assert sorted(log.action.value for log in logs) == ["create", "create", "delete", "update"]
    blob = str([log.changes for log in logs])
    assert "987654321" not in blob and "123654321" not in blob
    update = next(log for log in logs if log.action == AuditAction.UPDATE)
    assert update.changes["document_number"] == {"old": "•••• 4321", "new": "•••• 4321"}
