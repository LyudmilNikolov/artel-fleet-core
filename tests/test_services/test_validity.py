"""Shared validity helpers: computed status (incl. no expiry) and renewal proposals."""

from datetime import date

import pytest

from app.services.validity import add_months, renewal_dates, validity_status

TODAY = date(2026, 10, 21)


@pytest.mark.parametrize(
    ("end", "expected"),
    [
        (None, "no_expiry"),
        (date(2026, 10, 20), "expired"),
        (date(2026, 10, 21), "expiring_soon"),  # inclusive: expires today
        (date(2026, 11, 20), "expiring_soon"),  # today + 30 days
        (date(2026, 11, 21), "valid"),
    ],
)
def test_validity_status(end, expected):
    assert validity_status(end, TODAY, 30) == expected


def test_window_is_configurable():
    assert validity_status(date(2026, 12, 1), TODAY, 30) == "valid"
    assert validity_status(date(2026, 12, 1), TODAY, 60) == "expiring_soon"


def test_compliance_status_wrapper_is_unchanged():
    from app.services.compliance_service import compliance_status

    assert compliance_status(date(2026, 10, 20), TODAY, 30) == "expired"
    assert compliance_status(date(2026, 11, 21), TODAY, 30) == "valid"


def test_renewal_keeps_whole_month_alignment_and_day_length():
    assert renewal_dates(date(2025, 11, 1), date(2026, 10, 31)) == (date(2026, 11, 1), date(2027, 10, 31))
    assert renewal_dates(date(2026, 1, 1), date(2026, 1, 10)) == (date(2026, 1, 11), date(2026, 1, 20))


def test_add_months_clamps_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
