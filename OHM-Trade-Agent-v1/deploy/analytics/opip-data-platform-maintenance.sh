#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/opip-learning/repo/OHM-Trade-Agent-v1"
COMPOSE="$APP_ROOT/deploy/analytics/docker-compose.yml"
ENV_FILE="/etc/opip-data-platform.env"
STATE_FILE="/var/lib/opip-data-platform/rollout.env"
LOCK_FILE="/var/lock/opip-learning-plane.lock"

[[ -r "$ENV_FILE" && -r "$STATE_FILE" ]] || exit 78
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
# shellcheck disable=SC1090
source "$STATE_FILE"
set +a
export OPIP_DEPLOYED_SHA="${DEPLOYED_SHA:-${OPIP_DEPLOYED_SHA:-}}"
[[ "$OPIP_DEPLOYED_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "valid OPIP_DEPLOYED_SHA is required for analytics maintenance" >&2
  exit 78
}

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

docker compose -f "$COMPOSE" --profile admin run --rm opip-data-admin \
  python -m app.opip.data_platform.maintenance --prune
docker compose -f "$COMPOSE" --profile admin run --rm opip-data-admin \
  python -m app.opip.data_platform.reconcile
docker compose -f "$COMPOSE" --profile admin run --rm opip-data-admin \
  python -m app.opip.data_platform.health
