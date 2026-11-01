"""Field-level messages for rejected web forms.

Pydantic reports built-in problems (wrong length, too many decimals, out of range, not a date, ...) without a
message of ours. Each one is turned into a short localized sentence that names the rule, keyed by field, so a form
can show it under the field instead of one generic "Invalid input" banner.
"""

from pydantic import ValidationError

from app.i18n import _
from app.services.errors import render_error
from app.utils.formatting import format_number

MONEY_FIELDS = {"amount", "cost", "purchase_price", "fuel_price_per_liter"}
KNOWN_PREFIXES = ("error.", "errors.", "auth.")


def _limit(value, lang: str, field: str = "") -> str:
    """A limit as text; a year is never grouped ("2100", not "2,100")."""
    if field == "year":
        return str(int(value))
    return format_number(value, lang, 0 if float(value) == int(value) else 2)


def _describe(detail: dict, field: str, lang: str) -> str:
    kind, ctx = detail.get("type", ""), detail.get("ctx") or {}
    custom = ctx.get("error")
    if custom is not None and str(custom).startswith(KNOWN_PREFIXES):
        return render_error(str(custom), lang)
    if kind in ("string_too_short", "string_too_long") and field == "vin":
        return _("validation.vin_length", lang)
    if kind == "string_too_short":
        return _("validation.min_length", lang, min=ctx.get("min_length", 1))
    if kind == "string_too_long":
        return _("validation.max_length", lang, max=ctx.get("max_length", ""))
    if kind in ("decimal_max_places",):
        if field in MONEY_FIELDS:
            return _("validation.money_format", lang)
        return _("validation.max_decimals", lang, places=ctx.get("decimal_places", 2))
    if kind == "decimal_whole_digits":
        return _("validation.too_many_digits", lang, digits=ctx.get("whole_digits", ""))
    if kind == "decimal_max_digits":
        return _("validation.too_many_digits", lang, digits=ctx.get("max_digits", ""))
    if kind in ("greater_than_equal", "greater_than"):
        limit = ctx.get("ge", ctx.get("gt", 0))
        if kind == "greater_than" and float(limit) == 0:
            return _("validation.positive", lang)
        if float(limit) == 0:
            return _("validation.not_negative", lang)
        return _("validation.min_value", lang, limit=_limit(limit, lang, field))
    if kind in ("less_than_equal", "less_than"):
        return _("validation.max_value", lang, limit=_limit(ctx.get("le", ctx.get("lt", 0)), lang, field))
    if kind in ("int_parsing", "int_from_float", "int_type"):
        return _("validation.integer", lang)
    if kind in ("decimal_parsing", "decimal_type", "float_parsing", "float_type", "decimal_syntax"):
        return _("validation.number", lang)
    if kind.startswith("date") or kind in ("time_parsing",):
        return _("validation.date", lang)
    if kind == "missing":
        return _("validation.required", lang)
    if kind in ("enum", "literal_error"):
        return _("validation.choice", lang)
    return _("validation.invalid", lang)


def field_errors_from(exc: ValidationError, lang: str) -> dict[str, str]:
    """``{field: message}`` for the first problem of every field a form submitted."""
    errors: dict[str, str] = {}
    for detail in exc.errors():
        field = str(detail["loc"][0]) if detail["loc"] else "_form"
        errors.setdefault(field, _describe(detail, field, lang))
    return errors


def banner_for(exc: Exception, field_errors: dict[str, str], lang: str) -> str:
    """Top-of-form text: one pointer to the marked fields, or the single message of a non-field error."""
    if field_errors and "_form" not in field_errors:
        return _("validation.fix_fields", lang)
    if field_errors:
        return field_errors["_form"]
    return render_error(exc, lang)
