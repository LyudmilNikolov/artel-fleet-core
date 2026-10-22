"""AttentionService: the flat list of documents that need attention, shared by bell, inbox and dashboard."""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.models.driver import DriverStatus
from app.models.vehicle import VehicleStatus
from app.schemas.compliance import ComplianceCreate, ComplianceRenew
from app.schemas.driver import DriverCreate
from app.schemas.driver_document import DriverDocumentCreate, DriverDocumentRenew
from app.schemas.vehicle import VehicleCreate, VehicleUpdate
from app.services.attention_service import AttentionService, neighbours
from app.services.compliance_service import ComplianceService
from app.services.driver_document_service import DriverDocumentService
from app.services.driver_scope import UNRESTRICTED, DriverScope
from app.services.driver_service import DriverService
from app.services.vehicle_service import VehicleService

TODAY = date(2026, 10, 21)


async def vehicle(db, plate=None, kind="tractor_unit"):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else plate or f"CA{uuid4().hex[:6].upper()}",
            brand="Volvo",
            model="FH",
            department="Test department",
        )
    )


async def driver(db, name="Иван Петров", status=DriverStatus.ACTIVE):
    return await DriverService(db).create(DriverCreate(full_name=name, status=status))


async def vehicle_doc(db, v, kind="civil_liability", days=10):
    end = TODAY + timedelta(days=days)
    return await ComplianceService(db, today=TODAY, window=30).create(
        ComplianceCreate(vehicle_id=v.id, document_type=kind, start_date=end - timedelta(days=365), end_date=end), None
    )


async def driver_doc(db, d, kind="id_card", days=10, number="987654321"):
    end = None if days is None else TODAY + timedelta(days=days)
    return await DriverDocumentService(db, today=TODAY, window=30).create(
        DriverDocumentCreate(driver_id=d.id, document_type=kind, document_number=number, end_date=end), None
    )


def svc(db, scope=UNRESTRICTED):
    return AttentionService(db, today=TODAY, scope=scope)


@pytest.mark.asyncio
async def test_lists_expired_and_expiring_documents_with_days_left(db_session):
    v = await vehicle(db_session, "CA1111AA")
    d = await driver(db_session, "Мария Георгиева")
    await vehicle_doc(db_session, v, "casco", days=-3)
    await vehicle_doc(db_session, v, "civil_liability", days=12)
    await driver_doc(db_session, d, "id_card", days=5)
    await vehicle_doc(db_session, v, "technical_inspection", days=200)  # valid: not listed

    items = await svc(db_session).items()
    assert [(i.source, i.status, i.days_left) for i in items] == [
        ("vehicle:casco", "expired", -3),
        ("driver:id_card", "expiring_soon", 5),
        ("vehicle:civil_liability", "expiring_soon", 12),
    ]
    first = items[0]
    assert first.subject == "CA1111AA" and first.entity_type == "compliance_document"
    assert first.key == f"compliance_document:{first.entity_id}"
    assert first.url == f"/notifications/item/compliance_document/{first.entity_id}"
    assert items[1].subject == "Мария Георгиева" and items[1].entity_type == "driver_document"
    assert first.stopped is False


@pytest.mark.asyncio
async def test_items_never_carry_document_numbers(db_session):
    d = await driver(db_session)
    await driver_doc(db_session, d, "driving_licence", days=3, number="SECRET-NUMBER-77")
    items = await svc(db_session).items()
    assert items and "SECRET-NUMBER-77" not in repr(items)


@pytest.mark.asyncio
async def test_only_current_entries_count_and_renewal_creates_a_new_key(db_session):
    v = await vehicle(db_session)
    old = await vehicle_doc(db_session, v, "casco", days=4)
    before = await svc(db_session).items()
    assert [i.entity_id for i in before] == [old.id]

    renewed = await ComplianceService(db_session, today=TODAY, window=30).renew(
        old.id,
        ComplianceRenew(start_date=TODAY - timedelta(days=1), end_date=TODAY + timedelta(days=400)),
        None,
    )
    assert renewed.id != old.id
    assert await svc(db_session).items() == []  # old entry is history, new one is valid


@pytest.mark.asyncio
async def test_renewed_driver_document_is_replaced_by_successor(db_session):
    d = await driver(db_session)
    old = await driver_doc(db_session, d, "psycho_test", days=2)
    await DriverDocumentService(db_session, today=TODAY, window=30).renew(
        old.id, DriverDocumentRenew(start_date=TODAY, end_date=TODAY + timedelta(days=15)), None
    )
    items = await svc(db_session).items()
    assert len(items) == 1 and items[0].entity_id != old.id and items[0].days_left == 15


@pytest.mark.asyncio
async def test_no_expiry_documents_never_warn(db_session):
    d = await driver(db_session)
    await driver_doc(db_session, d, "id_card", days=None)
    assert await svc(db_session).items() == []


@pytest.mark.asyncio
async def test_inactive_drivers_and_decommissioned_vehicles_are_excluded(db_session):
    gone = await driver(db_session, "Терминиран", DriverStatus.TERMINATED)
    leave = await driver(db_session, "В отпуск", DriverStatus.ON_LEAVE)
    await driver_doc(db_session, gone, days=-5)
    await driver_doc(db_session, leave, days=-5)
    v = await vehicle(db_session)
    await vehicle_doc(db_session, v, "casco", days=-5)
    await VehicleService(db_session).update(v.id, VehicleUpdate(status=VehicleStatus.DECOMMISSIONED))
    assert await svc(db_session).items() == []


@pytest.mark.asyncio
async def test_non_applicable_compliance_types_are_excluded(db_session):
    v = await vehicle(db_session, kind="van")
    await vehicle_doc(db_session, v, "civil_liability", days=3)
    # a tachograph entry on a van would be inapplicable; the service rejects it, so insert it directly
    from app.models.compliance import ComplianceDocument

    db_session.add(
        ComplianceDocument(
            vehicle_id=v.id,
            document_type="tachograph",
            start_date=TODAY - timedelta(days=300),
            end_date=TODAY + timedelta(days=2),
        )
    )
    await db_session.flush()
    items = await svc(db_session).items()
    assert [i.source for i in items] == ["vehicle:civil_liability"]


@pytest.mark.asyncio
async def test_filters_by_source_status_and_search(db_session):
    v = await vehicle(db_session, "CB2222BB")
    d = await driver(db_session, "Петър Стоянов")
    await vehicle_doc(db_session, v, "casco", days=-1)
    await vehicle_doc(db_session, v, "civil_liability", days=9)
    await driver_doc(db_session, d, "adr_certificate", days=9)
    s = svc(db_session)
    assert [i.source for i in await s.items(source="driver:adr_certificate")] == ["driver:adr_certificate"]
    assert [i.source for i in await s.items(status="expired")] == ["vehicle:casco"]
    assert len(await s.items(q="cb2222")) == 2
    assert [i.subject for i in await s.items(q="стоянов")] == ["Петър Стоянов"]
    assert await s.items(source="nonsense") == []


@pytest.mark.asyncio
async def test_driver_scope_sees_own_documents_and_assigned_vehicles_only(db_session):
    mine = await driver(db_session, "Аз")
    other = await driver(db_session, "Друг")
    v_mine = await vehicle(db_session, "CA0001AA")
    v_other = await vehicle(db_session, "CA0002AA")
    await driver_doc(db_session, mine, "id_card", days=3)
    await driver_doc(db_session, other, "id_card", days=3)
    await vehicle_doc(db_session, v_mine, "casco", days=3)
    await vehicle_doc(db_session, v_other, "casco", days=3)

    scope = DriverScope(restricted=True, driver_id=mine.id, vehicle_ids=(v_mine.id,))
    items = await svc(db_session, scope).items()
    assert sorted(i.subject for i in items) == ["CA0001AA", "Аз"]

    nothing = await svc(db_session, DriverScope(restricted=True)).items()
    assert nothing == []
    assert len(await svc(db_session).items()) == 4


@pytest.mark.asyncio
async def test_counts_match_the_legacy_attention_counts(db_session):
    v = await vehicle(db_session)
    d = await driver(db_session)
    await vehicle_doc(db_session, v, "casco", days=-2)
    await vehicle_doc(db_session, v, "civil_liability", days=7)
    await driver_doc(db_session, d, "id_card", days=-9)
    await driver_doc(db_session, d, "medical_certificate", days=20)
    await driver_doc(db_session, d, "psycho_test", days=25)

    counts = await svc(db_session).counts()
    assert counts == {
        "vehicle": {"expired": 1, "expiring_soon": 1},
        "driver": {"expired": 1, "expiring_soon": 2},
    }
    assert counts["vehicle"] == await ComplianceService(db_session, today=TODAY, window=30).attention_counts()
    assert counts["driver"] == await DriverDocumentService(db_session, today=TODAY, window=30).attention_counts()


@pytest.mark.asyncio
async def test_sort_order_and_neighbours(db_session):
    v = await vehicle(db_session, "CA3333CC")
    for kind, days in (("casco", 8), ("civil_liability", -4), ("technical_inspection", 2)):
        await vehicle_doc(db_session, v, kind, days=days)
    items = await svc(db_session).items()
    assert [i.days_left for i in items] == [-4, 2, 8]
    assert neighbours(items, items[0].key) == (None, items[1])
    assert neighbours(items, items[1].key) == (items[0], items[2])
    assert neighbours(items, items[2].key) == (items[1], None)
    assert neighbours(items, "missing:key") == (None, None)


@pytest.mark.asyncio
async def test_find_returns_one_item_in_scope_or_none(db_session):
    v = await vehicle(db_session)
    doc = await vehicle_doc(db_session, v, "casco", days=3)
    valid = await vehicle_doc(db_session, v, "tachograph", days=300)
    s = svc(db_session)
    assert (await s.find("compliance_document", doc.id)).entity_id == doc.id
    assert await s.find("compliance_document", valid.id) is None  # nothing to act on
    assert await s.find("driver_document", doc.id) is None
    assert await s.find("bogus", doc.id) is None
    scope = DriverScope(restricted=True, driver_id=None, vehicle_ids=())
    assert await svc(db_session, scope).find("compliance_document", doc.id) is None
