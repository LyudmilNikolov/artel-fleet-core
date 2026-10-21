"""Web forms validate currency even though the control is fixed to EUR."""

import pytest


@pytest.mark.asyncio
async def test_web_expense_currency_rejected(client, admin_user, db_session):
    # Commit only the fixture savepoint; the outer test transaction still rolls back.
    await db_session.commit()
    login = await client.post("/login", data={"username": admin_user.username, "password": "testpass123"})
    assert login.status_code == 302
    for currency in ["KZT", "RUB", "USD", "TRY", "eur", "GBP", ""]:
        response = await client.post(
            "/expenses/new",
            data={
                "vehicle_id": "00000000-0000-0000-0000-000000000001",
                "category": "fuel",
                "amount": "0.10",
                "date": "2026-01-01",
                "currency": currency,
            },
        )
        assert response.status_code == 400
        assert "ValidationError" not in response.text
        assert "EUR" in response.text


# ---- picker, mileage popup, document sync ---------------------------------------------------------

from datetime import date  # noqa: E402
from uuid import uuid4  # noqa: E402

from sqlalchemy import func, select  # noqa: E402

from app.models.compliance import ComplianceDocument  # noqa: E402
from app.models.expense import Expense  # noqa: E402
from app.schemas.vehicle import VehicleCreate  # noqa: E402
from app.services.vehicle_service import VehicleService  # noqa: E402


async def _vehicle(db, kind="tractor_unit", plate=None, mileage=1000):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else plate or "CA" + uuid4().hex[:6].upper(),
            brand="Volvo",
            model="FH",
            department="Test department",
            current_mileage=mileage if kind in ("tractor_unit", "van", "passenger_car") else None,
        )
    )


async def _login(client, username="testmanager"):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


async def _count(db, model):
    return await db.scalar(select(func.count()).select_from(model))


@pytest.mark.asyncio
async def test_form_uses_picker_and_grouped_categories(client, db_session, fleet_manager_user):
    truck = await _vehicle(db_session, plate="CB6060TT")
    await _login(client)
    form = await client.get("/expenses/new", params={"vehicle_id": str(truck.id)})
    assert form.status_code == 200
    assert 'hx-get="/expenses/vehicle-options"' in form.text and '<select name="vehicle_id"' not in form.text
    assert "<optgroup" in form.text and 'value="casco"' in form.text and 'name="mileage_at_refuel"' in form.text
    assert 'name="valid_from"' in form.text and "CB6060TT" in form.text
    options = await client.get("/expenses/vehicle-options", params={"q": "6060"})
    assert 'data-tracked="1"' in options.text and "tachograph" in options.text
    await _vehicle(db_session, "van", plate="CB6161VV")
    options = await client.get("/expenses/vehicle-options", params={"q": "6161"})
    assert "tachograph" not in options.text and "casco" in options.text


@pytest.mark.asyncio
async def test_document_expense_creates_both_records(client, db_session, fleet_manager_user):
    truck = await _vehicle(db_session)
    await _login(client)
    data = {
        "vehicle_id": str(truck.id),
        "category": "civil_liability",
        "amount": "420.00",
        "date": "2026-02-01",
        "vendor": "Армеец",
        "valid_from": "2026-02-01",
        "valid_until": "2027-01-31",
        "policy_number": "ГО-77",
    }
    response = await client.post("/expenses/new", data=data)
    assert response.status_code == 303
    document = (await db_session.scalars(select(ComplianceDocument))).one()
    assert (document.document_type, str(document.amount), document.provider, document.policy_number) == (
        "civil_liability",
        "420.00",
        "Армеец",
        "ГО-77",
    )
    detail = await client.get(response.headers["location"])
    assert f"/compliance/{document.id}" in detail.text and "Document with deadline" in detail.text
    page = await client.get(f"/compliance/{document.id}")
    assert f"/expenses/{document.expense_id}" in page.text and "Open the expense" in page.text
    assert "🔗" in (await client.get("/expenses")).text or "&#128279;" in (await client.get("/expenses")).text

    missing = await client.post("/expenses/new", data={**data, "valid_until": ""})
    assert missing.status_code == 400 and "start and end of validity" in missing.text
    assert await _count(db_session, Expense) == 1


@pytest.mark.asyncio
async def test_document_form_amount_appears_in_expenses(client, db_session, fleet_manager_user):
    truck = await _vehicle(db_session)
    await _login(client)
    response = await client.post(
        "/compliance/new",
        data={
            "vehicle_id": str(truck.id),
            "document_type": "casco",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "amount": "880.00",
            "provider": "Алианц",
        },
    )
    assert response.status_code == 302
    expense = (await db_session.scalars(select(Expense))).one()
    assert expense.category.value == "casco" and str(expense.amount) == "880.00" and expense.vendor == "Алианц"
    listing = await client.get("/expenses")
    assert "Алианц" in listing.text and "880" in listing.text
    # Deleting the expense keeps the document and clears its cost; the edit form locks the category.
    edit = await client.get(f"/expenses/{expense.id}/edit")
    assert "disabled" in edit.text and "tied to a document" in edit.text and 'name="valid_from"' not in edit.text
    response = await client.post(f"/expenses/{expense.id}/delete")
    assert response.status_code == 303
    document = (await db_session.scalars(select(ComplianceDocument))).one()
    await db_session.refresh(document)
    assert document.amount is None and document.expense_id is None
    assert await _count(db_session, Expense) == 0


@pytest.mark.asyncio
async def test_mileage_popup_and_edit_delete(client, db_session, fleet_manager_user):
    truck = await _vehicle(db_session, mileage=1000)
    await _login(client)
    data = {
        "vehicle_id": str(truck.id),
        "category": "fuel",
        "amount": "60.00",
        "date": "2026-03-01",
        "fuel_liters": "40",
    }
    response = await client.post("/expenses/new", data={**data, "mileage_at_refuel": "1300"})
    assert response.status_code == 303
    await db_session.refresh(truck)
    assert truck.current_mileage == 1300

    response = await client.post("/expenses/new", data={**data, "mileage_at_refuel": "1100"})
    assert response.status_code == 409 and "Mileage is lower than the current one" in response.text
    assert "1,300 km" in response.text and 'value="1100"' in response.text
    assert await _count(db_session, Expense) == 1
    response = await client.post(
        "/expenses/new", data={**data, "mileage_at_refuel": "1100", "confirm_mileage_overwrite": "1"}
    )
    assert response.status_code == 303
    await db_session.refresh(truck)
    assert truck.current_mileage == 1100

    expense = (await db_session.scalars(select(Expense).order_by(Expense.created_at))).first()
    detail = await client.get(f"/expenses/{expense.id}")
    assert detail.status_code == 200 and "1,300 km" in detail.text and "40" in detail.text
    response = await client.post(
        f"/expenses/{expense.id}/edit",
        data={
            "category": "parts",
            "amount": "75.50",
            "date": "2026-03-02",
            "vendor": "Auto Parts",
            "fuel_liters": "40",
        },
    )
    assert response.status_code == 303
    await db_session.refresh(expense)
    assert (expense.category.value, str(expense.amount), expense.vendor, expense.fuel_liters) == (
        "parts",
        "75.50",
        "Auto Parts",
        None,
    )
    assert (
        await client.post(f"/expenses/{expense.id}/edit", data={"category": "fuel", "amount": "", "date": "2026-03-02"})
    ).status_code == 400
    assert (await client.post(f"/expenses/{expense.id}/delete")).status_code == 303
    assert await _count(db_session, Expense) == 1


@pytest.mark.asyncio
async def test_viewer_is_read_only_and_labels_in_bulgarian(client, db_session, viewer_user, fleet_manager_user):
    truck = await _vehicle(db_session)
    from app.schemas.expense import ExpenseCreate
    from app.services.expense_service import ExpenseService

    expense = await ExpenseService(db_session).create(
        ExpenseCreate(vehicle_id=truck.id, category="fuel", amount="10.00", date=date(2026, 1, 1)), None
    )
    await _login(client, "testviewer")
    listing = await client.get("/expenses")
    assert listing.status_code == 200 and 'href="/expenses/new"' not in listing.text
    detail = await client.get(f"/expenses/{expense.id}")
    assert detail.status_code == 200 and "/delete" not in detail.text and "/edit" not in detail.text
    for path in ("/expenses/new", f"/expenses/{expense.id}/edit"):
        response = await client.get(path)
        assert response.status_code == 302 and response.headers["location"] == "/", path
    data = {"vehicle_id": str(truck.id), "category": "fuel", "amount": "1", "date": "2026-01-01"}
    assert (await client.post("/expenses/new", data=data)).status_code == 302
    assert (await client.post(f"/expenses/{expense.id}/delete")).status_code == 302
    assert await _count(db_session, Expense) == 1
    await _login(client)
    await client.get("/set-language/bg")
    form = await client.get("/expenses/new")
    assert "Добавяне на разход" in form.text and "Документи и срокове" in form.text and "Тахограф" in form.text


@pytest.mark.asyncio
async def test_form_rules_no_currency_no_generic_insurance_trailer_blocks(client, db_session, fleet_manager_user):
    trailer = await VehicleService(db_session).create(
        VehicleCreate(
            vehicle_type="container_semi_trailer",
            license_plate="CB4040TR",
            brand="Schmitz",
            model="Cargobull",
            department="Test department",
        )
    )
    truck = await _vehicle(db_session, plate="CB4141TT")
    await _login(client)
    form = await client.get("/expenses/new")
    assert 'name="currency"' not in form.text and 'value="insurance"' not in form.text
    assert "(EUR)" in form.text
    assert 'value="insurance"' not in (await client.get("/expenses")).text
    options = await client.get("/expenses/vehicle-options", params={"q": "4040"})
    assert "[&#34;fuel&#34;, &#34;toll&#34;]" in options.text or '["fuel", "toll"]' in options.text
    assert "data-blocked='[]'" in (await client.get("/expenses/vehicle-options", params={"q": "4141"})).text

    data = {"vehicle_id": str(trailer.id), "amount": "20.00", "date": "2026-03-01"}
    for category in ("fuel", "toll"):
        response = await client.post("/expenses/new", data={**data, "category": category})
        assert response.status_code == 400 and "does not apply to the selected vehicle type" in response.text
    response = await client.post("/expenses/new", data={**data, "category": "insurance"})
    assert response.status_code == 400 and "Civil liability or CASCO" in response.text
    assert await _count(db_session, Expense) == 0

    # Validity left in the hidden fields of a non-document category is ignored, not an error.
    response = await client.post(
        "/expenses/new",
        data={
            "vehicle_id": str(truck.id),
            "category": "parts",
            "amount": "5.00",
            "date": "2026-03-01",
            "valid_from": "2026-03-01",
            "valid_until": "2027-03-01",
            "policy_number": "ignored",
        },
    )
    assert response.status_code == 303
    assert await _count(db_session, ComplianceDocument) == 0

    # An existing expense in the retired category can still be opened and edited.
    from app.models.expense import ExpenseCategory

    legacy = Expense(vehicle_id=truck.id, category=ExpenseCategory.INSURANCE, amount=1, date=date(2026, 1, 1))
    db_session.add(legacy)
    await db_session.flush()
    await db_session.refresh(legacy)
    edit = await client.get(f"/expenses/{legacy.id}/edit")
    assert edit.status_code == 200 and 'value="insurance"' in edit.text
    response = await client.post(
        f"/expenses/{legacy.id}/edit",
        data={"category": "insurance", "amount": "2.50", "date": "2026-01-01", "vendor": "Legacy"},
    )
    assert response.status_code == 303
