"""The real client address, also when the app runs behind a proxy (Sevalla / Cloudflare).

Forwarded headers are only trusted when ``TRUST_PROXY`` is on, because anyone can send them directly to an app that
is reachable without a proxy.
"""

import ipaddress
from collections.abc import Mapping

from app.config import settings


def _valid(value: str | None) -> str | None:
    value = (value or "").strip()
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


def client_ip(headers: Mapping[str, str], peer: str | None, trust_proxy: bool | None = None) -> str | None:
    """The client IP: ``CF-Connecting-IP``, else the first ``X-Forwarded-For`` hop, else the socket peer.

    ``headers`` must have lowercase keys. Invalid values are ignored.
    """
    trust = settings.TRUST_PROXY if trust_proxy is None else trust_proxy
    if trust:
        forwarded = _valid(headers.get("cf-connecting-ip")) or _valid(
            (headers.get("x-forwarded-for") or "").split(",")[0]
        )
        if forwarded:
            return forwarded
    return _valid(peer) or peer


def request_ip(request) -> str | None:
    """Client IP of a Starlette request."""
    headers = {k.lower(): v for k, v in request.headers.items()}
    return client_ip(headers, request.client.host if request.client else None)
