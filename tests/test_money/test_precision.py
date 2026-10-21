from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from app.models.expense import Currency, Expense
from app.schemas.expense import ExpenseCreate, ExpenseUpdate
from app.schemas.report import TCOReportRow
from app.utils.export import CSVExporter, ExcelExporter
from app.utils.formatting import money


def expense_data(**values):
    return dict(vehicle_id=uuid4(), category="fuel", amount="0.10", date="2026-01-01", **values)


@pytest.mark.parametrize("currency", ["KZT", "RUB", "USD", "TRY", "eur", "GBP", None])
def test_removed_currencies_rejected(currency):
    with pytest.raises(ValidationError):
        ExpenseCreate(**expense_data(currency=currency))
    with pytest.raises(ValidationError):
        ExpenseUpdate(currency=currency)
    with pytest.raises(ValueError):
        Expense(currency=currency)


def test_defaults_and_precision():
    assert ExpenseCreate(**expense_data()).currency == Currency.EUR
    assert ExpenseUpdate().model_dump(exclude_unset=True) == {}
    assert ExpenseCreate(**expense_data()).amount + Decimal("0.20") == Decimal("0.30")
    for value in ["10000000000.00", "0.001"]:
        with pytest.raises(ValidationError):
            ExpenseUpdate(amount=value)
    with pytest.raises(ValidationError):
        ExpenseUpdate(fuel_price_per_liter="1000000.00")


def test_display_zero_and_missing():
    assert money(None, "bg") == "—"
    assert "0,00" in money(Decimal("0"), "bg")
    assert "0.00" in money(Decimal("0"), "en")
    assert money(Decimal("1234.56"), "en").endswith("EUR")


def test_export_roundtrip():
    exporter = ExcelExporter("Разходи", monetary_columns=(1,))
    exporter.add_headers(["Стойност (EUR)", "Брой"])
    exporter.add_row([Decimal("0.30"), 3])
    exporter.add_row([Decimal("123456789012345.67"), 4])
    ws = load_workbook(BytesIO(exporter.to_bytes())).active
    assert ws["A2"].value == 0.3
    assert "EUR" in ws["A2"].number_format
    assert ws["B2"].number_format == "General"
    assert ws["A3"].value == "123456789012345.67"
    csv = CSVExporter()
    csv.add_headers(["Стойност (EUR)"])
    csv.add_row([Decimal("0.30")])
    assert csv.to_bytes().decode("utf-8-sig") == "Стойност (EUR)\r\n0.30\r\n"


def test_report_json_keeps_decimal_strings():
    row = TCOReportRow(
        id="x",
        license_plate="A",
        brand="B",
        model="C",
        year=2026,
        purchase_price=Decimal("0.10"),
        total_expenses=Decimal("0.20"),
        tco=Decimal("0.30"),
    )
    assert row.model_dump(mode="json")["tco"] == "0.30"


@pytest.mark.asyncio
async def test_localized_exports_keep_exact_values(monkeypatch):
    from app.i18n import load_translations
    from app.services.report_service import ReportService

    load_translations()
    service = ReportService(None)

    async def analysis(*args):
        return {"by_category": [{"category": "fuel", "total": Decimal("0.30"), "count": 2}]}

    monkeypatch.setattr(service, "expense_analysis", analysis)
    bg = (await service.export_expense_csv(locale="bg")).decode("utf-8-sig")
    en = (await service.export_expense_csv(locale="en")).decode("utf-8-sig")
    assert "EUR" in bg and "EUR" in en
    assert "Гориво" in bg and "Fuel" in en
    assert "0.30" in bg and "0.30" in en


@pytest.mark.parametrize(
    "field,schema",
    [
        ("purchase_price", "vehicle"),
        ("cost", "maintenance"),
        ("amount", "contract"),
    ],
)
def test_other_monetary_update_limits(field, schema):
    from app.schemas.contract import ContractUpdate
    from app.schemas.maintenance import MaintenanceUpdate
    from app.schemas.vehicle import VehicleUpdate

    model = {"vehicle": VehicleUpdate, "maintenance": MaintenanceUpdate, "contract": ContractUpdate}[schema]
    assert getattr(model(**{field: "9999999999.99"}), field) == Decimal("9999999999.99")
    assert getattr(model(**{field: "0"}), field) == Decimal("0")
    assert getattr(model(**{field: None}), field) is None
    for value in ["0.001", "10000000000.00"]:
        with pytest.raises(ValidationError):
            model(**{field: value})


@pytest.mark.asyncio
async def test_internal_currency_inputs_revalidated_before_database_access():
    from app.services.expense_service import ExpenseService

    invalid = ExpenseCreate.model_construct(**expense_data(currency="USD"))
    with pytest.warns(UserWarning, match="serializer warnings"), pytest.raises(ValidationError):
        await ExpenseService(None).create(invalid, uuid4())
