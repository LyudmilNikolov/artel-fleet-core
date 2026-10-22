#!/usr/bin/env bash
# Back up the database (pg_dump) and the uploaded files (MinIO volume).
#
#   ./scripts/backup.sh [auto|pre-update|manual]      (default: manual)
#
#   auto         the scheduled backup (cron); old ones are pruned automatically
#   pre-update   taken by update.sh before a release
#   manual       taken by hand; never pruned
#
# Writes two files that share a timestamp into $BACKUP_DIR:
#   fleetcore-db-<label>-<stamp>.dump        PostgreSQL custom-format dump (compressed)
#   fleetcore-files-<label>-<stamp>.tar.gz   uploaded documents and photos
# The dump is checked for readability before it is kept. Optional: OFFSITE_COMMAND is run afterwards with
# BACKUP_DIR, DB_FILE and FILES_FILE exported (for example: rclone copy "$BACKUP_DIR" remote:fleetcore).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_common.sh
source scripts/_common.sh

LABEL="${1:-manual}"
case "$LABEL" in auto | pre-update | manual) ;; *) die "label must be auto, pre-update or manual" ;; esac

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"
DB_FILE="$BACKUP_DIR/fleetcore-db-$LABEL-$STAMP.dump"
FILES_FILE="$BACKUP_DIR/fleetcore-files-$LABEL-$STAMP.tar.gz"
trap 'rm -f "$DB_FILE.part" "$FILES_FILE.part"' EXIT

log "Dumping database $DB_NAME"
compose exec -T db pg_dump -U "$DB_USER" -Fc --no-owner "$DB_NAME" > "$DB_FILE.part"
compose exec -T db pg_restore --list < "$DB_FILE.part" > /dev/null || die "the dump is not readable"
mv "$DB_FILE.part" "$DB_FILE"

log "Archiving uploaded files (MinIO)"
MINIO_CONTAINER="$(compose ps -q minio)"
[ -n "$MINIO_CONTAINER" ] || die "the minio container is not running"
docker run --rm --volumes-from "$MINIO_CONTAINER:ro" -v "$BACKUP_DIR:/backup" \
    -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" alpine:3.20 \
    sh -c "tar czf /backup/$(basename "$FILES_FILE").part -C /data . && chown \$HOST_UID:\$HOST_GID /backup/$(basename "$FILES_FILE").part"
mv "$FILES_FILE.part" "$FILES_FILE"
chmod 600 "$DB_FILE" "$FILES_FILE"

log "Done: $(basename "$DB_FILE") ($(du -h "$DB_FILE" | cut -f1)), $(basename "$FILES_FILE") ($(du -h "$FILES_FILE" | cut -f1))"

if [ -n "${OFFSITE_COMMAND:-}" ]; then
    log "Copying off-site"
    export BACKUP_DIR DB_FILE FILES_FILE
    bash -c "$OFFSITE_COMMAND"
fi

if [ "$LABEL" != "manual" ]; then
    python3 scripts/prune_backups.py "$BACKUP_DIR"
fi
