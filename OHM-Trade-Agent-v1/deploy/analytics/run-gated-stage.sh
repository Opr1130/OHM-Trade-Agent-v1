#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_SHA="${1:-}"
STAGE="${2:-}"
RELEASE_DIR="${3:-}"
ENV_UPLOAD="${4:-}"
APP_ROOT="$RELEASE_DIR/OHM-Trade-Agent-v1"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run the gated analytics stage as root" >&2
  exit 77
fi
if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "a 40-character main SHA is required" >&2
  exit 64
fi
case "$STAGE" in
  prepare|activate|empty|backup|restore-drill|backfill|shipper|reads-ready) ;;
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
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
    done
    for unit in \
      opip-learning-sync.service \
      opip-learning-capture.service \
      opip-learning-outcomes.service; do
      systemctl stop "$unit" >/dev/null 2>&1 || true
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
    install -o root -g root -m 0600 "$ENV_UPLOAD" /etc/opip-data-platform.env
    bash "$APP_ROOT/deploy/analytics/bootstrap-opip-data-platform.sh" "$TARGET_SHA" empty
    ;;
  backup)
    grep -Fxq "OPIP_DEPLOYED_SHA=$TARGET_SHA" /etc/opip-data-platform.env
    bash "$APP_ROOT/deploy/analytics/opip-postgres-backup.sh"
    ;;
  restore-drill)
    grep -Fxq "OPIP_DEPLOYED_SHA=$TARGET_SHA" /etc/opip-data-platform.env
    bash "$APP_ROOT/deploy/analytics/opip-postgres-restore-drill.sh"
    ;;
  backfill|shipper|reads-ready)
    bash "$APP_ROOT/deploy/analytics/bootstrap-opip-data-platform.sh" "$TARGET_SHA" "$STAGE"
    ;;
esac

echo "O'Pip analytics stage succeeded"
echo "sha=$TARGET_SHA"
echo "stage=$STAGE"
