"""REST API for driver documents: CRUD, renewal, RBAC, masking of personal data, scans access."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.document import Document, DocumentType, EntityType
from app.models.driver import Driver
from app.models.user import User, UserRole
from app.utils.security import create_access_token, hash_password
from tests.conftest import auth_header

BASE = "/api/v1/driver-documents"
TODAY = datetime.now(UTC).date()


async def make_driver(db, name="Иван Петров", user_id=None):
    driver = Driver(id=uuid4(), full_name=name, user_id=user_id)
    db.add(driver)
    await db.flush()
    return driver


async def make_user(db, role: UserRole):
    name = role.value + uuid4().hex[:8]
    user = User(
        id=uuid4(),
        email=f"{name}@test.com",
        username=name,
        full_name=name,
        hashed_password=hash_password("testpass123"),
        role=role,
        is_active=True,
        language="en",
    )
    db.add(user)
    await db.flush()
    return user, create_access_token(user.id, user.role.value)


def body(driver_id, **kw):
    return {
        "driver_id": str(driver_id),
        "document_type": "driving_licence",
        "document_number": "987654321",
        "categories": "b, c",
        "start_date": str(TODAY - timedelta(days=100)),
        "end_date": str(TODAY + timedelta(days=20)),
        **kw,
    }


@pytest.mark.asyncio
async def test_crud_renew_and_types(client, db_session, manager_token):
    driver = await make_driver(db_session)
    h = auth_header(manager_token)

    created = await client.post(BASE, json=body(driver.id), headers=h)
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["categories"] == "B, C" and item["document_number"] == "987654321"
    assert item["status"] == "expiring_soon" and item["days_left"] == 20 and item["is_current"] is True

    # PATCH: omitted keeps, explicit null clears (no expiry)
    patched = await client.patch(f"{BASE}/{item['id']}", json={"end_date": None}, headers=h)
    assert patched.status_code == 200
    assert patched.json()["status"] == "no_expiry" and patched.json()["days_left"] is None
    assert patched.json()["document_number"] == "987654321"

    renewed = await client.post(
        f"{BASE}/{item['id']}/renew",
        json={"start_date": str(TODAY), "end_date": str(TODAY + timedelta(days=3650))},
        headers=h,
    )
    assert renewed.status_code == 201
    assert renewed.json()["renewed_from_id"] == item["id"] and renewed.json()["document_number"] == "987654321"

    current = await client.get(BASE, params={"driver_id": str(driver.id)}, headers=h)
    assert [i["id"] for i in current.json()["items"]] == [renewed.json()["id"]]
    history = await client.get(BASE, params={"driver_id": str(driver.id), "include_history": "true"}, headers=h)
    assert history.json()["total"] == 2
    assert (await client.get(BASE, params={"status": "valid"}, headers=h)).json()["total"] == 1

    deleted = await client.delete(f"{BASE}/{renewed.json()['id']}", params={"scope": "all"}, headers=h)
    assert deleted.status_code == 204
    assert (await client.get(f"{BASE}/{item['id']}", headers=h)).status_code == 404

    types = await client.get(BASE + "/types", headers=h)
    codes = [t["code"] for t in types.json()]
    assert codes[:5] == ["id_card", "driving_licence", "tachograph_card", "psycho_test", "professional_competence"]
    licence = next(t for t in types.json() if t["code"] == "driving_licence")
    assert licence["number_required"] and licence["has_categories"] and licence["label"] == "Driving licence"


@pytest.mark.asyncio
async def test_validation_errors(client, db_session, manager_token):
    driver = await make_driver(db_session)
    h = auth_header(manager_token)
    missing_number = await client.post(BASE, json=body(driver.id, document_number=None), headers=h)
    assert missing_number.status_code == 400
    assert missing_number.json()["detail"] == "The number is required for this document type"
    bad_categories = await client.post(
        BASE, json=body(driver.id, document_type="id_card", categories="B"), headers=h
    )
    assert bad_categories.json()["detail"] == "Categories apply only to a driving licence"
    assert (await client.post(BASE, json=body(driver.id, document_type="passport"), headers=h)).status_code == 422
    assert (
        await client.post(BASE, json=body(driver.id, end_date=str(TODAY - timedelta(days=500))), headers=h)
    ).status_code == 422
    assert (await client.post(BASE, json=body(uuid4()), headers=h)).status_code == 404
    assert (await client.get(f"{BASE}/{uuid4()}", headers=h)).status_code == 404
    assert (await client.get(BASE, params={"status": "bogus"}, headers=h)).status_code == 422
    assert (await client.get(BASE, params={"document_type": "bogus"}, headers=h)).status_code == 400


@pytest.mark.asyncio
async def test_writes_need_manager_and_viewer_sees_masked_numbers(client, db_session, manager_token, viewer_token):
    driver = await make_driver(db_session)
    created = await client.post(BASE, json=body(driver.id), headers=auth_header(manager_token))
    entry_id = created.json()["id"]
    v = auth_header(viewer_token)

    assert (await client.post(BASE, json=body(driver.id), headers=v)).status_code == 403
    assert (await client.patch(f"{BASE}/{entry_id}", json={"notes": "x"}, headers=v)).status_code == 403
    assert (await client.post(f"{BASE}/{entry_id}/renew", json={}, headers=v)).status_code == 403
    assert (await client.delete(f"{BASE}/{entry_id}", headers=v)).status_code == 403
    assert (await client.get(BASE)).status_code == 401

    one = await client.get(f"{BASE}/{entry_id}", headers=v)
    assert one.status_code == 200 and one.json()["document_number"] == "•••• 4321"
    listed = await client.get(BASE, headers=v)
    assert listed.json()["items"][0]["document_number"] == "•••• 4321"
    assert listed.json()["items"][0]["status"] == "expiring_soon"  # status and dates stay visible


@pytest.mark.asyncio
async def test_driver_role_sees_only_own_documents_in_full(client, db_session, manager_token):
    me_user, me_token = await make_user(db_session, UserRole.DRIVER)
    mine = await make_driver(db_session, "Mine", user_id=me_user.id)
    theirs = await make_driver(db_session, "Theirs")
    m = auth_header(manager_token)
    my_entry = (await client.post(BASE, json=body(mine.id), headers=m)).json()
    their_entry = (await client.post(BASE, json=body(theirs.id, document_number="555555555"), headers=m)).json()
    h = auth_header(me_token)

    own = await client.get(f"{BASE}/{my_entry['id']}", headers=h)
    assert own.status_code == 200 and own.json()["document_number"] == "987654321"
    assert (await client.get(f"{BASE}/{their_entry['id']}", headers=h)).status_code == 403
    listing = await client.get(BASE, headers=h)
    assert [i["id"] for i in listing.json()["items"]] == [my_entry["id"]]
    assert (await client.get(BASE, params={"driver_id": str(theirs.id)}, headers=h)).status_code == 403
    assert (await client.post(BASE, json=body(mine.id), headers=h)).status_code == 403

    # a driver-role user without a linked driver record has nothing to read
    stray_user, stray_token = await make_user(db_session, UserRole.DRIVER)
    assert (await client.get(BASE, headers=auth_header(stray_token))).status_code == 403


@pytest.mark.asyncio
async def test_scans_are_limited_to_managers_and_the_driver_themself(client, db_session, manager_token, viewer_token):
    me_user, me_token = await make_user(db_session, UserRole.DRIVER)
    mine = await make_driver(db_session, "Mine", user_id=me_user.id)
    theirs = await make_driver(db_session, "Theirs")
    m = auth_header(manager_token)
    my_entry = (await client.post(BASE, json=body(mine.id), headers=m)).json()
    their_entry = (await client.post(BASE, json=body(theirs.id), headers=m)).json()
    docs = [
        Document(
            entity_type=kind,
            entity_id=entity_id,
            type=DocumentType.SCAN,
            filename="s.pdf",
            s3_key="k/" + uuid4().hex,
            mime_type="application/pdf",
            size_bytes=1,
        )
        for kind, entity_id in [
            (EntityType.DRIVER_DOCUMENT, uuid_of(my_entry)),
            (EntityType.DRIVER_DOCUMENT, uuid_of(their_entry)),
            (EntityType.DRIVER, theirs.id),
        ]
    ]
    db_session.add_all(docs)
    await db_session.flush()

    def listing(kind, entity_id):
        return f"/api/v1/documents/entity/{kind}/{entity_id}"

    assert (await client.get(listing("driver_document", their_entry["id"]), headers=m)).status_code == 200
    assert (await client.get(listing("driver_document", my_entry["id"]), headers=auth_header(me_token))).status_code == 200
    assert (await client.get(listing("driver_document", their_entry["id"]), headers=auth_header(me_token))).status_code == 403
    assert (await client.get(listing("driver", theirs.id), headers=auth_header(me_token))).status_code == 403
    assert (await client.get(listing("driver_document", my_entry["id"]), headers=auth_header(viewer_token))).status_code == 403
    assert (await client.get(listing("driver", mine.id), headers=auth_header(viewer_token))).status_code == 403
    # non-driver entities are unaffected
    assert (await client.get(listing("vehicle", uuid4()), headers=auth_header(viewer_token))).status_code == 200


def uuid_of(item):
    from uuid import UUID

    return UUID(item["id"])


@pytest.mark.asyncio
async def test_drivers_api_has_no_removed_fields_and_reports_alerts(client, db_session, manager_token):
    h = auth_header(manager_token)
    created = await client.post(
        "/api/v1/drivers",
        json={"full_name": "Тест Шофьор", "employee_id": "E-1", "license_number": "L-1", "phone": "+359888"},
        headers=h,
    )
    assert created.status_code == 201
    driver = created.json()
    for removed in ("employee_id", "license_number", "license_category", "license_expiry", "medical_expiry"):
        assert removed not in driver
    await client.post(BASE, json=body(driver["id"], end_date=str(TODAY - timedelta(days=3))), headers=h)

    one = (await client.get(f"/api/v1/drivers/{driver['id']}", headers=h)).json()
    assert one["document_alerts"] == {"expired": 1, "expiring_soon": 0}
    listed = (await client.get("/api/v1/drivers", params={"doc_status": "expired"}, headers=h)).json()
    assert [d["id"] for d in listed["items"]] == [driver["id"]]
    assert listed["items"][0]["document_alerts"]["expired"] == 1
    assert (await client.get("/api/v1/drivers", params={"doc_status": "expiring_soon"}, headers=h)).json()["total"] == 0
