"""Test fixtures for FleetCore."""

# ruff: noqa: E402 -- isolate environment before application settings are imported.

import base64
import json
import os
from uuid import uuid4

# Never inherit development database or Vault configuration in tests.
os.environ["VAULT_ADDR"] = ""
os.environ["VAULT_TOKEN"] = ""
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
if TEST_DATABASE_URL and "test" not in TEST_DATABASE_URL.rsplit("/", 1)[-1]:
    raise RuntimeError("TEST_DATABASE_URL must name a disposable test database")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL or "postgresql+asyncpg://test:test@localhost/fleetcore_test_unconfigured"
os.environ["DATABASE_URL_SYNC"] = os.environ.get("TEST_DATABASE_URL_SYNC") or os.environ["DATABASE_URL"].replace(
    "+asyncpg", "+psycopg2"
)
os.environ.setdefault("SECRET_KEY", "test-only-session-key")
# Never touch a development Redis from tests (the login rate limiter then stays in memory)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import get_db
from app.main import create_app
from app.models.user import User, UserRole
from app.utils.security import create_access_token, hash_password, password_fingerprint


@pytest_asyncio.fixture
async def db_session():
    """Create a test DB session with transaction rollback."""
    if not TEST_DATABASE_URL:
        pytest.skip("Set TEST_DATABASE_URL to an explicitly disposable PostgreSQL database")
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def app(db_session):
    """Create a test FastAPI app with overridden DB dependency."""
    application = create_app()

    async def override_get_db():
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    return application


def _session_cookie(client) -> str | None:
    """The newest session cookie of a test client (tolerates a manually set duplicate)."""
    values = [c.value for c in client.cookies.jar if c.name == "fleetcore_session"]
    return values[-1] if values else None


def _session_csrf_token(cookie_value: str | None) -> str | None:
    """Read the CSRF token out of the signed session cookie (the payload is plain base64 JSON)."""
    if not cookie_value:
        return None
    payload = cookie_value.split(".")[0]
    try:
        return json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4))).get("csrf")
    except Exception:
        return None


@pytest_asyncio.fixture
async def client(app):
    """Create an async HTTP test client. Like the browser script, it sends the CSRF header on unsafe web requests."""
    holder = {}

    async def add_csrf(request):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith("/api/"):
            token = _session_csrf_token(_session_cookie(holder["client"]))
            if token and "x-csrf-token" not in request.headers:
                request.headers["X-CSRF-Token"] = token

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        event_hooks={"request": [add_csrf]},
    ) as ac:
        holder["client"] = ac
        yield ac


@pytest.fixture(autouse=True)
def fresh_login_limiter():
    """Every test starts without failed-login counters."""
    from app.utils.rate_limit import reset_limiter

    reset_limiter()
    yield
    reset_limiter()


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """Create an admin user for testing."""
    user = User(
        id=uuid4(),
        email="testadmin@test.com",
        username="testadmin",
        full_name="Test Admin",
        hashed_password=hash_password("testpass123"),
        role=UserRole.ADMIN,
        is_active=True,
        language="en",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def fleet_manager_user(db_session: AsyncSession) -> User:
    """Create a fleet manager user for testing."""
    user = User(
        id=uuid4(),
        email="testmanager@test.com",
        username="testmanager",
        full_name="Test Manager",
        hashed_password=hash_password("testpass123"),
        role=UserRole.FLEET_MANAGER,
        is_active=True,
        language="en",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def viewer_user(db_session: AsyncSession) -> User:
    """Create a viewer user for testing."""
    user = User(
        id=uuid4(),
        email="testviewer@test.com",
        username="testviewer",
        full_name="Test Viewer",
        hashed_password=hash_password("testpass123"),
        role=UserRole.VIEWER,
        is_active=True,
        language="en",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
def admin_token(admin_user: User) -> str:
    """Get JWT token for admin user."""
    return create_access_token(admin_user.id, admin_user.role.value, password_fingerprint(admin_user.hashed_password))


@pytest.fixture
def manager_token(fleet_manager_user: User) -> str:
    """Get JWT token for fleet manager user."""
    return create_access_token(
        fleet_manager_user.id, fleet_manager_user.role.value, password_fingerprint(fleet_manager_user.hashed_password)
    )


@pytest.fixture
def viewer_token(viewer_user: User) -> str:
    """Get JWT token for viewer user."""
    return create_access_token(
        viewer_user.id, viewer_user.role.value, password_fingerprint(viewer_user.hashed_password)
    )


def auth_header(token: str) -> dict:
    """Return Authorization header."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def vehicle_departments(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "VEHICLE_DEPARTMENTS", ["Test department"])
