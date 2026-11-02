"""Edge protections as pure ASGI middleware: security headers and the country restriction."""

import json
import logging

from app.config import settings

logger = logging.getLogger(__name__)

HEALTH_PATH = "/api/v1/health"

_BLOCKED_HTML = """<!DOCTYPE html><html lang="bg"><head><meta charset="utf-8"><title>403</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#1f2937">
<h1 style="font-size:1.25rem">Достъпът от вашата държава не е разрешен</h1>
<p>Access from your country is not allowed.</p></body></html>"""


def allowed_countries() -> set[str]:
    """ALLOWED_COUNTRIES as a set of upper-case ISO codes."""
    return {code.strip().upper() for code in settings.ALLOWED_COUNTRIES.split(",") if code.strip()}


def content_security_policy() -> str:
    """CSP for this app: everything from our own origin; uploaded files come from the S3 host.

    Alpine.js evaluates inline expressions and templates use inline scripts, hence 'unsafe-inline' and 'unsafe-eval'
    for scripts. The directives that matter most here are the ones that stop framing, plugins, base-tag and form
    hijacking.
    """
    s3 = ""
    if settings.MINIO_EXTERNAL_ENDPOINT or settings.MINIO_ENDPOINT:
        scheme = (
            "https"
            if (settings.MINIO_USE_SSL if settings.MINIO_EXTERNAL_USE_SSL is None else settings.MINIO_EXTERNAL_USE_SSL)
            else "http"
        )
        s3 = f" {scheme}://{settings.MINIO_EXTERNAL_ENDPOINT or settings.MINIO_ENDPOINT}"
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data: blob:{s3}; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        f"frame-src 'self'{s3}; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )


class SecurityHeadersMiddleware:
    """Adds the standard hardening headers to every HTTP response."""

    def __init__(self, app):
        self.app = app

    def _headers(self) -> list[tuple[bytes, bytes]]:
        headers = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
        ]
        if settings.APP_ENV == "production":
            headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
        if settings.CSP_MODE != "off":
            name = (
                b"content-security-policy-report-only"
                if settings.CSP_MODE == "report-only"
                else b"content-security-policy"
            )
            headers.append((name, content_security_policy().encode()))
        return headers

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                extra = []
                content_type = dict(message.get("headers", [])).get(b"content-type", b"")
                if (
                    content_type.startswith(b"text/html")
                    and b"cache-control" not in present
                    and not scope["path"].startswith("/static/")
                ):
                    # App pages hold live data and one-time forms: Back/forward must reload, not replay an old page.
                    extra.append((b"cache-control", b"no-store"))
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        *extra,
                        *(h for h in self._headers() if h[0] not in present),
                    ],
                }
            await send(message)

        await self.app(scope, receive, send_with_headers)


class GeoRestrictionMiddleware:
    """Only allow visitors from ALLOWED_COUNTRIES, using the ``CF-IPCountry`` header set by Cloudflare.

    GEO_MODE: ``off`` (ignore), ``log`` (only log what would be blocked: use it first to check the header arrives),
    ``enforce`` (answer 403). The health check is always allowed. A request without the header is allowed and
    logged: without Cloudflare in front there is no country to judge. Unknown (``XX``) and Tor (``T1``) traffic is
    blocked in enforce mode unless listed.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        mode = settings.GEO_MODE
        if scope["type"] != "http" or mode == "off" or scope.get("path") == HEALTH_PATH:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        country = headers.get("cf-ipcountry", "").strip().upper()
        if not country:
            logger.warning("geo: no CF-IPCountry header on %s (allowed)", scope.get("path"))
        elif country not in allowed_countries():
            if mode == "log":
                logger.warning("geo: would block %s on %s", country, scope.get("path"))
            else:
                logger.warning("geo: blocked %s on %s", country, scope.get("path"))
                await self._forbidden(scope, send)
                return
        await self.app(scope, receive, send)

    async def _forbidden(self, scope, send):
        if scope.get("path", "").startswith("/api/"):
            body, content_type = (
                json.dumps({"detail": "Access from your country is not allowed"}).encode(),
                b"application/json",
            )
        else:
            body, content_type = _BLOCKED_HTML.encode(), b"text/html; charset=utf-8"
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", content_type),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
