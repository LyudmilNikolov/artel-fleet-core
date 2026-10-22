import csv
from decimal import Decimal
from io import BytesIO, StringIO
from unittest.mock import AsyncMock

import pytest
from openpyxl import load_workbook

from app.services.report_service import ReportService


@pytest.mark.asyncio
async def test_export_status_and_category_labels_are_localized():
    service = ReportService(None)
    service.maintenance_history = AsyncMock(
        return_value=[
            {
                "license_plate": "KEEP",
                "vehicle_type": "forklift",
                "brand": "Brand",
                "model": "Model",
                "type": "repair",
                "title": "User content",
                "status": "completed",
                "scheduled_date": "2026-01-02",
                "completed_date": "2026-01-03",
                "cost": Decimal("0.30"),
                "service_provider": "Vendor",
            }
        ]
    )
    service.expense_analysis = AsyncMock(
        return_value={"by_category": [{"category": "fuel", "total": Decimal("0.30"), "count": 2}]}
    )
    for locale, expected_status, expected_category, expected_fuel in [
        ("bg", "Приключено", "Категория", "Гориво"),
        ("en", "Completed", "Category", "Fuel"),
    ]:
        wb = load_workbook(BytesIO(await service.export_maintenance_excel(locale=locale)))
        rows = list(wb.active.values)
        row = next(row for row in rows if row[0] == "KEEP")
        assert row[5] == expected_status
        assert row[4] == "User content"
        assert row[6] == "2026-01-02"
        assert row[8] == 0.3
        output = await service.export_expense_csv(locale=locale)
        assert output.startswith(b"\xef\xbb\xbf")
        csv_rows = list(csv.reader(StringIO(output.decode("utf-8-sig"))))
        assert csv_rows[0][0] == expected_category
        assert "EUR" in csv_rows[0][1]
        assert csv_rows[1] == [expected_fuel, "0.30", "2"]
