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

for cmd in ssh tar install flock mv date sha256sum stat awk rm find sort xargs; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing learning sync command: $cmd" >&2
    exit 69
  }
done

install -d -o root -g root -m 0755 "$DATA_ROOT" "$INCOMING" "$STATE_ROOT"

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
capture_disposition_file="$STATE_ROOT/capture.disposition.env"
outcomes_disposition_file="$STATE_ROOT/outcomes.disposition.env"
last_sync_file="$DATA_ROOT/.last_sync"
last_sync_at="$(status_time "$(state_value "$last_sync_file" last_sync_at_utc)")"
capture_at="$(status_time "$(state_value "$capture_file" finished_at_utc)")"
capture_rc="$(status_rc "$(state_value "$capture_file" exit_code)")"
outcomes_at="$(status_time "$(state_value "$outcomes_file" finished_at_utc)")"
outcomes_rc="$(status_rc "$(state_value "$outcomes_file" exit_code)")"

status_token() {
  local raw="$1"
  local fallback="${2:-NONE}"
  if [[ "$raw" =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
    printf '%s\n' "$raw"
  else
    printf '%s\n' "$fallback"
  fi
}

status_count() {
  local raw="$1"
  if [[ "$raw" =~ ^[0-9]{1,9}$ ]]; then
    printf '%s\n' "$raw"
  else
    printf 'NONE\n'
  fi
}

capture_disposition="$(status_token "$(state_value "$capture_disposition_file" disposition)")"
outcomes_disposition="$(status_token "$(state_value "$outcomes_disposition_file" disposition)")"
release_compatibility="$(status_token "$(state_value "$outcomes_disposition_file" release_compatibility_status)" NONE)"
if [[ "$release_compatibility" == "NONE" ]]; then
  release_compatibility="$(status_token "$(state_value "$capture_disposition_file" release_compatibility_status)" NONE)"
fi
pending_ack="$(status_count "$(state_value "$outcomes_disposition_file" accountability_pending_count)")"
if [[ "$pending_ack" == "NONE" ]]; then
  # Prefer the JSON consumption summary written by the outcomes job when present.
  pending_ack="$(
    awk -F'[:,]' '
      /"accountability_pending_count"/ {
        for (i = 1; i <= NF; i++) {
          if ($i ~ /accountability_pending_count/) {
            gsub(/[^0-9]/, "", $(i + 1))
            if ($(i + 1) != "") { print $(i + 1); exit }
          }
        }
      }
    ' "$DATA_ROOT/.learning_consumption/outcomes.json" 2>/dev/null || true
  )"
  pending_ack="$(status_count "$pending_ack")"
fi

# Evidence sync may still run under RELEASE_DRIFT so operators retain pull
# diagnostics; capture/outcomes refuse compute separately (fail closed).
status_command="opip-export-v2 sha=$OPIP_DEPLOYED_SHA sync_success_at=$last_sync_at capture_at=$capture_at capture_rc=$capture_rc outcomes_at=$outcomes_at outcomes_rc=$outcomes_rc capture_disposition=$capture_disposition outcomes_disposition=$outcomes_disposition release_compatibility=$release_compatibility outcomes_pending_ack=$pending_ack"

rm -rf "$INCOMING"/*
rm -f "$ARCHIVE"

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
[[ "$schema" == "3" ]] || {
  echo "O'Pip learning sync: unsupported manifest schema=$schema" >&2
  exit 65
}

# Record release compatibility against the synced export for diagnose lag.
# Sync itself is allowed under drift so evidence pull remains diagnosable.
production_sha="$(manifest_value production_deployed_sha)"
release_status="UNVERIFIED"
if [[ "$OPIP_DEPLOYED_SHA" =~ ^[0-9a-f]{40}$ && "$production_sha" =~ ^[0-9a-f]{40}$ ]]; then
  if [[ "$OPIP_DEPLOYED_SHA" == "$production_sha" ]]; then
    release_status="CURRENT"
  else
    release_status="RELEASE_DRIFT"
  fi
fi
printf 'release_compatibility_status=%s\nproduction_deployed_sha=%s\nworker_deployed_sha=%s\nrecorded_at_utc=%s\n' \
  "$release_status" \
  "${production_sha:-}" \
  "$OPIP_DEPLOYED_SHA" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$STATE_ROOT/release_compatibility.env.tmp"
mv -f -- "$STATE_ROOT/release_compatibility.env.tmp" "$STATE_ROOT/release_compatibility.env"
if [[ "$release_status" == "RELEASE_DRIFT" ]]; then
  echo "O'Pip learning sync: RELEASE_DRIFT worker=$OPIP_DEPLOYED_SHA production=$production_sha (sync allowed; compute blocked)" >&2
fi

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

tree_bytes() {
  find "$1" -type f -printf '%s\n' | awk '{total += $1} END {printf "%d\n", total}'
}

tree_sha256() {
  (
    cd "$1"
    find . -type f -print0 | sort -z | xargs -0 -r sha256sum
  ) | sha256sum | awk '{print $1}'
}

validate_archive() {
  local name="$1"
  local key="$2"
  local path="$INCOMING/$name"
  local expected_bytes expected_sha actual_bytes actual_sha
  [[ -d "$path" ]] || {
    echo "O'Pip learning sync: missing archive directory=$name" >&2
    return 1
  }
  expected_bytes="$(manifest_value "${key}_bytes")"
  expected_sha="$(manifest_value "${key}_sha256")"
  actual_bytes="$(tree_bytes "$path")"
  actual_sha="$(tree_sha256 "$path")"
  [[ "$expected_bytes" =~ ^[0-9]+$ && "$actual_bytes" == "$expected_bytes" ]] || {
    echo "O'Pip learning sync: archive size mismatch for $name" >&2
    return 1
  }
  [[ "$expected_sha" =~ ^[0-9a-f]{64}$ && "$actual_sha" == "$expected_sha" ]] || {
    echo "O'Pip learning sync: archive checksum mismatch for $name" >&2
    return 1
  }
}

validate_artifact "p1_shadow_outbox.jsonl" "p1_shadow_outbox_jsonl"
validate_artifact "full_market_observations.jsonl" "full_market_observations_jsonl"
validate_artifact "p1_evidence_ledger.jsonl" "p1_evidence_ledger_jsonl"
validate_artifact "intelligence_learning/events.jsonl" "intelligence_learning_events_jsonl"
validate_artifact "opip/qualification/screening_evaluations.jsonl" "opip_qualification_screening_evaluations_jsonl"
validate_artifact "opip/qualification/funnel_events.jsonl" "opip_qualification_funnel_events_jsonl"
validate_artifact "opip/qualification/scan_summaries.jsonl" "opip_qualification_scan_summaries_jsonl"
validate_artifact "paper_trading/events.jsonl" "paper_trading_events_jsonl"
validate_artifact "telegram_delivery_events.jsonl" "telegram_delivery_events_jsonl"
validate_artifact "decision_telemetry.jsonl" "decision_telemetry_jsonl"
validate_artifact "opip_trade_quality_evidence_v1.jsonl" "opip_trade_quality_evidence_v1_jsonl"
validate_artifact "candidate_trace.jsonl" "candidate_trace_jsonl"
validate_archive "opip/qualification/screening_evaluations_archive" "opip_qualification_screening_archive"
validate_archive "opip/qualification/funnel_events_archive" "opip_qualification_funnel_archive"
validate_archive "opip/qualification/scan_summaries_archive" "opip_qualification_summaries_archive"

for name in \
  p1_shadow_outbox.jsonl \
  full_market_observations.jsonl \
  p1_evidence_ledger.jsonl \
  intelligence_learning/events.jsonl \
  opip/qualification/screening_evaluations.jsonl \
  opip/qualification/funnel_events.jsonl \
  opip/qualification/scan_summaries.jsonl \
  paper_trading/events.jsonl \
  telegram_delivery_events.jsonl \
  decision_telemetry.jsonl \
  opip_trade_quality_evidence_v1.jsonl \
  candidate_trace.jsonl; do
  install -d -o root -g root -m 0755 "$(dirname "$DATA_ROOT/$name")"
  mv -f -- "$INCOMING/$name" "$DATA_ROOT/$name"
done
for name in \
  opip/qualification/screening_evaluations_archive \
  opip/qualification/funnel_events_archive \
  opip/qualification/scan_summaries_archive; do
  install -d -o root -g root -m 0755 "$(dirname "$DATA_ROOT/$name")"
  rm -rf -- "$DATA_ROOT/$name"
  mv -f -- "$INCOMING/$name" "$DATA_ROOT/$name"
done
mv -f -- "$INCOMING/manifest.env" "$DATA_ROOT/manifest.env"
rm -f "$ARCHIVE"

printf 'last_sync_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$DATA_ROOT/.last_sync"

echo "O'Pip learning evidence sync: OK"
