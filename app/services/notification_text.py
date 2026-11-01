"""Text of a stored in-app notification in the reader's language.

A notification stores its text as written (kept for old rows and as a fallback) and, for newer rows, the
translation keys and their values. ``params`` values that are ``{"t": "<key>"}`` are translated at display time
(for example a document type); a ``note`` (free text from a person) is appended as it was typed.
"""

from app.i18n import _


def _values(params: dict | None, lang: str) -> dict:
    values = {}
    for name, value in (params or {}).items():
        values[name] = _(value["t"], lang) if isinstance(value, dict) and "t" in value else value
    return values


def notification_text(notification, lang: str) -> tuple[str, str]:
    """``(title, message)`` for ``lang``; rows saved without keys keep the text they were stored with."""
    if not notification.title_key and not notification.message_key:
        return notification.title, notification.message
    values = _values(notification.params, lang)
    title = _(notification.title_key, lang, **values) if notification.title_key else notification.title
    message = _(notification.message_key, lang, **values) if notification.message_key else notification.message
    if values.get("note"):
        message += " " + str(values["note"])
    return title, message
