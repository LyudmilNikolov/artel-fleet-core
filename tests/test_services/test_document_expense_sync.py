"""One cost, two views: documents and expenses stay in sync; financial records are never deleted implicitly."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.compliance import ComplianceDocument
from app.models.document import Document, DocumentType, EntityType
from app.models.expense import Expense, ExpenseCategory
from app.schemas.compliance import ComplianceCreate, ComplianceRenew, ComplianceUpdate
from app.schemas.expense import ExpenseCreate, ExpenseUpdate
from app.schemas.vehicle import VehicleCreate
from app.services.compliance_service import ComplianceService
from app.services.errors import DomainError, MileageBelowCurrentError
from app.services.expense_service import ExpenseService
from app.services.validity import local_today
from app.services.vehicle_service import VehicleService
from app.vehicle_rules import MILEAGE_TYPES

TODAY = local_today()


class FakeS3:
    def __init__(self):
        self.deleted = []

    def delete_file(self, key):
        self.deleted.append(key)


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr("app.services.expense_service.get_s3_client", lambda: fake)
    return fake


async def make_vehicle(db, kind="tractor_unit", mileage=1000):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else "CA" + uuid4().hex[:6].upper(),
            brand="Volvo",
            model="FH",
            department="Test department",
            current_mileage=mileage if kind in MILEAGE_TYPES else None,
        )
    )


def doc(vehicle_id, document_type="casco", start=None, end=None, **kw):
    start = start or TODAY - timedelta(days=10)
    return ComplianceCreate(
        vehicle_id=vehicle_id,
        document_type=document_type,
        start_date=start,
        end_date=end or start + timedelta(days=364),
        **kw,
    )


def expense(vehicle_id, category="fuel", amount="50.00", **kw):
    return ExpenseCreate(vehicle_id=vehicle_id, category=category, amount=amount, date=TODAY, **kw)


async def expenses(db):
    return list((await db.scalars(select(Expense).order_by(Expense.created_at))).all())


async def reload(db, obj):
    await db.refresh(obj)
    return obj


# ---- document -> expense ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_with_amount_creates_linked_expense(db_session):
    v = await make_vehicle(db_session)
    view = await ComplianceService(db_session).create(
        doc(v.id, amount=Decimal("480.50"), provider="Булстрад", policy_number="P-9"), None
    )
    [linked] = await expenses(db_session)
    assert view.entry.expense_id == linked.id
    assert (linked.vehicle_id, linked.category, linked.amount, linked.vendor, linked.description) == (
        v.id,
        ExpenseCategory.CASCO,
        Decimal("480.50"),
        "Булстрад",
        "P-9",
    )
    assert linked.date == view.start_date  # started in the past: that is the payment date
    assert linked.compliance_document.id == view.id


@pytest.mark.asyncio
async def test_future_start_is_recorded_as_paid_today(db_session):
    v = await make_vehicle(db_session)
    await ComplianceService(db_session).create(
        doc(v.id, "civil_liability", start=TODAY + timedelta(days=20), amount=Decimal("300")), None
    )
    assert (await expenses(db_session))[0].date == TODAY


@pytest.mark.asyncio
async def test_document_without_amount_has_no_expense_until_one_is_added(db_session):
    v = await make_vehicle(db_session)
    svc = ComplianceService(db_session)
    view = await svc.create(doc(v.id), None)
    assert await expenses(db_session) == []
    updated = await svc.update(view.id, ComplianceUpdate(amount=Decimal("99.99")))
    [linked] = await expenses(db_session)
    assert updated.entry.expense_id == linked.id and linked.amount == Decimal("99.99")


@pytest.mark.asyncio
async def test_document_edits_flow_to_expense_and_clearing_unlinks_without_deleting(db_session):
    v = await make_vehicle(db_session)
    svc = ComplianceService(db_session)
    view = await svc.create(doc(v.id, amount=Decimal("100"), provider="A"), None)
    [linked] = await expenses(db_session)
    await svc.update(view.id, ComplianceUpdate(amount=Decimal("120.00"), provider="B"))
    await reload(db_session, linked)
    assert (linked.amount, linked.vendor) == (Decimal("120.00"), "B")
    # Editing dates never touches the expense.
    await svc.update(view.id, ComplianceUpdate(end_date=TODAY + timedelta(days=700)))
    await reload(db_session, linked)
    assert linked.amount == Decimal("120.00")
    # Clearing the amount keeps the financial record, just unlinked.
    cleared = await svc.update(view.id, ComplianceUpdate(amount=None))
    assert cleared.entry.amount is None and cleared.entry.expense_id is None
    assert len(await expenses(db_session)) == 1
    await reload(db_session, linked)
    assert linked.amount == Decimal("120.00") and linked.compliance_document is None


@pytest.mark.asyncio
async def test_renewal_with_amount_creates_a_new_expense(db_session):
    v = await make_vehicle(db_session)
    svc = ComplianceService(db_session)
    first = await svc.create(doc(v.id, amount=Decimal("100")), None)
    await svc.renew(
        first.id,
        ComplianceRenew(start_date=TODAY, end_date=TODAY + timedelta(days=365), amount=Decimal("110")),
        None,
    )
    assert sorted(e.amount for e in await expenses(db_session)) == [Decimal("100.00"), Decimal("110.00")]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["entry", "all"])
async def test_deleting_a_document_keeps_its_expense(db_session, scope):
    v = await make_vehicle(db_session)
    svc = ComplianceService(db_session)
    view = await svc.create(doc(v.id, amount=Decimal("100")), None)
    await svc.delete(view.id, scope)
    [kept] = await expenses(db_session)
    assert kept.amount == Decimal("100.00")
    await reload(db_session, kept)
    assert kept.compliance_document is None


# ---- expense -> document ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expense_with_validity_creates_linked_document(db_session):
    v = await make_vehicle(db_session)
    created = await ExpenseService(db_session).create(
        expense(
            v.id,
            "technical_inspection",
            "55.00",
            vendor="ГТП Автотест",
            valid_from=TODAY,
            valid_until=TODAY + timedelta(days=364),
            policy_number="PR-1",
        ),
        None,
    )
    linked = created.compliance_document
    assert linked is not None
    assert (linked.vehicle_id, linked.document_type, linked.amount, linked.provider, linked.policy_number) == (
        v.id,
        "technical_inspection",
        Decimal("55.00"),
        "ГТП Автотест",
        "PR-1",
    )
    assert linked.expense_id == created.id and created.compliance_document_id == linked.id
    view = await ComplianceService(db_session).get(linked.id)
    assert view.is_current and view.status == "valid"


@pytest.mark.asyncio
async def test_expense_validation_rules_write_nothing(db_session):
    van = await make_vehicle(db_session, "van")
    forklift = await make_vehicle(db_session, "forklift")
    svc = ExpenseService(db_session)
    dates = dict(valid_from=TODAY, valid_until=TODAY + timedelta(days=30))
    cases = [
        (expense(van.id, "tachograph", **dates), "error.compliance_type_not_applicable"),
        (expense(forklift.id, "casco", **dates), "error.compliance_type_not_applicable"),
        (expense(van.id, "casco", valid_from=TODAY), "error.expense_document_dates_required"),
        (
            expense(van.id, "casco", valid_from=TODAY, valid_until=TODAY - timedelta(days=1)),
            "error.compliance_end_before_start",
        ),
        (expense(van.id, "fuel", **dates), "error.expense_validity_not_applicable"),
        (expense(uuid4(), "casco", **dates), "error.vehicle_not_found"),
    ]
    for data, key in cases:
        with pytest.raises(DomainError) as exc:
            await svc.create(data, None)
        assert exc.value.key == key
    assert await expenses(db_session) == []
    assert await db_session.scalar(select(func.count()).select_from(ComplianceDocument)) == 0
    # Without validity a document category is a plain expense.
    plain = await svc.create(expense(van.id, "casco"), None)
    assert plain.compliance_document is None


@pytest.mark.asyncio
async def test_expense_edits_flow_to_document_and_category_is_locked(db_session):
    v = await make_vehicle(db_session)
    svc = ExpenseService(db_session)
    created = await svc.create(
        expense(v.id, "casco", "200.00", vendor="A", valid_from=TODAY, valid_until=TODAY + timedelta(days=364)), None
    )
    linked = created.compliance_document
    await svc.update(created.id, ExpenseUpdate(amount=Decimal("250.00"), vendor="B", description="x"))
    await reload(db_session, linked)
    assert (linked.amount, linked.provider) == (Decimal("250.00"), "B")
    with pytest.raises(DomainError) as exc:
        await svc.update(created.id, ExpenseUpdate(category="fuel"))
    assert exc.value.key == "error.expense_category_locked"
    await svc.update(
        created.id, ExpenseUpdate(category="casco", date=TODAY - timedelta(days=3))
    )  # same category is fine
    plain = await svc.create(expense(v.id, "fuel"), None)
    assert (await svc.update(plain.id, ExpenseUpdate(category="parts"))).category == ExpenseCategory.PARTS


@pytest.mark.asyncio
async def test_deleting_an_expense_clears_the_document_cost_and_files(db_session, s3):
    v = await make_vehicle(db_session)
    svc = ExpenseService(db_session)
    created = await svc.create(
        expense(v.id, "tachograph", "80.00", valid_from=TODAY, valid_until=TODAY + timedelta(days=700)), None
    )
    linked = created.compliance_document
    receipt = Document(
        entity_type=EntityType.EXPENSE,
        entity_id=created.id,
        type=DocumentType.INVOICE,
        filename="r.pdf",
        s3_key=f"expense/{created.id}/r.pdf",
        mime_type="application/pdf",
        size_bytes=1,
    )
    db_session.add(receipt)
    await db_session.flush()
    await svc.delete(created.id)
    assert await expenses(db_session) == []
    await reload(db_session, linked)
    assert linked.amount is None and linked.expense_id is None
    assert await db_session.get(Document, receipt.id) is None and s3.deleted == [receipt.s3_key]
    with pytest.raises(DomainError):
        await svc.delete(created.id)


# ---- mileage on expenses ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expense_mileage_updates_vehicle_with_confirmation_for_lower_values(db_session):
    v = await make_vehicle(db_session)
    svc = ExpenseService(db_session)
    await svc.create(expense(v.id, mileage_at_refuel=1300), None)
    await reload(db_session, v)
    assert v.current_mileage == 1300
    with pytest.raises(MileageBelowCurrentError) as exc:
        await svc.create(expense(v.id, mileage_at_refuel=1200), None)
    assert (exc.value.value, exc.value.previous) == (1200, 1300)
    assert len(await expenses(db_session)) == 1
    await svc.create(expense(v.id, mileage_at_refuel=1200, overwrite_mileage=True), None)
    await reload(db_session, v)
    assert v.current_mileage == 1200


@pytest.mark.asyncio
async def test_expense_mileage_on_update_and_untracked_vehicle(db_session):
    v = await make_vehicle(db_session)
    forklift = await make_vehicle(db_session, "forklift")
    svc = ExpenseService(db_session)
    created = await svc.create(expense(v.id), None)
    await svc.update(created.id, ExpenseUpdate(mileage_at_refuel=1500))
    await reload(db_session, v)
    assert v.current_mileage == 1500
    with pytest.raises(MileageBelowCurrentError):
        await svc.update(created.id, ExpenseUpdate(mileage_at_refuel=900))
    with pytest.raises(DomainError) as exc:
        await svc.create(expense(forklift.id, mileage_at_refuel=10), None)
    assert exc.value.key == "error.mileage_unsupported"


def test_update_schema_rejects_blank_required_fields():
    for field in ("category", "amount", "date"):
        with pytest.raises(Exception):
            ExpenseUpdate(**{field: None})
    assert ExpenseUpdate(vendor=None).model_dump(exclude_unset=True) == {"vendor": None}


# ---- retired and vehicle-specific categories ------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_insurance_is_retired_but_existing_rows_stay_editable(db_session):
    v = await make_vehicle(db_session)
    svc = ExpenseService(db_session)
    with pytest.raises(DomainError) as exc:
        await svc.create(expense(v.id, "insurance"), None)
    assert exc.value.key == "error.expense_category_retired"
    legacy = Expense(vehicle_id=v.id, category=ExpenseCategory.INSURANCE, amount=Decimal("10"), date=TODAY)
    db_session.add(legacy)
    await db_session.flush()
    await db_session.refresh(legacy)
    assert (
        await svc.update(legacy.id, ExpenseUpdate(vendor="Old insurer", category="insurance"))
    ).vendor == "Old insurer"
    with pytest.raises(DomainError) as exc:
        await svc.update((await svc.create(expense(v.id, "fuel"), None)).id, ExpenseUpdate(category="insurance"))
    assert exc.value.key == "error.expense_category_retired"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["container_semi_trailer", "curtain_side_semi_trailer"])
async def test_trailers_have_no_fuel_or_toll_expenses(db_session, kind):
    trailer = await make_vehicle(db_session, kind)
    svc = ExpenseService(db_session)
    for category in ("fuel", "toll"):
        with pytest.raises(DomainError) as exc:
            await svc.create(expense(trailer.id, category), None)
        assert exc.value.key == "error.expense_category_not_applicable"
    assert await expenses(db_session) == []
    ok = await svc.create(expense(trailer.id, "service"), None)
    with pytest.raises(DomainError) as exc:
        await svc.update(ok.id, ExpenseUpdate(category="toll"))
    assert exc.value.key == "error.expense_category_not_applicable"
    truck = await make_vehicle(db_session)
    assert (await svc.create(expense(truck.id, "toll"), None)).category == ExpenseCategory.TOLL
    forklift = await make_vehicle(db_session, "forklift")
    assert (await svc.create(expense(forklift.id, "fuel"), None)).category == ExpenseCategory.FUEL
