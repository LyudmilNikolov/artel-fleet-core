#!/usr/bin/env bash
# Go back to the previous release (the one update.sh replaced), or to an explicit version.
#
#   FLEET_ENV=production ./scripts/rollback.sh [v1.1.0]
#
# This swaps the application code only. It does NOT undo database migrations: that is safe when the release you
# are leaving only added tables or columns (the normal case). If it changed existing data or structure, restore the
# pre-update backup instead (./scripts/restore.sh --yes <dump> <files>), see docs/deployment.md.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_common.sh
source scripts/_common.sh

[ "${FLEET_ENV:-}" = "production" ] || [ -n "${FLEET_COMPOSE_CMD:-}" ] || die "run this on the server with FLEET_ENV=production"
TARGET="${1:-$(cat .deploy-state/previous-version 2> /dev/null || true)}"
[[ "$TARGET" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.]+)?$ ]] || die "no previous version known; give one: ./scripts/rollback.sh v1.1.0"
docker image inspect "fleetcore:$TARGET" > /dev/null 2>&1 || die "image fleetcore:$TARGET is not on this server (rebuild it: git checkout $TARGET && docker build -t fleetcore:$TARGET --build-arg APP_VERSION=$TARGET .)"

CURRENT="$(grep '^FLEETCORE_VERSION=' "$ENV_FILE" | cut -d= -f2 || true)"
log "Rolling back ${CURRENT:-?} -> $TARGET (database is not changed)"
sed -i.bak "s/^FLEETCORE_VERSION=.*/FLEETCORE_VERSION=$TARGET/" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
mkdir -p .deploy-state
[ -z "$CURRENT" ] || printf '%s\n' "$CURRENT" > .deploy-state/previous-version
compose up -d

for _ in $(seq 1 30); do
    if compose exec -T app curl -fsS http://localhost:8000/api/v1/health 2> /dev/null | grep -q "\"version\": *\"$TARGET\""; then
        log "OK: $TARGET is running"
        exit 0
    fi
    sleep 2
done
die "$TARGET did not become healthy. Look at: $COMPOSE_CMD logs app"
