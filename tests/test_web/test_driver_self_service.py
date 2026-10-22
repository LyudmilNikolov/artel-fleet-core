"""A driver login manages their own documents: tabs, licences (add / edit / renew) and uploads of their own scans."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.driver_document import DriverDocument
from app.schemas.driver_document import DriverDocumentCreate
from app.services.driver_document_service import DriverDocumentService
from tests.test_web.test_driver_access import get, login, make_driver, make_driver_login


class FakeS3:
    def __init__(self):
        self.objects = {}

    def upload_file(self, data, filename, mime_type, folder=""):
        key = f"{folder}/{uuid4()}-{filename}"
        self.objects[key] = data
        return key

    def get_presigned_url(self, key):
        return f"http://files.test/{key}"

    def delete_file(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    for module in ("document_service", "driver_document_service", "driver_service"):
        monkeypatch.setattr(f"app.services.{module}.get_s3_client", lambda: fake)
    return fake


@pytest.fixture
async def pair(db_session):
    """Two drivers with logins; the first one has a licence entry."""
    mine = await make_driver(db_session, "Mine Driver")
    other = await make_driver(db_session, "Other Driver")
    me = await make_driver_login(db_session, mine, "mine")
    await make_driver_login(db_session, other, "other")
    service = DriverDocumentService(db_session)
    entry = await service.create(
        DriverDocumentCreate(
            driver_id=mine.id,
            document_type="driving_licence",
            document_number="BG1234567",
            categories="B",
            end_date=date.today() + timedelta(days=100),
        ),
        None,
    )
    theirs = await service.create(
        DriverDocumentCreate(driver_id=other.id, document_type="id_card", document_number="OTHER-777", end_date=None),
        None,
    )
    await db_session.commit()
    return {"mine": mine, "other": other, "me": me, "entry": entry, "theirs": theirs}


async def entries_for(db, driver):
    return list((await db.scalars(select(DriverDocument).where(DriverDocument.driver_id == driver.id))).all())


@pytest.mark.asyncio
async def test_driver_sees_all_tabs_and_edit_links_without_a_vehicle(client, pair):
    await login(client, "mine")
    own = f"/drivers/{pair['mine'].id}"
    page = await client.get(own)
    assert page.status_code == 200
    for tab in ("General data", "Uploaded documents", "Licences &amp; Documents"):
        assert tab in page.text
    assert 'hx-post="/documents/upload"' in page.text  # the upload box
    # the tabs' content loads (it used to be closed to a driver without a vehicle)
    gallery = await client.get(f"/documents/gallery/driver/{pair['mine'].id}")
    assert gallery.status_code == 200
    tab = await client.get(f"{own}/tab/licences")
    assert tab.status_code == 200 and "BG1234567" in tab.text
    assert f"/driver-documents/{pair['entry'].id}/renew" in tab.text
    assert f"/driver-documents/new?driver_id={pair['mine'].id}" in tab.text.replace("&amp;", "&")
    # the profile itself stays read-only: no edit / delete / assign controls
    assert f"/drivers/{pair['mine'].id}/edit" not in page.text and "/delete" not in page.text
    assert "Assign vehicle" not in page.text


@pytest.mark.asyncio
async def test_driver_adds_a_licence_to_their_own_profile(client, db_session, pair):
    await login(client, "mine")
    form = await client.get(f"/driver-documents/new?driver_id={pair['mine'].id}&document_type=id_card")
    assert form.status_code == 200
    created = await client.post(
        "/driver-documents/new",
        data={
            "driver_id": str(pair["mine"].id),
            "document_type": "id_card",
            "document_number": "A1B2C3",
            "end_date": "2031-01-01",
        },
        follow_redirects=False,
    )
    assert created.status_code == 302 and created.headers["location"].startswith("/driver-documents/")
    types = sorted(e.document_type for e in await entries_for(db_session, pair["mine"]))
    assert types == ["driving_licence", "id_card"]
    entry = next(e for e in await entries_for(db_session, pair["mine"]) if e.document_type == "id_card")
    audit = await db_session.scalar(select(AuditLog).where(AuditLog.entity_id == entry.id))
    assert audit.user_id == pair["me"].id  # who added it is recorded
    # the detail page offers edit and renew but not delete
    detail = await client.get(created.headers["location"])
    assert detail.status_code == 200 and "/renew" in detail.text and "/edit" in detail.text
    assert "/delete" not in detail.text


@pytest.mark.asyncio
async def test_driver_edits_and_renews_their_own_entry(client, db_session, pair):
    await login(client, "mine")
    entry = pair["entry"]
    edit = await client.get(f"/driver-documents/{entry.id}/edit")
    assert edit.status_code == 200
    updated = await client.post(
        f"/driver-documents/{entry.id}/edit",
        data={"document_number": "BG7654321", "categories": "B", "end_date": str(date.today() + timedelta(days=200))},
        follow_redirects=False,
    )
    assert updated.status_code == 302
    await db_session.refresh(entry)
    assert entry.document_number == "BG7654321"
    renew = await client.post(
        f"/driver-documents/{entry.id}/renew",
        data={
            "document_number": "BG7654321",
            "categories": "B",
            "start_date": str(date.today()),
            "end_date": "2040-01-01",
        },
        follow_redirects=False,
    )
    assert renew.status_code == 302
    assert len(await entries_for(db_session, pair["mine"])) == 2  # the old entry stays as history


@pytest.mark.asyncio
async def test_driver_cannot_touch_another_drivers_documents(client, db_session, pair):
    await login(client, "mine")
    theirs, other = pair["theirs"], pair["other"]
    before = len(await entries_for(db_session, other))
    own = f"/drivers/{pair['mine'].id}"
    # forms and submissions for somebody else's profile or entry go nowhere
    for method, path, data in (
        ("get", f"/driver-documents/new?driver_id={other.id}", None),
        (
            "post",
            "/driver-documents/new",
            {"driver_id": str(other.id), "document_type": "id_card", "document_number": "X"},
        ),
        ("get", f"/driver-documents/{theirs.id}/edit", None),
        ("post", f"/driver-documents/{theirs.id}/edit", {"document_number": "HACKED"}),
        ("get", f"/driver-documents/{theirs.id}/renew", None),
        ("post", f"/driver-documents/{theirs.id}/renew", {"document_number": "X", "start_date": "2030-01-01"}),
        ("get", f"/driver-documents/{theirs.id}", None),
        ("get", f"/documents/gallery/driver/{other.id}", None),
        ("get", f"/documents/gallery/driver_document/{theirs.id}", None),
    ):
        response = await getattr(client, method)(path, **({"data": data} if data else {}), follow_redirects=False)
        assert response.status_code in (302, 403), (path, response.status_code)
        if response.status_code == 302:
            assert response.headers["location"] in ("/", "/drivers", own), path
    await db_session.refresh(theirs)
    assert theirs.document_number == "OTHER-777" and len(await entries_for(db_session, other)) == before


@pytest.mark.asyncio
async def test_driver_cannot_delete_entries(client, db_session, pair):
    await login(client, "mine")
    response = await client.post(
        f"/driver-documents/{pair['entry'].id}/delete", data={"scope": "entry"}, follow_redirects=False
    )
    assert response.status_code in (302, 303)
    assert await db_session.get(DriverDocument, pair["entry"].id) is not None


@pytest.mark.asyncio
async def test_driver_uploads_and_removes_their_own_scan(client, db_session, pair, s3):
    await login(client, "mine")
    files = {"file": ("licence.pdf", b"%PDF-1.4 test", "application/pdf")}
    for entity_type, entity_id in (("driver", pair["mine"].id), ("driver_document", pair["entry"].id)):
        response = await client.post(
            "/documents/upload",
            files=files,
            data={"entity_type": entity_type, "entity_id": str(entity_id), "doc_type": "license"},
        )
        assert response.status_code == 200, response.text[:200]
        assert "licence.pdf" in response.text
    docs = list((await db_session.scalars(select(Document))).all())
    assert len(docs) == 2 and all(d.uploaded_by == pair["me"].id for d in docs)
    assert len(s3.objects) == 2
    # the driver sees and can download their file
    gallery = await client.get(f"/documents/gallery/driver/{pair['mine'].id}")
    assert "licence.pdf" in gallery.text and f"/documents/{docs[0].id}" in gallery.text
    download = await get(client, f"/documents/{next(d for d in docs if d.entity_type.value == 'driver').id}/download")
    assert download.status_code == 302 and download.headers["location"].startswith("http://files.test/")
    # they can remove a file they uploaded
    mine = next(d for d in docs if d.entity_type.value == "driver")
    assert (await client.delete(f"/documents/{mine.id}")).status_code == 200
    assert await db_session.scalar(select(Document).where(Document.id == mine.id)) is None


@pytest.mark.asyncio
async def test_driver_cannot_upload_to_other_records_or_remove_others_files(client, db_session, pair, s3, admin_user):
    await login(client, "mine")
    files = {"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}
    other = pair["other"]
    for entity_type, entity_id in (
        ("driver", other.id),
        ("driver_document", pair["theirs"].id),
        ("vehicle", uuid4()),
        ("expense", uuid4()),
    ):
        response = await client.post(
            "/documents/upload",
            files=files,
            data={"entity_type": entity_type, "entity_id": str(entity_id), "doc_type": "other"},
        )
        assert response.status_code == 403, (entity_type, response.text[:120])
    assert not s3.objects
    # a file uploaded by someone else (even on the driver's own record) cannot be removed by the driver
    from app.models.document import DocumentType, EntityType
    from app.services.document_service import DocumentService

    doc = await DocumentService(db_session).upload(
        file_data=b"%PDF-1.4",
        filename="manager.pdf",
        mime_type="application/pdf",
        entity_type=EntityType.DRIVER,
        entity_id=pair["mine"].id,
        doc_type=DocumentType.OTHER,
        uploaded_by=admin_user.id,
    )
    await db_session.commit()
    assert (await client.delete(f"/documents/{doc.id}")).status_code == 403
    assert await db_session.get(Document, doc.id) is not None
    # no delete button is shown for it either
    assert (
        f'hx-delete="/documents/{doc.id}"'
        not in (await client.get(f"/documents/gallery/driver/{pair['mine'].id}")).text
    )


@pytest.mark.asyncio
async def test_viewers_still_cannot_edit_driver_documents(client, db_session, pair, viewer_user):
    await db_session.commit()
    assert (await client.post("/login", data={"username": "testviewer", "password": "testpass123"})).status_code == 302
    for path in (f"/driver-documents/{pair['entry'].id}/edit", f"/driver-documents/new?driver_id={pair['mine'].id}"):
        response = await get(client, path)
        assert response.status_code == 302 and response.headers["location"] == "/", path
