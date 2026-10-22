"""Login brute-force protection: failed attempts are counted per user+IP and per IP in a sliding fixed window.

Counters live in Redis when ``REDIS_URL`` is reachable (shared by all instances), otherwise in process memory
(development and tests). Any Redis problem degrades to memory instead of locking everybody out or crashing a login.
"""

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)

USER_LIMIT = 5  # failed attempts per username + IP
IP_LIMIT = 30  # failed attempts per IP
WINDOW_SECONDS = 15 * 60


class RateLimitedError(Exception):
    """Too many failed logins; ``retry_after`` is in seconds."""

    def __init__(self, retry_after: int):
        self.retry_after = max(1, int(retry_after))
        super().__init__(f"rate limited for {self.retry_after}s")


@dataclass
class _Entry:
    count: int
    expires: float


class MemoryStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._data: dict[str, _Entry] = {}
        self._clock = clock

    def get(self, key: str) -> tuple[int, int]:
        entry = self._data.get(key)
        if not entry or entry.expires <= self._clock():
            self._data.pop(key, None)
            return 0, 0
        return entry.count, int(entry.expires - self._clock()) + 1

    def incr(self, key: str, window: int) -> int:
        now = self._clock()
        entry = self._data.get(key)
        if not entry or entry.expires <= now:
            entry = _Entry(0, now + window)
        entry.count += 1
        self._data[key] = entry
        return entry.count

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


class RedisStore:
    """Synchronous Redis counters (cheap calls on a login path). Falls back to memory on any error."""

    def __init__(self, url: str, fallback: MemoryStore):
        import redis

        self._client = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
        self._fallback = fallback

    def get(self, key: str) -> tuple[int, int]:
        try:
            value = self._client.get(key)
            return (int(value), max(self._client.ttl(key), 1)) if value else (0, 0)
        except Exception:
            logger.warning("rate limiter: redis unavailable, using memory")
            return self._fallback.get(key)

    def incr(self, key: str, window: int) -> int:
        try:
            count = int(self._client.incr(key))
            if count == 1:
                self._client.expire(key, window)
            return count
        except Exception:
            return self._fallback.incr(key, window)

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception:
            self._fallback.delete(key)

    def clear(self) -> None:
        self._fallback.clear()


class LoginLimiter:
    def __init__(self, store, user_limit: int = USER_LIMIT, ip_limit: int = IP_LIMIT, window: int = WINDOW_SECONDS):
        self.store, self.user_limit, self.ip_limit, self.window = store, user_limit, ip_limit, window

    @staticmethod
    def _keys(ip: str | None, username: str) -> tuple[str, str]:
        ip = ip or "unknown"
        digest = hashlib.sha256(f"{ip}|{username.strip().casefold()}".encode()).hexdigest()[:24]
        return f"login:fail:user:{digest}", f"login:fail:ip:{ip}"

    def check(self, ip: str | None, username: str) -> None:
        """Raise RateLimitedError when this username+IP or this IP already failed too often."""
        user_key, ip_key = self._keys(ip, username)
        for key, limit in ((user_key, self.user_limit), (ip_key, self.ip_limit)):
            count, ttl = self.store.get(key)
            if count >= limit:
                raise RateLimitedError(ttl or self.window)

    def failure(self, ip: str | None, username: str) -> None:
        for key in self._keys(ip, username):
            self.store.incr(key, self.window)

    def success(self, ip: str | None, username: str) -> None:
        """A good login clears the username+IP counter (the IP counter keeps counting)."""
        self.store.delete(self._keys(ip, username)[0])


_memory = MemoryStore()
limiter = LoginLimiter(_memory)


def configure_limiter() -> None:
    """Use Redis when configured (called at startup); otherwise keep the in-memory store."""
    global limiter
    if settings.REDIS_URL:
        try:
            limiter = LoginLimiter(RedisStore(settings.REDIS_URL, _memory))
        except Exception:
            logger.warning("rate limiter: could not set up redis, using memory")


def reset_limiter() -> None:
    """Forget all counters (tests)."""
    _memory.clear()
