"""Production configuration must fail closed; passwords follow one policy."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.errors import DomainError
from app.utils.password_policy import MIN_LENGTH, validate_password

GOOD = dict(
    APP_ENV="production",
    APP_DEBUG=False,
    SECRET_KEY="k" * 40,
    DATABASE_URL="postgresql+asyncpg://u:p@db/fleet",
)


def make(**changes):
    return Settings(_env_file=None, **{**GOOD, **changes})


def test_a_complete_production_configuration_is_accepted():
    assert make().APP_ENV == "production"


@pytest.mark.parametrize(
    "bad",
    [
        {"SECRET_KEY": ""},
        {"SECRET_KEY": "short"},
        {"SECRET_KEY": "k" * 31},
        {"APP_DEBUG": True},
        {"DATABASE_URL": ""},
    ],
)
def test_production_refuses_to_start_with_weak_settings(bad):
    with pytest.raises(ValidationError):
        make(**bad)


def test_development_stays_flexible_and_debug_is_off_by_default():
    dev = Settings(_env_file=None, APP_ENV="development", SECRET_KEY="", DATABASE_URL="")
    assert dev.APP_DEBUG is False


@pytest.mark.parametrize("password", ["", "short", "123456789", "a" * 9, "x" * 129])
def test_password_policy_rejects(password):
    with pytest.raises(DomainError) as error:
        validate_password(password)
    assert error.value.key == "error.user_password_short"


def test_password_policy_accepts_ten_characters_and_rejects_the_username():
    assert MIN_LENGTH == 10
    validate_password("abcdefghij")
    validate_password("p" * 128)
    with pytest.raises(DomainError) as error:
        validate_password("fleetmanager", username="FleetManager")
    assert error.value.key == "error.password_is_username"


@pytest.mark.asyncio
@pytest.mark.parametrize(("debug", "expected"), [(False, 404), (True, 200)])
async def test_docs_and_schema_are_only_public_in_debug(monkeypatch, debug, expected):
    from httpx import ASGITransport, AsyncClient

    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "APP_DEBUG", debug)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/openapi.json", "/docs"):
            assert (await client.get(path)).status_code == expected, path
