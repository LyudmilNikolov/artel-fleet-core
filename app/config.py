"""Application configuration with Vault integration.

Priority for secret resolution:
1. Explicit environment variables (highest priority)
2. Vault secrets (injected into env if not already set)
3. Pydantic field defaults (non-sensitive values only)
"""

from pydantic_settings import BaseSettings

# Inject Vault secrets into environment BEFORE Settings instantiation.
# This ensures Pydantic Settings picks them up naturally via env vars.
from app.utils.vault import inject_vault_secrets_to_env

inject_vault_secrets_to_env()


class Settings(BaseSettings):
    # App (non-sensitive, safe defaults)
    APP_NAME: str = "FleetCore"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    DEFAULT_LANGUAGE: str = "bg"

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
    MINIO_USE_SSL: bool = False

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


settings = Settings()
