#!/usr/bin/env bash
set -Eeuo pipefail

EXPORT_ROOT="/var/lib/opip-learning-export"
PUBLISH_LOCK="$EXPORT_ROOT/.publish.lock"
READER_STATE_ROOT="/var/lib/opip-learning-reader"
READER_STATE_FILE="$READER_STATE_ROOT/last_sync_request.env"
ORIGINAL="${SSH_ORIGINAL_COMMAND:-}"

for cmd in date mv flock tar; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "O'Pip learning reader: missing $cmd" >&2
    exit 69
  }
done

write_reader_state() {
  local protocol="$1"
  local worker_sha="$2"
  local sync_success_at="$3"
  local capture_at="$4"
  local capture_rc="$5"
  local outcomes_at="$6"
  local outcomes_rc="$7"

  [[ -d "$READER_STATE_ROOT" && -w "$READER_STATE_ROOT" ]] || return 0
  local tmp="$READER_STATE_ROOT/.last_sync_request.env.tmp.$$"
  umask 077
  {
    printf 'observed_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'protocol=%s\n' "$protocol"
    printf 'worker_deployed_sha=%s\n' "$worker_sha"
    printf 'last_successful_sync_at_utc=%s\n' "$sync_success_at"
    printf 'capture_finished_at_utc=%s\n' "$capture_at"
    printf 'capture_exit_code=%s\n' "$capture_rc"
    printf 'outcomes_finished_at_utc=%s\n' "$outcomes_at"
    printf 'outcomes_exit_code=%s\n' "$outcomes_rc"
  } > "$tmp"
  mv -f -- "$tmp" "$READER_STATE_FILE"
}

validate_status_value() {
  local value="$1"
  [[ "$value" == "NONE" || "$value" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]
}

if [[ "$ORIGINAL" == "opip-export-v1" ]]; then
  write_reader_state "1" "UNKNOWN" "UNKNOWN" "UNKNOWN" "UNKNOWN" "UNKNOWN" "UNKNOWN"
elif [[ "$ORIGINAL" =~ ^opip-export-v2[[:space:]]+sha=([0-9a-f]{40})[[:space:]]+sync_success_at=([^[:space:]]+)[[:space:]]+capture_at=([^[:space:]]+)[[:space:]]+capture_rc=([^[:space:]]+)[[:space:]]+outcomes_at=([^[:space:]]+)[[:space:]]+outcomes_rc=([^[:space:]]+)$ ]]; then
  worker_sha="${BASH_REMATCH[1]}"
  sync_success_at="${BASH_REMATCH[2]}"
  capture_at="${BASH_REMATCH[3]}"
  capture_rc="${BASH_REMATCH[4]}"
  outcomes_at="${BASH_REMATCH[5]}"
  outcomes_rc="${BASH_REMATCH[6]}"
  validate_status_value "$sync_success_at" || {
    echo "O'Pip learning reader: invalid sync-success timestamp" >&2
    exit 126
  }
  validate_status_value "$capture_at" || {
    echo "O'Pip learning reader: invalid capture timestamp" >&2
    exit 126
  }
  validate_status_value "$outcomes_at" || {
    echo "O'Pip learning reader: invalid outcomes timestamp" >&2
    exit 126
  }
  [[ "$capture_rc" == "NONE" || "$capture_rc" =~ ^[0-9]{1,3}$ ]] || {
    echo "O'Pip learning reader: invalid capture rc" >&2
    exit 126
  }
  [[ "$outcomes_rc" == "NONE" || "$outcomes_rc" =~ ^[0-9]{1,3}$ ]] || {
    echo "O'Pip learning reader: invalid outcomes rc" >&2
    exit 126
  }
  write_reader_state "2" "$worker_sha" "$sync_success_at" "$capture_at" "$capture_rc" "$outcomes_at" "$outcomes_rc"
else
  echo "O'Pip learning reader: command rejected" >&2
  exit 126
fi

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
  candidate_trace.jsonl \
  opip/qualification/screening_evaluations_archive \
  opip/qualification/funnel_events_archive \
  opip/qualification/scan_summaries_archive \
  manifest.env; do
  if [[ ! -r "$EXPORT_ROOT/$name" ]]; then
    echo "O'Pip learning reader: export unavailable: $name" >&2
    exit 66
  fi
done

# The forced command is read-only with respect to trading/export artifacts.
# A shared publish lock prevents the exporter from replacing any artifact while
# the tar stream is emitted. The only write is a bounded observability heartbeat
# in the dedicated reader-state directory.
exec 8<"$PUBLISH_LOCK"
flock -s 8
exec tar -C "$EXPORT_ROOT" -cf - \
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
  candidate_trace.jsonl \
  opip/qualification/screening_evaluations_archive \
  opip/qualification/funnel_events_archive \
  opip/qualification/scan_summaries_archive \
  manifest.env
