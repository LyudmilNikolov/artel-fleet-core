"""Per-request context for the audit log: who acted and from where.

The middleware stores a mutable dict, so the user resolved later by an auth dependency is visible
wherever the request's database session flushes (even in a copied task context).
"""

from contextvars import ContextVar
from uuid import UUID

from app.utils.client_ip import client_ip

_context: ContextVar[dict | None] = ContextVar("audit_context", default=None)


def current_audit_context() -> dict:
    """The active request context; an empty one outside a request (Celery, scripts, tests)."""
    return _context.get() or {}


def set_audit_user(user_id: UUID | None) -> None:
    """Record the authenticated user of the current request."""
    context = _context.get()
    if context is not None:
        context["user_id"] = user_id


class AuditContextMiddleware:
    """Pure ASGI middleware that opens the audit context for each HTTP request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        client = scope.get("client")
        token = _context.set(
            {
                "user_id": None,
                "ip_address": client_ip(
                    {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in (scope.get("headers") or [])},
                    client[0] if client else None,
                ),
                "user_agent": (headers.get(b"user-agent") or b"").decode("latin-1")[:500] or None,
            }
        )
        try:
            await self.app(scope, receive, send)
        finally:
            _context.reset(token)
