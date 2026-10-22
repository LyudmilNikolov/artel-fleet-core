"""Automatic audit logging: create / update / delete of audited models, user context, redaction."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.audit_context import _context
from app.audit_events import AUDITED_TABLES
from app.models.audit_log import AuditAction, AuditLog
from app.models.user import User, UserRole
from app.schemas.vehicle import VehicleCreate, VehicleUpdate
from app.services.vehicle_service import VehicleService
from app.utils.security import hash_password
from app.web.settings import AUDIT_ENTITY_TYPES


def payload(**values):
    return VehicleCreate(
        **{
            "vehicle_type": "passenger_car",
            "license_plate": "CA1234AB",
            "brand": "Toyota",
            "model": "Camry",
            "department": "Test department",
            **values,
        }
    )


async def entries(db, entity_type="vehicle"):
    await db.flush()
    return list((await db.scalars(select(AuditLog).where(AuditLog.entity_type == entity_type))).all())


@pytest.fixture
def request_context(admin_user):
    token = _context.set({"user_id": admin_user.id, "ip_address": "10.1.2.3", "user_agent": "pytest"})
    yield
    _context.reset(token)


@pytest.mark.asyncio
async def test_vehicle_create_update_delete_are_logged(db_session, admin_user, request_context):
    svc = VehicleService(db_session)
    vehicle = await svc.create(payload(), admin_user.id)
    [created] = await entries(db_session)
    assert created.action == AuditAction.CREATE
    assert created.user_id == admin_user.id and created.entity_id == vehicle.id
    assert created.ip_address == "10.1.2.3" and created.user_agent == "pytest"
    assert created.changes["_label"] == "CA1234AB — Toyota Camry"
    assert created.changes["license_plate"] == "CA1234AB" and created.changes["vehicle_type"] == "passenger_car"

    await svc.update(vehicle.id, VehicleUpdate(color="Red"), admin_user.id)
    updates = [e for e in await entries(db_session) if e.action == AuditAction.UPDATE]
    assert len(updates) == 1
    assert updates[0].changes["color"] == {"old": None, "new": "Red"}
    assert "updated_at" not in updates[0].changes

    # saving without a real change writes nothing
    await svc.update(vehicle.id, VehicleUpdate(color="Red"), admin_user.id)
    assert len([e for e in await entries(db_session) if e.action == AuditAction.UPDATE]) == 1

    await db_session.delete(vehicle)
    deleted = [e for e in await entries(db_session) if e.action == AuditAction.DELETE]
    assert len(deleted) == 1 and deleted[0].entity_id == vehicle.id
    assert deleted[0].changes["_label"] == "CA1234AB — Toyota Camry"


@pytest.mark.asyncio
async def test_system_changes_have_no_user(db_session):
    vehicle = await VehicleService(db_session).create(payload(license_plate="CA9999ZZ"))
    [entry] = await entries(db_session)
    assert entry.entity_id == vehicle.id and entry.user_id is None and entry.ip_address is None


@pytest.mark.asyncio
async def test_password_hash_is_never_stored(db_session):
    user = User(
        id=uuid4(),
        email="secret@test.com",
        username="secret",
        full_name="Secret",
        hashed_password=hash_password("pw-12345678"),
        role=UserRole.VIEWER,
        is_active=True,
    )
    db_session.add(user)
    [entry] = await entries(db_session, "user")
    assert entry.changes["hashed_password"] == "***"
    assert "pw-12345678" not in json.dumps(entry.changes)
    user.hashed_password = hash_password("another-12345")
    updated = [e for e in await entries(db_session, "user") if e.action == AuditAction.UPDATE]
    assert updated[0].changes["hashed_password"] == {"old": "***", "new": "***"}


def test_every_audited_entity_has_a_label_in_both_languages():
    for lang in ("bg", "en"):
        keys = json.loads(Path(f"app/i18n/{lang}/messages.json").read_text())
        for entity in {*AUDITED_TABLES.values(), *AUDIT_ENTITY_TYPES}:
            assert f"audit_entity.{entity}" in keys, (lang, entity)
