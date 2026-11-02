"""Return to the requested page after signing in (QA #35).

``LoginNextMiddleware`` turns the redirect of a signed-out page load to ``/login`` into ``/login?next=<page>``;
the login form carries ``next`` along and ``safe_next`` accepts it only as a local path (no open redirect).
"""

from urllib.parse import quote, urlsplit

NO_NEXT_PREFIXES = ("/login", "/logout", "/static", "/api/", "/set-language")


def safe_next(value: str | None, default: str = "/") -> str:
    """``value`` when it is a plain local path, otherwise ``default``.

    Rejects exports, other hosts (``//host``, ``https://host``), backslash tricks, control characters and the login /
    logout pages themselves (a redirect loop).
    """
    if not value or len(value) > 2048 or not value.startswith("/") or value.startswith("//"):
        return default
    if "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return default
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.path.startswith(NO_NEXT_PREFIXES):
        return default
    if "/export" in parts.path:  # a download is not a page to land on after signing in
        return default
    return value


class LoginNextMiddleware:
    """Adds ``?next=`` to the redirect that sends a signed-out browser from a page to the login form."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "GET" or scope["path"].startswith(NO_NEXT_PREFIXES):
            await self.app(scope, receive, send)
            return
        if any(name == b"hx-request" for name, _ in scope["headers"]):  # a page fragment is not a place to return to
            await self.app(scope, receive, send)
            return
        target = scope["path"] + (f"?{scope['query_string'].decode('latin-1')}" if scope["query_string"] else "")
        wanted = safe_next(target, "")

        async def send_with_next(message):
            if message["type"] == "http.response.start" and wanted and message["status"] in (302, 303):
                login_with_next = b"/login?next=" + quote(wanted, safe="").encode()
                headers = [
                    (name, login_with_next if name == b"location" and value == b"/login" else value)
                    for name, value in message["headers"]
                ]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_next)
