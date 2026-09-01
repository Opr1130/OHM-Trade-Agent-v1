#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_SHA="${1:-}"
STAGE="${2:-empty}"
ROOT="/opt/opip-learning"
REPO_ROOT="$ROOT/repo"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
COMPOSE="$APP_ROOT/deploy/analytics/docker-compose.yml"
ENV_FILE="/etc/opip-data-platform.env"
STATE_ROOT="/var/lib/opip-data-platform"
STATE_FILE="$STATE_ROOT/rollout.env"

if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: $0 <40-char-main-sha> <empty|backfill|shipper|reads-ready>" >&2
  exit 64
fi
case "$STAGE" in
  empty|backfill|shipper|reads-ready) ;;
  *) echo "invalid rollout stage: $STAGE" >&2; exit 64 ;;
esac
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run analytics bootstrap as root" >&2
  exit 77
fi
[[ -r "$ENV_FILE" ]] || {
  echo "missing $ENV_FILE; create it with mode 0600 from env.example" >&2
  exit 78
}
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Serialize with sync/capture/outcomes on the shared learning/analytics host.
# Timers use this same lock and will skip rather than compete for RAM or files.
exec 8>/var/lock/opip-learning-plane.lock
if ! flock -w 300 8; then
  echo "learning plane remained busy for five minutes; retry this stage later" >&2
  exit 75
fi

total_kb="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo)"
if [[ ! "$total_kb" =~ ^[0-9]+$ ]] || (( total_kb < 1800 * 1024 )); then
  echo "analytics host must be resized to at least 2 GiB before PostgreSQL" >&2
  exit 70
fi

: "${OPIP_PRODUCTION_PRIVATE_CIDR:?set OPIP_PRODUCTION_PRIVATE_CIDR to production-private-ip/32}"
if [[ ! "$OPIP_PRODUCTION_PRIVATE_CIDR" =~ ^10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/32$ ]]; then
  echo "OPIP_PRODUCTION_PRIVATE_CIDR must be a private 10.x.x.x/32 address" >&2
  exit 78
fi
: "${OPIP_OFFHOST_BACKUP_VERIFIED_AT_UTC:?verify off-host backup before database deployment}"
: "${OPIP_RESTORE_DRILL_VERIFIED_AT_UTC:?complete a restore drill before database deployment}"
now_epoch="$(date -u +%s)"
backup_epoch="$(date -u -d "$OPIP_OFFHOST_BACKUP_VERIFIED_AT_UTC" +%s 2>/dev/null || true)"
restore_epoch="$(date -u -d "$OPIP_RESTORE_DRILL_VERIFIED_AT_UTC" +%s 2>/dev/null || true)"
if [[ ! "$backup_epoch" =~ ^[0-9]+$ ]] || (( now_epoch - backup_epoch > 8 * 86400 )); then
  echo "off-host backup verification is missing or older than eight days" >&2
  exit 70
fi
if [[ ! "$restore_epoch" =~ ^[0-9]+$ ]] || (( now_epoch - restore_epoch > 90 * 86400 )); then
  echo "restore drill verification is missing or older than 90 days" >&2
  exit 70
fi

git -C "$REPO_ROOT" fetch --prune origin main
remote_main="$(git -C "$REPO_ROOT" rev-parse origin/main)"
[[ "$remote_main" == "$TARGET_SHA" ]] || {
  echo "refusing analytics deploy: target is not current origin/main" >&2
  exit 65
}
git -C "$REPO_ROOT" checkout -f main
git -C "$REPO_ROOT" reset --hard "$TARGET_SHA"

install -d -o root -g root -m 0700 \
  "$STATE_ROOT/postgres" \
  "$STATE_ROOT/config" \
  /var/backups/opip-postgres
if [[ ! -e "$STATE_FILE" ]]; then
  install -o root -g root -m 0600 /dev/null "$STATE_FILE"
fi
# rollout.env is created by this root-only script and contains scalar evidence.
# shellcheck disable=SC1090
source "$STATE_FILE"
EMPTY_DEPLOY_COUNT="${EMPTY_DEPLOY_COUNT:-0}"

cat > "$STATE_ROOT/config/pg_hba.conf" <<EOF
local all all scram-sha-256
host all all 127.0.0.1/32 scram-sha-256
host all all 172.29.0.0/24 scram-sha-256
host all opip_dashboard $OPIP_PRODUCTION_PRIVATE_CIDR scram-sha-256
EOF
chmod 0644 "$STATE_ROOT/config/pg_hba.conf"

write_state() {
  local key="$1" value="$2" temporary
  temporary="$(mktemp "$STATE_ROOT/rollout.env.XXXXXX")"
  awk -F= -v key="$key" '$1 != key' "$STATE_FILE" > "$temporary"
  printf '%s=%q\n' "$key" "$value" >> "$temporary"
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  mv -f -- "$temporary" "$STATE_FILE"
}

wait_for_postgres() {
  local ready="false"
  for _ in $(seq 1 30); do
    if docker compose -f "$COMPOSE" exec -T opip-postgres \
      pg_isready -U "${OPIP_POSTGRES_ADMIN_USER:-opip_admin}" -d "${OPIP_POSTGRES_DB:-opip}"; then
      ready="true"
      break
    fi
    sleep 2
  done
  [[ "$ready" == "true" ]] || {
    echo "PostgreSQL did not become ready" >&2
    exit 1
  }
}

admin_run() {
  docker compose -f "$COMPOSE" --profile admin run --rm opip-data-admin "$@"
}

require_stage() {
  local key="$1" label="$2"
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  [[ -n "${!key:-}" ]] || {
    echo "$label must complete before stage $STAGE" >&2
    exit 69
  }
}

export OPIP_DEPLOYED_SHA="$TARGET_SHA"
docker compose -f "$COMPOSE" build opip-shipper opip-data-admin
docker compose -f "$COMPOSE" up -d opip-postgres
wait_for_postgres

if [[ "$STAGE" == "empty" ]]; then
  admin_run python -m app.opip.data_platform.migrations migrate
  admin_run python -m app.opip.data_platform.migrations provision-roles
  if [[ -z "${EMPTY_STARTED_AT_UTC:-}" ]]; then
    write_state EMPTY_STARTED_AT_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  fi
  write_state EMPTY_DEPLOY_COUNT "$((EMPTY_DEPLOY_COUNT + 1))"
  write_state EMPTY_LAST_SHA "$TARGET_SHA"
elif [[ "$STAGE" == "backfill" ]]; then
  require_stage EMPTY_STARTED_AT_UTC "empty PostgreSQL stage"
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  if (( ${EMPTY_DEPLOY_COUNT:-0} < 2 )); then
    echo "empty PostgreSQL stage requires two successful deploys before backfill" >&2
    exit 69
  fi
  : "${OPIP_EMPTY_ROLLBACK_VERIFIED_AT_UTC:?record the empty-stage rollback verification before backfill}"
  rollback_epoch="$(date -u -d "$OPIP_EMPTY_ROLLBACK_VERIFIED_AT_UTC" +%s 2>/dev/null || true)"
  if [[ ! "$rollback_epoch" =~ ^[0-9]+$ ]] || (( rollback_epoch < restore_epoch )); then
    echo "empty-stage rollback evidence must be valid and newer than the restore drill" >&2
    exit 69
  fi
  admin_run python -m app.opip.data_platform.backfill
  admin_run python -m app.opip.data_platform.migrations refresh-views
  admin_run python -m app.opip.data_platform.reconcile
  write_state BACKFILL_COMPLETED_AT_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  write_state BACKFILL_SHA "$TARGET_SHA"
elif [[ "$STAGE" == "shipper" ]]; then
  require_stage BACKFILL_COMPLETED_AT_UTC "clean backfill"
  admin_run python -m app.opip.data_platform.reconcile
  docker compose -f "$COMPOSE" up -d opip-shipper
  if [[ -z "${SHIPPER_STARTED_AT_UTC:-}" ]]; then
    write_state SHIPPER_STARTED_AT_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  fi
  write_state SHIPPER_SHA "$TARGET_SHA"
else
  require_stage SHIPPER_STARTED_AT_UTC "shipper soak"
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  shipper_epoch="$(date -u -d "$SHIPPER_STARTED_AT_UTC" +%s 2>/dev/null || true)"
  if [[ ! "$shipper_epoch" =~ ^[0-9]+$ ]] || (( now_epoch - shipper_epoch < 7 * 86400 )); then
    echo "shipper must soak for seven days before historical reads are eligible" >&2
    exit 69
  fi
  admin_run python -m app.opip.data_platform.reconcile
  admin_run python -m app.opip.data_platform.health --require-ready
  write_state READS_READY_AT_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  write_state READS_READY_SHA "$TARGET_SHA"
fi

install -o root -g root -m 0755 \
  "$APP_ROOT/deploy/analytics/opip-data-platform-maintenance.sh" \
  /usr/local/sbin/opip-data-platform-maintenance
install -o root -g root -m 0755 \
  "$APP_ROOT/deploy/analytics/opip-postgres-backup.sh" \
  /usr/local/sbin/opip-postgres-backup
install -o root -g root -m 0755 \
  "$APP_ROOT/deploy/analytics/opip-postgres-restore-drill.sh" \
  /usr/local/sbin/opip-postgres-restore-drill
for unit in \
  opip-data-platform-maintenance.service \
  opip-data-platform-maintenance.timer \
  opip-postgres-backup.service \
  opip-postgres-backup.timer; do
  install -o root -g root -m 0644 \
    "$APP_ROOT/deploy/analytics/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now opip-postgres-backup.timer
if [[ "$STAGE" == "shipper" || "$STAGE" == "reads-ready" ]]; then
  systemctl enable --now opip-data-platform-maintenance.timer
fi

docker compose -f "$COMPOSE" ps
echo "O'Pip analytics data-platform stage succeeded"
echo "stage=$STAGE"
echo "sha=$TARGET_SHA"
