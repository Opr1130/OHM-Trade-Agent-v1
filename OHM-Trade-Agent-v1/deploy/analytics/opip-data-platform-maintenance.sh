#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/opip-learning/repo/OHM-Trade-Agent-v1"
COMPOSE="$APP_ROOT/deploy/analytics/docker-compose.yml"
LOCK_FILE="/var/lock/opip-data-platform-maintenance.lock"

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

docker compose -f "$COMPOSE" --profile admin run --rm opip-data-admin \
  python -m app.opip.data_platform.maintenance --prune
docker compose -f "$COMPOSE" --profile admin run --rm opip-data-admin \
  python -m app.opip.data_platform.reconcile
docker compose -f "$COMPOSE" --profile admin run --rm opip-data-admin \
  python -m app.opip.data_platform.health
