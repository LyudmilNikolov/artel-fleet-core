"""Failed logins are rate limited (web and API); a missing user costs the same as a wrong password."""

import pytest

from app.config import settings
from app.utils import rate_limit
from app.utils.rate_limit import LoginLimiter, MemoryStore

BAD = {"username": "testadmin", "password": "wrong-password-1"}
GOOD = {"username": "testadmin", "password": "testpass123"}


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rate_limit, "limiter", LoginLimiter(MemoryStore(clock=lambda: now[0])))
    return now


@pytest.mark.asyncio
async def test_web_login_locks_after_five_failures_even_for_the_right_password(client, admin_user, clock):
    for _ in range(5):
        assert (await client.post("/login", data=BAD)).status_code == 401
    blocked = await client.post("/login?lang=en", data=GOOD)
    assert blocked.status_code == 429 and "Too many failed attempts" in blocked.text
    assert int(blocked.headers["retry-after"]) > 0
    clock[0] += 15 * 60 + 1  # the window passes
    assert (await client.post("/login", data=GOOD)).status_code == 302


@pytest.mark.asyncio
async def test_a_good_login_clears_the_counter_and_other_users_are_unaffected(client, admin_user, viewer_user, clock):
    for _ in range(4):
        await client.post("/login", data=BAD)
    assert (await client.post("/login", data=GOOD)).status_code == 302  # success resets the username counter
    await client.post("/logout")
    for _ in range(4):
        await client.post("/login", data=BAD)
    assert (await client.post("/login", data={"username": "testviewer", "password": "testpass123"})).status_code == 302


@pytest.mark.asyncio
async def test_unknown_usernames_are_counted_too_and_per_ip_limit_applies(client, clock):
    for n in range(30):
        assert (
            await client.post("/login", data={"username": f"ghost{n}", "password": "irrelevant-pw"})
        ).status_code == 401
    assert (await client.post("/login", data={"username": "ghost-new", "password": "irrelevant-pw"})).status_code == 429


@pytest.mark.asyncio
async def test_the_limit_is_per_client_address_behind_a_trusted_proxy(client, admin_user, clock, monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    attacker = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(5):
        await client.post("/login", data=BAD, headers=attacker)
    assert (await client.post("/login", data=GOOD, headers=attacker)).status_code == 429
    assert (await client.post("/login", data=GOOD, headers={"X-Forwarded-For": "198.51.100.4"})).status_code == 302


@pytest.mark.asyncio
async def test_api_login_is_limited_with_retry_after(client, admin_user, clock):
    for _ in range(5):
        assert (await client.post("/api/v1/auth/login", json=BAD)).status_code == 401
    blocked = await client.post("/api/v1/auth/login", json=GOOD, headers={"Accept-Language": "en"})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert "Too many failed attempts" in blocked.json()["detail"]
    clock[0] += 15 * 60 + 1
    assert (await client.post("/api/v1/auth/login", json=GOOD)).status_code == 200


@pytest.mark.asyncio
async def test_unknown_user_still_runs_a_password_check(monkeypatch, db_session):
    from app.services import auth_service
    from app.services.errors import DomainError

    calls = []
    monkeypatch.setattr(auth_service, "verify_password", lambda plain, hashed: calls.append(hashed) or False)
    with pytest.raises(DomainError):
        await auth_service.AuthService(db_session).authenticate("nobody-at-all", "whatever-password")
    assert calls == [auth_service._DUMMY_HASH]
