"""REST API for compliance documents: CRUD, renewal, RBAC, money serialization."""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.models.user import User, UserRole
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService
from app.utils.security import create_access_token, password_fingerprint, hash_password
from tests.conftest import auth_header

BASE = "/api/v1/compliance-documents"


async def make_vehicle(db, kind="tractor_unit"):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else "CA" + uuid4().hex[:6].upper(),
            brand="DAF",
            model="XF",
            department="Test department",
        )
    )


def body(vehicle_id, **kw):
    today = date.today()
    return {
        "vehicle_id": str(vehicle_id),
        "document_type": "civil_liability",
        "start_date": str(today - timedelta(days=10)),
        "end_date": str(today + timedelta(days=355)),
        **kw,
    }


async def token_for(db, role: UserRole) -> str:
    name = "ro" + uuid4().hex[:8]
    user = User(
        id=uuid4(),
        email=f"{name}@test.com",
        username=name,
        full_name="Read only",
        hashed_password=hash_password("testpass123"),
        role=role,
        is_active=True,
        language="en",
    )
    db.add(user)
    await db.flush()
    return create_access_token(user.id, user.role.value, password_fingerprint(user.hashed_password))


@pytest.mark.asyncio
async def test_crud_and_renew(client, db_session, manager_token):
    vehicle = await make_vehicle(db_session)
    headers = auth_header(manager_token)
    response = await client.post(BASE, json=body(vehicle.id, amount="250.50", policy_number="ГО-1"), headers=headers)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["amount"] == "250.50"
    assert created["status"] == "valid" and created["is_current"] and created["applicable"]
    entry_id = created["id"]

    response = await client.get(f"{BASE}/{entry_id}", headers=headers)
    assert response.status_code == 200 and response.json()["policy_number"] == "ГО-1"

    response = await client.patch(f"{BASE}/{entry_id}", json={"amount": None, "notes": "n"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["amount"] is None and response.json()["policy_number"] == "ГО-1"

    renewal = {
        "start_date": str(date.today() + timedelta(days=300)),
        "end_date": str(date.today() + timedelta(days=700)),
    }
    response = await client.post(f"{BASE}/{entry_id}/renew", json=renewal, headers=headers)
    assert response.status_code == 201, response.text
    renewed = response.json()
    assert renewed["renewed_from_id"] == entry_id and renewed["document_type"] == "civil_liability"

    listing = (await client.get(BASE, params={"vehicle_id": str(vehicle.id)}, headers=headers)).json()
    assert [i["id"] for i in listing["items"]] == [renewed["id"]]
    listing = (
        await client.get(BASE, params={"vehicle_id": str(vehicle.id), "include_history": True}, headers=headers)
    ).json()
    assert listing["total"] == 2

    response = await client.delete(f"{BASE}/{renewed['id']}", headers=headers)
    assert response.status_code == 204
    assert (await client.get(f"{BASE}/{renewed['id']}", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_types_endpoint(client, db_session, manager_token):
    tractor = await make_vehicle(db_session)
    van = await make_vehicle(db_session, "van")
    headers = {**auth_header(manager_token), "Accept-Language": "bg"}
    codes = [
        o["code"]
        for o in (await client.get(f"{BASE}/types", params={"vehicle_id": str(tractor.id)}, headers=headers)).json()
    ]
    assert codes == ["civil_liability", "casco", "technical_inspection", "tachograph"]
    options = (await client.get(f"{BASE}/types", params={"vehicle_id": str(van.id)}, headers=headers)).json()
    assert [o["code"] for o in options] == ["civil_liability", "casco", "technical_inspection"]
    assert options[1]["label"] == "Каско"
    assert (await client.get(f"{BASE}/types", params={"vehicle_id": str(uuid4())}, headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_validation_errors(client, db_session, manager_token):
    van = await make_vehicle(db_session, "van")
    headers = auth_header(manager_token)
    response = await client.post(BASE, json=body(van.id, document_type="tachograph"), headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"] == "This document type does not apply to the selected vehicle"
    assert (await client.post(BASE, json=body(van.id, amount="12.345"), headers=headers)).status_code == 422
    assert (await client.post(BASE, json=body(van.id, document_type="leasing"), headers=headers)).status_code == 422
    late = body(van.id, start_date=str(date.today()), end_date=str(date.today() - timedelta(days=1)))
    assert (await client.post(BASE, json=late, headers=headers)).status_code == 422
    assert (await client.post(BASE, json=body(uuid4()), headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_driver_role_has_no_access_to_vehicle_documents(client, db_session, manager_token):
    """Drivers only reach their own profile and assigned vehicles (see tests/test_web/test_driver_access.py)."""
    vehicle = await make_vehicle(db_session)
    created = (await client.post(BASE, json=body(vehicle.id), headers=auth_header(manager_token))).json()
    headers = auth_header(await token_for(db_session, UserRole.DRIVER))
    assert (await client.get(BASE, headers=headers)).status_code == 403
    assert (await client.get(f"{BASE}/{created['id']}", headers=headers)).status_code == 403
    assert (await client.post(BASE, json=body(vehicle.id), headers=headers)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.VIEWER])
async def test_read_only_roles(client, db_session, manager_token, role):
    vehicle = await make_vehicle(db_session)
    created = (await client.post(BASE, json=body(vehicle.id), headers=auth_header(manager_token))).json()
    headers = auth_header(await token_for(db_session, role))
    assert (await client.get(BASE, headers=headers)).status_code == 200
    assert (await client.get(f"{BASE}/{created['id']}", headers=headers)).status_code == 200
    assert (await client.post(BASE, json=body(vehicle.id), headers=headers)).status_code == 403
    assert (await client.patch(f"{BASE}/{created['id']}", json={"notes": "x"}, headers=headers)).status_code == 403
    renewal = {"start_date": "2030-01-01", "end_date": "2030-12-31"}
    assert (await client.post(f"{BASE}/{created['id']}/renew", json=renewal, headers=headers)).status_code == 403
    assert (await client.delete(f"{BASE}/{created['id']}", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_contracts_api_removed(client, manager_token):
    assert (await client.get("/api/v1/contracts", headers=auth_header(manager_token))).status_code == 404


@pytest.mark.asyncio
async def test_document_upload_requires_manager(client, db_session, viewer_token, manager_token):
    vehicle = await make_vehicle(db_session)
    created = (await client.post(BASE, json=body(vehicle.id), headers=auth_header(manager_token))).json()
    files = {"file": ("p.pdf", b"%PDF-1.4", "application/pdf")}
    data = {"entity_type": "compliance_document", "entity_id": created["id"], "doc_type": "insurance"}
    response = await client.post("/api/v1/documents/upload", files=files, data=data, headers=auth_header(viewer_token))
    assert response.status_code == 403
    data["entity_id"] = str(uuid4())
    response = await client.post("/api/v1/documents/upload", files=files, data=data, headers=auth_header(manager_token))
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_delete_scope_all(client, db_session, manager_token):
    vehicle = await make_vehicle(db_session)
    headers = auth_header(manager_token)
    first = (await client.post(BASE, json=body(vehicle.id), headers=headers)).json()
    renewal = {
        "start_date": str(date.today() + timedelta(days=300)),
        "end_date": str(date.today() + timedelta(days=700)),
    }
    second = (await client.post(f"{BASE}/{first['id']}/renew", json=renewal, headers=headers)).json()
    other = (await client.post(BASE, json=body(vehicle.id, document_type="casco"), headers=headers)).json()

    assert (
        await client.delete(f"{BASE}/{second['id']}", params={"scope": "bogus"}, headers=headers)
    ).status_code == 422
    assert (await client.delete(f"{BASE}/{second['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"{BASE}/{first['id']}", headers=headers)).json()["is_current"] is True
    second = (await client.post(f"{BASE}/{first['id']}/renew", json=renewal, headers=headers)).json()
    assert (await client.delete(f"{BASE}/{second['id']}", params={"scope": "all"}, headers=headers)).status_code == 204
    assert (await client.get(f"{BASE}/{first['id']}", headers=headers)).status_code == 404
    assert (await client.get(f"{BASE}/{other['id']}", headers=headers)).status_code == 200
