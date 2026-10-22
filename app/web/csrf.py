"""CSRF protection for the cookie-based web UI.

A random token lives in the signed session. Every state-changing web request from a logged-in browser must send it
back, either in the ``X-CSRF-Token`` header (HTMX / fetch) or in a ``csrf_token`` form field (plain forms; the field
is added by ``static/js/app.js``). The Bearer-token API is not affected. Anonymous requests have no session to abuse
and are exempt (the login form is protected by the rate limiter instead).
"""

import hmac
import secrets

from fastapi import HTTPException, Request

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


async def csrf_protect(request: Request) -> None:
    """Router dependency: reject unsafe requests of a logged-in session that lack the right token."""
    if request.method in SAFE_METHODS or not request.session.get("user_id"):
        return
    expected = request.session.get(SESSION_KEY) or ""
    supplied = request.headers.get(HEADER)
    if supplied is None:
        form = await request.form()  # cached by Starlette, handlers can read it again
        supplied = str(form.get(FIELD) or "")
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="errors.csrf")
