"""Guard against a repeated form submit (QA #39).

The create form carries a random one-time token. After a successful save the token is remembered in the signed
session together with the saved record's URL; a second POST with the same token (browser Back to the old form, a
double click, a replayed request) is sent to that record instead of creating a duplicate. A form without a token
(API-style clients, older tests) is not guarded.
"""

import re
import secrets

from fastapi import Request

FIELD = "submit_token"
SESSION_KEY = "submitted"
KEEP = 8  # the session is a cookie: only the most recent saves are remembered
_VALID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def new_token() -> str:
    return secrets.token_urlsafe(16)


def token_for_form(submitted: str | None) -> str:
    """Keep the token of a re-rendered form (so a corrected resubmit is still one save), else issue a new one."""
    return submitted if submitted and _VALID.match(submitted) else new_token()


def saved_url(request: Request, token: str | None) -> str | None:
    """Where the earlier save with this token went, or ``None`` if the token is unused (or absent)."""
    if not token or not _VALID.match(token):
        return None
    return dict(request.session.get(SESSION_KEY, [])).get(token)


def remember(request: Request, token: str | None, url: str) -> None:
    """Record a successful save for ``token`` (ignored without a valid token)."""
    if not token or not _VALID.match(token):
        return
    saved = [pair for pair in request.session.get(SESSION_KEY, []) if pair[0] != token]
    saved.append([token, url])
    request.session[SESSION_KEY] = saved[-KEEP:]
