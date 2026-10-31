"""Locale-explicit display helpers; monetary inputs stay decimal."""

from datetime import date, datetime
from decimal import Decimal

from babel.numbers import format_currency

from app.services.validity import to_local


def money(value: Decimal | int | str | None, lang: str = "bg") -> str:
    if value is None:
        return "—"
    locale = "en_GB" if lang == "en" else "bg_BG"
    return format_currency(Decimal(str(value)), "EUR", format="#,##0.00 ¤¤", locale=locale)


format_money = money


def format_local_date(value: date | datetime | str | None, lang: str = "bg") -> str:
    """A date as dd.mm.yyyy (bg) / dd/mm/yyyy (en). Timestamps are shown on the business-time-zone calendar.

    An ISO date string (as kept in JSON) is formatted too; any other text is shown as it is.
    """
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = to_local(value).date()
    return value.strftime("%d/%m/%Y" if lang == "en" else "%d.%m.%Y")


def format_local_datetime(value: datetime | None, lang: str = "bg", seconds: bool = False) -> str:
    """A stored UTC timestamp in business time, e.g. ``21.10.2026 14:05`` (``14:05:09`` with ``seconds``)."""
    if value is None:
        return "—"
    clock = "%H:%M:%S" if seconds else "%H:%M"
    return to_local(value).strftime(("%d/%m/%Y " if lang == "en" else "%d.%m.%Y ") + clock)
