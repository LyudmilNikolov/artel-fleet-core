"""One-time messages shown after a redirect (QA #36, #37).

A handler calls ``flash(request, "key")`` before redirecting; the next page rendered with ``base.html`` shows it as a
toast (``components/toast.html``) and the message is gone. Only the translation key and its parameters are stored in
the signed session, so the text follows the language of the reader when it is shown, not of the writer.
"""

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.i18n import _

SESSION_KEY = "flash"
KEEP = 3  # the session is a cookie: only the latest few messages are kept
KINDS = ("success", "error", "info")


def flash(request: Request, key: str, kind: str = "success", **params: str) -> None:
    """Queue ``key`` (a translation key) to be shown once on the next page."""
    kind = kind if kind in KINDS else "info"
    queue = [item for item in request.session.get(SESSION_KEY, []) if item[:2] != [kind, key]]
    queue.append([kind, key, {name: str(value) for name, value in params.items()}])
    request.session[SESSION_KEY] = queue[-KEEP:]


def take(request: Request, lang: str) -> list[dict]:
    """The queued messages as ``{"kind", "text"}`` in ``lang``; the queue is emptied."""
    queue = request.session.pop(SESSION_KEY, None) or []
    return [{"kind": kind, "text": _(key, lang=lang, **params)} for kind, key, params in queue]


def deny(request: Request, url: str = "/", status_code: int = 302) -> RedirectResponse:
    """Send a user away from a page or action their role does not allow, saying so."""
    flash(request, "errors.access_denied", "error")
    return RedirectResponse(url=url, status_code=status_code)
