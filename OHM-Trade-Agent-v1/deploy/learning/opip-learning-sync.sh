#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="/etc/opip-learning.env"
LOCK_FILE="/var/lock/opip-learning-plane.lock"
DATA_ROOT="/var/lib/opip-learning/data"
STATE_ROOT="/var/lib/opip-learning/state"
INCOMING="$DATA_ROOT/.incoming"
ARCHIVE="$DATA_ROOT/.export.tar"

[[ -r "$ENV_FILE" ]] || {
  echo "missing O'Pip learning environment: $ENV_FILE" >&2
  exit 78
}
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${OPIP_PRODUCTION_HOST:?OPIP_PRODUCTION_HOST is required}"
: "${OPIP_PRODUCTION_USER:?OPIP_PRODUCTION_USER is required}"
: "${OPIP_DEPLOYED_SHA:?OPIP_DEPLOYED_SHA is required}"
: "${OPIP_LEARNING_SSH_KEY:=/root/.ssh/opip-learning}"

[[ "$OPIP_DEPLOYED_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "invalid OPIP_DEPLOYED_SHA" >&2
  exit 78
}

for cmd in ssh tar install flock mv date sha256sum stat awk rm; do
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

state_value() {
  local file="$1"
  local key="$2"
  awk -F= -v k="$key" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$file" 2>/dev/null || true
}

status_time() {
  local raw="$1"
  if [[ "$raw" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
    printf '%s\n' "$raw"
  else
    printf 'NONE\n'
  fi
}

status_rc() {
  local raw="$1"
  if [[ "$raw" =~ ^[0-9]{1,3}$ ]]; then
    printf '%s\n' "$raw"
  else
    printf 'NONE\n'
  fi
}

capture_file="$STATE_ROOT/capture.last.env"
outcomes_file="$STATE_ROOT/outcomes.last.env"
last_sync_file="$DATA_ROOT/.last_sync"
last_sync_at="$(status_time "$(state_value "$last_sync_file" last_sync_at_utc)")"
capture_at="$(status_time "$(state_value "$capture_file" finished_at_utc)")"
capture_rc="$(status_rc "$(state_value "$capture_file" exit_code)")"
outcomes_at="$(status_time "$(state_value "$outcomes_file" finished_at_utc)")"
outcomes_rc="$(status_rc "$(state_value "$outcomes_file" exit_code)")"
status_command="opip-export-v2 sha=$OPIP_DEPLOYED_SHA sync_success_at=$last_sync_at capture_at=$capture_at capture_rc=$capture_rc outcomes_at=$outcomes_at outcomes_rc=$outcomes_rc"

rm -f "$INCOMING"/* "$ARCHIVE"

ssh \
  -i "$OPIP_LEARNING_SSH_KEY" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=10 \
  "$OPIP_PRODUCTION_USER@$OPIP_PRODUCTION_HOST" \
  "$status_command" > "$ARCHIVE"

tar -xf "$ARCHIVE" -C "$INCOMING"

manifest="$INCOMING/manifest.env"
[[ -r "$manifest" ]] || {
  echo "O'Pip learning sync: missing manifest" >&2
  exit 66
}

manifest_value() {
  local key="$1"
  awk -F= -v k="$key" '$1 == k {print $2; exit}' "$manifest"
}

schema="$(manifest_value schema_version)"
[[ "$schema" == "2" ]] || {
  echo "O'Pip learning sync: unsupported manifest schema=$schema" >&2
  exit 65
}

validate_artifact() {
  local name="$1"
  local key="$2"
  local path="$INCOMING/$name"
  local expected_bytes expected_sha actual_bytes actual_sha

  [[ -f "$path" ]] || {
    echo "O'Pip learning sync: missing artifact=$name" >&2
    return 1
  }

  expected_bytes="$(manifest_value "${key}_bytes")"
  expected_sha="$(manifest_value "${key}_sha256")"
  actual_bytes="$(stat -c '%s' "$path")"
  actual_sha="$(sha256sum "$path" | awk '{print $1}')"

  [[ "$expected_bytes" =~ ^[0-9]+$ ]] || return 1
  [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ "$actual_bytes" == "$expected_bytes" ]] || {
    echo "O'Pip learning sync: size mismatch for $name" >&2
    return 1
  }
  [[ "$actual_sha" == "$expected_sha" ]] || {
    echo "O'Pip learning sync: checksum mismatch for $name" >&2
    return 1
  }
}

validate_artifact "p1_shadow_outbox.jsonl" "p1_shadow_outbox_jsonl"
validate_artifact "full_market_observations.jsonl" "full_market_observations_jsonl"

for name in p1_shadow_outbox.jsonl full_market_observations.jsonl manifest.env; do
  mv -f -- "$INCOMING/$name" "$DATA_ROOT/$name"
done
rm -f "$ARCHIVE"

printf 'last_sync_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$DATA_ROOT/.last_sync"

echo "O'Pip learning evidence sync: OK"
