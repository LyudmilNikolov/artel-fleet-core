"""Render monetary UI with Decimal inputs and JSON-safe localized chart labels."""

from decimal import Decimal
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from app.utils.formatting import money


@pytest.fixture
def templates():
    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    env.globals.update(
        _=lambda key, **kwargs: key,
        money=lambda value: money(value, "bg"),
        fuel_average=lambda cost, liters: Decimal(str(cost)) / Decimal(str(liters)) if liters else None,
        lang="bg",
        user=None,
        languages=[],
    )
    return env


def test_all_templates_parse(templates):
    for path in Path("app/templates").rglob("*.html"):
        templates.parse(path.read_text())


def test_dashboard_money_remains_exact(templates):
    rendered = templates.get_template("dashboard/partials/expense_chart.html").render(
        data={
            "period_months": 1,
            "total": Decimal("0.30"),
            "monthly": [{"month": "2026-10", "total": Decimal("0.30")}],
            "categories": [{"category": "fuel", "total": Decimal("0.30")}],
        }
    )
    assert "EUR" in rendered
    assert "0,30" in rendered
    assert 'const valuesExact = ["0.30"]' in rendered
    assert "valuesExact.map(Number)" in rendered
    assert "valuesDisplay[context.dataIndex]" in rendered


def test_expense_form_has_no_currency_control_only_eur(templates):
    """Only EUR is supported: no currency input or choices, the amount label names the currency."""
    text = Path("app/templates/expenses/form.html").read_text()
    assert 'name="currency"' not in text
    assert "for c in currencies" not in text
    assert "(EUR)" in text


def report_filter_context():
    """Variables the shared report filter bar needs (normally supplied by app/web/reports.py)."""
    from app.schemas.report import ReportFilters
    from app.vehicle_rules import LIST_TYPE_FILTERS
    from app.web.pickers import picker_state

    return {
        "filters": ReportFilters(),
        "picker": picker_state(None),
        "type_filters": LIST_TYPE_FILTERS,
        "filter_query": {},
        "filter_error": None,
        "vehicle_mismatch": False,
        "statuses": [],
    }


def test_report_templates_render_decimal_and_missing_purchase_price(templates):
    from types import SimpleNamespace

    context = {
        "user": SimpleNamespace(full_name="Example", role=SimpleNamespace(value="admin")),
        "start_date": None,
        "end_date": None,
        **report_filter_context(),
    }
    tco = templates.get_template("reports/tco.html").render(
        **context,
        data=[
            {
                "vehicle_id": "test",
                "license_plate": "123 ABC 01",
                "vehicle_type": "passenger_car",
                "year": None,
                "brand": "Brand",
                "model": "Model",
                "purchase_price": None,
                "total_expenses": Decimal("0.30"),
                "tco": Decimal("0.30"),
            }
        ],
    )
    assert "0,30 EUR" in tco
    assert "—" in tco
    fuel = templates.get_template("reports/fuel.html").render(
        **context,
        data=[
            {
                "vehicle_id": "test",
                "license_plate": "123 ABC 01",
                "vehicle_type": "passenger_car",
                "year": None,
                "brand": "Brand",
                "model": "Model",
                "total_liters": 2.0,
                "total_cost": Decimal("0.30"),
                "avg_price": Decimal("0.15"),
                "refuel_count": 1,
                "l_per_100km": None,
            }
        ],
    )
    assert "0,15 EUR" in fuel
    expense = templates.get_template("reports/expenses.html").render(
        **context,
        data={
            "grand_total": Decimal("0.30"),
            "total_count": 1,
            "by_category": [{"category": "fuel", "total": Decimal("0.30"), "count": 1}],
            "monthly": [{"month": "2026-10", "total": Decimal("0.30"), "count": 1}],
        },
    )
    assert 'const monthlyTotalsExact = ["0.30"]' in expense


def test_monthly_chart_accepts_full_iso_dates(templates):
    import json
    import re
    import shutil
    import subprocess
    from types import SimpleNamespace

    if not shutil.which("node"):
        pytest.skip("Node is required for chart JavaScript regression")
    rendered = templates.get_template("reports/expenses.html").render(
        user=SimpleNamespace(full_name="Example", role=SimpleNamespace(value="admin")),
        start_date=None,
        end_date=None,
        **report_filter_context(),
        data={
            "grand_total": Decimal("0.30"),
            "total_count": 1,
            "by_category": [],
            "monthly": [{"month": "2026-10-01", "total": Decimal("0.30"), "count": 1}],
        },
    )
    callback = re.search(r"labels: monthLabels.map\(l => \{(.*?)\}\),", rendered, re.S).group(1)
    script = "console.log(JSON.stringify(['2026-10-01','2026-10'].map(l => {" + callback + "})))"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    labels = json.loads(result.stdout)
    assert labels[0] == labels[1]
    assert all("Invalid" not in label for label in labels)


def test_seed_notifications_select_recipient_language():
    from app.models.notification import NotificationType
    from scripts.seed_data import notification_templates

    for kind in NotificationType:
        english = notification_templates("en", kind)
        bulgarian = notification_templates("bg", kind)
        assert english != bulgarian
        assert notification_templates("ru", kind) == bulgarian
        assert all(title and body for title, body in english + bulgarian)


def test_bulgarian_seed_document_renders_cyrillic(monkeypatch, tmp_path):
    import io

    from PIL import Image, ImageFont

    from app.models.document import DocumentType
    from scripts.seed_documents import generate_document_image

    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    font_path = next((path for path in candidates if path.exists()), None)
    if font_path is None:
        pytest.skip("Cyrillic test font is unavailable")
    original = ImageFont.truetype
    monkeypatch.setattr(ImageFont, "truetype", lambda path, size, **kwargs: original(str(font_path), size, **kwargs))
    font = original(str(font_path), 24)
    assert bytes(font.getmask("Ж")) != bytes(font.getmask("\ufffd"))
    image_bytes = generate_document_image(
        DocumentType.INVOICE,
        "Фактура за техническо обслужване",
        ["Сума: 123,45 EUR", "Регистрационен номер: 123 ABC 01"],
    )
    image = Image.open(io.BytesIO(image_bytes))
    assert image.size == (600, 800)
    image.save(tmp_path / "bulgarian-seed-document.png")
