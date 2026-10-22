"""What a PaaS needs: plain database URLs, R2-compatible S3 settings, $PORT, migrations only on the web process."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from app.utils import s3
from app.utils.s3 import S3Client

ROOT = Path(__file__).resolve().parents[2]


def make(**env):
    # explicit values beat the real environment (the test run sets its own DATABASE_URL_SYNC)
    return Settings(_env_file=None, **{"DATABASE_URL_SYNC": "", **env})


@pytest.mark.parametrize(
    ("given", "async_url", "sync_url"),
    [
        (
            "postgresql://u:p@host:5432/db",
            "postgresql+asyncpg://u:p@host:5432/db",
            "postgresql+psycopg2://u:p@host:5432/db",
        ),
        ("postgres://u:p@host/db", "postgresql+asyncpg://u:p@host/db", "postgresql+psycopg2://u:p@host/db"),
        ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        (
            "postgresql://u:p@h/db?sslmode=require",
            "postgresql+asyncpg://u:p@h/db?ssl=require",
            "postgresql+psycopg2://u:p@h/db?sslmode=require",
        ),
    ],
)
def test_database_urls_are_derived_from_one_plain_url(given, async_url, sync_url):
    settings = make(DATABASE_URL=given)
    assert settings.DATABASE_URL == async_url and settings.DATABASE_URL_SYNC == sync_url


def test_an_explicit_sync_url_wins():
    settings = make(DATABASE_URL="postgresql://u:p@h/db", DATABASE_URL_SYNC="postgresql+psycopg2://x:y@other/db")
    assert settings.DATABASE_URL_SYNC == "postgresql+psycopg2://x:y@other/db"


def test_s3_client_is_r2_compatible(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "S3_REGION", "auto")
    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "acct.r2.cloudflarestorage.com")
    client = S3Client()
    config = client.client.meta.config
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"
    assert client.client.meta.region_name == "auto"


def test_a_bucket_that_cannot_be_created_does_not_crash(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio.example.com:9000")
    monkeypatch.setattr(s3, "_bucket_ready", False)

    class Denied:
        def head_bucket(self, **kw):
            raise RuntimeError("403")

        def create_bucket(self, **kw):
            raise RuntimeError("403")

    client = S3Client()
    client.client = Denied()
    client.ensure_bucket()  # logs a warning, does not raise
    assert s3._bucket_ready is False


def run_entrypoint(tmp_path, command, **env):
    """Run docker-entrypoint.sh with a fake `alembic` and report whether migrations ran."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    marker = tmp_path / "migrated"
    fake = bin_dir / "alembic"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    environment = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path), **env}
    result = subprocess.run(
        ["bash", str(ROOT / "docker-entrypoint.sh"), *command], env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return marker.exists()


def test_migrations_run_for_the_web_process_only(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "worker").mkdir()
    assert run_entrypoint(tmp_path / "web", ["sh", "-c", "echo exec uvicorn app.main:app"]) is True
    assert run_entrypoint(tmp_path / "worker", ["sh", "-c", "echo celery -A app.tasks.celery_app worker"]) is False


def test_run_migrations_variable_overrides_the_automatic_choice(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert run_entrypoint(tmp_path / "a", ["true"], RUN_MIGRATIONS="true") is True
    assert run_entrypoint(tmp_path / "b", ["sh", "-c", "echo uvicorn"], RUN_MIGRATIONS="false") is False


def test_dockerfile_uses_port_non_root_and_pinned_dependencies():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "--port ${PORT:-8000}" in dockerfile and "USER app" in dockerfile
    assert "-c requirements.txt" in dockerfile
    pins = [line for line in (ROOT / "requirements.txt").read_text().splitlines() if line and not line.startswith("#")]
    assert len(pins) > 50 and all("==" in line for line in pins)
    assert not any(line.lower().startswith(("pytest", "ruff", "mypy")) for line in pins)  # runtime only


@pytest.mark.asyncio
async def test_create_admin_is_idempotent_and_applies_the_password_policy(db_session):
    from app.services.errors import DomainError
    from scripts.create_superuser import create_admin

    user, created = await create_admin(
        db_session, username="boss-admin", email="boss@x.co", full_name="Boss", password="long-enough-pw"
    )
    assert created and user.role.value == "admin"
    again, created = await create_admin(
        db_session, username="boss-admin", email="other@x.co", full_name="X", password="another-long-pw"
    )
    assert not created and again.id == user.id and again.email == "boss@x.co"
    with pytest.raises(DomainError):
        await create_admin(db_session, username="weak-admin", email="w@x.co", full_name="W", password="short")
