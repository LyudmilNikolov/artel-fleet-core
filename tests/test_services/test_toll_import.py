"""Toll import from the Shell Priced Transaction CSV: parsing, and re-uploads that only add what is new."""

import datetime as dt
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.expense import Expense, ExpenseCategory
from app.models.notification import Notification
from app.models.toll_import import TollImportStatus, TollTransaction
from app.models.vehicle import VehicleStatus
from app.schemas.expense import ExpenseCreate
from app.schemas.vehicle import VehicleCreate
from app.services.errors import DomainError
from app.services.expense_service import ExpenseService
from app.services.toll_import_service import TollImportService
from app.services.vehicle_service import VehicleService
from app.utils import shell_toll_csv
from app.utils.plates import plate_key

HEADER = [
    "Вид запис",
    "Дата на доставка",
    "Време",
    "Регистрационен номер на превозното средство",
    "Валута на трансакцията",
    "Брутна покупна цена във валута на трансакцията (вкл ДДС и след отстъпка)",
    "Номер на фактурата",
    "Име на мрежата",
    "Име на продукт",
    "Идентификатор на трансакция",
    "Флаг за възстановяване",
    "",
]


def line(tid, plate, day="09/10/26", amount="10.51", time="04:39:03 PM", currency="EUR", invoice="", refund="N"):
    return [
        "",
        day,
        time,
        plate,
        currency,
        amount,
        invoice,
        "Toll4Europe Bulgaria EUR",
        "Road tax",
        "'" + tid,
        refund,
        "",
    ]


def csv_bytes(*rows, encoding="utf-8-sig", delimiter=";", header=HEADER):
    lines = [delimiter.join(f'"{v}"' for v in header)] + [delimiter.join(f'"{v}"' for v in r) for r in rows]
    return ("\r\n".join(lines) + "\r\n").encode(encoding)


# ---- parsing (no database) -------------------------------------------------------------------------------


def test_plate_key_reads_cyrillic_latin_and_spaces_alike():
    assert plate_key("CO7930CX") == plate_key("СО 7930 СХ") == plate_key("co-7930-cx") == "CO7930CX"
    assert plate_key(None) == ""


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32"])
def test_parse_reads_the_export_in_any_unicode_encoding(encoding):
    rows = shell_toll_csv.parse(csv_bytes(line("311827997206", "CO7930CX"), encoding=encoding))
    assert len(rows) == 1
    row = rows[0]
    assert row.transaction_id == "311827997206"  # Excel's ' text marker removed
    assert row.plate == "CO7930CX"
    assert row.date == dt.date(2026, 10, 9)
    assert row.time == dt.time(16, 39, 3)
    assert row.amount == Decimal("10.51") and row.currency == "EUR"
    assert row.product == "Road tax" and row.network == "Toll4Europe Bulgaria EUR"
    assert row.invoice_number is None and row.problem is None


def test_parse_accepts_comma_delimiter_and_skips_blank_lines():
    data = csv_bytes(line("1", "CA1"), ["" for _ in HEADER], line("2", "CA2"), delimiter=",")
    assert [r.transaction_id for r in shell_toll_csv.parse(data)] == ["1", "2"]


def test_parse_marks_unusable_lines_without_failing_the_file():
    rows = shell_toll_csv.parse(
        csv_bytes(
            line("", "CA1"),
            line("2", "CA1", day="2026/31/31"),
            line("3", "CA1", amount="1.005"),
            line("4", "CA1", currency="BGN"),
            line("5", "CA1", refund="Y"),
            line("6", "CA1", amount="-3.00"),
            line("7", "CA1", amount="4.1000"),  # trailing zeros are exact
        )
    )
    assert [(r.problem, r.problem_field) for r in rows] == [
        ("missing_field", "transaction_id"),
        ("bad_date", "date"),
        ("bad_amount", "amount"),
        ("not_eur", "currency"),
        ("refund", None),
        ("refund", None),
        (None, None),
    ]
    assert rows[-1].amount == Decimal("4.10")


def test_parse_rejects_other_files():
    with pytest.raises(DomainError) as exc:
        shell_toll_csv.parse(csv_bytes(header=["Име на обект", "Код на оператора за пътни такси"]))
    assert exc.value.key == "error.toll_import_wrong_report"
    with pytest.raises(DomainError) as exc:
        shell_toll_csv.parse(b"a;b;c\r\n1;2;3\r\n")
    assert exc.value.key == "error.toll_import_columns"
    with pytest.raises(DomainError) as exc:
        shell_toll_csv.parse(b"PK\x03\x04 an xlsx workbook")
    assert exc.value.key == "error.toll_import_excel"
    with pytest.raises(DomainError) as exc:
        shell_toll_csv.parse(b"\xff\xfe\x00\x00\x00")
    assert exc.value.key == "error.toll_import_encoding"


# ---- importing ---------------------------------------------------------------------------------------------


async def vehicle(db, plate, kind="tractor_unit", status=None):
    created = await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=plate,
            brand="Volvo",
            model="FH",
            department="Test department",
            current_mileage=1000 if kind in ("tractor_unit", "van", "passenger_car") else None,
        )
    )
    if status:
        created.status = status
        await db.flush()
    return created


async def run(db, user, data, name="export.csv"):
    service = TollImportService(db)
    batch = await service.preview(data, name, user)
    return await service.apply(batch.id, user)


async def toll_expenses(db):
    result = await db.scalars(
        select(Expense).where(Expense.category == ExpenseCategory.TOLL).order_by(Expense.date, Expense.amount)
    )
    return list(result.all())


@pytest.mark.asyncio
async def test_preview_writes_nothing_and_apply_groups_one_expense_per_vehicle_and_day(db_session, admin_user):
    truck = await vehicle(db_session, "СО 7930 СХ")  # typed in Cyrillic with spaces
    other = await vehicle(db_session, "CB4162MA")
    data = csv_bytes(
        line("1", "CO7930CX", amount="10.51"),
        line("2", "CO7930CX", amount="4.06", time="06:01:00 PM"),
        line("3", "CO7930CX", day="10/10/26", amount="1.00"),
        line("4", "CB4162MA", amount="20.00", invoice="9400124192"),
    )
    service = TollImportService(db_session)
    batch = await service.preview(data, "export.csv", admin_user)
    assert batch.status == TollImportStatus.PREVIEW
    assert batch.summary["new_rows"] == 4 and batch.summary["new_amount"] == "35.57"
    assert batch.summary["expenses_created"] == 3 and batch.summary["vehicles"] == 2
    assert await toll_expenses(db_session) == []

    batch = await service.apply(batch.id, admin_user)
    assert batch.status == TollImportStatus.APPLIED and batch.rows is None and batch.problems == []
    expenses = await toll_expenses(db_session)
    assert [(e.vehicle_id, e.date, e.amount) for e in expenses] == [
        (truck.id, dt.date(2026, 10, 9), Decimal("14.57")),
        (other.id, dt.date(2026, 10, 9), Decimal("20.00")),
        (truck.id, dt.date(2026, 10, 10), Decimal("1.00")),
    ]
    assert expenses[0].vendor == "Toll4Europe Bulgaria EUR" and expenses[0].description == "Road tax"
    assert expenses[0].created_by == admin_user.id
    await db_session.refresh(expenses[0])
    assert [t.external_id for t in expenses[0].toll_transactions] == ["1", "2"]
    audit = await db_session.scalar(select(AuditLog).where(AuditLog.entity_type == "expense_import"))
    assert audit.entity_id == batch.id and audit.changes["new_rows"] == 4
    assert await db_session.scalar(select(func.count()).select_from(Notification)) == 0  # nothing to report


@pytest.mark.asyncio
async def test_same_file_twice_changes_nothing(db_session, admin_user):
    await vehicle(db_session, "CO7930CX")
    data = csv_bytes(line("1", "CO7930CX"), line("2", "CO7930CX", amount="4.06"))
    await run(db_session, admin_user, data)
    before = [(e.id, e.amount) for e in await toll_expenses(db_session)]

    service = TollImportService(db_session)
    again = await service.preview(data, "export.csv", admin_user)
    assert again.summary["new_rows"] == 0 and again.summary["duplicate_rows"] == 2
    await service.apply(again.id, admin_user)
    assert [(e.id, e.amount) for e in await toll_expenses(db_session)] == before
    assert await db_session.scalar(select(func.count()).select_from(TollTransaction)) == 2


@pytest.mark.asyncio
async def test_overlapping_file_adds_only_new_transactions_to_the_same_day(db_session, admin_user):
    await vehicle(db_session, "CO7930CX")
    await run(db_session, admin_user, csv_bytes(line("1", "CO7930CX", amount="10.00")))
    # The next export repeats transaction 1 (now invoiced) and has a late one for the same day plus a new day.
    batch = await run(
        db_session,
        admin_user,
        csv_bytes(
            line("1", "CO7930CX", amount="10.00", invoice="9400124192"),
            line("2", "CO7930CX", amount="2.50"),
            line("3", "CO7930CX", day="11/10/26", amount="7.00"),
        ),
    )
    assert batch.summary["duplicate_rows"] == 1 and batch.summary["new_rows"] == 2
    assert batch.summary["expenses_updated"] == 1 and batch.summary["expenses_created"] == 1
    assert [(e.date, e.amount) for e in await toll_expenses(db_session)] == [
        (dt.date(2026, 10, 9), Decimal("12.50")),
        (dt.date(2026, 10, 11), Decimal("7.00")),
    ]
    first = await db_session.scalar(select(TollTransaction).where(TollTransaction.external_id == "1"))
    assert first.invoice_number == "9400124192"
    assert batch.summary["invoice_updates"] == 1
    assert batch.summary["invoiced"] == [
        {
            "transaction_id": "1",
            "plate": "CO7930CX",
            "date": "2026-10-09",
            "amount": "10.00",
            "old_invoice": None,
            "invoice": "9400124192",
            "expense_id": str(first.expense_id),
        }
    ]


@pytest.mark.asyncio
async def test_problem_lines_block_only_themselves_and_are_reported(db_session, admin_user):
    await vehicle(db_session, "CO7930CX")
    await vehicle(db_session, "CA1111TR", kind="curtain_side_semi_trailer")
    await vehicle(db_session, "CA2222DD", status=VehicleStatus.DECOMMISSIONED)
    admin_user.language = "en"
    batch = await run(
        db_session,
        admin_user,
        csv_bytes(
            line("1", "CO7930CX"),
            line("2", "XX0000XX", amount="3.00"),
            line("3", "CA1111TR"),
            line("4", "CA2222DD"),
            line("5", "CO7930CX", currency="BGN"),
            line("1", "CO7930CX", amount="99.00"),  # same ID again with other data
        ),
    )
    reasons = {p["transaction_id"]: p["reason"] for p in batch.problems}
    assert reasons == {
        "2": "unknown_vehicle",
        "3": "not_applicable",
        "4": "decommissioned",
        "5": "not_eur",
        "1": "conflict_in_file",
    }
    assert batch.summary["new_rows"] == 1 and batch.summary["problem_rows"] == 5
    assert [e.amount for e in await toll_expenses(db_session)] == [Decimal("10.51")]
    note = await db_session.scalar(select(Notification).where(Notification.user_id == admin_user.id))
    assert note.entity_type == "expense_import" and note.entity_id == batch.id
    assert "5 lines" in note.message and "XX0000XX" in note.message


@pytest.mark.asyncio
async def test_missing_vehicle_added_later_is_imported_on_the_next_upload(db_session, admin_user):
    data = csv_bytes(line("1", "CO7930CX"), line("2", "CB4162MA", amount="5.00"))
    await vehicle(db_session, "CO7930CX")
    service = TollImportService(db_session)
    await run(db_session, admin_user, data)
    assert [p["plate"] for p in await service.waiting()] == ["CB4162MA"]

    await vehicle(db_session, "CB4162MA")
    batch = await run(db_session, admin_user, data)
    assert batch.summary["new_rows"] == 1 and batch.summary["duplicate_rows"] == 1 and batch.problems == []
    assert await service.waiting() == []


@pytest.mark.asyncio
async def test_already_imported_transaction_with_other_data_is_reported_not_changed(db_session, admin_user):
    await vehicle(db_session, "CO7930CX")
    await run(db_session, admin_user, csv_bytes(line("1", "CO7930CX", amount="10.00")))
    batch = await run(db_session, admin_user, csv_bytes(line("1", "CO7930CX", amount="11.00")))
    assert [p["reason"] for p in batch.problems] == ["changed"]
    assert [e.amount for e in await toll_expenses(db_session)] == [Decimal("10.00")]


@pytest.mark.asyncio
async def test_vehicle_added_between_preview_and_apply_is_used(db_session, admin_user):
    service = TollImportService(db_session)
    batch = await service.preview(csv_bytes(line("1", "CO7930CX")), "export.csv", admin_user)
    assert batch.problems[0]["reason"] == "unknown_vehicle"
    await vehicle(db_session, "CO7930CX")
    batch = await service.apply(batch.id, admin_user)
    assert batch.problems == [] and batch.summary["new_rows"] == 1
    with pytest.raises(DomainError) as exc:
        await service.apply(batch.id, admin_user)
    assert exc.value.key == "error.toll_import_already_applied"


@pytest.mark.asyncio
async def test_typed_in_toll_expense_is_never_merged_and_deleting_an_import_forgets_it(db_session, admin_user):
    truck = await vehicle(db_session, "CO7930CX")
    manual = await ExpenseService(db_session).create(
        ExpenseCreate(vehicle_id=truck.id, category="toll", amount=Decimal("1.00"), date=dt.date(2026, 10, 9)),
        admin_user.id,
    )
    data = csv_bytes(line("1", "CO7930CX"))
    await run(db_session, admin_user, data)
    expenses = await toll_expenses(db_session)
    assert len(expenses) == 2 and manual.amount == Decimal("1.00")

    imported = next(e for e in expenses if e.id != manual.id)
    await ExpenseService(db_session).delete(imported.id)
    await db_session.flush()
    assert await db_session.scalar(select(func.count()).select_from(TollTransaction)) == 0
    batch = await run(db_session, admin_user, data)
    assert batch.summary["new_rows"] == 1


@pytest.mark.asyncio
async def test_cancel_discards_only_a_preview(db_session, admin_user):
    service = TollImportService(db_session)
    batch = await service.preview(csv_bytes(line(uuid4().hex, "XX1")), "export.csv", admin_user)
    await service.cancel(batch.id)
    assert await service.get(batch.id) is None
