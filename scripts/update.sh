#!/usr/bin/env bash
# Update a production server to a new release.
#
#   FLEET_ENV=production ./scripts/update.sh v1.2.0
#
# Steps: back up (pre-update) -> build the release image -> apply database migrations in a one-off container
# (so a failing migration stops here, before anything is replaced) -> start the new version -> health check.
# The previous version is remembered in .deploy-state/previous-version for rollback (see docs/deployment.md).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_common.sh
source scripts/_common.sh

[ "${FLEET_ENV:-}" = "production" ] || [ -n "${FLEET_COMPOSE_CMD:-}" ] || die "run this on the server with FLEET_ENV=production (it uses .env.production)"
VERSION="${1:-}"
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.]+)?$ ]] || die "give a version like v1.2.0"
[ -f "$ENV_FILE" ] || die "$ENV_FILE not found (copy .env.production.example)"
mkdir -p .deploy-state

set_version() {
    if grep -q '^FLEETCORE_VERSION=' "$ENV_FILE"; then
        sed -i.bak "s/^FLEETCORE_VERSION=.*/FLEETCORE_VERSION=$1/" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        printf 'FLEETCORE_VERSION=%s\n' "$1" >> "$ENV_FILE"
    fi
}

PREVIOUS="$(grep '^FLEETCORE_VERSION=' "$ENV_FILE" | cut -d= -f2 || true)"
log "Updating ${PREVIOUS:-<none>} -> $VERSION"

log "1/5 Backup before the update"
./scripts/backup.sh pre-update

log "2/5 Building image fleetcore:$VERSION"
docker build -t "fleetcore:$VERSION" --build-arg "APP_VERSION=$VERSION" .

log "3/5 Applying database migrations"
set_version "$VERSION"
if ! compose run --rm --no-deps app alembic upgrade head; then
    set_version "${PREVIOUS:-$VERSION}"
    die "migration failed; nothing was replaced and the running version is untouched. Fix it and run again."
fi

log "4/5 Starting $VERSION"
[ -z "$PREVIOUS" ] || printf '%s\n' "$PREVIOUS" > .deploy-state/previous-version
compose up -d

log "5/5 Waiting for the application to become healthy"
for _ in $(seq 1 30); do
    if compose exec -T app curl -fsS http://localhost:8000/api/v1/health 2> /dev/null | grep -q "\"version\": *\"$VERSION\""; then
        log "OK: $VERSION is running"
        log "Rollback if needed: see docs/deployment.md (previous version: ${PREVIOUS:-none})"
        exit 0
    fi
    sleep 2
done
die "the new version did not become healthy. Look at: $COMPOSE_CMD logs app. Rollback steps: docs/deployment.md"
