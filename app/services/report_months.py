"""Month series for the expense charts."""

from datetime import date
from decimal import Decimal


def fill_months(rows: list[dict]) -> list[dict]:
    """``rows`` (``{"month": "YYYY-MM-DD", "total", "count"}``, ascending, only months with expenses) with a zero row
    for every month in between, so the chart's month axis has no gaps. The report table and exports keep the original."""
    if len(rows) < 2:
        return list(rows)
    found = {date.fromisoformat(row["month"]).replace(day=1): row for row in rows}
    current, last = min(found), max(found)
    filled = []
    while current <= last:
        filled.append(found.get(current) or {"month": current.isoformat(), "total": Decimal("0"), "count": 0})
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return filled
