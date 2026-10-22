"""MaintenanceService rules: completion dates, unknown vehicles, attachments on delete."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.document import Document, DocumentType, EntityType
from app.models.maintenance import MaintenanceStatus
from app.schemas.maintenance import MaintenanceCreate, MaintenanceUpdate
from app.schemas.vehicle import VehicleCreate
from app.services.errors import DomainError
from app.services.maintenance_service import MaintenanceService
from app.services.validity import local_today
from app.services.vehicle_service import VehicleService


class FakeS3:
    def __init__(self):
        self.deleted = []

    def delete_file(self, key):
        self.deleted.append(key)


async def make_vehicle(db):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type="van",
            license_plate="CA" + uuid4().hex[:6].upper(),
            brand="Ford",
            model="Transit",
            department="Test department",
        )
    )


def payload(vehicle_id, **kw):
    return MaintenanceCreate(vehicle_id=vehicle_id, type="repair", title="Brake pads", **kw)


def today():
    return local_today()


@pytest.mark.asyncio
async def test_create_scheduled_has_no_completed_date(db_session):
    v = await make_vehicle(db_session)
    record = await MaintenanceService(db_session).create(payload(v.id, completed_date=date(2020, 1, 1)), None)
    assert record.status == MaintenanceStatus.SCHEDULED
    assert record.completed_date is None
    assert record.vehicle.id == v.id


@pytest.mark.asyncio
async def test_create_completed_defaults_to_today_or_uses_given_date(db_session):
    v = await make_vehicle(db_session)
    service = MaintenanceService(db_session)
    record = await service.create(payload(v.id, status="completed", cost="120.50"), None)
    assert record.status == MaintenanceStatus.COMPLETED and record.completed_date == today()
    given = date(2026, 3, 1)
    record = await service.create(payload(v.id, status="completed", completed_date=given), None)
    assert record.completed_date == given


@pytest.mark.asyncio
async def test_unknown_vehicle_rejected(db_session):
    with pytest.raises(DomainError) as exc:
        await MaintenanceService(db_session).create(payload(uuid4()), None)
    assert exc.value.key == "error.vehicle_not_found"


@pytest.mark.asyncio
async def test_update_status_keeps_completed_date_consistent(db_session):
    v = await make_vehicle(db_session)
    service = MaintenanceService(db_session)
    record = await service.create(payload(v.id), None)
    done = await service.update(record.id, MaintenanceUpdate(status="completed"))
    assert done.completed_date == today()
    # Editing other fields keeps the date; an explicit date replaces it.
    assert (await service.update(record.id, MaintenanceUpdate(title="New"))).completed_date == today()
    moved = await service.update(record.id, MaintenanceUpdate(completed_date=today() - timedelta(days=2)))
    assert moved.completed_date == today() - timedelta(days=2)
    reopened = await service.update(record.id, MaintenanceUpdate(status="in_progress"))
    assert reopened.status == MaintenanceStatus.IN_PROGRESS and reopened.completed_date is None


@pytest.mark.asyncio
async def test_complete_action(db_session):
    v = await make_vehicle(db_session)
    service = MaintenanceService(db_session)
    record = await service.create(payload(v.id), None)
    done = await service.complete(record.id)
    assert done.status == MaintenanceStatus.COMPLETED and done.completed_date == today()
    with pytest.raises(DomainError):
        await service.complete(uuid4())


def test_update_schema_rejects_null_required_fields():
    for field in ("title", "status", "type"):
        with pytest.raises(ValidationError):
            MaintenanceUpdate(**{field: None})
    assert MaintenanceUpdate(cost=None).model_dump(exclude_unset=True) == {"cost": None}


@pytest.mark.asyncio
async def test_delete_removes_attachments_and_files(db_session, monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr("app.services.maintenance_service.get_s3_client", lambda: fake)
    v = await make_vehicle(db_session)
    service = MaintenanceService(db_session)
    record = await service.create(payload(v.id), None)
    doc = Document(
        entity_type=EntityType.MAINTENANCE,
        entity_id=record.id,
        type=DocumentType.INVOICE,
        filename="i.pdf",
        s3_key=f"maintenance/{record.id}/i.pdf",
        mime_type="application/pdf",
        size_bytes=1,
    )
    db_session.add(doc)
    await db_session.flush()
    await service.delete(record.id)
    assert await service.get_by_id(record.id) is None
    assert await db_session.get(Document, doc.id) is None
    assert fake.deleted == [doc.s3_key]
    with pytest.raises(DomainError):
        await service.delete(record.id)
