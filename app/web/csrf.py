"""CSRF protection for the cookie-based web UI.

A random token lives in the signed session. Every state-changing web request from a logged-in browser must send it
back, either in the ``X-CSRF-Token`` header (HTMX / fetch) or in a ``csrf_token`` form field (plain forms; the field
is added by ``static/js/app.js``). The Bearer-token API is not affected. Anonymous requests have no session to abuse
and are exempt (the login form is protected by the rate limiter instead).
"""

import hmac
import secrets

from fastapi import HTTPException, Request
from starlette.datastructures import FormData

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
SESSION_KEY = "csrf"
HEADER = "x-csrf-token"
FIELD = "csrf_token"


def get_token(request: Request) -> str:
    """The session's CSRF token, created on first use."""
    token = request.session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_KEY] = token
    return token


def rotate_token(request: Request) -> str:
    request.session[SESSION_KEY] = secrets.token_urlsafe(32)
    return request.session[SESSION_KEY]


# The login form needs the user's credentials and starts a brand-new session, so a forged request gains nothing; a
# stale login tab must still work while the browser is signed in (the session token has since been replaced).
EXEMPT_PATHS = {"/login"}

FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


async def csrf_protect(request: Request) -> None:
    """Router dependency: reject unsafe requests of a logged-in session that lack the right token.

    The token field is removed from the parsed form afterwards: handlers pass the submitted fields to schemas that
    forbid unknown fields, so the field must never reach them.
    """
    if request.method in SAFE_METHODS or request.url.path in EXEMPT_PATHS or not request.session.get("user_id"):
        return
    form = None
    if request.headers.get("content-type", "").lower().startswith(FORM_TYPES):
        form = await request.form()  # cached by Starlette, handlers read the same (cleaned) form
    expected = request.session.get(SESSION_KEY) or ""
    supplied = request.headers.get(HEADER)
    if supplied is None:
        supplied = str(form.get(FIELD) or "") if form is not None else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="errors.csrf")
    if form is not None and FIELD in form:
        request._form = FormData([(key, value) for key, value in form.multi_items() if key != FIELD])
