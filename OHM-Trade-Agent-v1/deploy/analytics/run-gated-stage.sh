#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_SHA="${1:-}"
STAGE="${2:-}"
RELEASE_DIR="${3:-}"
ENV_UPLOAD="${4:-}"
APP_ROOT="$RELEASE_DIR/OHM-Trade-Agent-v1"
STATE_ROOT="/var/lib/opip-data-platform"
BACKUP_ROOT="/var/backups/opip-postgres"
ENV_FILE="/etc/opip-data-platform.env"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run the gated analytics stage as root" >&2
  exit 77
fi
if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "a 40-character main SHA is required" >&2
  exit 64
fi
case "$STAGE" in
  prepare|activate|empty|backup|restore-drill|offhost-verified|rollback-verified|backfill|shipper|reads-ready) ;;
  *) echo "unsupported analytics stage" >&2; exit 64 ;;
esac
if [[ "$RELEASE_DIR" != /var/tmp/opip-analytics-"$TARGET_SHA"-* ]]; then
  echo "invalid release directory" >&2
  exit 64
fi
if [[ ! -f "$APP_ROOT/deploy/analytics/bootstrap-opip-data-platform.sh" ]]; then
  echo "exact release payload is incomplete" >&2
  exit 66
fi

cleanup() {
  rm -rf -- "$RELEASE_DIR"
  if [[ -n "$ENV_UPLOAD" ]]; then
    rm -f -- "$ENV_UPLOAD"
  fi
}
trap cleanup EXIT

case "$STAGE" in
  prepare)
    for unit in \
      opip-learning-sync.timer \
      opip-learning-capture.timer \
      opip-learning-outcomes.timer; do
      if systemctl cat "$unit" >/dev/null 2>&1; then
        systemctl disable --now "$unit"
        ! systemctl is-active --quiet "$unit"
        ! systemctl is-enabled --quiet "$unit"
      fi
    done
    for unit in \
      opip-learning-sync.service \
      opip-learning-capture.service \
      opip-learning-outcomes.service; do
      if systemctl cat "$unit" >/dev/null 2>&1; then
        systemctl stop "$unit"
        ! systemctl is-active --quiet "$unit"
      fi
    done
    bash "$APP_ROOT/deploy/learning/bootstrap-opip-learning-worker.sh" \
      "$TARGET_SHA" 10.116.0.2 opiplearn
    ;;
  activate)
    grep -Fxq "OPIP_DEPLOYED_SHA=$TARGET_SHA" /etc/opip-learning.env
    test -s /root/.ssh/opip-learning
    test -s /root/.ssh/known_hosts
    systemctl start opip-learning-sync.service
    systemctl start opip-learning-capture.service
    systemctl start opip-learning-outcomes.service
    systemctl enable --now \
      opip-learning-sync.timer \
      opip-learning-capture.timer \
      opip-learning-outcomes.timer
    ;;
  empty)
    if [[ -z "$ENV_UPLOAD" ]]; then
      echo "sealed analytics environment is required for empty stage" >&2
      exit 66
    fi
    if [[ ! "$ENV_UPLOAD" =~ ^/tmp/opip-analytics-env-[0-9]+$ ]]; then
      echo "invalid sealed analytics environment path" >&2
      exit 64
    fi
    install -o root -g root -m 0600 "$ENV_UPLOAD" "$ENV_FILE"
    normalized="$(mktemp /etc/opip-data-platform.env.XXXXXX)"
    awk -F= -v sha="$TARGET_SHA" '
      BEGIN { found=0 }
      $1 == "OPIP_DEPLOYED_SHA" {
        print "OPIP_DEPLOYED_SHA=" sha
        found=1
        next
      }
      { print }
      END {
        if (!found) print "OPIP_DEPLOYED_SHA=" sha
      }
    ' "$ENV_FILE" > "$normalized"
    chown root:root "$normalized"
    chmod 0600 "$normalized"
    mv -f -- "$normalized" "$ENV_FILE"
    bash "$APP_ROOT/deploy/analytics/bootstrap-opip-data-platform.sh" "$TARGET_SHA" empty
    ;;
  backup)
    grep -Fxq "OPIP_DEPLOYED_SHA=$TARGET_SHA" "$ENV_FILE"
    bash "$APP_ROOT/deploy/analytics/opip-postgres-backup.sh"
    ;;
  restore-drill)
    grep -Fxq "OPIP_DEPLOYED_SHA=$TARGET_SHA" "$ENV_FILE"
    bash "$APP_ROOT/deploy/analytics/opip-postgres-restore-drill.sh"
    ;;
  offhost-verified)
    grep -Fxq "OPIP_DEPLOYED_SHA=$TARGET_SHA" "$ENV_FILE"
    latest_backup="$(find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'opip-postgres-*.dump' -printf '%T@ %p\n' | sort -nr | awk 'NR == 1 {sub(/^[^ ]+ /, ""); print}')"
    [[ -n "$latest_backup" && -r "$latest_backup" && -r "$latest_backup.sha256" ]] || {
      echo "cannot attest off-host protection without a checksummed local backup" >&2
      exit 70
    }
    sha256sum --check --status "$latest_backup.sha256"
    install -d -o root -g root -m 0700 "$STATE_ROOT"
    temporary="$(mktemp "$STATE_ROOT/offhost-backup.env.XXXXXX")"
    {
      printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      printf 'backup_file=%s\n' "$(basename "$latest_backup")"
      printf 'sha=%s\n' "$TARGET_SHA"
    } > "$temporary"
    chown root:root "$temporary"
    chmod 0600 "$temporary"
    mv -f -- "$temporary" "$STATE_ROOT/offhost-backup.env"
    echo "off-host backup attestation recorded after independent infrastructure verification"
    ;;
  rollback-verified)
    grep -Fxq "OPIP_DEPLOYED_SHA=$TARGET_SHA" "$ENV_FILE"
    [[ -r "$STATE_ROOT/rollout.env" && -r "$STATE_ROOT/last-restore-drill.env" ]] || {
      echo "empty rollout and restore-drill evidence are required before rollback attestation" >&2
      exit 70
    }
    # shellcheck disable=SC1090
    source "$STATE_ROOT/rollout.env"
    (( ${EMPTY_DEPLOY_COUNT:-0} >= 2 )) || {
      echo "two successful empty deployments are required before rollback attestation" >&2
      exit 70
    }
    restore_at="$(awk -F= '$1 == "verified_at_utc" {print $2; exit}' "$STATE_ROOT/last-restore-drill.env")"
    started_at="${EMPTY_STARTED_AT_UTC:-}"
    completed_at="${EMPTY_LAST_COMPLETED_AT_UTC:-}"
    restore_epoch="$(date -u -d "$restore_at" +%s 2>/dev/null || true)"
    started_epoch="$(date -u -d "$started_at" +%s 2>/dev/null || true)"
    completed_epoch="$(date -u -d "$completed_at" +%s 2>/dev/null || true)"
    now_epoch="$(date -u +%s)"
    if [[ ! "$restore_epoch" =~ ^[0-9]+$ || ! "$started_epoch" =~ ^[0-9]+$ || ! "$completed_epoch" =~ ^[0-9]+$ ]] \
      || (( started_epoch > restore_epoch || restore_epoch > completed_epoch || completed_epoch > now_epoch )); then
      echo "restore drill must fall between the first and latest successful empty deployments" >&2
      exit 70
    fi
    temporary="$(mktemp "$STATE_ROOT/empty-rollback.env.XXXXXX")"
    {
      printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      printf 'restore_verified_at_utc=%s\n' "$restore_at"
      printf 'empty_deploy_count=%s\n' "$EMPTY_DEPLOY_COUNT"
      printf 'sha=%s\n' "$TARGET_SHA"
    } > "$temporary"
    chown root:root "$temporary"
    chmod 0600 "$temporary"
    mv -f -- "$temporary" "$STATE_ROOT/empty-rollback.env"
    echo "empty-stage rollback evidence recorded"
    ;;
  backfill|shipper|reads-ready)
    bash "$APP_ROOT/deploy/analytics/bootstrap-opip-data-platform.sh" "$TARGET_SHA" "$STAGE"
    ;;
esac

echo "O'Pip analytics stage succeeded"
echo "sha=$TARGET_SHA"
echo "stage=$STAGE"
