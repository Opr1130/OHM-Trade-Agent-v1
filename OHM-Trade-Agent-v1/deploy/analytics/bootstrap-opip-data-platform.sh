#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_SHA="${1:-}"
STAGE="${2:-empty}"
ROOT="/opt/opip-learning"
REPO_ROOT="$ROOT/repo"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
COMPOSE="$APP_ROOT/deploy/analytics/docker-compose.yml"
ENV_FILE="/etc/opip-data-platform.env"
GRAFANA_ENV_FILE="/etc/opip-grafana.env"
STATE_ROOT="/var/lib/opip-data-platform"
STATE_FILE="$STATE_ROOT/rollout.env"
OFFHOST_EVIDENCE="$STATE_ROOT/offhost-backup.env"
RESTORE_EVIDENCE="$STATE_ROOT/last-restore-drill.env"
ROLLBACK_EVIDENCE="$STATE_ROOT/empty-rollback.env"
POSTGRES_TLS_CA="/etc/opip-data-platform/tls/postgres-ca.crt"
POSTGRES_TLS_CERT="/etc/opip-data-platform/tls/postgres-server.crt"
POSTGRES_TLS_KEY="/etc/opip-data-platform/tls/postgres-server.key"

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

require_uri_unreserved_password() {
  local name="$1" value="${!1:-}"
  if [[ -z "$value" || ! "$value" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    echo "$name must be non-empty and use URI-unreserved characters only (A-Z a-z 0-9 - . _ ~)" >&2
    exit 78
  fi
}
require_uri_unreserved_password OPIP_POSTGRES_ADMIN_PASSWORD
require_uri_unreserved_password OPIP_SHIPPER_PASSWORD

require_grafana_verify_full() {
  if [[ "${OPIP_GRAFANA_DB_SSLMODE:-verify-full}" != "verify-full" ]]; then
    echo "OPIP_GRAFANA_DB_SSLMODE must be exactly verify-full" >&2
    exit 78
  fi
}
require_grafana_verify_full

write_grafana_env_file() {
  local temporary key
  local -a keys=(
    OPIP_GRAFANA_ADMIN_USER
    OPIP_GRAFANA_ADMIN_PASSWORD
    OPIP_GRAFANA_DB_USER
    OPIP_GRAFANA_DB_PASSWORD
    OPIP_GRAFANA_DB_NAME
    OPIP_GRAFANA_DB_SSLMODE
    OPIP_GRAFANA_POSTGRES_HOST
    OPIP_GRAFANA_POSTGRES_PORT
    OPIP_GRAFANA_BIND_ADDRESS
    OPIP_GRAFANA_HOST_PORT
    OPIP_GRAFANA_HTTP_PORT
    OPIP_GRAFANA_DOMAIN
    OPIP_GRAFANA_ROOT_URL
    OPIP_GRAFANA_SERVE_FROM_SUB_PATH
  )

  temporary="$(mktemp /etc/opip-grafana.env.XXXXXX)"
  : > "$temporary"
  for key in "${keys[@]}"; do
    if ! awk -F= -v key="$key" '$1 == key {print; found=1; exit} END {if (!found) exit 1}' \
      "$ENV_FILE" >> "$temporary"; then
      rm -f -- "$temporary"
      echo "missing required Grafana setting in $ENV_FILE: $key" >&2
      exit 78
    fi
  done
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  mv -f -- "$temporary" "$GRAFANA_ENV_FILE"
}
write_grafana_env_file

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE" "$@"
}

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
now_epoch="$(date -u +%s)"
backup_epoch=""
restore_epoch=""

git -C "$REPO_ROOT" fetch --prune origin main
remote_main="$(git -C "$REPO_ROOT" rev-parse origin/main)"
[[ "$remote_main" == "$TARGET_SHA" ]] || {
  echo "refusing analytics deploy: target is not current origin/main" >&2
  exit 65
}
git -C "$REPO_ROOT" checkout -f main
git -C "$REPO_ROOT" reset --hard "$TARGET_SHA"

install -d -o root -g root -m 0711 "$STATE_ROOT"
install -d -o root -g root -m 0700 \
  "$STATE_ROOT/config" \
  /var/backups/opip-postgres
install -d -o 472 -g 472 -m 0750 "$STATE_ROOT/grafana"
# Never reset an initialized bind-mounted PGDATA directory to root:root while
# PostgreSQL is running. Recover the directory owner from a canonical file so
# repeat empty deployments remain safe without hard-coding the image UID/GID.
if [[ -r "$STATE_ROOT/postgres/PG_VERSION" ]]; then
  pgdata_owner="$(stat -c '%u:%g' "$STATE_ROOT/postgres/PG_VERSION")"
  if [[ ! "$pgdata_owner" =~ ^[0-9]+:[0-9]+$ ]]; then
    echo "unable to determine existing PostgreSQL data owner" >&2
    exit 70
  fi
  chown "$pgdata_owner" "$STATE_ROOT/postgres"
  chmod 0700 "$STATE_ROOT/postgres"
else
  install -d -o root -g root -m 0700 "$STATE_ROOT/postgres"
fi
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

validate_postgres_tls_key() {
  local postgres_image runtime_ids pg_uid pg_gid key_metadata key_mode key_uid key_gid

  [[ -f "$POSTGRES_TLS_CA" && -r "$POSTGRES_TLS_CA" ]] || {
    echo "missing or unreadable PostgreSQL TLS CA: $POSTGRES_TLS_CA" >&2
    exit 78
  }
  [[ -f "$POSTGRES_TLS_CERT" && -r "$POSTGRES_TLS_CERT" ]] || {
    echo "missing or unreadable PostgreSQL TLS certificate: $POSTGRES_TLS_CERT" >&2
    exit 78
  }
  [[ -f "$POSTGRES_TLS_KEY" ]] || {
    echo "missing PostgreSQL TLS private key: $POSTGRES_TLS_KEY" >&2
    exit 78
  }

  # Pull the exact Compose image but do not start PostgreSQL. Resolve the
  # postgres runtime UID/GID from that image rather than hard-coding Alpine IDs.
  compose pull opip-postgres >/dev/null
  postgres_image="$(compose config --images | awk '/(^|\/)postgres:/ {print; exit}')"
  [[ -n "$postgres_image" ]] || {
    echo "unable to resolve PostgreSQL image for TLS-key preflight" >&2
    exit 78
  }
  runtime_ids="$(
    docker run --rm --entrypoint sh "$postgres_image" -c \
      'printf "%s:%s\n" "$(id -u postgres)" "$(id -g postgres)"'
  )"
  if [[ ! "$runtime_ids" =~ ^[0-9]+:[0-9]+$ ]]; then
    echo "unable to resolve PostgreSQL runtime UID/GID from $postgres_image" >&2
    exit 78
  fi
  IFS=: read -r pg_uid pg_gid <<<"$runtime_ids"

  key_metadata="$(stat -Lc '%a:%u:%g' "$POSTGRES_TLS_KEY")"
  if [[ ! "$key_metadata" =~ ^[0-9]+:[0-9]+:[0-9]+$ ]]; then
    echo "unable to read PostgreSQL TLS key ownership/mode" >&2
    exit 78
  fi
  IFS=: read -r key_mode key_uid key_gid <<<"$key_metadata"

  if [[ "$key_mode" == "600" && "$key_uid" == "$pg_uid" && "$key_gid" == "$pg_gid" ]]; then
    return 0
  fi
  if [[ "$key_mode" == "640" && "$key_uid" == "0" && "$key_gid" == "$pg_gid" ]]; then
    return 0
  fi

  echo "invalid PostgreSQL TLS key ownership/mode: got mode=$key_mode uid=$key_uid gid=$key_gid; expected 0600 owned by $pg_uid:$pg_gid or root:$pg_gid with 0640" >&2
  exit 78
}

wait_for_postgres() {
  local ready="false"
  for _ in $(seq 1 30); do
    if compose exec -T opip-postgres \
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
  compose --profile admin run --rm opip-data-admin "$@"
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

validate_promotion_evidence() {
  local attested_backup_name attested_backup offhost_at restore_at restore_backup
  [[ -r "$OFFHOST_EVIDENCE" ]] || {
    echo "independent off-host backup attestation is required before promotion" >&2
    exit 70
  }
  [[ -r "$RESTORE_EVIDENCE" ]] || {
    echo "local restore-drill evidence is required before promotion" >&2
    exit 70
  }
  offhost_at="$(awk -F= '$1 == "verified_at_utc" {print $2; exit}' "$OFFHOST_EVIDENCE")"
  attested_backup_name="$(awk -F= '$1 == "backup_file" {print $2; exit}' "$OFFHOST_EVIDENCE")"
  restore_at="$(awk -F= '$1 == "verified_at_utc" {print $2; exit}' "$RESTORE_EVIDENCE")"
  restore_backup="$(awk -F= '$1 == "backup_file" {print $2; exit}' "$RESTORE_EVIDENCE")"
  backup_epoch="$(date -u -d "$offhost_at" +%s 2>/dev/null || true)"
  restore_epoch="$(date -u -d "$restore_at" +%s 2>/dev/null || true)"
  if [[ ! "$backup_epoch" =~ ^[0-9]+$ ]] \
    || (( backup_epoch > now_epoch || now_epoch - backup_epoch > 8 * 86400 )); then
    echo "off-host backup verification is invalid, future-dated, or older than eight days" >&2
    exit 70
  fi
  if [[ ! "$restore_epoch" =~ ^[0-9]+$ ]] \
    || (( restore_epoch > now_epoch || now_epoch - restore_epoch > 90 * 86400 )); then
    echo "restore drill verification is invalid, future-dated, or older than 90 days" >&2
    exit 70
  fi
  if [[ ! "$attested_backup_name" =~ ^opip-postgres-[0-9]{8}T[0-9]{6}Z\.dump$ ]]; then
    echo "off-host backup attestation references an invalid local dump name" >&2
    exit 70
  fi
  attested_backup="/var/backups/opip-postgres/$attested_backup_name"
  [[ -r "$attested_backup" && -r "$attested_backup.sha256" ]] || {
    echo "the attested PostgreSQL dump and checksum are required before promotion" >&2
    exit 70
  }
  sha256sum --check --status "$attested_backup.sha256" || {
    echo "attested PostgreSQL dump checksum verification failed" >&2
    exit 70
  }
  if (( backup_epoch < $(stat -c '%Y' "$attested_backup") )); then
    echo "off-host backup attestation predates the attested local PostgreSQL dump" >&2
    exit 70
  fi
  if [[ "$restore_backup" != "$attested_backup_name" ]]; then
    echo "restore drill must validate the attested PostgreSQL dump" >&2
    exit 70
  fi
}

export OPIP_DEPLOYED_SHA="$TARGET_SHA"
validate_postgres_tls_key
# Both application services share the same immutable image tag; build once to
# avoid a concurrent BuildKit export race on the identical tag.
docker compose -f "$COMPOSE" build opip-shipper
compose up -d opip-postgres
wait_for_postgres

# A fresh host may initialize the empty database first. Promotion beyond the
# empty stage requires a real dump, restore drill, and independently recorded
# off-host evidence after PostgreSQL is running.
if [[ "$STAGE" != "empty" ]]; then
  validate_promotion_evidence
fi

if [[ "$STAGE" == "empty" ]]; then
  admin_run python -m app.opip.data_platform.migrations migrate
  admin_run python -m app.opip.data_platform.migrations provision-roles
  if [[ -z "${EMPTY_STARTED_AT_UTC:-}" ]]; then
    write_state EMPTY_STARTED_AT_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  fi
  write_state EMPTY_DEPLOY_COUNT "$((EMPTY_DEPLOY_COUNT + 1))"
  write_state EMPTY_LAST_SHA "$TARGET_SHA"
  write_state EMPTY_LAST_COMPLETED_AT_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
elif [[ "$STAGE" == "backfill" ]]; then
  require_stage EMPTY_STARTED_AT_UTC "empty PostgreSQL stage"
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  if (( ${EMPTY_DEPLOY_COUNT:-0} < 2 )); then
    echo "empty PostgreSQL stage requires two successful deploys before backfill" >&2
    exit 69
  fi
  [[ -r "$ROLLBACK_EVIDENCE" ]] || {
    echo "explicit empty-stage rollback evidence is required before backfill" >&2
    exit 69
  }
  rollback_at="$(awk -F= '$1 == "verified_at_utc" {print $2; exit}' "$ROLLBACK_EVIDENCE")"
  rollback_restore_at="$(awk -F= '$1 == "restore_verified_at_utc" {print $2; exit}' "$ROLLBACK_EVIDENCE")"
  rollback_count="$(awk -F= '$1 == "empty_deploy_count" {print $2; exit}' "$ROLLBACK_EVIDENCE")"
  rollback_sha="$(awk -F= '$1 == "sha" {print $2; exit}' "$ROLLBACK_EVIDENCE")"
  rollback_epoch="$(date -u -d "$rollback_at" +%s 2>/dev/null || true)"
  if [[ ! "$rollback_epoch" =~ ^[0-9]+$ ]] \
    || (( rollback_epoch > now_epoch || rollback_epoch < restore_epoch )); then
    echo "empty-stage rollback evidence must not be future-dated and must be newer than the restore drill" >&2
    exit 69
  fi
  if [[ "$rollback_restore_at" != "$(awk -F= '$1 == "verified_at_utc" {print $2; exit}' "$RESTORE_EVIDENCE")" ]] \
    || [[ ! "$rollback_count" =~ ^[0-9]+$ ]] || (( rollback_count < 2 )) \
    || [[ "$rollback_sha" != "$TARGET_SHA" ]]; then
    echo "empty-stage rollback evidence does not match the verified rollout state" >&2
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
  compose up -d opip-shipper
  if [[ -z "${SHIPPER_STARTED_AT_UTC:-}" ]]; then
    write_state SHIPPER_STARTED_AT_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  fi
  write_state SHIPPER_SHA "$TARGET_SHA"
else
  require_stage SHIPPER_STARTED_AT_UTC "shipper soak"
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  shipper_epoch="$(date -u -d "$SHIPPER_STARTED_AT_UTC" +%s 2>/dev/null || true)"
  if [[ ! "$shipper_epoch" =~ ^[0-9]+$ ]] \
    || (( shipper_epoch > now_epoch || now_epoch - shipper_epoch < 7 * 86400 )); then
    echo "shipper must soak for seven days before historical reads are eligible" >&2
    exit 69
  fi
  admin_run python -m app.opip.data_platform.reconcile
  admin_run python -m app.opip.data_platform.health --require-ready
  write_state READS_READY_AT_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  write_state READS_READY_SHA "$TARGET_SHA"
fi

write_state DEPLOYED_SHA "$TARGET_SHA"

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

compose ps
echo "O'Pip analytics data-platform stage succeeded"
echo "stage=$STAGE"
echo "sha=$TARGET_SHA"