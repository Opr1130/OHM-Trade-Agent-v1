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

for cmd in install flock cp mv stat date sha256sum getent chown chmod touch dirname rm find sort xargs awk; do
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
  local archive_source="${3:-}"
  local archive_name="${4:-}"
  local source_lock="$(dirname "$source")/.$(basename "$source").lock"
  local temp="$EXPORT_ROOT/.$name.tmp.$$"
  local target="$EXPORT_ROOT/$name"
  local archive_temp=""
  local archive_target=""

  if getent group "$READER_GROUP" >/dev/null 2>&1; then
    install -d -o root -g "$READER_GROUP" -m 0750 \
      "$(dirname "$temp")" "$(dirname "$target")"
  else
    install -d -o root -g root -m 0700 \
      "$(dirname "$temp")" "$(dirname "$target")"
  fi
  if [[ -n "$archive_name" ]]; then
    archive_temp="$EXPORT_ROOT/.$archive_name.tmp.$"
    archive_target="$EXPORT_ROOT/$archive_name"
    rm -rf -- "$archive_temp"
    if getent group "$READER_GROUP" >/dev/null 2>&1; then
      install -d -o root -g "$READER_GROUP" -m 0750 "$archive_temp"
    else
      install -d -o root -g root -m 0700 "$archive_temp"
    fi
  fi

  exec {source_fd}>>"$source_lock"
  flock -s "$source_fd"
  if [[ -e "$source" ]]; then
    cp -- "$source" "$temp"
  else
    : > "$temp"
  fi
  if [[ -n "$archive_name" && -d "$archive_source" ]]; then
    cp -a -- "$archive_source/." "$archive_temp/"
  fi
  flock -u "$source_fd"
  eval "exec ${source_fd}>&-"

  if getent group "$READER_GROUP" >/dev/null 2>&1; then
    chown root:"$READER_GROUP" "$temp"
    chmod 0640 "$temp"
  else
    chown root:root "$temp"
    chmod 0600 "$temp"
  fi
  mv -f -- "$temp" "$target"
  if [[ -n "$archive_name" ]]; then
    if getent group "$READER_GROUP" >/dev/null 2>&1; then
      chown -R root:"$READER_GROUP" "$archive_temp"
      find "$archive_temp" -type d -exec chmod 0750 {} +
      find "$archive_temp" -type f -exec chmod 0640 {} +
    else
      chown -R root:root "$archive_temp"
      find "$archive_temp" -type d -exec chmod 0700 {} +
      find "$archive_temp" -type f -exec chmod 0600 {} +
    fi
    rm -rf -- "$archive_target"
    mv -f -- "$archive_temp" "$archive_target"
  fi
}

copy_locked_jsonl "$DATA_ROOT/p1_shadow_outbox.jsonl" "p1_shadow_outbox.jsonl"
copy_locked_jsonl "$DATA_ROOT/full_market_observations.jsonl" "full_market_observations.jsonl"
copy_locked_jsonl "$DATA_ROOT/p1_evidence_ledger.jsonl" "p1_evidence_ledger.jsonl"
copy_locked_jsonl "$DATA_ROOT/intelligence_learning/events.jsonl" "intelligence_learning/events.jsonl"
copy_locked_jsonl "$DATA_ROOT/opip/qualification/screening_evaluations.jsonl" "opip/qualification/screening_evaluations.jsonl" "$DATA_ROOT/opip/qualification/screening_evaluations_archive" "opip/qualification/screening_evaluations_archive"
copy_locked_jsonl "$DATA_ROOT/opip/qualification/funnel_events.jsonl" "opip/qualification/funnel_events.jsonl" "$DATA_ROOT/opip/qualification/funnel_events_archive" "opip/qualification/funnel_events_archive"
copy_locked_jsonl "$DATA_ROOT/opip/qualification/scan_summaries.jsonl" "opip/qualification/scan_summaries.jsonl" "$DATA_ROOT/opip/qualification/scan_summaries_archive" "opip/qualification/scan_summaries_archive"
copy_locked_jsonl "$DATA_ROOT/paper_trading/events.jsonl" "paper_trading/events.jsonl"
copy_locked_jsonl "$DATA_ROOT/telegram_delivery_events.jsonl" "telegram_delivery_events.jsonl"
copy_locked_jsonl "$DATA_ROOT/decision_telemetry.jsonl" "decision_telemetry.jsonl"
copy_locked_jsonl "$DATA_ROOT/opip_trade_quality_evidence_v1.jsonl" "opip_trade_quality_evidence_v1.jsonl"
copy_locked_jsonl "$DATA_ROOT/candidate_trace.jsonl" "candidate_trace.jsonl"

manifest_tmp="$EXPORT_ROOT/.manifest.env.tmp.$$"
tree_bytes() {
  find "$1" -type f -printf '%s\n' | awk '{total += $1} END {printf "%d\n", total}'
}
tree_sha256() {
  (
    cd "$1"
    find . -type f -print0 | sort -z | xargs -0 -r sha256sum
  ) | sha256sum | awk '{print $1}'
}
{
  printf 'schema_version=3\n'
  printf 'exported_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  while IFS='|' read -r name key; do
    path="$EXPORT_ROOT/$name"
    printf '%s_bytes=%s\n' "$key" "$(stat -c '%s' "$path")"
    printf '%s_sha256=%s\n' "$key" "$(sha256sum "$path" | awk '{print $1}')"
  done <<'ARTIFACTS'
p1_shadow_outbox.jsonl|p1_shadow_outbox_jsonl
full_market_observations.jsonl|full_market_observations_jsonl
p1_evidence_ledger.jsonl|p1_evidence_ledger_jsonl
intelligence_learning/events.jsonl|intelligence_learning_events_jsonl
opip/qualification/screening_evaluations.jsonl|opip_qualification_screening_evaluations_jsonl
opip/qualification/funnel_events.jsonl|opip_qualification_funnel_events_jsonl
opip/qualification/scan_summaries.jsonl|opip_qualification_scan_summaries_jsonl
paper_trading/events.jsonl|paper_trading_events_jsonl
telegram_delivery_events.jsonl|telegram_delivery_events_jsonl
decision_telemetry.jsonl|decision_telemetry_jsonl
opip_trade_quality_evidence_v1.jsonl|opip_trade_quality_evidence_v1_jsonl
candidate_trace.jsonl|candidate_trace_jsonl
ARTIFACTS
  while IFS='|' read -r name key; do
    path="$EXPORT_ROOT/$name"
    printf '%s_bytes=%s\n' "$key" "$(tree_bytes "$path")"
    printf '%s_sha256=%s\n' "$key" "$(tree_sha256 "$path")"
  done <<'ARCHIVES'
opip/qualification/screening_evaluations_archive|opip_qualification_screening_archive
opip/qualification/funnel_events_archive|opip_qualification_funnel_archive
opip/qualification/scan_summaries_archive|opip_qualification_summaries_archive
ARCHIVES
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
