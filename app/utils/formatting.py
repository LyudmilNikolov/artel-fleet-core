"""Locale-explicit display helpers; monetary inputs stay decimal."""

from decimal import Decimal

from babel.numbers import format_currency


def money(value: Decimal | int | str | None, lang: str = "bg") -> str:
    if value is None:
        return "—"
    locale = "en_GB" if lang == "en" else "bg_BG"
    return format_currency(Decimal(str(value)), "EUR", format="#,##0.00 ¤¤", locale=locale)


format_money = money
