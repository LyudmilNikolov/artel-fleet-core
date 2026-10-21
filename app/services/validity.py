"""Date-based validity rules shared by vehicle compliance documents and driver documents.

Status is computed from an inclusive end date and never stored. A missing end date means
the document does not expire.
"""

from calendar import monthrange
from datetime import UTC, date, datetime, timedelta


def utc_today() -> date:
    """Today's date in UTC (all dates are UTC by convention)."""
    return datetime.now(UTC).date()


def validity_status(end_date: date | None, today: date, window: int) -> str:
    """no_expiry / expired / expiring_soon / valid from an inclusive end date and a window in days."""
    if end_date is None:
        return "no_expiry"
    if end_date < today:
        return "expired"
    if end_date <= today + timedelta(days=window):
        return "expiring_soon"
    return "valid"


def add_months(value: date, months: int) -> date:
    """Add calendar months, clamping the day to the target month's length."""
    year, month = divmod(value.month - 1 + months, 12)
    year += value.year
    month += 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def renewal_dates(start: date, end: date) -> tuple[date, date]:
    """Propose the next period: start the day after ``end`` and keep the same length.

    Whole-month periods (e.g. one year ending the day before the anniversary) keep calendar
    alignment across leap years; other periods keep their length in days.
    """
    new_start = end + timedelta(days=1)
    months = (new_start.year - start.year) * 12 + new_start.month - start.month
    if months > 0 and add_months(start, months) == new_start:
        return new_start, add_months(new_start, months) - timedelta(days=1)
    return new_start, new_start + (end - start)
