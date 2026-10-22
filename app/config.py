"""Application configuration with Vault integration.

Priority for secret resolution:
1. Explicit environment variables (highest priority)
2. Vault secrets (injected into env if not already set)
3. Pydantic field defaults (non-sensitive values only)
"""

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings

# Inject Vault secrets into environment BEFORE Settings instantiation.
# This ensures Pydantic Settings picks them up naturally via env vars.
from app.utils.vault import inject_vault_secrets_to_env

inject_vault_secrets_to_env()


class Settings(BaseSettings):
    # App (non-sensitive, safe defaults)
    APP_NAME: str = "FleetCore"
    # Release version: set at image build time (--build-arg APP_VERSION=v1.2.0), "dev" otherwise
    APP_VERSION: str = "dev"
    APP_ENV: str = "development"
    # Country restriction (needs Cloudflare's CF-IPCountry header): off | log (only log) | enforce (403)
    GEO_MODE: Literal["off", "log", "enforce"] = "off"
    ALLOWED_COUNTRIES: str = "BG"  # comma-separated ISO codes
    # Content-Security-Policy: enforce | report-only | off
    CSP_MODE: Literal["off", "report-only", "enforce"] = "enforce"

    # Behind a proxy (Sevalla/Cloudflare): trust CF-Connecting-IP / X-Forwarded-For for the client address
    TRUST_PROXY: bool = False

    # Debug exposes /docs, logs SQL and must never be on in production (enforced below)
    APP_DEBUG: bool = False
    DEFAULT_LANGUAGE: str = "bg"

    # Public address of the web UI, used for links in emails and Telegram messages
    APP_BASE_URL: str = "http://localhost:8000"

    # Business time zone: "today", days left and displayed times (storage stays UTC)
    APP_TIMEZONE: str = "Europe/Sofia"

    VEHICLE_DEPARTMENTS: list[str] = []

    # Compliance documents: days before end_date when a current entry counts as "expiring soon"
    COMPLIANCE_EXPIRING_SOON_DAYS: int = 30

    # Scheduled maintenance counts as "upcoming" when its scheduled date is within this many days
    MAINTENANCE_UPCOMING_DAYS: int = 14

    # Planned service items: a due km / date counts as "due soon" within these windows
    SERVICE_DUE_SOON_KM: int = 10000
    SERVICE_DUE_SOON_DAYS: int = 30

    # Driver documents: days before end_date when a current entry counts as "expiring soon"
    DRIVER_DOCUMENT_EXPIRING_SOON_DAYS: int = 30

    # Database (no defaults — must come from Vault or env)
    DATABASE_URL: str = ""
    DATABASE_URL_SYNC: str = ""

    # Redis
    REDIS_URL: str = ""

    # Security (no default for SECRET_KEY)
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # MinIO / S3
    MINIO_ENDPOINT: str = ""
    MINIO_EXTERNAL_ENDPOINT: str = ""  # Browser-accessible endpoint (e.g. localhost:9000)
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "fleetcore"
    S3_REGION: str = "us-east-1"  # Cloudflare R2 accepts "auto" as well
    MINIO_USE_SSL: bool = False
    # https for the browser-facing download links when the public address uses TLS (inside Docker MinIO stays http);
    # unset = same as MINIO_USE_SSL
    MINIO_EXTERNAL_USE_SSL: bool | None = None

    # SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@fleetcore.local"

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Pagination (non-sensitive)
    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 200

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def _normalise_database_urls(self):
        """Accept the plain ``postgresql://`` URL a platform hands out and derive the async and sync forms."""
        url = self.DATABASE_URL
        if url:
            for prefix in ("postgres://", "postgresql://"):
                if url.startswith(prefix):
                    url = "postgresql+asyncpg://" + url[len(prefix) :]
            # asyncpg spells the TLS option ssl=, psycopg2 spells it sslmode=
            async_url = url.replace("sslmode=", "ssl=") if "+asyncpg" in url else url
            self.DATABASE_URL = async_url
            if not self.DATABASE_URL_SYNC:
                self.DATABASE_URL_SYNC = async_url.replace("+asyncpg", "+psycopg2").replace("ssl=", "sslmode=")
        elif self.DATABASE_URL_SYNC.startswith(("postgres://", "postgresql://")):
            self.DATABASE_URL_SYNC = "postgresql+psycopg2://" + self.DATABASE_URL_SYNC.split("://", 1)[1]
        return self

    @model_validator(mode="after")
    def _production_must_fail_closed(self):
        """In production refuse to start with a weak or missing signing key, debug on, or no database."""
        if self.APP_ENV == "production":
            problems = []
            if len(self.SECRET_KEY) < 32:
                problems.append("SECRET_KEY must be at least 32 characters")
            if self.APP_DEBUG:
                problems.append("APP_DEBUG must be false")
            if not self.DATABASE_URL:
                problems.append("DATABASE_URL must be set")
            if self.GEO_MODE == "enforce" and not self.ALLOWED_COUNTRIES.strip():
                problems.append("ALLOWED_COUNTRIES must not be empty when GEO_MODE=enforce")
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self


settings = Settings()
