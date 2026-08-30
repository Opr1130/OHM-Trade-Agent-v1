#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="/etc/opip-learning.env"
LOCK_FILE="/var/lock/opip-learning-plane.lock"
DATA_ROOT="/var/lib/opip-learning/data"
INCOMING="$DATA_ROOT/.incoming"

[[ -r "$ENV_FILE" ]] || {
  echo "missing O'Pip learning environment: $ENV_FILE" >&2
  exit 78
}
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${OPIP_PRODUCTION_HOST:?OPIP_PRODUCTION_HOST is required}"
: "${OPIP_PRODUCTION_USER:?OPIP_PRODUCTION_USER is required}"
: "${OPIP_PRODUCTION_EXPORT_PATH:=/var/lib/opip-learning-export}"
: "${OPIP_LEARNING_SSH_KEY:=/root/.ssh/opip-learning}"

for cmd in rsync install flock mv date; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing learning sync command: $cmd" >&2
    exit 69
  }
done

install -d -o root -g root -m 0755 "$DATA_ROOT" "$INCOMING"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "O'Pip learning plane busy; sync skipped"
  exit 0
fi

rm -f "$INCOMING"/*
rsync -a --delete-delay   -e "ssh -i $OPIP_LEARNING_SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10"   "$OPIP_PRODUCTION_USER@$OPIP_PRODUCTION_HOST:$OPIP_PRODUCTION_EXPORT_PATH/"   "$INCOMING/"

for name in p1_shadow_outbox.jsonl full_market_observations.jsonl manifest.env; do
  if [[ -e "$INCOMING/$name" ]]; then
    mv -f -- "$INCOMING/$name" "$DATA_ROOT/$name"
  fi
done

printf 'last_sync_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"   > "$DATA_ROOT/.last_sync"

echo "O'Pip learning evidence sync: OK"
