"""QA #8: rejected forms say what is wrong on the field instead of one generic 'Invalid input' banner."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.expense import ExpenseCreate
from app.schemas.maintenance import MaintenanceCreate
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService
from app.web.form_errors import field_errors_from

VEHICLE = {
    "vehicle_type": "van",
    "license_plate": "CB8595CB",
    "brand": "Renault",
    "model": "Premium",
    "department": "Test department",
    "year": "2011",
    "fuel_type": "diesel",
    "transmission": "automatic",
}


def _errors(schema, lang="en", **values) -> dict:
    with pytest.raises(ValidationError) as exc:
        schema(**values)
    return field_errors_from(exc.value, lang)


def test_vin_length_message_names_the_rule():
    assert _errors(VehicleCreate, **{**VEHICLE, "vin": "WVWZZZ3CZWE12345"}) == {
        "vin": "VIN must be exactly 17 characters"
    }
    assert _errors(VehicleCreate, "bg", **{**VEHICLE, "vin": "WVWZZZ3CZWE12345"})["vin"] == (
        "VIN трябва да е точно 17 знака"
    )


def test_money_and_number_limits_are_explained_per_field():
    vehicle = str(uuid4())
    cost = _errors(
        MaintenanceCreate, vehicle_id=vehicle, type="repair", title="x", cost="1200.999", scheduled_date="2026-01-01"
    )
    assert cost == {"cost": "Enter an amount with up to 2 decimals, e.g. 80.50"}
    amount = _errors(ExpenseCreate, vehicle_id=vehicle, category="fuel", amount="99999999999", date="2026-01-01")
    assert amount == {"amount": "At most 10 digits before the decimal point"}
    negative = _errors(ExpenseCreate, vehicle_id=vehicle, category="fuel", amount="-5", date="2026-01-01")
    assert negative == {"amount": "Cannot be negative"}
    year = _errors(VehicleCreate, **{**VEHICLE, "year": "3000"})
    assert "year" in year and "2100" in year["year"]
    date = _errors(ExpenseCreate, vehicle_id=vehicle, category="fuel", amount="5", date="not-a-date")
    assert date == {"date": "Enter a valid date"}
    missing = _errors(ExpenseCreate, "bg", vehicle_id=vehicle, category="fuel", date="2026-01-01")
    assert missing == {"amount": "Полето е задължително"}


async def _login(client, username="testmanager"):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


@pytest.mark.asyncio
async def test_vehicle_form_marks_the_vin_field(client, db_session, fleet_manager_user):
    await _login(client)
    response = await client.post("/vehicles/new", data={**VEHICLE, "vin": "WVWZZZ3CZWE12345"})
    assert response.status_code == 422
    assert "VIN must be exactly 17 characters" in response.text
    assert "Invalid input" not in response.text  # no generic banner
    assert "Please correct the highlighted fields." in response.text
    bg = await client.post("/vehicles/new?lang=bg", data={**VEHICLE, "vin": "WVWZZZ3CZWE12345"})
    assert "VIN трябва да е точно 17 знака" in bg.text and "Невалидни входни данни" not in bg.text


@pytest.mark.asyncio
async def test_maintenance_form_marks_the_cost_field(client, db_session, fleet_manager_user):
    vehicle = await VehicleService(db_session).create(VehicleCreate(**{**VEHICLE, "license_plate": "CA7777AA"}))
    await _login(client)
    data = {"vehicle_id": str(vehicle.id), "type": "repair", "title": "Brakes", "status": "scheduled",
            "scheduled_date": "2026-02-01", "cost": "1200.999"}
    response = await client.post("/maintenance/new", data=data)
    assert response.status_code == 400
    assert "Enter an amount with up to 2 decimals, e.g. 80.50" in response.text
    assert 'aria-invalid="true"' in response.text and "Invalid input" not in response.text
    assert 'value="1200.999"' in response.text  # the entry stays editable


@pytest.mark.asyncio
async def test_expense_form_marks_the_amount_field(client, db_session, fleet_manager_user):
    vehicle = await VehicleService(db_session).create(VehicleCreate(**{**VEHICLE, "license_plate": "CA8888AA"}))
    await _login(client)
    data = {"vehicle_id": str(vehicle.id), "category": "fuel", "amount": "99999999999", "date": "2026-03-01",
            "fuel_liters": "40"}
    response = await client.post("/expenses/new", data=data)
    assert response.status_code == 400
    assert "At most 10 digits before the decimal point" in response.text
    assert 'aria-invalid="true"' in response.text and "Invalid input" not in response.text
