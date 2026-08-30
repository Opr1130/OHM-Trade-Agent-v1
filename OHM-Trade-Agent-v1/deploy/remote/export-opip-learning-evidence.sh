#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
DATA_ROOT="$APP_ROOT/data"
EXPORT_ROOT="/var/lib/opip-learning-export"
LOCK_FILE="/var/run/opip-learning-export.lock"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run O'Pip learning evidence export as root" >&2
  exit 77
fi

for cmd in install flock cp mv stat date; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required export command: $cmd" >&2
    exit 69
  }
done

install -d -o root -g root -m 0755 "$EXPORT_ROOT"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "O'Pip learning export already active; skipping"
  exit 0
fi

copy_locked_jsonl() {
  local source="$1"
  local name="$2"
  local source_lock="$DATA_ROOT/.$name.lock"
  local temp="$EXPORT_ROOT/.$name.tmp.$$"
  local target="$EXPORT_ROOT/$name"

  if [[ ! -e "$source" ]]; then
    return 0
  fi

  exec {source_fd}>>"$source_lock"
  flock -s "$source_fd"
  cp -- "$source" "$temp"
  flock -u "$source_fd"
  eval "exec ${source_fd}>&-"

  chmod 0644 "$temp"
  mv -f -- "$temp" "$target"
}

copy_locked_jsonl "$DATA_ROOT/p1_shadow_outbox.jsonl" "p1_shadow_outbox.jsonl"
copy_locked_jsonl "$DATA_ROOT/full_market_observations.jsonl" "full_market_observations.jsonl"

manifest_tmp="$EXPORT_ROOT/.manifest.env.tmp.$$"
{
  printf 'schema_version=1\n'
  printf 'exported_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for name in p1_shadow_outbox.jsonl full_market_observations.jsonl; do
    path="$EXPORT_ROOT/$name"
    if [[ -e "$path" ]]; then
      printf '%s_bytes=%s\n' "${name//[^A-Za-z0-9]/_}" "$(stat -c '%s' "$path")"
    fi
  done
} > "$manifest_tmp"
chmod 0644 "$manifest_tmp"
mv -f -- "$manifest_tmp" "$EXPORT_ROOT/manifest.env"

echo "O'Pip learning evidence export: OK"
