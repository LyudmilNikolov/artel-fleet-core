"""QA #25: every expense category has its own colour that reads on the light page and the dark card."""

import itertools
import math
from datetime import date
from decimal import Decimal

from app.chart_palette import CATEGORY_COLORS_DARK, CATEGORY_COLORS_LIGHT
from app.models.expense import ExpenseCategory
from app.services.report_months import fill_months

WHITE, DARK_CARD = "#ffffff", "#1f2937"


def _linear(channel: int) -> float:
    value = channel / 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _rgb(color: str) -> list[int]:
    return [int(color[i : i + 2], 16) for i in (1, 3, 5)]


def _luminance(color: str) -> float:
    r, g, b = (_linear(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _lab(color: str) -> tuple[float, float, float]:
    r, g, b = (_linear(c) for c in _rgb(color))
    x, y, z = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047, r * 0.2126 + g * 0.7152 + b * 0.0722, (
        r * 0.0193 + g * 0.1192 + b * 0.9505
    ) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def test_every_expense_category_has_a_colour_in_both_themes():
    categories = {c.value for c in ExpenseCategory}
    assert set(CATEGORY_COLORS_LIGHT) == categories == set(CATEGORY_COLORS_DARK)


def test_colours_are_distinct_and_readable_against_their_background():
    for palette, background in ((CATEGORY_COLORS_LIGHT, WHITE), (CATEGORY_COLORS_DARK, DARK_CARD)):
        assert len(set(palette.values())) == len(palette)
        for name, color in palette.items():
            assert _contrast(color, background) >= 3.0, (name, color)
        for (a, ca), (b, cb) in itertools.combinations(palette.items(), 2):
            assert math.dist(_lab(ca), _lab(cb)) >= 15, (a, b)  # perceptibly different (CIE76 distance)


def test_only_other_is_grey():
    for palette in (CATEGORY_COLORS_LIGHT, CATEGORY_COLORS_DARK):
        greys = [n for n, c in palette.items() if max(_rgb(c)) - min(_rgb(c)) < 40]
        assert greys == ["other"]


def row(month: str, total: str, count: int) -> dict:
    return {"month": month, "total": Decimal(total), "count": count}


def test_fill_months_adds_empty_months_between_the_first_and_last():
    rows = [row("2025-10-01", "10.50", 2), row("2026-02-01", "5.00", 1)]
    filled = fill_months(rows)
    assert [r["month"] for r in filled] == ["2025-10-01", "2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01"]
    assert filled[0] is rows[0] and filled[-1] is rows[1]
    assert filled[1] == row("2025-11-01", "0", 0) and filled[3]["total"] == Decimal("0")
    assert len(rows) == 2  # the table and exports keep the original rows


def test_fill_months_leaves_short_and_complete_series_alone():
    assert fill_months([]) == []
    single = [row("2026-03-01", "1", 1)]
    assert fill_months(single) == single
    full = [row("2026-11-01", "1", 1), row("2026-12-01", "2", 1), row("2027-01-01", "3", 1)]
    assert fill_months(full) == full
    assert date.fromisoformat(fill_months([row("2025-12-01", "1", 1), row("2026-02-01", "1", 1)])[1]["month"]) == date(
        2026, 1, 1
    )
