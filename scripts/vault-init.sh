#!/bin/sh
set -e

echo "=== FleetCore Vault Initialization ==="

# Wait for Vault to be ready
until vault status >/dev/null 2>&1; do
    echo "Waiting for Vault..."
    sleep 1
done

echo "Vault is ready. Seeding secrets..."

# Keep the existing application secret key when re-seeding (so logged-in sessions survive), else generate one
SECRET_KEY=$(vault kv get -field=SECRET_KEY secret/fleetcore 2>/dev/null || true)
if [ -z "$SECRET_KEY" ]; then
    SECRET_KEY=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 64)
fi

# Write all FleetCore secrets to Vault KV v2
#
# Note: MinIO uses its factory defaults when MINIO_ROOT_USER/MINIO_ROOT_PASSWORD
# are not set in docker-compose. The values here must match what MinIO expects.
# In production, configure MinIO with custom credentials via Vault's dynamic
# secrets engine or operator configuration.
#
# PostgreSQL uses trust authentication for local dev (no password needed).
# In production, use Vault's database secrets engine for dynamic credentials.

vault kv put secret/fleetcore \
    DATABASE_URL="postgresql+asyncpg://fleetcore@db:5432/fleetcore" \
    DATABASE_URL_SYNC="postgresql+psycopg2://fleetcore@db:5432/fleetcore" \
    REDIS_URL="redis://redis:6379/0" \
    SECRET_KEY="${SECRET_KEY}" \
    MINIO_ENDPOINT="minio:9000" \
    MINIO_ACCESS_KEY="minioadmin" \
    MINIO_SECRET_KEY="minioadmin" \
    MINIO_BUCKET="fleetcore" \
    MINIO_USE_SSL="false" \
    SMTP_HOST="${SMTP_HOST:-}" \
    SMTP_PORT="${SMTP_PORT:-587}" \
    SMTP_USER="${SMTP_USER:-}" \
    SMTP_PASSWORD="${SMTP_PASSWORD:-}" \
    SMTP_FROM="${SMTP_FROM:-noreply@fleetcore.local}" \
    TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}" \
    TELEGRAM_CHAT_ID=""

echo "Vault secrets initialized successfully."
echo "Generated SECRET_KEY: ${SECRET_KEY:0:8}..."
