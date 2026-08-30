#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
DATA_ROOT="$APP_ROOT/data"
EXPORT_ROOT="/var/lib/opip-learning-export"
TRIGGER_LOCK="/var/run/opip-learning-export.lock"
PUBLISH_LOCK="$EXPORT_ROOT/.publish.lock"
READER_GROUP="opiplearn"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run O'Pip learning evidence export as root" >&2
  exit 77
fi

for cmd in install flock cp mv stat date sha256sum getent chown chmod touch; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required export command: $cmd" >&2
    exit 69
  }
done

if getent group "$READER_GROUP" >/dev/null 2>&1; then
  install -d -o root -g "$READER_GROUP" -m 0750 "$EXPORT_ROOT"
else
  install -d -o root -g root -m 0700 "$EXPORT_ROOT"
fi

exec 9>"$TRIGGER_LOCK"
if ! flock -n 9; then
  echo "O'Pip learning export already active; skipping"
  exit 0
fi

touch "$PUBLISH_LOCK"
if getent group "$READER_GROUP" >/dev/null 2>&1; then
  chown root:"$READER_GROUP" "$PUBLISH_LOCK"
  chmod 0640 "$PUBLISH_LOCK"
else
  chown root:root "$PUBLISH_LOCK"
  chmod 0600 "$PUBLISH_LOCK"
fi

# Readers take a shared lock on this same file. Holding the exclusive lock for
# the complete publish guarantees they can never receive mixed generations.
exec 8>"$PUBLISH_LOCK"
flock -x 8

copy_locked_jsonl() {
  local source="$1"
  local name="$2"
  local source_lock="$DATA_ROOT/.$name.lock"
  local temp="$EXPORT_ROOT/.$name.tmp.$$"
  local target="$EXPORT_ROOT/$name"

  if [[ -e "$source" ]]; then
    exec {source_fd}>>"$source_lock"
    flock -s "$source_fd"
    cp -- "$source" "$temp"
    flock -u "$source_fd"
    eval "exec ${source_fd}>&-"
  else
    : > "$temp"
  fi

  if getent group "$READER_GROUP" >/dev/null 2>&1; then
    chown root:"$READER_GROUP" "$temp"
    chmod 0640 "$temp"
  else
    chown root:root "$temp"
    chmod 0600 "$temp"
  fi
  mv -f -- "$temp" "$target"
}

copy_locked_jsonl "$DATA_ROOT/p1_shadow_outbox.jsonl" "p1_shadow_outbox.jsonl"
copy_locked_jsonl "$DATA_ROOT/full_market_observations.jsonl" "full_market_observations.jsonl"

manifest_tmp="$EXPORT_ROOT/.manifest.env.tmp.$$"
p1="$EXPORT_ROOT/p1_shadow_outbox.jsonl"
obs="$EXPORT_ROOT/full_market_observations.jsonl"
{
  printf 'schema_version=2\n'
  printf 'exported_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'p1_shadow_outbox_jsonl_bytes=%s\n' "$(stat -c '%s' "$p1")"
  printf 'p1_shadow_outbox_jsonl_sha256=%s\n' "$(sha256sum "$p1" | awk '{print $1}')"
  printf 'full_market_observations_jsonl_bytes=%s\n' "$(stat -c '%s' "$obs")"
  printf 'full_market_observations_jsonl_sha256=%s\n' "$(sha256sum "$obs" | awk '{print $1}')"
} > "$manifest_tmp"

if getent group "$READER_GROUP" >/dev/null 2>&1; then
  chown root:"$READER_GROUP" "$manifest_tmp"
  chmod 0640 "$manifest_tmp"
else
  chown root:root "$manifest_tmp"
  chmod 0600 "$manifest_tmp"
fi
mv -f -- "$manifest_tmp" "$EXPORT_ROOT/manifest.env"

echo "O'Pip learning evidence export: OK"
