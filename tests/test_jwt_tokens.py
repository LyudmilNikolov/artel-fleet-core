"""Signed tokens: the HS256 behaviour the API relies on (PyJWT)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.config import settings
from app.utils.security import create_access_token, create_refresh_token, decode_token


def test_round_trip_carries_the_claims():
    user_id = uuid4()
    payload = decode_token(create_access_token(user_id, "admin", "marker"))
    assert payload["sub"] == str(user_id) and payload["role"] == "admin"
    assert payload["type"] == "access" and payload["pv"] == "marker"
    refresh = decode_token(create_refresh_token(user_id, "marker"))
    assert refresh["type"] == "refresh" and refresh["pv"] == "marker"


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
def test_malformed_tokens_are_rejected(token):
    assert decode_token(token) is None


def test_expired_and_tampered_tokens_are_rejected():
    expired = jwt.encode(
        {"sub": "x", "type": "access", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    assert decode_token(expired) is None
    good = create_access_token(uuid4(), "viewer", "m")
    assert decode_token(good[:-2] + ("AA" if not good.endswith("AA") else "BB")) is None
    assert decode_token(jwt.encode({"sub": "x", "exp": datetime.now(UTC) + timedelta(hours=1)}, "another-secret-key-of-32-chars!!", algorithm="HS256")) is None


def test_the_none_algorithm_and_other_algorithms_are_not_accepted():
    unsigned = jwt.encode({"sub": "x", "type": "access", "exp": datetime.now(UTC) + timedelta(hours=1)}, None, algorithm="none")
    assert decode_token(unsigned) is None
    other = jwt.encode(
        {"sub": "x", "type": "access", "exp": datetime.now(UTC) + timedelta(hours=1)}, settings.SECRET_KEY, algorithm="HS512"
    )
    assert decode_token(other) is None
