"""Shared report filters: parsing, every filter combination per report, L/100 km, maintenance rules."""

from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.models.expense import Expense, ExpenseCategory
from app.models.maintenance import MaintenanceRecord, MaintenanceStatus, MaintenanceType
from app.schemas.report import ExpenseReportFilters, MaintenanceReportFilters, ReportFilters, parse_report_filters
from app.schemas.vehicle import VehicleCreate
from app.services.errors import DomainError
from app.services.report_service import ReportService
from app.services.vehicle_service import VehicleService
from app.vehicle_rules import TRAILER_TYPES


async def vehicle(db, kind, mileage=1000):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else "CA" + uuid4().hex[:6].upper(),
            brand="Brand",
            model="Model",
            department="Test department",
            current_mileage=mileage if kind in ("tractor_unit", "van", "passenger_car") else None,
        )
    )


async def expense(db, v, amount, day, category=ExpenseCategory.SERVICE, liters=None, km=None):
    db.add(
        Expense(
            vehicle_id=v.id,
            category=category,
            amount=Decimal(amount),
            date=day,
            fuel_liters=liters,
            mileage_at_refuel=km,
        )
    )
    await db.flush()


async def maintenance(db, v, status, scheduled, completed=None, cost=None):
    db.add(
        MaintenanceRecord(
            vehicle_id=v.id,
            type=MaintenanceType.REPAIR,
            title="Job",
            status=status,
            scheduled_date=scheduled,
            completed_date=completed,
            cost=Decimal(cost) if cost is not None else None,
        )
    )
    await db.flush()


def test_parser_empty_and_valid():
    assert parse_report_filters({}).is_empty
    assert parse_report_filters({"start_date": "", "vehicle_type": " "}).is_empty
    f = parse_report_filters({"start_date": "2026-01-01", "vehicle_type": "trailer"})
    assert f.to_query() == {"start_date": "2026-01-01", "vehicle_type": "trailer"}
    assert f.vehicle_types == list(TRAILER_TYPES)
    m = parse_report_filters({"status": "completed"}, MaintenanceReportFilters)
    assert m.to_query() == {"status": "completed"}


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ({"start_date": "bad"}, "error.report_invalid_date"),
        ({"start_date": "2026-05-01", "end_date": "2026-01-01"}, "error.report_date_order"),
        ({"vehicle_id": "not-a-uuid"}, "error.report_invalid_vehicle"),
        ({"vehicle_type": "boat"}, "error.report_unknown_vehicle_type"),
    ],
)
def test_parser_errors(raw, key):
    with pytest.raises(DomainError) as exc:
        parse_report_filters(raw)
    assert exc.value.key == key


def test_parser_status_error():
    with pytest.raises(DomainError) as exc:
        parse_report_filters({"status": "nope"}, MaintenanceReportFilters)
    assert exc.value.key == "error.report_invalid_status"
    # a plain report ignores status
    assert parse_report_filters({"status": "nope"}).is_empty


@pytest.mark.asyncio
async def test_tco_filter_combinations(db_session):
    truck = await vehicle(db_session, "tractor_unit")
    van = await vehicle(db_session, "van")
    trailer = await vehicle(db_session, "container_semi_trailer")
    await expense(db_session, truck, "100.50", date(2026, 3, 1))
    await expense(db_session, van, "20.00", date(2025, 1, 1))
    svc = ReportService(db_session)

    def ids(rows):
        return {r["id"] for r in rows}

    everything = ids(await svc.tco_report())
    assert {str(truck.id), str(van.id), str(trailer.id)} <= everything
    # vehicles without expenses in the period are kept (regression: the date filter acted like an inner join)
    in_2026 = await svc.tco_report(ReportFilters(start_date=date(2026, 1, 1)))
    assert ids(in_2026) == everything
    assert next(r for r in in_2026 if r["id"] == str(van.id))["total_expenses"] == Decimal("0")
    assert next(r for r in in_2026 if r["id"] == str(truck.id))["total_expenses"] == Decimal("100.50")
    assert ids(await svc.tco_report(ReportFilters(vehicle_id=truck.id))) == {str(truck.id)}
    assert ids(await svc.tco_report(ReportFilters(vehicle_type="trailer"))) == {str(trailer.id)}
    assert ids(await svc.tco_report(ReportFilters(vehicle_type="van"))) == {str(van.id)}
    both = ReportFilters(vehicle_id=truck.id, vehicle_type="tractor_unit", start_date=date(2026, 1, 1))
    assert ids(await svc.tco_report(both)) == {str(truck.id)}
    mismatch = ReportFilters(vehicle_id=truck.id, vehicle_type="van")
    assert await svc.tco_report(mismatch) == []
    assert await svc.vehicle_mismatch(mismatch) is True
    assert await svc.vehicle_mismatch(both) is False


@pytest.mark.asyncio
async def test_tco_includes_maintenance_cost_on_screen_and_in_excel(db_session):
    """QA #1: TCO = purchase price + expenses + maintenance (cancelled excluded), same period rules as the maintenance report."""
    truck = await vehicle(db_session, "tractor_unit")
    await expense(db_session, truck, "100.00", date(2026, 3, 1))
    await expense(db_session, truck, "50.00", date(2026, 3, 2))  # two expense rows must not multiply maintenance
    await maintenance(db_session, truck, MaintenanceStatus.COMPLETED, date(2026, 3, 1), date(2026, 3, 5), "350.50")
    await maintenance(db_session, truck, MaintenanceStatus.SCHEDULED, date(2026, 3, 9), None, "10.00")
    await maintenance(db_session, truck, MaintenanceStatus.CANCELLED, date(2026, 3, 3), None, "999.00")
    await maintenance(db_session, truck, MaintenanceStatus.COMPLETED, date(2025, 1, 1), date(2025, 1, 2), "70.00")
    svc = ReportService(db_session)

    row = next(r for r in await svc.tco_report(ReportFilters(vehicle_id=truck.id)) if r["id"] == str(truck.id))
    assert row["total_expenses"] == Decimal("150.00")
    assert row["maintenance_cost"] == Decimal("430.50")  # 350.50 + 10.00 + 70.00, cancelled ignored
    assert row["tco"] == Decimal("580.50")

    in_2026 = ReportFilters(vehicle_id=truck.id, start_date=date(2026, 1, 1))
    row = (await svc.tco_report(in_2026))[0]
    assert row["maintenance_cost"] == Decimal("360.50") and row["tco"] == Decimal("510.50")

    rows = _sheet(await svc.export_tco_excel(in_2026, locale="en"))
    header = next(r for r in rows if "Plate" in r or "Registration" in r or any("Maintenance" in str(c) for c in r))
    assert any("Maintenance" in str(c) for c in header)
    total = _value(rows, "Total")
    assert total[6:9] == (150, Decimal("360.50"), Decimal("510.50")) or total[6:9] == (150, 360.5, 510.5)


@pytest.mark.asyncio
async def test_expense_analysis_filters(db_session):
    truck = await vehicle(db_session, "tractor_unit")
    van = await vehicle(db_session, "van")
    await expense(db_session, truck, "10.10", date(2026, 3, 1))
    await expense(db_session, van, "5.05", date(2026, 3, 2), ExpenseCategory.PARTS)
    svc = ReportService(db_session)
    assert (await svc.expense_analysis())["grand_total"] == Decimal("15.15")
    only_truck = await svc.expense_analysis(ReportFilters(vehicle_id=truck.id))
    assert only_truck["grand_total"] == Decimal("10.10") and only_truck["total_count"] == 1
    by_type = await svc.expense_analysis(ReportFilters(vehicle_type="van"))
    assert [c["category"] for c in by_type["by_category"]] == ["parts"]
    assert (await svc.expense_analysis(ReportFilters(vehicle_id=truck.id, vehicle_type="van")))["total_count"] == 0
    assert (await svc.expense_analysis(ReportFilters(end_date=date(2026, 3, 1))))["grand_total"] == Decimal("10.10")


@pytest.mark.asyncio
async def test_fuel_filters_and_l_per_100km(db_session):
    truck = await vehicle(db_session, "tractor_unit")
    other = await vehicle(db_session, "van")
    forklift = await vehicle(db_session, "forklift")
    trailer = await vehicle(db_session, "curtain_side_semi_trailer")
    fuel = ExpenseCategory.FUEL
    await expense(db_session, truck, "100.00", date(2026, 1, 1), fuel, liters=50, km=1000)
    await expense(db_session, truck, "60.00", date(2026, 1, 10), fuel, liters=30, km=1500)
    await expense(db_session, truck, "90.00", date(2026, 1, 20), fuel, liters=45, km=2000)
    await expense(db_session, other, "40.00", date(2026, 1, 5), fuel, liters=20, km=500)  # one refuel only
    await expense(db_session, forklift, "30.00", date(2026, 1, 5), fuel, liters=15, km=None)
    svc = ReportService(db_session)
    rows = {r["id"]: r for r in await svc.fuel_consumption()}
    # full-to-full: (30 + 45) L over 1000 km = 7.5 L/100 km; the first refuel is excluded
    assert rows[str(truck.id)]["l_per_100km"] == Decimal("7.5")
    assert rows[str(other.id)]["l_per_100km"] is None
    assert rows[str(forklift.id)]["l_per_100km"] is None
    assert rows[str(forklift.id)]["total_liters"] == 15
    # the period narrows the refuels used
    jan_early = await svc.fuel_consumption(ReportFilters(end_date=date(2026, 1, 12), vehicle_id=truck.id))
    assert jan_early[0]["l_per_100km"] == Decimal("6.0")  # 30 L over 500 km
    assert await svc.fuel_consumption(ReportFilters(vehicle_type="trailer")) == []
    assert await svc.fuel_consumption(ReportFilters(vehicle_id=trailer.id)) == []
    assert [r["id"] for r in await svc.fuel_consumption(ReportFilters(vehicle_type="forklift"))] == [str(forklift.id)]
    assert await svc.fuel_consumption(ReportFilters(vehicle_id=truck.id, vehicle_type="van")) == []


@pytest.mark.asyncio
async def test_maintenance_service_date_status_and_totals(db_session):
    truck = await vehicle(db_session, "tractor_unit")
    van = await vehicle(db_session, "van")
    done = MaintenanceStatus.COMPLETED
    await maintenance(db_session, truck, done, date(2026, 1, 5), date(2026, 2, 10), "100.00")
    await maintenance(db_session, truck, MaintenanceStatus.CANCELLED, date(2026, 2, 12), None, "999.00")
    await maintenance(db_session, van, MaintenanceStatus.SCHEDULED, date(2026, 3, 1), None, None)
    svc = ReportService(db_session)
    assert len(await svc.maintenance_history()) == 3
    # the completed date wins over the scheduled date: this record belongs to February
    feb = MaintenanceReportFilters(start_date=date(2026, 2, 1), end_date=date(2026, 2, 28))
    assert {r["status"] for r in await svc.maintenance_history(feb)} == {"completed", "cancelled"}
    jan = MaintenanceReportFilters(start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
    assert await svc.maintenance_history(jan) == []
    only_done = await svc.maintenance_history(MaintenanceReportFilters(status=done))
    assert [r["status"] for r in only_done] == ["completed"]
    page = await svc.maintenance_page(None, 1, 2)
    assert page["total"] == 3 and page["pages"] == 2 and len(page["rows"]) == 2
    assert page["total_cost"] == Decimal("100.00")  # cancelled excluded, null cost counts as zero
    assert page["completed"] == 1
    newest_first = [r["scheduled_date"] for r in (await svc.maintenance_page(None, 1, 10))["rows"]]
    # sorted by service date (completed, else scheduled): 03-01, cancelled 02-12, completed 02-10
    assert newest_first == ["2026-03-01", "2026-02-12", "2026-01-05"]
    by_vehicle = await svc.maintenance_history(MaintenanceReportFilters(vehicle_id=van.id))
    assert [r["cost"] for r in by_vehicle] == [None]
    assert await svc.maintenance_history(MaintenanceReportFilters(vehicle_id=van.id, vehicle_type="tractor_unit")) == []


@pytest.mark.asyncio
async def test_exports_use_the_same_filters(db_session):
    truck = await vehicle(db_session, "tractor_unit")
    van = await vehicle(db_session, "van")
    await expense(db_session, truck, "12.34", date(2026, 3, 1))
    await expense(db_session, van, "1.00", date(2026, 3, 1))
    await maintenance(db_session, van, MaintenanceStatus.COMPLETED, date(2026, 3, 1), date(2026, 3, 2), "0.30")
    svc = ReportService(db_session)
    for locale, marker in (("bg", "Превозно средство"), ("en", "Vehicle")):
        filters = ReportFilters(vehicle_id=truck.id)
        rows = list(load_workbook(BytesIO(await svc.export_tco_excel(filters, locale=locale))).active.values)
        assert rows[1][0].count(marker) == 1 and truck.license_plate in rows[1][0]
        plates = [r[0] for r in rows if r[0] and r[0].startswith("CA")]
        assert plates == [truck.license_plate]
        assert next(r for r in rows if r[0] == truck.license_plate)[6] == 12.34
    maint = MaintenanceReportFilters(vehicle_type="van", status=MaintenanceStatus.COMPLETED)
    sheet = list(load_workbook(BytesIO(await svc.export_maintenance_excel(maint, locale="en"))).active.values)
    assert "Vehicle type: Van" in sheet[1][0] and "Status: Completed" in sheet[1][0]
    assert [r[0] for r in sheet if r[0] and r[0].startswith("CA")] == [van.license_plate]
    expense_rows = list(
        load_workbook(
            BytesIO(await svc.export_expense_excel(ReportFilters(vehicle_id=truck.id), locale="en"))
        ).active.values
    )
    assert next(r for r in expense_rows if r[0] == "Grand total")[1] == 12.34


def test_category_filter_parses_and_rejects_unknown_values():
    f = parse_report_filters({"category": "toll", "vehicle_type": "tractor_unit"}, ExpenseReportFilters)
    assert f.category == ExpenseCategory.TOLL and f.to_query() == {"vehicle_type": "tractor_unit", "category": "toll"}
    assert not hasattr(parse_report_filters({"category": "toll"}), "category")  # other reports ignore it
    with pytest.raises(DomainError) as exc:
        parse_report_filters({"category": "nope"}, ExpenseReportFilters)
    assert exc.value.key == "error.report_invalid_category"


@pytest.mark.asyncio
async def test_expense_analysis_and_export_filter_by_category(db_session):
    truck = await vehicle(db_session, "tractor_unit")
    van = await vehicle(db_session, "van")
    await expense(db_session, truck, "10.00", date(2026, 3, 1), ExpenseCategory.TOLL)
    await expense(db_session, truck, "20.00", date(2026, 4, 1), ExpenseCategory.TOLL)
    await expense(db_session, truck, "99.00", date(2026, 3, 1), ExpenseCategory.FUEL, liters=Decimal("50"))
    await expense(db_session, van, "5.00", date(2026, 3, 2), ExpenseCategory.TOLL)
    svc = ReportService(db_session)

    tolls = await svc.expense_analysis(ExpenseReportFilters(category=ExpenseCategory.TOLL))
    assert tolls["grand_total"] == Decimal("35.00") and tolls["total_count"] == 3
    assert [c["category"] for c in tolls["by_category"]] == ["toll"]
    assert [(m["month"], m["total"]) for m in tolls["monthly"]] == [
        ("2026-03-01", Decimal("15.00")),
        ("2026-04-01", Decimal("20.00")),
    ]
    truck_tolls = ExpenseReportFilters(category=ExpenseCategory.TOLL, vehicle_id=truck.id)
    assert (await svc.expense_analysis(truck_tolls))["grand_total"] == Decimal("30.00")
    rows = list(load_workbook(BytesIO(await svc.export_expense_excel(truck_tolls, locale="en"))).active.values)
    assert next(r for r in rows if r[0] == "Grand total")[1] == 30
    assert [r[0] for r in rows if r[0] in ("Toll", "Fuel")] == ["Toll"]
    assert "Category: Toll" in await svc.filter_label(truck_tolls, "en")


def _sheet(content: bytes) -> list[tuple]:
    return list(load_workbook(BytesIO(content)).active.values)


def _value(rows: list[tuple], label: str):
    """Value next to a summary label, or the cell values of a totals row."""
    return next(r for r in rows if label in r)


@pytest.mark.asyncio
async def test_every_excel_export_carries_the_summary_and_totals(db_session):
    truck = await vehicle(db_session, "tractor_unit")
    van = await vehicle(db_session, "van")
    await expense(db_session, truck, "10.00", date(2026, 3, 1), ExpenseCategory.TOLL)
    await expense(db_session, truck, "20.00", date(2026, 4, 1), ExpenseCategory.TOLL)
    await expense(db_session, truck, "60.00", date(2026, 3, 5), ExpenseCategory.FUEL, liters=Decimal("40"), km=1000)
    await expense(db_session, van, "30.00", date(2026, 4, 2), ExpenseCategory.FUEL, liters=Decimal("20"), km=1000)
    await maintenance(db_session, van, MaintenanceStatus.COMPLETED, date(2026, 3, 1), date(2026, 3, 2), "100.00")
    await maintenance(db_session, van, MaintenanceStatus.CANCELLED, date(2026, 3, 3), None, "999.00")
    svc = ReportService(db_session)

    rows = _sheet(await svc.export_expense_excel(locale="en"))
    assert "Summary" in [r[0] for r in rows]
    assert _value(rows, "Grand total")[1] == 120 and _value(rows, "Total transactions")[1] == 4
    assert _value(rows, "Categories")[1] == 2
    category_total, month_total = [r for r in rows if r[0] == "Total"]
    assert category_total[1:] == (120, 4, 1)  # amount, count, share 100 %
    assert month_total[1:] == (120, 4, 30)  # amount, count, average per transaction
    assert next(r for r in rows if r[0] == "Toll")[1:] == (30, 2, 0.25)
    assert [r[0] for r in rows if r[0] in ("2026-03", "2026-04")] == ["2026-03", "2026-04"]
    wb = load_workbook(BytesIO(await svc.export_expense_excel(locale="bg")))
    assert any(c.value == "Обобщение" for c in wb.active["A"])
    toll_share = next(r for r in wb.active.iter_rows() if r[0].value == "Пътна такса")[3]
    assert toll_share.number_format == "0.0%"

    rows = _sheet(await svc.export_tco_excel(locale="en"))
    assert _value(rows, "Fleet TCO")[1] == 220 and _value(rows, "Total vehicles")[1] == 2  # 120 expenses + 100 maintenance
    assert _value(rows, "Total")[8] == 220

    rows = _sheet(await svc.export_fuel_excel(locale="en"))
    assert _value(rows, "Total liters")[1] == 60 and _value(rows, "Fuel cost")[1] == 90
    # QA #9: same columns as the screen, including the average price per liter (cost / liters, to the cent)
    header = next(r for r in rows if any(c and "price" in str(c).lower() for c in r))
    assert header[6] == "Avg price / liter (EUR)"  # right after liters and cost, as on screen
    vehicle_rows = [r for r in rows if r[4] in (40, 20)]
    assert len(vehicle_rows) == 2 and all(Decimal(str(r[6])) == Decimal("1.5") for r in vehicle_rows)
    total = _value(rows, "Total")
    assert total[4:6] == (60, 90) and Decimal(str(total[6])) == Decimal("1.5") and total[7] == 2

    rows = _sheet(await svc.export_maintenance_excel(locale="en"))
    assert _value(rows, "Records")[1] == 2
    cost_rows = [r for r in rows if "Total cost (excl. cancelled)" in r]
    assert cost_rows[0][1] == 100 and cost_rows[-1][8] == 100  # summary and totals row ignore the cancelled 999


@pytest.mark.asyncio
async def test_fuel_export_average_price_is_rounded_to_cents(db_session):
    """QA #9: 100.00 EUR for 60 L is 1.6666... per liter; the sheet holds 1.67, like the screen."""
    truck = await vehicle(db_session, "tractor_unit")
    await expense(db_session, truck, "100.00", date(2026, 3, 1), ExpenseCategory.FUEL, liters=Decimal("60"), km=1000)
    svc = ReportService(db_session)
    rows = _sheet(await svc.export_fuel_excel(ReportFilters(vehicle_id=truck.id), locale="en"))
    row = next(r for r in rows if r[4] == 60)
    assert Decimal(str(row[6])) == Decimal("1.67")
    assert Decimal(str(_value(rows, "Total")[6])) == Decimal("1.67")
    bg = _sheet(await svc.export_fuel_excel(ReportFilters(vehicle_id=truck.id), locale="bg"))
    assert any(c == "Средна цена / литър (EUR)" for r in bg for c in r)
