"""QA #21: small translation gaps: source labels, raw codes in the audit log, Cyrillic in English text, plurals."""

from uuid import uuid4

import pytest

from app.i18n import _
from app.models.audit_log import AuditAction, AuditLog
from app.models.mileage import MileageLog, MileageSource
from app.models.notification import Notification, NotificationType
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService


async def _login(client, username="testmanager"):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


def test_english_text_has_no_cyrillic_acronym():
    for key in ("compliance_type.technical_inspection", "expense_category.technical_inspection",
                "notification_source.vehicle:technical_inspection"):
        text = _(key, "en")
        assert text == "Annual technical inspection" and not any("А" <= ch <= "я" for ch in text), text


@pytest.mark.asyncio
async def test_mileage_source_is_translated(client, db_session, fleet_manager_user):
    vehicle = await VehicleService(db_session).create(
        VehicleCreate(vehicle_type="van", license_plate="QA9000AA", brand="Iveco", model="D", department="Test department")
    )
    for source in (MileageSource.MANUAL, MileageSource.OBD, MileageSource.GPS):
        db_session.add(MileageLog(vehicle_id=vehicle.id, recorded_by=fleet_manager_user.id, value=100, source=source))
    await db_session.flush()
    await _login(client)
    bg = (await client.get(f"/vehicles/{vehicle.id}/tab/mileage?lang=bg")).text
    assert "Ръчно" in bg and "MANUAL" not in bg
    en = (await client.get(f"/vehicles/{vehicle.id}/tab/mileage?lang=en")).text
    assert "Manual" in en and "MANUAL" not in en and "OBD" in en and "GPS" in en


@pytest.mark.asyncio
async def test_audit_log_shows_translated_labels_values_and_plurals(client, db_session, admin_user):
    one = AuditLog(user_id=admin_user.id, action=AuditAction.UPDATE, entity_type="compliance_document", entity_id=uuid4(),
                   changes={"_label": "vehicle:technical_inspection", "document_type": {"old": "casco", "new": "technical_inspection"}})
    many = AuditLog(user_id=admin_user.id, action=AuditAction.UPDATE, entity_type="vehicle", entity_id=uuid4(),
                    changes={"notes": {"old": "a", "new": "b"}, "color": {"old": "x", "new": "y"}})
    db_session.add_all([one, many])
    await db_session.flush()
    await _login(client, "testadmin")
    bg = (await client.get("/settings/audit?lang=bg")).text
    assert "1 поле<" in bg.replace(" </summary>", "<") or ">1 поле" in bg
    assert "1 полета" not in bg and "2 полета" in bg
    assert ">technical_inspection<" not in bg and "vehicle:technical_inspection" not in bg
    en = (await client.get("/settings/audit?lang=en")).text
    assert "Annual technical inspection" in en and ">technical_inspection<" not in en
    assert "1 field" in en and "2 fields" in en


@pytest.mark.asyncio
async def test_unread_message_count_has_a_singular(client, db_session, fleet_manager_user):
    db_session.add(Notification(user_id=fleet_manager_user.id, title="t", message="m", type=NotificationType.SYSTEM))
    await db_session.flush()
    await _login(client)
    bg = (await client.get("/notifications/dropdown?lang=bg")).text
    assert "1 непрочетено съобщение" in bg and "1 непрочетени съобщения" not in bg
    en = (await client.get("/notifications/dropdown?lang=en")).text
    assert "1 unread message" in en and "1 unread messages" not in en
    db_session.add(Notification(user_id=fleet_manager_user.id, title="t2", message="m2", type=NotificationType.SYSTEM))
    await db_session.flush()
    assert "2 непрочетени съобщения" in (await client.get("/notifications/dropdown?lang=bg")).text
