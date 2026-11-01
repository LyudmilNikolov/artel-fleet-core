"""A driver login manages their own documents: tabs, licences (add / edit / renew) and uploads of their own scans."""

import json
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document
from app.models.driver_document import DriverDocument
from app.models.driver_document_change import DriverDocumentChange
from app.models.notification import Notification
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

    def get_presigned_url(self, key, **kwargs):
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


async def changes_for(db, driver=None):
    query = select(DriverDocumentChange).order_by(DriverDocumentChange.created_at)
    if driver is not None:
        query = query.where(DriverDocumentChange.driver_id == driver.id)
    return list((await db.scalars(query)).all())


async def manager_login(client, username="testmanager"):
    await client.post("/logout")
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


# --- drivers propose, managers approve -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_driver_new_document_waits_for_approval(client, db_session, pair, fleet_manager_user):
    await login(client, "mine")
    form = await client.get(f"/driver-documents/new?driver_id={pair['mine'].id}&document_type=id_card")
    assert (
        form.status_code == 200
        and "Submit for approval" in form.text
        and "takes effect only after approval" in form.text
    )
    sent = await client.post(
        "/driver-documents/new",
        data={
            "driver_id": str(pair["mine"].id),
            "document_type": "id_card",
            "document_number": "A1B2C3",
            "end_date": "2031-01-01",
        },
        follow_redirects=False,
    )
    assert sent.status_code == 303 and sent.headers["location"] == f"/drivers/{pair['mine'].id}#licences"
    # nothing was written to the driver's documents
    assert [e.document_type for e in await entries_for(db_session, pair["mine"])] == ["driving_licence"]
    [change] = await changes_for(db_session, pair["mine"])
    assert (change.status, change.action, change.document_type) == ("pending", "create", "id_card")
    assert change.payload["document_number"] == "A1B2C3" and change.requested_by == pair["me"].id
    # the driver sees it as waiting, and a manager was notified
    notice = await client.get(f"/drivers/{pair['mine'].id}")
    assert "Sent for approval" in notice.text
    # the message is shown once: a reload (or coming back later) no longer shows it
    assert "Sent for approval" not in (await client.get(f"/drivers/{pair['mine'].id}")).text
    tab = await client.get(f"/drivers/{pair['mine'].id}/tab/licences")
    assert "Waiting for approval" in tab.text and "A1B2C3" in tab.text and "Cancel request" in tab.text
    assert "/approve" not in tab.text  # a driver cannot approve
    manager_note = await db_session.scalar(select(Notification).where(Notification.user_id == fleet_manager_user.id))
    assert manager_note is not None and "Mine Driver" in manager_note.message
    # QA #11: the stored message follows the language the manager is looking at now, not the one it was created in
    await client.post("/logout")
    await manager_login(client)
    for lang, title in (("bg", "Промяна в документ за одобрение"), ("en", "Document change to approve")):
        inbox = await client.get("/notifications", params={"tab": "messages", "lang": lang})
        assert title in inbox.text, lang
        other = "Document change to approve" if lang == "bg" else "Промяна в документ за одобрение"
        assert other not in inbox.text, lang
    assert await db_session.scalar(select(Notification).where(Notification.user_id == pair["me"].id)) is None


@pytest.mark.asyncio
async def test_manager_approves_and_the_change_is_applied(client, db_session, pair, fleet_manager_user):
    await login(client, "mine")
    await client.post(
        "/driver-documents/new",
        data={
            "driver_id": str(pair["mine"].id),
            "document_type": "id_card",
            "document_number": "A1B2C3",
            "end_date": "2031-01-01",
        },
    )
    await manager_login(client)
    listing = await client.get("/drivers")
    assert "waiting for approval" in listing.text and 'href="/driver-documents/pending"' in listing.text
    pending = await client.get("/driver-documents/pending")
    assert pending.status_code == 200 and "Mine Driver" in pending.text and "A1B2C3" in pending.text
    assert "Approve" in pending.text and "Reject" in pending.text
    [change] = await changes_for(db_session, pair["mine"])
    done = await client.post(
        f"/driver-documents/changes/{change.id}/approve",
        data={"next": "/driver-documents/pending"},
        follow_redirects=False,
    )
    assert done.status_code == 303 and done.headers["location"] == "/driver-documents/pending"
    await db_session.refresh(change)
    assert change.status == "approved" and change.decided_by == fleet_manager_user.id and change.result_entry_id
    created = next(e for e in await entries_for(db_session, pair["mine"]) if e.document_type == "id_card")
    assert (
        created.id == change.result_entry_id
        and created.document_number == "A1B2C3"
        and created.end_date == date(2031, 1, 1)
    )
    # the driver is told, and approving twice does nothing
    note = await db_session.scalar(select(Notification).where(Notification.user_id == pair["me"].id))
    assert note is not None and "approved" in note.title.lower()
    again = await client.post(f"/driver-documents/changes/{change.id}/approve", follow_redirects=False)
    assert again.status_code == 422 and "already decided" in again.text
    assert len([e for e in await entries_for(db_session, pair["mine"]) if e.document_type == "id_card"]) == 1
    assert "Nothing is waiting" in (await client.get("/driver-documents/pending")).text


@pytest.mark.asyncio
async def test_audit_trail_of_a_change_never_holds_full_numbers(client, db_session, pair, fleet_manager_user):
    await login(client, "mine")
    await client.post(
        "/driver-documents/new",
        data={
            "driver_id": str(pair["mine"].id),
            "document_type": "id_card",
            "document_number": "SECRET-NUMBER-9",
            "end_date": "2031-01-01",
        },
    )
    await manager_login(client)
    [change] = await changes_for(db_session, pair["mine"])
    await client.post(f"/driver-documents/changes/{change.id}/approve")
    entries = list((await db_session.scalars(select(AuditLog))).all())
    assert "SECRET-NUMBER-9" not in json.dumps([e.changes for e in entries])
    mine = [e for e in entries if e.entity_type == "driver_document_change"]
    assert [(e.action, e.user_id) for e in mine] == [
        (AuditAction.CREATE, pair["me"].id),
        (AuditAction.UPDATE, fleet_manager_user.id),
    ]


async def submit_edit(client, entry, number="WRONG-1"):
    return await client.post(
        f"/driver-documents/{entry.id}/edit",
        data={"document_number": number, "categories": "B", "end_date": "2040-01-01"},
    )


@pytest.mark.asyncio
async def test_manager_asks_to_redo_and_the_driver_corrects_and_resubmits(client, db_session, pair, fleet_manager_user):
    await login(client, "mine")
    await submit_edit(client, pair["entry"])
    await manager_login(client)
    [change] = await changes_for(db_session, pair["mine"])
    sent_back = await client.post(
        f"/driver-documents/changes/{change.id}/request-changes", data={"note": "Photo is unreadable, please renew"}
    )
    assert sent_back.status_code in (302, 303)
    await db_session.refresh(change)
    await db_session.refresh(pair["entry"])
    assert change.status == "revision" and change.decision_note == "Photo is unreadable, please renew"
    assert pair["entry"].document_number == "BG1234567"  # nothing applied
    assert "Nothing is waiting" in (await client.get("/driver-documents/pending")).text  # off the manager's list
    await client.post("/logout")

    await login(client, "mine")
    note = await db_session.scalar(select(Notification).where(Notification.user_id == pair["me"].id))
    assert "Changes requested" in note.title and "Please correct and resubmit" in note.message
    tab = await client.get(f"/drivers/{pair['mine'].id}/tab/licences")
    assert "Changes requested" in tab.text and "Photo is unreadable, please renew" in tab.text
    assert f"/driver-documents/changes/{change.id}/resubmit" in tab.text and "Delete request" in tab.text
    # the form is prefilled with what was sent, shows the note, and posts to the same request
    form = await client.get(f"/driver-documents/changes/{change.id}/resubmit")
    assert (
        form.status_code == 200 and 'value="WRONG-1"' in form.text and "Photo is unreadable, please renew" in form.text
    )
    assert f'action="/driver-documents/changes/{change.id}/resubmit"' in form.text
    # an invalid correction keeps the request sent back
    bad = await client.post(
        f"/driver-documents/changes/{change.id}/resubmit",
        data={"document_number": "FIXED-3", "categories": "B", "start_date": "2040-01-01", "end_date": "2030-01-01"},
    )
    assert bad.status_code == 422
    await db_session.refresh(change)
    assert change.status == "revision"
    fixed = await client.post(
        f"/driver-documents/changes/{change.id}/resubmit",
        data={"document_number": "FIXED-3", "categories": "B", "end_date": "2041-01-01"},
        follow_redirects=False,
    )
    assert fixed.status_code == 303 and fixed.headers["location"] == f"/drivers/{pair['mine'].id}#licences"
    await db_session.refresh(change)
    assert (change.status, change.decision_note, change.payload["document_number"]) == ("pending", None, "FIXED-3")
    assert len(await changes_for(db_session, pair["mine"])) == 1  # the same request, not a second one
    tab = await client.get(f"/drivers/{pair['mine'].id}/tab/licences")
    assert "Waiting for approval" in tab.text and "Photo is unreadable" not in tab.text
    # the manager sees it again and can approve
    await manager_login(client)
    assert "FIXED-3" in (await client.get("/driver-documents/pending")).text
    await client.post(f"/driver-documents/changes/{change.id}/approve")
    await db_session.refresh(pair["entry"])
    assert pair["entry"].document_number == "FIXED-3"


@pytest.mark.asyncio
async def test_rejecting_deletes_the_request_and_tells_the_driver(
    client, db_session, pair, fleet_manager_user
):
    await login(client, "mine")
    await submit_edit(client, pair["entry"])
    await manager_login(client)
    [change] = await changes_for(db_session, pair["mine"])
    change_id = change.id
    await client.post(f"/driver-documents/changes/{change_id}/reject")
    assert await db_session.get(DriverDocumentChange, change_id) is None
    await db_session.refresh(pair["entry"])
    assert pair["entry"].document_number == "BG1234567"
    # QA #13: the driver gets a message that it was rejected (and it follows the language they read in)
    note = await db_session.scalar(select(Notification).where(Notification.user_id == pair["me"].id))
    assert note is not None and "rejected" in note.title.lower()
    assert note.title_key == "driver_doc.notify_rejected_title"
    from app.services.notification_text import notification_text

    assert "отхвърлен" in notification_text(note, "bg")[0].lower()
    await client.post("/logout")
    await login(client, "mine")
    inbox = await client.get("/notifications", params={"tab": "messages"})
    assert "rejected" in inbox.text.lower()
    tab = await client.get(f"/drivers/{pair['mine'].id}/tab/licences")
    assert "WRONG-1" not in tab.text and "Waiting for approval" not in tab.text and "Changes requested" not in tab.text
    assert "Waiting for approval and sent back" not in tab.text
    # only the append-only audit log remembers it
    deleted = await db_session.scalar(
        select(AuditLog).where(AuditLog.entity_id == change_id, AuditLog.action == AuditAction.DELETE)
    )
    assert deleted is not None and deleted.user_id == fleet_manager_user.id
    # and the driver may propose again
    assert (await submit_edit(client, pair["entry"], "AGAIN-2")).status_code == 303


@pytest.mark.asyncio
async def test_driver_can_delete_a_request_that_was_sent_back(client, db_session, pair, fleet_manager_user):
    await login(client, "mine")
    await submit_edit(client, pair["entry"])
    await manager_login(client)
    [change] = await changes_for(db_session, pair["mine"])
    change_id = change.id
    await client.post(f"/driver-documents/changes/{change_id}/request-changes", data={"note": "redo"})
    await client.post("/logout")
    await login(client, "mine")
    deleted = await client.post(f"/driver-documents/changes/{change_id}/cancel", follow_redirects=False)
    assert deleted.status_code == 303
    assert await db_session.get(DriverDocumentChange, change_id) is None
    assert "redo" not in (await client.get(f"/drivers/{pair['mine'].id}/tab/licences")).text


@pytest.mark.asyncio
async def test_sent_back_requests_belong_to_their_driver(client, db_session, pair, fleet_manager_user):
    await login(client, "other")
    await client.post(
        "/driver-documents/new",
        data={"driver_id": str(pair["other"].id), "document_type": "psycho_test", "document_number": "P1"},
    )
    await manager_login(client)
    [theirs] = await changes_for(db_session, pair["other"])
    theirs_id = theirs.id
    await client.post(f"/driver-documents/changes/{theirs_id}/request-changes", data={"note": "redo"})
    await client.post("/logout")
    await login(client, "mine")
    forms = await client.get(f"/driver-documents/changes/{theirs_id}/resubmit", follow_redirects=False)
    assert forms.status_code == 302 and forms.headers["location"] == "/"
    await client.post(f"/driver-documents/changes/{theirs_id}/resubmit", data={"document_number": "HACK"})
    await client.post(f"/driver-documents/changes/{theirs_id}/cancel")
    still = await db_session.get(DriverDocumentChange, theirs_id)
    assert still is not None and still.status == "revision" and still.payload["document_number"] == "P1"
    # sending back needs a manager; a driver cannot do it, nor approve it
    for action in ("request-changes", "approve", "reject"):
        response = await client.post(f"/driver-documents/changes/{theirs_id}/{action}", follow_redirects=False)
        assert response.status_code in (302, 303)
    assert (await db_session.get(DriverDocumentChange, theirs_id)).status == "revision"


@pytest.mark.asyncio
async def test_a_new_proposal_replaces_an_old_request_for_changes(client, db_session, pair, fleet_manager_user):
    await login(client, "mine")
    await submit_edit(client, pair["entry"])
    await manager_login(client)
    [old] = await changes_for(db_session, pair["mine"])
    old_id = old.id
    await client.post(f"/driver-documents/changes/{old_id}/request-changes", data={"note": "redo"})
    await client.post("/logout")
    await login(client, "mine")
    assert (await submit_edit(client, pair["entry"], "NEWER-9")).status_code == 303
    assert await db_session.get(DriverDocumentChange, old_id) is None
    [current] = await changes_for(db_session, pair["mine"])
    assert current.status == "pending" and current.payload["document_number"] == "NEWER-9"
    # a request that was sent back can no longer be approved by anyone
    await client.post("/logout")
    await manager_login(client)
    assert (await client.post(f"/driver-documents/changes/{old_id}/approve")).status_code == 422


@pytest.mark.asyncio
async def test_driver_edit_and_renewal_apply_only_after_approval(client, db_session, pair, fleet_manager_user):
    await login(client, "mine")
    entry = pair["entry"]
    await client.get(f"/driver-documents/{entry.id}/edit")
    sent = await client.post(
        f"/driver-documents/{entry.id}/edit",
        data={"document_number": "BG7654321", "categories": "B", "end_date": str(date.today() + timedelta(days=200))},
        follow_redirects=False,
    )
    assert sent.status_code == 303
    await db_session.refresh(entry)
    assert entry.document_number == "BG1234567"
    assert (
        "A change to this document is waiting for approval" in (await client.get(f"/driver-documents/{entry.id}")).text
    )
    # the pending edit blocks a second request for the same document type until it is decided
    second = await client.post(
        f"/driver-documents/{entry.id}/renew",
        data={"document_number": "X1", "categories": "B", "start_date": str(date.today()), "end_date": "2040-01-01"},
    )
    assert second.status_code == 422 and "already waiting for approval" in second.text
    assert len(await changes_for(db_session)) == 1
    await manager_login(client)
    [change] = await changes_for(db_session)
    assert (
        "BG1234567" in (await client.get("/driver-documents/pending")).text
    )  # current value shown next to the proposal
    await client.post(f"/driver-documents/changes/{change.id}/approve")
    await db_session.refresh(entry)
    assert entry.document_number == "BG7654321"
    # a renewal: the old entry stays as history
    await client.post("/logout")
    await login(client, "mine")
    await client.post(
        f"/driver-documents/{entry.id}/renew",
        data={
            "document_number": "BG7654321",
            "categories": "B",
            "start_date": str(date.today()),
            "end_date": "2041-01-01",
        },
    )
    assert len(await entries_for(db_session, pair["mine"])) == 1
    await manager_login(client)
    renewal = (await changes_for(db_session, pair["mine"]))[-1]
    await client.post(f"/driver-documents/changes/{renewal.id}/approve")
    licences = [e for e in await entries_for(db_session, pair["mine"]) if e.document_type == "driving_licence"]
    assert len(licences) == 2 and {e.renewed_from_id for e in licences} == {None, entry.id}


@pytest.mark.asyncio
async def test_invalid_or_empty_submissions_are_not_stored(client, db_session, pair):
    await login(client, "mine")
    no_number = await client.post(
        "/driver-documents/new",
        data={"driver_id": str(pair["mine"].id), "document_type": "driving_licence", "categories": "B"},
    )
    assert no_number.status_code == 422 and "The number is required" in no_number.text
    unchanged = await client.post(
        f"/driver-documents/{pair['entry'].id}/edit",
        data={"document_number": "BG1234567", "categories": "B", "end_date": str(pair["entry"].end_date)},
    )
    assert unchanged.status_code == 422 and "Nothing was changed" in unchanged.text
    assert await changes_for(db_session) == []


@pytest.mark.asyncio
async def test_driver_can_cancel_their_request_but_not_decide_or_cancel_others(
    client, db_session, pair, fleet_manager_user
):
    await login(client, "other")
    await client.post(
        "/driver-documents/new",
        data={"driver_id": str(pair["other"].id), "document_type": "psycho_test", "document_number": "P1"},
    )
    [theirs] = await changes_for(db_session, pair["other"])
    await client.post("/logout")
    await login(client, "mine")
    # no way to approve or reject, and no way to withdraw somebody else's request
    for action in ("approve", "reject"):
        response = await client.post(f"/driver-documents/changes/{theirs.id}/{action}", follow_redirects=False)
        assert response.status_code in (302, 303)
    await client.post(f"/driver-documents/changes/{theirs.id}/cancel")
    await db_session.refresh(theirs)
    assert theirs.status == "pending"
    assert (await get(client, "/driver-documents/pending")).status_code == 302
    # their own can be withdrawn
    await client.post(
        "/driver-documents/new",
        data={"driver_id": str(pair["mine"].id), "document_type": "psycho_test", "document_number": "P2"},
    )
    own = next(c for c in await changes_for(db_session, pair["mine"]))
    own_id = own.id
    assert (await client.post(f"/driver-documents/changes/{own.id}/cancel", follow_redirects=False)).status_code == 303
    assert await db_session.get(DriverDocumentChange, own_id) is None  # deleted, not kept
    # a deleted request can no longer be approved
    await manager_login(client)
    assert (await client.post(f"/driver-documents/changes/{own_id}/approve")).status_code == 422
    assert not [e for e in await entries_for(db_session, pair["mine"]) if e.document_type == "psycho_test"]


@pytest.mark.asyncio
async def test_managers_still_change_documents_directly(client, db_session, pair, fleet_manager_user):
    await manager_login(client)
    updated = await client.post(
        f"/driver-documents/{pair['entry'].id}/edit",
        data={"document_number": "DIRECT-1", "categories": "B", "end_date": str(pair["entry"].end_date)},
        follow_redirects=False,
    )
    assert updated.status_code == 302
    await db_session.refresh(pair["entry"])
    assert pair["entry"].document_number == "DIRECT-1"
    assert await changes_for(db_session) == []
    form = await client.get(f"/driver-documents/{pair['entry'].id}/edit")
    assert "Submit for approval" not in form.text and "takes effect only after approval" not in form.text


@pytest.mark.asyncio
async def test_pending_changes_do_not_affect_alerts_or_other_roles(client, db_session, pair, viewer_user):
    await login(client, "mine")
    await client.post(
        f"/driver-documents/{pair['entry'].id}/renew",
        data={"document_number": "N", "categories": "B", "start_date": str(date.today()), "end_date": "2050-01-01"},
    )
    await client.post("/logout")
    assert (await client.post("/login", data={"username": "testviewer", "password": "testpass123"})).status_code == 302
    tab = await client.get(f"/drivers/{pair['mine'].id}/tab/licences")
    assert "Waiting for approval" not in tab.text  # only the driver and managers see requests
    assert (await get(client, "/driver-documents/pending")).headers["location"] == "/"


@pytest.mark.asyncio
async def test_uploads_need_no_approval(client, db_session, pair, s3):
    await login(client, "mine")
    files = {"file": ("scan.pdf", b"%PDF-1.4 x", "application/pdf")}
    response = await client.post(
        "/documents/upload",
        files=files,
        data={"entity_type": "driver", "entity_id": str(pair["mine"].id), "doc_type": "other"},
    )
    assert response.status_code == 200 and "scan.pdf" in response.text
    assert await changes_for(db_session) == []
    assert "scan.pdf" in (await client.get(f"/documents/gallery/driver/{pair['mine'].id}")).text


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


@pytest.mark.asyncio
async def test_asking_to_redo_needs_a_note(client, db_session, pair, fleet_manager_user):
    """QA #14: the driver must be told what to fix, so a blank (or spaces-only) note is refused."""
    await login(client, "mine")
    await submit_edit(client, pair["entry"])
    await manager_login(client)
    [change] = await changes_for(db_session, pair["mine"])
    page = await client.get("/driver-documents/pending")
    assert 'name="note"' in page.text and 'required' in page.text.split('name="note"')[1].split(">")[0]
    for blank in ("", "   "):
        refused = await client.post(f"/driver-documents/changes/{change.id}/request-changes", data={"note": blank})
        assert refused.status_code == 422 and "what the driver should fix" in refused.text
        await db_session.refresh(change)
        assert change.status == "pending"  # nothing was sent back
    assert await db_session.scalar(select(Notification).where(Notification.user_id == pair["me"].id)) is None
    ok = await client.post(
        f"/driver-documents/changes/{change.id}/request-changes", data={"note": "Photo is unreadable"}, follow_redirects=False
    )
    assert ok.status_code == 303
    await db_session.refresh(change)
    assert change.status == "revision" and change.decision_note == "Photo is unreadable"
