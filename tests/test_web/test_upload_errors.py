"""QA #12: a rejected upload is explained on the page instead of leaving the file in the drop zone in silence."""

from uuid import uuid4

import pytest

from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService


async def _vehicle(db):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type="van",
            license_plate="CA" + uuid4().hex[:6].upper(),
            brand="Iveco",
            model="Daily",
            department="Test department",
        )
    )


async def _login(client):
    assert (await client.post("/login", data={"username": "testmanager", "password": "testpass123"})).status_code == 302


@pytest.mark.asyncio
async def test_upload_box_has_an_error_region_wired_to_the_server_response(client, db_session, fleet_manager_user):
    vehicle = await _vehicle(db_session)
    await _login(client)
    page = (await client.get(f"/vehicles/{vehicle.id}")).text
    assert 'hx-post="/documents/upload"' in page
    assert 'data-upload-error' in page and 'role="alert"' in page  # where the message appears
    assert "htmx:response-error" in page or "@htmx:response-error" in page  # the 4xx body is shown
    assert "htmx:send-error" in page or "@htmx:send-error" in page  # and a lost connection


@pytest.mark.asyncio
async def test_rejected_file_type_returns_a_readable_message_in_both_languages(client, db_session, fleet_manager_user):
    vehicle = await _vehicle(db_session)
    await _login(client)
    data = {"entity_type": "vehicle", "entity_id": str(vehicle.id), "doc_type": "other"}
    files = {"file": ("malware.exe", b"MZ\x90\x00", "application/x-msdownload")}
    en = await client.post("/documents/upload?lang=en", data=data, files=files)
    assert en.status_code == 400 and "Unsupported file type" in en.text
    bg = await client.post("/documents/upload?lang=bg", data=data, files=files)
    assert bg.status_code == 400 and "Unsupported file type" not in bg.text and bg.text.strip()


def test_generic_upload_failure_message_exists_in_both_languages():
    from app.i18n import _

    assert _("errors.upload_failed", "en") != "errors.upload_failed"
    assert _("errors.upload_failed", "bg") != "errors.upload_failed"
