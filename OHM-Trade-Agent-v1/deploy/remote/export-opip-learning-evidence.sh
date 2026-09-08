#!/usr/bin/env bash
set -Eeuo pipefail

# Migration audit: the pre-retirement export contract used schema_version=3.
# Active output below is schema 4 and intentionally excludes the P1 shadow outbox.

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

for cmd in install flock cp mv stat date sha256sum getent chown chmod touch dirname rm find sort xargs awk grep tr; do
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

# Write export-tree empty attestation from canonical copied files only.
# Never writes into DATA_ROOT. Eligibility must match
# production_empty_export_attestation_eligible() in
# app/opip/learning/empty_export_attestation.py. A complete=false
# window-index is ambiguous production lineage and never mints proof.
# When state.json exists it must match the exact certified-empty schema
# (same keys/values as _window_index_state_proves_empty_archive_without_manifest).
state_json_is_certified_empty_without_manifest() {
  local state="$1"
  [[ -f "$state" ]] || return 1
  local compact
  # Production writers emit compact sorted JSON. Strip insignificant
  # whitespace so a pretty-printed certified-empty state still matches.
  # Fail closed on any other key set, value, or malformed fragment.
  compact="$(tr -d '[:space:]' <"$state")"
  [[ -n "$compact" ]] || return 1
  [[ "$compact" =~ ^\{\"complete\":true,\"coverage_day_count\":0,\"coverage_start_day\":null,\"coverage_through_day\":null,\"manifest_mtime_ns\":0,\"manifest_present\":false,\"manifest_sha256\":\"\",\"manifest_size\":0,\"schema_version\":1,\"shard_sha256\":\{\},\"updated_at_utc\":\"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})\"\}$ ]]
}

write_empty_export_attestation_if_canonical() {
  local hot_file="$1"
  local archive_dir="$2"
  local prefix="$3"
  [[ -n "$archive_dir" && -d "$archive_dir" && -n "$prefix" ]] || return 0

  local hot_bytes=0
  if [[ -f "$hot_file" ]]; then
    hot_bytes="$(stat -c '%s' "$hot_file")"
  fi
  if [[ "$hot_bytes" != "0" ]]; then
    return 0
  fi
  if [[ -f "$archive_dir/manifest.json" || -f "$archive_dir/manifest.json.sha256" ]]; then
    return 0
  fi

  local segment_count=0
  local segment
  while IFS= read -r -d '' segment; do
    segment_count=$((segment_count + 1))
  done < <(find "$archive_dir" -type f -name "${prefix}-*.jsonl.gz" -print0)
  if (( segment_count > 0 )); then
    return 0
  fi

  local index_dir="$archive_dir/window_index_v1"
  if [[ -d "$index_dir" ]]; then
    local extra_index=0
    local extra
    while IFS= read -r -d '' extra; do
      extra_index=$((extra_index + 1))
    done < <(find "$index_dir" -mindepth 1 ! -name 'state.json' -print0)
    if (( extra_index > 0 )); then
      return 0
    fi
    local state="$index_dir/state.json"
    if [[ ! -f "$state" ]]; then
      return 0
    fi
    if ! state_json_is_certified_empty_without_manifest "$state"; then
      return 0
    fi
  fi

  local sha
  sha="$(cat /var/lib/ohm-deploy/last-good-sha 2>/dev/null || true)"
  if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    sha=""
  fi
  local exported_at
  exported_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%s\n' "{\"archive_prefix\":\"${prefix}\",\"exported_at_utc\":\"${exported_at}\",\"hot_bytes\":0,\"kind\":\"empty_export_attestation_v1\",\"manifest_present\":false,\"production_deployed_sha\":\"${sha}\",\"schema_version\":1,\"segment_count\":0,\"signature_present\":false}" > "$archive_dir/empty_export_attestation_v1.json"
}

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
    archive_temp="$EXPORT_ROOT/.$archive_name.tmp.$$"
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

  if [[ -n "$archive_name" ]]; then
    write_empty_export_attestation_if_canonical \
      "$temp" \
      "$archive_temp" \
      "$(basename "$source" .jsonl)"
  fi

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

# P1 shadow outbox was retired after it grew large enough to choke the evidence
# plane. Never republish the legacy artifact; remove any stale exported copy
# while holding the exclusive publish lock.
rm -f -- "$EXPORT_ROOT/p1_shadow_outbox.jsonl"

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
production_deployed_sha="$(cat /var/lib/ohm-deploy/last-good-sha 2>/dev/null || true)"
if [[ ! "$production_deployed_sha" =~ ^[0-9a-f]{40}$ ]]; then
  # Leave empty so workers report UNVERIFIED and fail closed until the
  # authoritative deploy receipt exists. Do not fall back to git HEAD.
  production_deployed_sha=""
fi

{
  printf 'schema_version=4\n'
  printf 'exported_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'production_deployed_sha=%s\n' "$production_deployed_sha"
  printf 'p1_shadow_outbox_retired=1\n'
  while IFS='|' read -r name key; do
    path="$EXPORT_ROOT/$name"
    printf '%s_bytes=%s\n' "$key" "$(stat -c '%s' "$path")"
    printf '%s_sha256=%s\n' "$key" "$(sha256sum "$path" | awk '{print $1}')"
  done <<'ARTIFACTS'
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
