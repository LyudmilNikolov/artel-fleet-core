import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
import jwt

from app.config import settings

BCRYPT_MAX_BYTES = 72


def _password_bytes(password: str) -> bytes:
    """bcrypt only uses the first 72 bytes; truncate explicitly, as bcrypt 5 raises instead of truncating."""
    return password.encode()[:BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(plain_password), hashed_password.encode())
    except ValueError:
        return False


def password_fingerprint(hashed_password: str) -> str:
    """A short, non-reversible marker of the current password hash. Sessions and tokens carry it, so changing the
    password invalidates all of them without any extra storage."""
    return hashlib.sha256(("fleetcore-pv|" + (hashed_password or "")).encode()).hexdigest()[:16]


def create_access_token(user_id: UUID, role: str, pv: str | None = None) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "pv": pv,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(user_id: UUID, pv: str | None = None) -> str:
    expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "pv": pv,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None
