"""Locale-independent service errors, rendered only at application boundaries."""

from typing import Any


class DomainError(ValueError):
    def __init__(self, key: str, **params: Any):
        self.key = key
        self.params = params
        super().__init__(key)

    def as_detail(self) -> dict:
        return {"key": self.key, "params": self.params}


def error_detail(error: Exception) -> dict:
    if isinstance(error, DomainError):
        return error.as_detail()
    return {"key": "errors.invalid_input", "params": {}}


def render_error(error: Any, lang: str = "bg") -> str:
    from app.i18n import _

    if isinstance(error, DomainError):
        return _(error.key, lang, **error.params)
    if isinstance(error, dict) and "key" in error:
        return _(error["key"], lang, **error.get("params", {}))
    key = str(error)
    # Only known application message keys may pass this boundary; never leak dumps.
    if key.startswith(("error.", "errors.", "auth.")):
        return _(key, lang)
    return _("errors.invalid_input", lang)


class MileageBelowCurrentError(DomainError):
    """A reading is lower than the vehicle's current baseline and was not confirmed as an overwrite."""

    def __init__(self, value: int, previous: int):
        self.value = value
        self.previous = previous
        super().__init__("error.mileage_below_current", value=value, previous=previous)
