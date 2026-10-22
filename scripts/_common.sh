#!/usr/bin/env bash
# Shared settings for backup.sh, restore.sh and update.sh. Sourced, not run.
#
#   FLEET_ENV=production   use docker-compose.prod.yml and .env.production (set this on the server)
#   FLEET_COMPOSE_CMD      replace the whole docker compose command (tests, unusual setups)
#   BACKUP_DIR             where backups are written (default ./backups)
#   DB_USER / DB_NAME      database role and name (default fleetcore)

if [ -n "${FLEET_COMPOSE_CMD:-}" ]; then
    COMPOSE_CMD="$FLEET_COMPOSE_CMD"   # full override, for example a test stack with another project name
elif [ "${FLEET_ENV:-development}" = "production" ]; then
    COMPOSE_CMD="docker compose -f docker-compose.prod.yml --env-file ${FLEET_ENV_FILE:-.env.production}"
else
    COMPOSE_CMD="docker compose"
fi

ENV_FILE="${FLEET_ENV_FILE:-.env.production}"   # settings file of the production stack
BACKUP_DIR="${BACKUP_DIR:-./backups}"
DB_USER="${DB_USER:-fleetcore}"
DB_NAME="${DB_NAME:-fleetcore}"

compose() { $COMPOSE_CMD "$@"; }
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
