#!/usr/bin/env bash
# Restore a backup made by backup.sh.
#
#   ./scripts/restore.sh --verify backups/fleetcore-db-auto-....dump
#       Restore into a throw-away database, print a few counts, drop it. Touches nothing else.
#       Use this regularly to prove that your backups can really be restored.
#
#   ./scripts/restore.sh --yes backups/fleetcore-db-auto-....dump [backups/fleetcore-files-auto-....tar.gz]
#       REPLACE the live database (and, if given, the uploaded files) with the backup.
#       The app, worker and beat are stopped during the restore and started again afterwards.
#       Take a fresh backup first (./scripts/backup.sh manual) unless the live data is already lost.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_common.sh
source scripts/_common.sh

MODE=""
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --verify) MODE="verify" ;;
        --yes) MODE="replace" ;;
        *) ARGS+=("$arg") ;;
    esac
done
[ -n "$MODE" ] || die "use --verify <dump> or --yes <dump> [files.tar.gz] (see the header of this script)"
DUMP="${ARGS[0]:-}"
FILES="${ARGS[1]:-}"
[ -f "$DUMP" ] || die "dump not found: $DUMP"
[ -z "$FILES" ] || [ -f "$FILES" ] || die "files archive not found: $FILES"

psql_admin() { compose exec -T db psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 "$@"; }
restore_into() { compose exec -T db pg_restore -U "$DB_USER" -d "$1" --no-owner --exit-on-error < "$DUMP"; }

if [ "$MODE" = "verify" ]; then
    SCRATCH="${DB_NAME}_verify_$(date -u +%H%M%S)"
    log "Restoring into scratch database $SCRATCH"
    psql_admin -c "CREATE DATABASE \"$SCRATCH\"" > /dev/null
    trap 'psql_admin -c "DROP DATABASE IF EXISTS \"$SCRATCH\"" > /dev/null' EXIT
    restore_into "$SCRATCH"
    log "Restored. Contents:"
    compose exec -T db psql -U "$DB_USER" -d "$SCRATCH" -At -F ' : ' -c "
        SELECT 'schema revision', version_num FROM alembic_version
        UNION ALL SELECT 'vehicles', count(*)::text FROM vehicles
        UNION ALL SELECT 'drivers', count(*)::text FROM drivers
        UNION ALL SELECT 'users', count(*)::text FROM users
        UNION ALL SELECT 'vehicle documents', count(*)::text FROM compliance_documents
        UNION ALL SELECT 'driver documents', count(*)::text FROM driver_documents
        UNION ALL SELECT 'audit entries', count(*)::text FROM audit_logs"
    log "The backup is restorable. Scratch database dropped."
    exit 0
fi

log "Stopping app, worker and beat"
compose stop app celery_worker celery_beat
log "Replacing database $DB_NAME"
psql_admin -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid()" > /dev/null
psql_admin -c "DROP DATABASE IF EXISTS \"$DB_NAME\"" > /dev/null
psql_admin -c "CREATE DATABASE \"$DB_NAME\"" > /dev/null
restore_into "$DB_NAME"

if [ -n "$FILES" ]; then
    log "Restoring uploaded files (existing files with the same name are overwritten)"
    MINIO_CONTAINER="$(compose ps -q minio)"
    [ -n "$MINIO_CONTAINER" ] || die "the minio container is not running"
    docker run --rm --volumes-from "$MINIO_CONTAINER" -v "$(cd "$(dirname "$FILES")" && pwd):/backup:ro" alpine:3.20 \
        tar xzf "/backup/$(basename "$FILES")" -C /data
fi

log "Starting app, worker and beat"
compose up -d app celery_worker celery_beat
log "Restore finished. Check the application, then run: ./scripts/backup.sh manual"
