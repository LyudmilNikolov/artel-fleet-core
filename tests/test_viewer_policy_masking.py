"""QA #4: a viewer sees vehicle policy / document numbers masked, like driver document numbers."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.expense import ExpenseCategory
from app.schemas.compliance import ComplianceCreate
from app.schemas.expense import ExpenseCreate
from app.schemas.vehicle import VehicleCreate
from app.services.compliance_service import ComplianceService
from app.services.expense_service import ExpenseService
from app.services.vehicle_service import VehicleService
from tests.conftest import auth_header

POLICY = "QA-POL-0002"
MASKED = "•••• 0002"
API = "/api/v1/compliance-documents"


async def _vehicle_with_policy(db):
    vehicle = await VehicleService(db).create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CA" + uuid4().hex[:6].upper(),
            brand="DAF",
            model="XF",
            department="Test department",
        )
    )
    today = date.today()
    entry = await ComplianceService(db).create(
        ComplianceCreate(
            vehicle_id=vehicle.id,
            document_type="civil_liability",
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=300),
            policy_number=POLICY,
        ),
        None,
    )
    return vehicle, entry


async def _login(client, username):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


@pytest.mark.asyncio
async def test_viewer_sees_masked_policy_numbers_on_every_web_page(client, db_session, viewer_user):
    vehicle, entry = await _vehicle_with_policy(db_session)
    await _login(client, "testviewer")
    for path in (f"/compliance?q={vehicle.license_plate}", f"/compliance/{entry.id}", f"/vehicles/{vehicle.id}/tab/compliance"):
        page = await client.get(path)
        assert page.status_code == 200, path
        assert POLICY not in page.text, path
        assert MASKED in page.text, path


@pytest.mark.asyncio
async def test_managers_still_see_full_policy_numbers(client, db_session, fleet_manager_user):
    vehicle, entry = await _vehicle_with_policy(db_session)
    await _login(client, "testmanager")
    for path in (f"/compliance?q={vehicle.license_plate}", f"/compliance/{entry.id}", f"/vehicles/{vehicle.id}/tab/compliance"):
        page = await client.get(path)
        assert POLICY in page.text and MASKED not in page.text, path


@pytest.mark.asyncio
async def test_api_masks_policy_numbers_for_viewers_only(client, db_session, viewer_token, manager_token):
    vehicle, entry = await _vehicle_with_policy(db_session)
    viewer, manager = auth_header(viewer_token), auth_header(manager_token)
    one = await client.get(f"{API}/{entry.id}", headers=viewer)
    assert one.json()["policy_number"] == MASKED
    listed = await client.get(API, params={"vehicle_id": str(vehicle.id)}, headers=viewer)
    assert [i["policy_number"] for i in listed.json()["items"]] == [MASKED]
    assert (await client.get(f"{API}/{entry.id}", headers=manager)).json()["policy_number"] == POLICY
    listed = await client.get(API, params={"vehicle_id": str(vehicle.id)}, headers=manager)
    assert [i["policy_number"] for i in listed.json()["items"]] == [POLICY]


@pytest.mark.asyncio
async def test_expense_description_copy_of_the_policy_number_is_masked_for_viewers(
    client, db_session, viewer_user, viewer_token, manager_token, fleet_manager_user
):
    """The linked expense copies the policy number into its description; that route must not leak it."""
    vehicle, _entry = await _vehicle_with_policy(db_session)
    today = date.today()
    expense = await ExpenseService(db_session).create(
        ExpenseCreate(
            vehicle_id=vehicle.id,
            category=ExpenseCategory.CASCO,
            amount=Decimal("100.00"),
            date=today,
            valid_from=today,
            valid_until=today + timedelta(days=364),
            policy_number="CASCO-7788",
            description="Policy CASCO-7788 paid",
        ),
        fleet_manager_user.id,
    )
    await db_session.flush()
    mine = await client.get(f"/api/v1/expenses/{expense.id}", headers=auth_header(viewer_token))
    assert "CASCO-7788" not in mine.json()["description"] and "•••• 7788" in mine.json()["description"]
    manager = await client.get(f"/api/v1/expenses/{expense.id}", headers=auth_header(manager_token))
    assert manager.json()["description"] == "Policy CASCO-7788 paid"
    client.headers.pop("Authorization", None)
    await _login(client, "testviewer")
    page = await client.get(f"/expenses/{expense.id}")
    assert "CASCO-7788" not in page.text and "•••• 7788" in page.text
