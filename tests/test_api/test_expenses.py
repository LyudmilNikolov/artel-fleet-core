"""EUR and exact cents at the actual HTTP boundary."""

import pytest

from tests.conftest import auth_header
from tests.test_api.test_vehicles import VEHICLE_DATA


@pytest.mark.asyncio
async def test_eur_create_update_and_report(client, manager_token):
    headers = auth_header(manager_token)
    response = await client.post("/api/v1/vehicles", json=VEHICLE_DATA, headers=headers)
    assert response.status_code == 201
    vehicle_id = response.json()["id"]
    data = dict(vehicle_id=vehicle_id, category="fuel", amount="0.10", date="2026-01-01")
    first = await client.post("/api/v1/expenses", json=data, headers=headers)
    assert first.status_code == 201
    assert first.json()["currency"] == "EUR"
    assert first.json()["amount"] == "0.10"
    second = await client.post("/api/v1/expenses", json={**data, "amount": "0.20"}, headers=headers)
    assert second.status_code == 201
    for currency in ["KZT", "RUB", "USD", "TRY", "eur", "GBP", None]:
        invalid = await client.post("/api/v1/expenses", json={**data, "currency": currency}, headers=headers)
        assert invalid.status_code == 422
        invalid = await client.patch(
            f"/api/v1/expenses/{first.json()['id']}", json={"currency": currency}, headers=headers
        )
        assert invalid.status_code == 422
    for amount in ["0.001", "10000000000.00"]:
        invalid = await client.post("/api/v1/expenses", json={**data, "amount": amount}, headers=headers)
        assert invalid.status_code == 422
    report = await client.get("/api/v1/reports/expense-analysis", headers=headers)
    assert report.status_code == 200
    assert report.json()["grand_total"] == "0.30"


async def _vehicle(db, kind="tractor_unit", mileage=1000):
    from uuid import uuid4

    from app.schemas.vehicle import VehicleCreate
    from app.services.vehicle_service import VehicleService

    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate="CA" + uuid4().hex[:6].upper(),
            brand="DAF",
            model="XF",
            department="Test department",
            current_mileage=mileage,
        )
    )


@pytest.mark.asyncio
async def test_expense_creates_linked_document_and_syncs(client, db_session, manager_token):
    vehicle = await _vehicle(db_session)
    headers = auth_header(manager_token)
    created = await client.post(
        "/api/v1/expenses",
        json={
            "vehicle_id": str(vehicle.id),
            "category": "tachograph",
            "amount": "75.00",
            "date": "2026-03-01",
            "vendor": "Тахосервиз",
            "valid_from": "2026-03-01",
            "valid_until": "2028-02-29",
            "policy_number": "T-7",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    document_id = body["compliance_document_id"]
    assert document_id
    document = (await client.get(f"/api/v1/compliance-documents/{document_id}", headers=headers)).json()
    assert document["document_type"] == "tachograph" and document["amount"] == "75.00"
    assert document["provider"] == "Тахосервиз" and document["expense_id"] == body["id"]

    patched = await client.patch(f"/api/v1/expenses/{body['id']}", json={"amount": "90.00"}, headers=headers)
    assert patched.status_code == 200
    document = (await client.get(f"/api/v1/compliance-documents/{document_id}", headers=headers)).json()
    assert document["amount"] == "90.00"
    locked = await client.patch(f"/api/v1/expenses/{body['id']}", json={"category": "fuel"}, headers=headers)
    assert locked.status_code == 400

    assert (await client.delete(f"/api/v1/expenses/{body['id']}", headers=headers)).status_code == 204
    document = (await client.get(f"/api/v1/compliance-documents/{document_id}", headers=headers)).json()
    assert document["amount"] is None and document["expense_id"] is None


@pytest.mark.asyncio
async def test_document_amount_creates_expense_through_api(client, db_session, manager_token):
    vehicle = await _vehicle(db_session)
    headers = auth_header(manager_token)
    document = (
        await client.post(
            "/api/v1/compliance-documents",
            json={
                "vehicle_id": str(vehicle.id),
                "document_type": "casco",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "amount": "640.00",
                "provider": "ДЗИ",
            },
            headers=headers,
        )
    ).json()
    expense = (await client.get(f"/api/v1/expenses/{document['expense_id']}", headers=headers)).json()
    assert expense["category"] == "casco" and expense["amount"] == "640.00" and expense["vendor"] == "ДЗИ"
    assert expense["compliance_document_id"] == document["id"]
    assert (await client.delete(f"/api/v1/compliance-documents/{document['id']}", headers=headers)).status_code == 204
    kept = (await client.get(f"/api/v1/expenses/{expense['id']}", headers=headers)).json()
    assert kept["amount"] == "640.00" and kept["compliance_document_id"] is None


@pytest.mark.asyncio
async def test_expense_validity_errors(client, db_session, manager_token):
    van = await _vehicle(db_session, "van")
    headers = auth_header(manager_token)
    base = {"vehicle_id": str(van.id), "category": "tachograph", "amount": "10.00", "date": "2026-03-01"}
    dates = {"valid_from": "2026-03-01", "valid_until": "2027-03-01"}
    assert (await client.post("/api/v1/expenses", json={**base, **dates}, headers=headers)).status_code == 400
    assert (
        await client.post("/api/v1/expenses", json={**base, "category": "fuel", **dates}, headers=headers)
    ).status_code == 400
    assert (
        await client.post("/api/v1/expenses", json={**base, "valid_from": "2026-03-01"}, headers=headers)
    ).status_code == 400
    assert (
        await client.post("/api/v1/expenses", json={**base, "category": "casco"}, headers=headers)
    ).status_code == 201


@pytest.mark.asyncio
async def test_expense_cannot_lower_mileage_even_with_an_overwrite_flag(client, db_session, manager_token):
    vehicle = await _vehicle(db_session)
    headers = auth_header(manager_token)
    data = {"vehicle_id": str(vehicle.id), "category": "fuel", "amount": "50.00", "date": "2026-03-01"}
    assert (
        await client.post("/api/v1/expenses", json={**data, "mileage_at_refuel": 1500}, headers=headers)
    ).status_code == 201
    for extra in ({}, {"overwrite_mileage": True}):
        lower = await client.post("/api/v1/expenses", json={**data, "mileage_at_refuel": 1200, **extra}, headers=headers)
        detail = lower.json()["detail"]
        assert lower.status_code == 400 and "1200" in detail and "1500" in detail and "maintenance" in detail.lower()
    await db_session.refresh(vehicle)
    assert vehicle.current_mileage == 1500


@pytest.mark.asyncio
async def test_retired_and_trailer_categories_rejected(client, db_session, manager_token):
    from uuid import uuid4

    from app.schemas.vehicle import VehicleCreate
    from app.services.vehicle_service import VehicleService

    trailer = await VehicleService(db_session).create(
        VehicleCreate(
            vehicle_type="curtain_side_semi_trailer",
            license_plate="CA" + uuid4().hex[:6].upper(),
            brand="Krone",
            model="Profi",
            department="Test department",
        )
    )
    headers = auth_header(manager_token)
    base = {"vehicle_id": str(trailer.id), "amount": "10.00", "date": "2026-03-01"}
    for category in ("insurance", "fuel", "toll"):
        response = await client.post("/api/v1/expenses", json={**base, "category": category}, headers=headers)
        assert response.status_code == 400, category
    assert (
        await client.post("/api/v1/expenses", json={**base, "category": "service"}, headers=headers)
    ).status_code == 201
