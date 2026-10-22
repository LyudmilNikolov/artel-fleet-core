#!/bin/bash
set -e

echo "=== FleetCore Entrypoint ==="

# Wait for Vault if VAULT_ADDR is configured
if [ -n "$VAULT_ADDR" ]; then
    echo "Waiting for Vault at ${VAULT_ADDR}..."
    for i in $(seq 1 30); do
        if curl -sf "${VAULT_ADDR}/v1/sys/health" >/dev/null 2>&1; then
            echo "Vault is ready."
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "WARNING: Vault not reachable after 30 attempts, continuing without Vault..."
        fi
        sleep 1
    done
fi

# If first argument is "seed", run seed script
if [ "$1" = "seed" ]; then
    echo "Running database migrations..."
    alembic upgrade head
    echo "Running seed data..."
    python scripts/seed_data.py
    exit 0
fi

# If first argument is "create-superuser", run superuser script
if [ "$1" = "create-superuser" ]; then
    echo "Running database migrations..."
    alembic upgrade head
    echo "Creating superuser..."
    python scripts/create_superuser.py
    exit 0
fi

# Only run migrations for the main app (uvicorn), not for celery workers
# RUN_MIGRATIONS=true/false overrides the automatic choice (web process: yes, workers: no)
if [ "${RUN_MIGRATIONS:-}" = "true" ] || { [ "${RUN_MIGRATIONS:-}" != "false" ] && echo "$*" | grep -q "uvicorn"; }; then
    echo "Running database migrations..."
    alembic upgrade head
fi

echo "Starting: $@"
exec "$@"
