"""ComplianceService rules on PostgreSQL: applicability, current entry, renewal, deletion."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.compliance import ComplianceDocument
from app.models.document import Document, DocumentType, EntityType
from app.models.vehicle import VehicleStatus
from app.schemas.compliance import ComplianceCreate, ComplianceRenew, ComplianceUpdate
from app.schemas.vehicle import VehicleCreate, VehicleUpdate
from app.services.compliance_service import ComplianceService
from app.services.dashboard_service import DashboardService
from app.services.errors import DomainError
from app.services.vehicle_service import VehicleService

TODAY = date(2026, 10, 21)


class FakeS3:
    def __init__(self):
        self.deleted = []

    def delete_file(self, key):
        self.deleted.append(key)


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr("app.services.compliance_service.get_s3_client", lambda: fake)
    monkeypatch.setattr("app.services.vehicle_service.get_s3_client", lambda: fake)
    return fake


async def vehicle(db, kind="tractor_unit", plate=None):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else plate or f"CA{uuid4().hex[:6].upper()}",
            brand="Volvo",
            model="FH",
            department="Test department",
        )
    )


def entry(
    vehicle_id,
    document_type="civil_liability",
    start=TODAY - timedelta(days=100),
    end=TODAY + timedelta(days=200),
    **kw,
):
    return ComplianceCreate(vehicle_id=vehicle_id, document_type=document_type, start_date=start, end_date=end, **kw)


def service(db):
    return ComplianceService(db, today=TODAY, window=30)


@pytest.mark.asyncio
async def test_create_and_computed_status(db_session):
    v = await vehicle(db_session)
    created = await service(db_session).create(entry(v.id, amount=Decimal("123.45"), provider=" ДЗИ "), None)
    assert created.status == "valid" and created.is_current and created.applicable
    assert created.days_left == 200
    assert created.provider == "ДЗИ"
    assert created.amount == Decimal("123.45")
    soon = await service(db_session).create(entry(v.id, "casco", end=TODAY + timedelta(days=30)), None)
    assert soon.status == "expiring_soon"
    gone = await service(db_session).create(entry(v.id, "technical_inspection", end=TODAY - timedelta(days=1)), None)
    assert gone.status == "expired"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,document_type", [("van", "tachograph"), ("forklift", "civil_liability")])
async def test_incompatible_type_rejected(db_session, kind, document_type):
    v = await vehicle(db_session, kind)
    with pytest.raises(DomainError) as exc:
        await service(db_session).create(entry(v.id, document_type), None)
    assert exc.value.key == "error.compliance_type_not_applicable"


@pytest.mark.asyncio
async def test_unknown_vehicle_rejected(db_session):
    with pytest.raises(DomainError) as exc:
        await service(db_session).create(entry(uuid4()), None)
    assert exc.value.key == "error.compliance_vehicle_not_found"


def test_schema_rules():
    with pytest.raises(ValidationError, match="error.compliance_end_before_start"):
        entry(uuid4(), start=TODAY, end=TODAY - timedelta(days=1))
    for amount in ("12.345", "-1", "10000000000.00", "NaN"):
        with pytest.raises(ValidationError):
            entry(uuid4(), amount=amount)
    with pytest.raises(ValidationError):
        entry(uuid4(), document_type="leasing")
    assert entry(uuid4(), start=TODAY, end=TODAY).end_date == TODAY
    assert entry(uuid4(), amount="0").amount == Decimal("0")


@pytest.mark.asyncio
async def test_latest_end_is_current_and_history_never_warns(db_session):
    v = await vehicle(db_session)
    svc = service(db_session)
    old = await svc.create(entry(v.id, start=TODAY - timedelta(days=400), end=TODAY - timedelta(days=35)), None)
    new = await svc.create(entry(v.id, start=TODAY - timedelta(days=34), end=TODAY + timedelta(days=330)), None)
    items, total = await svc.list_entries(vehicle_id=v.id)
    assert total == 1 and items[0].id == new.id and items[0].status == "valid"
    history, total = await svc.list_entries(vehicle_id=v.id, current_only=False)
    assert total == 2
    assert {h.id: h.is_current for h in history} == {old.id: False, new.id: True}
    assert (await svc.list_entries(vehicle_id=v.id, status="expired"))[1] == 0
    assert (await svc.list_entries(vehicle_id=v.id, status="expired", current_only=False))[1] == 1
    assert await svc.attention_counts() == {"expiring_soon": 0, "expired": 0}


@pytest.mark.asyncio
async def test_renew_creates_new_entry_and_keeps_history(db_session):
    v = await vehicle(db_session)
    svc = service(db_session)
    source = await svc.create(
        entry(v.id, start=date(2025, 11, 10), end=date(2026, 11, 9), provider="Булстрад", policy_number="P-1"), None
    )
    proposal = svc.renewal_proposal(source)
    assert proposal == {"start_date": date(2026, 11, 10), "end_date": date(2027, 11, 9), "provider": "Булстрад"}
    # Bought early: the new period starts before the source ends.
    renewed = await svc.renew(
        source.id, ComplianceRenew(start_date=date(2026, 11, 1), end_date=date(2027, 10, 31), provider="ДЗИ"), None
    )
    assert renewed.id != source.id
    assert renewed.vehicle_id == v.id and renewed.document_type == "civil_liability"
    assert renewed.renewed_from_id == source.id and renewed.is_current
    old = await svc.get(source.id)
    assert not old.is_current
    assert (old.start_date, old.end_date, old.policy_number) == (date(2025, 11, 10), date(2026, 11, 9), "P-1")
    assert (await svc.successor(source.id)).id == renewed.id


@pytest.mark.asyncio
async def test_update_patch_semantics(db_session):
    v = await vehicle(db_session)
    svc = service(db_session)
    created = await svc.create(entry(v.id, provider="A", notes="keep"), None)
    updated = await svc.update(created.id, ComplianceUpdate(end_date=TODAY + timedelta(days=500), provider=None))
    assert updated.end_date == TODAY + timedelta(days=500)
    assert updated.provider is None and updated.notes == "keep"
    with pytest.raises(DomainError) as exc:
        await svc.update(created.id, ComplianceUpdate(end_date=TODAY - timedelta(days=200)))
    assert exc.value.key == "error.compliance_end_before_start"
    with pytest.raises(ValidationError):
        ComplianceUpdate(start_date=None)
    assert "vehicle_id" not in ComplianceUpdate.model_fields and "document_type" not in ComplianceUpdate.model_fields


@pytest.mark.asyncio
async def test_type_change_keeps_history_and_stops_warnings(db_session):
    v = await vehicle(db_session, "tractor_unit")
    svc = service(db_session)
    tacho = await svc.create(entry(v.id, "tachograph", end=TODAY - timedelta(days=5)), None)
    await svc.create(entry(v.id, "casco", end=TODAY - timedelta(days=5)), None)
    assert (await svc.attention_counts())["expired"] == 2
    await VehicleService(db_session).update(v.id, VehicleUpdate(vehicle_type="van"))
    kept = await svc.get(tacho.id)
    assert kept is not None and not kept.applicable
    assert (await svc.attention_counts())["expired"] == 1
    with pytest.raises(DomainError) as exc:
        await svc.renew(tacho.id, ComplianceRenew(start_date=TODAY, end_date=TODAY + timedelta(days=365)), None)
    assert exc.value.key == "error.compliance_type_not_applicable"
    summary = await svc.summary_for_vehicle(await VehicleService(db_session).get_by_id(v.id))
    assert [code for code, _ in summary.slots] == ["civil_liability", "casco", "technical_inspection"]
    assert len(summary.entries) == 2


@pytest.mark.asyncio
async def test_decommissioned_vehicles_excluded_from_counts(db_session):
    v = await vehicle(db_session)
    svc = service(db_session)
    await svc.create(entry(v.id, end=TODAY + timedelta(days=3)), None)
    assert (await svc.attention_counts())["expiring_soon"] == 1
    await VehicleService(db_session).update(v.id, VehicleUpdate(status=VehicleStatus.DECOMMISSIONED))
    assert (await svc.attention_counts())["expiring_soon"] == 0


@pytest.mark.asyncio
async def test_dashboard_uses_compliance_counts(db_session):
    v = await vehicle(db_session)
    svc = ComplianceService(db_session)
    await svc.create(entry(v.id, start=svc.today - timedelta(days=300), end=svc.today - timedelta(days=1)), None)
    data = await DashboardService(db_session).attention_needed()
    assert data["expired_compliance"] == 1
    assert "expiring_contracts" not in data


async def attach(db, entity_type, entity_id):
    doc = Document(
        entity_type=entity_type,
        entity_id=entity_id,
        type=DocumentType.INSURANCE,
        filename="p.pdf",
        s3_key=f"compliance_document/{entity_id}/p.pdf",
        mime_type="application/pdf",
        size_bytes=1,
    )
    db.add(doc)
    await db.flush()
    return doc


@pytest.mark.asyncio
async def test_delete_entry_removes_attachments_and_unlinks_renewal(db_session, s3):
    v = await vehicle(db_session)
    svc = service(db_session)
    source = await svc.create(entry(v.id), None)
    renewed = await svc.renew(source.id, ComplianceRenew(start_date=TODAY, end_date=TODAY + timedelta(days=400)), None)
    doc = await attach(db_session, EntityType.COMPLIANCE_DOCUMENT, source.id)
    await svc.delete(source.id)
    assert await svc.get(source.id) is None
    assert (await svc.get(renewed.id)).renewed_from_id is None
    assert await db_session.get(Document, doc.id) is None
    assert s3.deleted == [doc.s3_key]
    with pytest.raises(DomainError):
        await svc.delete(source.id)


@pytest.mark.asyncio
async def test_vehicle_delete_removes_compliance_documents(db_session, s3):
    v = await vehicle(db_session)
    created = await service(db_session).create(entry(v.id), None)
    doc = await attach(db_session, EntityType.COMPLIANCE_DOCUMENT, created.id)
    await VehicleService(db_session).delete(v.id)
    count = await db_session.scalar(select(func.count()).select_from(ComplianceDocument))
    assert count == 0
    assert doc.s3_key in s3.deleted


@pytest.mark.asyncio
async def test_vehicle_options_exclude_forklifts(db_session):
    await vehicle(db_session, "forklift")
    van = await vehicle(db_session, "van", plate="CB7777AB")
    options = await service(db_session).vehicle_options("")
    assert all(o.vehicle_type != "forklift" for o in options)
    assert [o.id for o in await service(db_session).vehicle_options("7777")] == [van.id]


@pytest.mark.asyncio
async def test_delete_scope_entry_restores_previous_and_all_removes_history(db_session, s3):
    v = await vehicle(db_session)
    other = await vehicle(db_session)
    svc = service(db_session)
    first = await svc.create(entry(v.id, start=TODAY - timedelta(days=400), end=TODAY - timedelta(days=35)), None)
    second = await svc.renew(
        first.id, ComplianceRenew(start_date=TODAY - timedelta(days=34), end_date=TODAY + timedelta(days=330)), None
    )
    casco = await svc.create(entry(v.id, "casco"), None)
    unrelated = await svc.create(entry(other.id), None)
    docs = [await attach(db_session, EntityType.COMPLIANCE_DOCUMENT, e.id) for e in (first, second, casco)]
    assert await svc.history_count(second.id) == 2

    assert await svc.delete(second.id, "entry") == 1
    assert (await svc.get(first.id)).is_current
    assert await svc.history_count(first.id) == 1

    second = await svc.renew(first.id, ComplianceRenew(start_date=TODAY, end_date=TODAY + timedelta(days=365)), None)
    assert await svc.delete(second.id, "all") == 2
    assert await svc.get(first.id) is None and await svc.get(second.id) is None
    # Other types and other vehicles are untouched, and so are their attachments.
    assert await svc.get(casco.id) is not None and await svc.get(unrelated.id) is not None
    assert await db_session.get(Document, docs[2].id) is not None
    assert await db_session.get(Document, docs[0].id) is None
    assert docs[0].s3_key in s3.deleted and docs[1].s3_key in s3.deleted and docs[2].s3_key not in s3.deleted

    with pytest.raises(DomainError):
        await svc.delete(casco.id, "everything")


@pytest.mark.asyncio
async def test_transport_licence_copy_is_for_tractor_units_only(db_session):
    tractor = await vehicle(db_session)
    created = await service(db_session).create(
        entry(tractor.id, "transport_licence", policy_number="22098-001", end=TODAY + timedelta(days=20)), None
    )
    assert created.status == "expiring_soon" and created.policy_number == "22098-001" and created.applicable
    renewed = await service(db_session).renew(
        created.id,
        ComplianceRenew(
            start_date=TODAY + timedelta(days=21), end_date=TODAY + timedelta(days=400), policy_number="22098-002"
        ),
        None,
    )
    assert renewed.is_current and renewed.policy_number == "22098-002"
    for kind in ("container_semi_trailer", "curtain_side_semi_trailer", "van", "passenger_car", "forklift"):
        other = await vehicle(db_session, kind)
        with pytest.raises(DomainError, match="error.compliance_type_not_applicable"):
            await service(db_session).create(entry(other.id, "transport_licence"), None)


@pytest.mark.asyncio
async def test_transport_licence_copy_has_no_cost_and_no_provider(db_session):
    tractor = await vehicle(db_session)
    for extra in ({"amount": Decimal("10.00")}, {"provider": "Some insurer"}):
        with pytest.raises(DomainError, match="error.compliance_no_cost_or_provider"):
            await service(db_session).create(entry(tractor.id, "transport_licence", **extra), None)
    created = await service(db_session).create(entry(tractor.id, "transport_licence"), None)
    with pytest.raises(DomainError, match="error.compliance_no_cost_or_provider"):
        await service(db_session).update(created.id, ComplianceUpdate(amount=Decimal("5.00")))
    with pytest.raises(DomainError, match="error.compliance_no_cost_or_provider"):
        await service(db_session).renew(
            created.id,
            ComplianceRenew(start_date=TODAY, end_date=TODAY + timedelta(days=300), provider="Some insurer"),
            None,
        )
    # other types keep both fields
    await service(db_session).create(entry(tractor.id, "casco", amount=Decimal("10.00"), provider="DZI"), None)
