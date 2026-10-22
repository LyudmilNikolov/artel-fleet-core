"""The real client address behind a proxy: forwarded headers count only when TRUST_PROXY is on."""

import pytest

from app.utils.client_ip import client_ip


@pytest.mark.parametrize(
    ("headers", "peer", "trust", "expected"),
    [
        ({}, "10.0.0.5", False, "10.0.0.5"),
        ({"x-forwarded-for": "203.0.113.9"}, "10.0.0.5", False, "10.0.0.5"),  # spoofable: ignored without a proxy
        ({"cf-connecting-ip": "203.0.113.9"}, "10.0.0.5", False, "10.0.0.5"),
        ({"x-forwarded-for": "203.0.113.9, 10.0.0.1"}, "10.0.0.5", True, "203.0.113.9"),
        ({"cf-connecting-ip": "198.51.100.7", "x-forwarded-for": "203.0.113.9"}, "10.0.0.5", True, "198.51.100.7"),
        ({"x-forwarded-for": "not-an-ip"}, "10.0.0.5", True, "10.0.0.5"),
        ({"x-forwarded-for": "2001:db8::1"}, "10.0.0.5", True, "2001:db8::1"),
        ({}, None, True, None),
    ],
)
def test_client_ip(headers, peer, trust, expected):
    assert client_ip(headers, peer, trust_proxy=trust) == expected
