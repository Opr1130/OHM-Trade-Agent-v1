#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/opt/OHM-Trade-Agent-v1"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
EXPORT_ROOT="/var/lib/opip-learning-export"
READER_STATE_ROOT="/var/lib/opip-learning-reader"
READER_STATE_FILE="$READER_STATE_ROOT/last_sync_request.env"
MANIFEST="$EXPORT_ROOT/manifest.env"
EXPORT_CRON="/etc/cron.d/opip-learning-export"
MAX_EXPORT_AGE_SECONDS=300
MAX_SYNC_AGE_SECONDS=720
MAX_CAPTURE_AGE_SECONDS=900
MAX_OUTCOMES_AGE_SECONDS=1800
MAX_FUTURE_SKEW_SECONDS=120

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run O'Pip learning diagnostics as root" >&2
  exit 77
fi

for cmd in date stat awk git docker; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing diagnostics command: $cmd" >&2
    exit 69
  }
done

now_epoch="$(date -u +%s)"
status="OK"
degrade() {
  if [[ "$status" == "OK" ]]; then
    status="DEGRADED"
  fi
}

env_value() {
  local file="$1"
  local key="$2"
  awk -F= -v k="$key" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$file" 2>/dev/null || true
}

age_seconds() {
  local raw="$1"
  local epoch
  [[ -n "$raw" ]] || return 1
  epoch="$(date -u -d "$raw" +%s 2>/dev/null || true)"
  [[ "$epoch" =~ ^[0-9]+$ ]] || return 1
  if (( epoch > now_epoch + MAX_FUTURE_SKEW_SECONDS )); then
    return 1
  elif (( epoch > now_epoch )); then
    printf '0\n'
  else
    printf '%s\n' "$((now_epoch - epoch))"
  fi
}

echo "OPIP_LEARNING_DIAGNOSTICS"
echo "checked_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

current_sha="$(cat /var/lib/ohm-deploy/last-good-sha 2>/dev/null || true)"
if [[ ! "$current_sha" =~ ^[0-9a-f]{40}$ ]]; then
  current_sha="$(git -c safe.directory="$REPO_ROOT" -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
fi
echo "production_sha=${current_sha:-UNKNOWN}"

if [[ -s "$EXPORT_CRON" ]]; then
  echo "production_export_cron=PRESENT"
else
  echo "production_export_cron=MISSING"
  status="FAIL"
fi

if [[ -s "$MANIFEST" ]]; then
  exported_at="$(env_value "$MANIFEST" exported_at_utc)"
  export_age="$(age_seconds "$exported_at" || true)"
  echo "exported_at_utc=${exported_at:-UNKNOWN}"
  echo "export_age_seconds=${export_age:-UNKNOWN}"
  echo "p1_shadow_outbox_jsonl_bytes=$(env_value "$MANIFEST" p1_shadow_outbox_jsonl_bytes)"
  echo "full_market_observations_jsonl_bytes=$(env_value "$MANIFEST" full_market_observations_jsonl_bytes)"
  if [[ ! "$export_age" =~ ^[0-9]+$ ]] || (( export_age > MAX_EXPORT_AGE_SECONDS )); then
    degrade
  fi
else
  echo "export_manifest=MISSING"
  status="FAIL"
fi

if [[ -s "$READER_STATE_FILE" ]]; then
  sync_seen="$(env_value "$READER_STATE_FILE" observed_at_utc)"
  request_age="$(age_seconds "$sync_seen" || true)"
  successful_sync_at="$(env_value "$READER_STATE_FILE" last_successful_sync_at_utc)"
  sync_age="$(age_seconds "$successful_sync_at" || true)"
  protocol="$(env_value "$READER_STATE_FILE" protocol)"
  worker_sha="$(env_value "$READER_STATE_FILE" worker_deployed_sha)"
  capture_at="$(env_value "$READER_STATE_FILE" capture_finished_at_utc)"
  capture_rc="$(env_value "$READER_STATE_FILE" capture_exit_code)"
  outcomes_at="$(env_value "$READER_STATE_FILE" outcomes_finished_at_utc)"
  outcomes_rc="$(env_value "$READER_STATE_FILE" outcomes_exit_code)"
  capture_age="$(age_seconds "$capture_at" || true)"
  outcomes_age="$(age_seconds "$outcomes_at" || true)"
  echo "worker_sync_request_observed_at_utc=${sync_seen:-UNKNOWN}"
  echo "worker_sync_request_age_seconds=${request_age:-UNKNOWN}"
  echo "worker_last_successful_sync_at_utc=${successful_sync_at:-UNKNOWN}"
  echo "worker_successful_sync_age_seconds=${sync_age:-UNKNOWN}"
  echo "worker_status_protocol=${protocol:-UNKNOWN}"
  echo "worker_deployed_sha=${worker_sha:-UNKNOWN}"
  echo "capture_finished_at_utc=${capture_at:-UNKNOWN}"
  echo "capture_age_seconds=${capture_age:-UNKNOWN}"
  echo "capture_exit_code=${capture_rc:-UNKNOWN}"
  echo "outcomes_finished_at_utc=${outcomes_at:-UNKNOWN}"
  echo "outcomes_age_seconds=${outcomes_age:-UNKNOWN}"
  echo "outcomes_exit_code=${outcomes_rc:-UNKNOWN}"
  if [[ ! "$sync_age" =~ ^[0-9]+$ ]] || (( sync_age > MAX_SYNC_AGE_SECONDS )); then
    echo "worker_evidence_sync_status=STALE_OR_UNVERIFIED"
    degrade
  else
    echo "worker_evidence_sync_status=OK"
  fi
  if [[ "$protocol" != "2" ]]; then
    echo "worker_compute_status=UNVERIFIED_LEGACY_SYNC_PROTOCOL"
    degrade
  elif [[ "$worker_sha" != "$current_sha" ]]; then
    echo "worker_compute_status=RELEASE_DRIFT"
    degrade
  elif [[ "$capture_rc" != "0" || "$outcomes_rc" != "0" ]]; then
    echo "worker_compute_status=FAILED_OR_INCOMPLETE"
    degrade
  elif [[ ! "$capture_age" =~ ^[0-9]+$ || "$capture_age" -gt "$MAX_CAPTURE_AGE_SECONDS" ]]; then
    echo "worker_compute_status=CAPTURE_STALE"
    degrade
  elif [[ ! "$outcomes_age" =~ ^[0-9]+$ || "$outcomes_age" -gt "$MAX_OUTCOMES_AGE_SECONDS" ]]; then
    echo "worker_compute_status=OUTCOMES_STALE"
    degrade
  else
    echo "worker_compute_status=OK"
  fi
else
  echo "worker_sync_heartbeat=MISSING"
  echo "worker_compute_status=UNVERIFIED"
  degrade
fi

if docker inspect ohm-trade-agent >/dev/null 2>&1; then
  core_running="$(docker inspect --format='{{.State.Running}}' ohm-trade-agent 2>/dev/null || true)"
  if [[ "$core_running" != "true" ]]; then
    echo "production_validation_data=CORE_CONTAINER_STOPPED"
    status="FAIL"
    analytics=""
  else
    analytics="$(
    docker exec ohm-trade-agent python -c '
import json
from app.services.dashboard_read_model import build_dashboard_read_model
d = build_dashboard_read_model(scope="all")
i = d.get("intelligence") or {}
p = i.get("paper_performance") or {}
pe = d.get("paper_engine") or {}
ps = pe.get("status") or {}
recent = d.get("recent_events") or []
out = {
    "generated_at_utc": d.get("generated_at_utc"),
    "evidence_state": i.get("evidence_state"),
    "events_considered": i.get("events_considered"),
    "early_watch_journeys": i.get("early_watch_journeys"),
    "qualified_signals": i.get("qualified_signals"),
    "paper_requested_signals": i.get("paper_requested_signals"),
    "paper_outcome_signals": i.get("paper_outcome_signals"),
    "paper_outcomes": p.get("count"),
    "paper_wins": p.get("wins"),
    "paper_losses": p.get("losses"),
    "paper_win_rate_pct": p.get("win_rate_pct"),
    "paper_avg_return_pct": p.get("avg_return_pct"),
    "calibration_samples": i.get("calibration_samples"),
    "paper_engine_status": ps.get("status"),
    "paper_open_trades": ps.get("open_trades"),
    "paper_closed_trades": ps.get("closed_trades"),
    "paper_realized_pnl_by_currency": ps.get("realized_pnl_by_currency"),
    "latest_intelligence_event_at": (recent[0].get("observed_at") if recent else None),
}
print(json.dumps(out, sort_keys=True, separators=(",", ":")))
' 2>/dev/null || true
    )"
  fi
  if [[ "$core_running" == "true" && -n "$analytics" ]]; then
    echo "production_validation_data=$analytics"
  elif [[ "$core_running" == "true" ]]; then
    echo "production_validation_data=UNAVAILABLE"
    degrade
  fi
else
  echo "production_validation_data=CORE_CONTAINER_MISSING"
  status="FAIL"
fi

if docker inspect ohm-trade-agent >/dev/null 2>&1 \
   && [[ "$(docker inspect --format='{{.State.Running}}' ohm-trade-agent 2>/dev/null || true)" == "true" ]]; then
  qualification_funnel="$(
    docker exec ohm-trade-agent python -m app.opip.decision.diagnostics_cli --hours 24 2>/dev/null || true
  )"
  if [[ -n "$qualification_funnel" ]]; then
    printf '%s\n' "$qualification_funnel"
  else
    echo "OPIP_QUALIFICATION_FUNNEL"
    echo "diagnostic=UNAVAILABLE"
    degrade
  fi
else
  echo "OPIP_QUALIFICATION_FUNNEL"
  echo "diagnostic=UNAVAILABLE"
  degrade
fi

# Read-only production runtime evidence for diagnosing a zero-signal state.
# This deliberately reports only bounded scheduler/operator/scan counters. It
# does not print credentials, environment variables, candidate payloads, or
# mutate operator state, ranking, alerting, paper admission, or exchange state.
if docker inspect ohm-trade-agent >/dev/null 2>&1 \
   && [[ "$(docker inspect --format='{{.State.Running}}' ohm-trade-agent 2>/dev/null || true)" == "true" ]]; then
  runtime_data="$(
    docker exec ohm-trade-agent python -c '
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.services.active_trade_registry import get_active_trades
from app.services.operator_control import (
    DEFAULT_TIMEZONE,
    MAX_OCCUPIED_SLOTS,
    NORMAL_SEARCH_INTERVAL_SECONDS,
    QUIET_END_HOUR,
    QUIET_START_HOUR,
    STATE_FILE,
    THROTTLE_AT_SLOTS,
    THROTTLED_SEARCH_INTERVAL_SECONDS,
    VALID_OVERRIDES,
)
from app.services.operations_analytics import SCAN_ACTIVITY_FILE, _read_jsonl
from app.services.order_intent_registry import get_live_order_intents
from app.services.pending_setup_registry import get_pending_setups
from app.services.registry_io import load_json

now = datetime.now(timezone.utc)
state = load_json(STATE_FILE)
override = str(state.get("override_mode") or "AUTO").upper()
if override not in VALID_OVERRIDES:
    override = "AUTO"
active_count = len(get_active_trades())
pending_count = len(get_pending_setups())
order_count = len(get_live_order_intents())
occupied = active_count + order_count
local_hour = now.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).hour
quiet = local_hour >= QUIET_START_HOUR or local_hour < QUIET_END_HOUR

def parsed(value):
    if not value:
        return None
    try:
        item = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if item.tzinfo is None:
        item = item.replace(tzinfo=timezone.utc)
    return item.astimezone(timezone.utc)

cooldown_until = parsed(state.get("cooldown_until"))
if override == "MAINTENANCE":
    effective = "MAINTENANCE"
    search_allowed = False
    interval = 0
    reason = "operator override"
elif override == "MONITOR":
    effective = "MONITOR"
    search_allowed = False
    interval = 0
    reason = "operator override"
elif override == "SEARCH":
    effective = "SEARCH"
    search_allowed = True
    interval = THROTTLED_SEARCH_INTERVAL_SECONDS if occupied >= THROTTLE_AT_SLOTS else NORMAL_SEARCH_INTERVAL_SECONDS
    reason = "operator override"
elif quiet:
    effective = "MONITOR"
    search_allowed = False
    interval = 0
    reason = "quiet hours 23:00-05:00 America/New_York"
elif occupied >= MAX_OCCUPIED_SLOTS:
    effective = "MONITOR"
    search_allowed = False
    interval = 0
    reason = "portfolio capacity reached"
elif cooldown_until is not None and now < cooldown_until:
    effective = "MONITOR"
    search_allowed = False
    interval = 0
    reason = "capacity-release cooldown"
else:
    effective = "SEARCH"
    search_allowed = True
    interval = THROTTLED_SEARCH_INTERVAL_SECONDS if occupied >= THROTTLE_AT_SLOTS else NORMAL_SEARCH_INTERVAL_SECONDS
    reason = "throttled search: two occupied slots" if occupied >= THROTTLE_AT_SLOTS else "capacity available"
last_search_started = parsed(state.get("last_search_started_at"))
search_due = bool(
    search_allowed
    and (
        last_search_started is None
        or (now - last_search_started).total_seconds() >= interval
    )
)

rows = _read_jsonl(SCAN_ACTIVITY_FILE)
recent = []
for row in rows:
    at = parsed(row.get("completed_at_utc") or row.get("timestamp_utc"))
    if at is not None and at >= now - timedelta(hours=24):
        recent.append(row)
latest = rows[-1] if rows else {}
last_at = parsed(latest.get("completed_at_utc") or latest.get("timestamp_utc"))
last_age = None if last_at is None else max(0, int((now - last_at).total_seconds()))
out = {
    "override_mode": override,
    "effective_mode": effective,
    "reason": reason,
    "quiet_hours": quiet,
    "search_allowed": search_allowed,
    "search_due": search_due,
    "search_interval_seconds": interval,
    "occupied_slots": occupied,
    "active_trades": active_count,
    "pending_setups": pending_count,
    "live_order_intents": order_count,
    "cooldown_until": (cooldown_until.isoformat() if cooldown_until else None),
    "last_search_started_at": (last_search_started.isoformat() if last_search_started else None),
    "scan_activity_rows_total": len(rows),
    "scan_activity_rows_24h": len(recent),
    "last_broad_scan_utc": (last_at.isoformat() if last_at else None),
    "last_broad_scan_age_seconds": last_age,
    "last_broad_scan_requested": latest.get("requested"),
    "last_broad_scan_analyzed": latest.get("analyzed"),
    "last_broad_scan_technical_shortlist": latest.get("technical_shortlist"),
    "last_broad_scan_qualified_survivors": latest.get("qualified_survivors"),
    "last_broad_scan_notifications_sent": latest.get("notifications_sent"),
}
print(json.dumps(out, sort_keys=True, separators=(",", ":")))
' 2>/dev/null || true
  )"
  if [[ -n "$runtime_data" ]]; then
    echo "production_runtime_data=$runtime_data"
  else
    echo "production_runtime_data=UNAVAILABLE"
    degrade
  fi
else
  echo "production_runtime_data=CORE_CONTAINER_UNAVAILABLE"
  degrade
fi

echo "diagnostics_status=$status"
[[ "$status" != "FAIL" ]]
