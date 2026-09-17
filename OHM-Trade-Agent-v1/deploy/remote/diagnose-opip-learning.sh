#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/opt/OHM-Trade-Agent-v1"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
EXPORT_ROOT="/var/lib/opip-learning-export"
READER_STATE_ROOT="/var/lib/opip-learning-reader"
READER_STATE_FILE="$READER_STATE_ROOT/last_sync_request.env"
MANIFEST="$EXPORT_ROOT/manifest.env"
EXPORT_CRON="/etc/cron.d/opip-learning-export"
# Read-only export observability targets. Diagnostics must never acquire,
# create, release, repair or remove any of these: acquiring a lock here would
# perturb the exact state under investigation, and holding one could make a real
# export run skip.
EXPORT_CRON_SRC="$APP_ROOT/deploy/cron.d/opip-learning-export"
EXPORT_INTERNAL_LOCK="/var/run/opip-learning-export.lock"
EXPORT_WRAPPER_LOCK="/var/run/opip-learning-export-trigger.lock"
EXPORT_PUBLISH_LOCK="$EXPORT_ROOT/.publish.lock"
EXPORT_LOG="/var/log/opip-learning-export.log"
EXPORT_RELEASE_RECEIPT="/var/lib/ohm-deploy/last-good-sha"
REPLICA_DIR_NAME_PATTERN='^canonical_learning_replica\.[0-9a-f]{64}$'
EXPORT_LOG_TAIL_LINES=100
EXPORT_LOG_MAX_BYTES=20000
EXPORT_JOURNAL_MAX_LINES=40
# A healthy export completes well inside one scheduling interval (2m40s), so an
# observed holder or process older than this is stalled rather than merely busy.
# 300s matches the production operational export-freshness threshold and the
# existing MAX_EXPORT_AGE_SECONDS below.
EXPORT_STALL_THRESHOLD_SECONDS=300
# Only O'Pip export-specific syslog/journal records are matched. A generic CRON
# match would return unrelated system jobs.
EXPORT_JOURNAL_PATTERN='opip-learning-export|export-opip-learning-evidence|opip-learning-export-trigger\.lock'
# Process matching must NOT include the log filename or the lock filename: a
# long-lived `tail`, `less` or similar consumer of the log would otherwise be
# reported as an exporter and, if its age exceeded the stall threshold, would
# falsely raise the verdict. Match only the exporter script name, which both
# the cron flock wrapper and any direct invocation carry on their command line.
EXPORT_PROCESS_PATTERN='export-opip-learning-evidence\.sh'
MAX_EXPORT_AGE_SECONDS=300
MAX_SYNC_AGE_SECONDS=720
MAX_CAPTURE_AGE_SECONDS=900
MAX_OUTCOMES_AGE_SECONDS=1800
MAX_FUTURE_SKEW_SECONDS=120

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run O'Pip learning diagnostics as root" >&2
  exit 77
fi

for cmd in date stat awk git docker flock timeout sha256sum grep find sed tail head tr readlink; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing diagnostics command: $cmd" >&2
    exit 69
  }
done

now_epoch="$(date -u +%s)"
status="OK"
export_age=""
release_compatibility_status=""
analytics=""
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
production_sha_source="LAST_GOOD"
if [[ ! "$current_sha" =~ ^[0-9a-f]{40}$ ]]; then
  current_sha="$(git -c safe.directory="$REPO_ROOT" -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
  production_sha_source="CHECKOUT_HEAD"
fi
if [[ ! "$current_sha" =~ ^[0-9a-f]{40}$ ]]; then
  production_sha_source="UNKNOWN"
fi
echo "production_sha=${current_sha:-UNKNOWN}"
echo "production_sha_source=$production_sha_source"

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
  capture_disposition="$(env_value "$READER_STATE_FILE" capture_disposition)"
  outcomes_disposition="$(env_value "$READER_STATE_FILE" outcomes_disposition)"
  release_compat="$(env_value "$READER_STATE_FILE" release_compatibility_status)"
  outcomes_pending_ack="$(env_value "$READER_STATE_FILE" outcomes_pending_ack)"
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
  echo "capture_disposition=${capture_disposition:-UNKNOWN}"
  echo "outcomes_disposition=${outcomes_disposition:-UNKNOWN}"
  echo "outcomes_pending_ack=${outcomes_pending_ack:-UNKNOWN}"

  echo "worker_reported_release_compatibility_status=${release_compat:-UNKNOWN}"
  # Live SHA comparison outranks a stale heartbeat only when production_sha
  # came from the deploy receipt. Checkout HEAD is not an authoritative
  # deployed SHA and must not hide UNVERIFIED or invent CURRENT/DRIFT.
  if [[ "$production_sha_source" == "LAST_GOOD" \
     && "$worker_sha" =~ ^[0-9a-f]{40}$ \
     && "$current_sha" =~ ^[0-9a-f]{40}$ ]]; then
    if [[ "$worker_sha" == "$current_sha" ]]; then
      release_compatibility_status="CURRENT"
    else
      release_compatibility_status="RELEASE_DRIFT"
    fi
  elif [[ -n "$release_compat" && "$release_compat" != "NONE" && "$release_compat" != "UNKNOWN" ]]; then
    release_compatibility_status="$release_compat"
  else
    release_compatibility_status="UNVERIFIED"
  fi
  echo "release_compatibility_status=$release_compatibility_status"

  if [[ "$outcomes_disposition" == "CONSUMED_OK" || "$outcomes_disposition" == "CONSUMED_EMPTY" ]]; then
    echo "last_successful_outcomes_consumption_at_utc=${outcomes_at:-UNKNOWN}"
  else
    echo "last_successful_outcomes_consumption_at_utc=UNKNOWN"
  fi
  if [[ "$outcomes_pending_ack" =~ ^[0-9]+$ ]]; then
    echo "accountability_pending_count=$outcomes_pending_ack"
  else
    echo "accountability_pending_count=UNKNOWN"
  fi
  if [[ "$outcomes_age" =~ ^[0-9]+$ && "$export_age" =~ ^[0-9]+$ ]]; then
    # Positive lag means outcomes consumption trails the latest export evidence.
    if (( outcomes_age > export_age )); then
      echo "consumption_lag_vs_export_seconds=$((outcomes_age - export_age))"
    else
      echo "consumption_lag_vs_export_seconds=0"
    fi
  else
    echo "consumption_lag_vs_export_seconds=UNKNOWN"
  fi

  if [[ ! "$sync_age" =~ ^[0-9]+$ ]] || (( sync_age > MAX_SYNC_AGE_SECONDS )); then
    echo "worker_evidence_sync_status=STALE_OR_UNVERIFIED"
    degrade
  else
    echo "worker_evidence_sync_status=OK"
  fi
  if [[ "$protocol" != "2" ]]; then
    echo "worker_compute_status=UNVERIFIED_LEGACY_SYNC_PROTOCOL"
    degrade
  elif [[ "$release_compatibility_status" == "RELEASE_DRIFT" ]]; then
    echo "worker_compute_status=RELEASE_DRIFT"
    degrade
  elif [[ "$release_compatibility_status" == "UNVERIFIED" \
       && "$production_sha_source" == "LAST_GOOD" \
       && "$worker_sha" =~ ^[0-9a-f]{40}$ \
       && "$current_sha" =~ ^[0-9a-f]{40}$ \
       && "$worker_sha" != "$current_sha" ]]; then
    echo "worker_compute_status=RELEASE_DRIFT"
    degrade
  elif [[ "$capture_disposition" == "BLOCKED_RELEASE_DRIFT" || "$outcomes_disposition" == "BLOCKED_RELEASE_DRIFT" ]]; then
    echo "worker_compute_status=BLOCKED_RELEASE_DRIFT"
    degrade
  elif [[ "$capture_disposition" == "SKIPPED_BUSY" || "$capture_disposition" == "SKIPPED_CAPACITY" \
       || "$outcomes_disposition" == "SKIPPED_BUSY" || "$outcomes_disposition" == "SKIPPED_CAPACITY" ]]; then
    echo "worker_compute_status=SKIPPED_CAPACITY_OR_BUSY"
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
  echo "release_compatibility_status=UNVERIFIED"
  echo "capture_disposition=UNKNOWN"
  echo "outcomes_disposition=UNKNOWN"
  echo "outcomes_pending_ack=UNKNOWN"
  echo "accountability_pending_count=UNKNOWN"
  echo "last_successful_outcomes_consumption_at_utc=UNKNOWN"
  echo "consumption_lag_vs_export_seconds=UNKNOWN"
  degrade
fi

HOST_CYCLE_LOCK="/var/run/ohm-unified-cycle.lock"
# Read-only liveness probe. Never delete the lock file. flock -n fails when
# the canonical cron still holds the exclusive lock.
# Never kill the owner or restart services from diagnostics.
_report_lock_owner() {
  local lock_path="$1"
  local pid=""
  local ppid=""
  local start_time=""
  local elapsed=""
  local command=""

  if command -v lslocks >/dev/null 2>&1; then
    pid="$(
      lslocks -n -o PID,PATH 2>/dev/null \
        | awk -v p="$lock_path" '$2 == p {print $1; exit}'
    )"
  fi
  if [[ -z "$pid" ]] && command -v fuser >/dev/null 2>&1; then
    pid="$(fuser "$lock_path" 2>/dev/null | awk '{print $1; exit}')"
  fi
  if [[ -z "$pid" ]] && command -v lsof >/dev/null 2>&1; then
    pid="$(lsof -t "$lock_path" 2>/dev/null | head -n 1)"
  fi
  if [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/stat" ]]; then
    # proc/<pid>/stat: field 4=ppid, field 22=starttime (clock ticks)
    ppid="$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null || true)"
    local start_ticks
    start_ticks="$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null || true)"
    if [[ "$start_ticks" =~ ^[0-9]+$ ]]; then
      local btime hz
      btime="$(awk '/^btime / {print $2}' /proc/stat 2>/dev/null || true)"
      hz="$(getconf CLK_TCK 2>/dev/null || echo 100)"
      if [[ "$btime" =~ ^[0-9]+$ && "$hz" =~ ^[0-9]+$ && "$hz" -gt 0 ]]; then
        local start_epoch=$((btime + start_ticks / hz))
        start_time="$(date -u -d "@$start_epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
        if [[ "$start_epoch" =~ ^[0-9]+$ && "$now_epoch" =~ ^[0-9]+$ ]]; then
          elapsed="$((now_epoch - start_epoch))"
        fi
      fi
    fi
    if [[ -r "/proc/$pid/comm" ]]; then
      command="$(head -c 64 "/proc/$pid/comm" 2>/dev/null | tr -d '\n' || true)"
    fi
    if [[ -z "$command" && -L "/proc/$pid/exe" ]]; then
      command="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
      command="${command##*/}"
    fi
  fi

  echo "lock_owner_pid=${pid:-UNKNOWN}"
  echo "lock_owner_ppid=${ppid:-UNKNOWN}"
  echo "lock_owner_start_time=${start_time:-UNKNOWN}"
  echo "lock_owner_elapsed=${elapsed:-UNKNOWN}"
  # Bounded identity only: raw argv is deliberately not emitted.
  echo "lock_owner_comm=${command:-UNKNOWN}"
}

if [[ -e "$HOST_CYCLE_LOCK" ]]; then
  exec {cycle_lock_fd}<>"$HOST_CYCLE_LOCK"
  if flock -n "$cycle_lock_fd"; then
    echo "unified_cycle_host_lock=IDLE"
    echo "lock_owner_pid=NONE"
    echo "lock_owner_ppid=NONE"
    echo "lock_owner_start_time=NONE"
    echo "lock_owner_elapsed=NONE"
    echo "lock_owner_comm=NONE"
    flock -u "$cycle_lock_fd"
  else
    echo "unified_cycle_host_lock=HELD"
    _report_lock_owner "$HOST_CYCLE_LOCK"
  fi
  eval "exec ${cycle_lock_fd}>&-"
else
  echo "unified_cycle_host_lock=ABSENT"
  echo "lock_owner_pid=ABSENT"
  echo "lock_owner_ppid=ABSENT"
  echo "lock_owner_start_time=ABSENT"
  echo "lock_owner_elapsed=ABSENT"
  echo "lock_owner_comm=ABSENT"
fi

# Learning coverage epoch is learning-worker local. When this diagnose host
# also mounts the replica data root, report it read-only; otherwise UNKNOWN.
LEARNING_DATA_ROOT="${OPIP_LEARNING_DATA_ROOT:-/var/lib/opip-learning/data}"
COVERAGE_EPOCH_FILE="$LEARNING_DATA_ROOT/.learning_coverage/legacy_coverage_discontinuity_v1.json"
if [[ -s "$COVERAGE_EPOCH_FILE" ]]; then
  epoch_probe="$(
    python3 -c '
import json, sys
path = sys.argv[1]
try:
    payload = json.load(open(path, encoding="utf-8"))
except Exception:
    print("INVALID|INVALID|INVALID|INVALID")
    raise SystemExit(0)
required = (
    "schema_version",
    "kind",
    "archive_prefix",
    "boundary_at_utc",
    "reason",
    "measurement_only",
    "trade_authority_changed",
    "policy_change_authorized",
)
if not isinstance(payload, dict) or any(k not in payload for k in required):
    print("INVALID|INVALID|INVALID|INVALID")
    raise SystemExit(0)
if (
    payload.get("schema_version") != 1
    or payload.get("kind") != "legacy_coverage_discontinuity_v1"
    or payload.get("reason") != "LEGACY_ARCHIVE_CONTINUITY_UNPROVEN"
    or payload.get("measurement_only") is not True
    or payload.get("trade_authority_changed") is not False
    or payload.get("policy_change_authorized") is not False
):
    print("INVALID|INVALID|INVALID|INVALID")
    raise SystemExit(0)
print(
    "VALID|%s|%s|%s"
    % (
        payload.get("boundary_at_utc") or "UNKNOWN",
        payload.get("archive_prefix") or "UNKNOWN",
        payload.get("reason") or "UNKNOWN",
    )
)
' "$COVERAGE_EPOCH_FILE" 2>/dev/null || echo "INVALID|INVALID|INVALID|INVALID"
  )"
  IFS='|' read -r learning_coverage_epoch_status learning_coverage_epoch_boundary_utc learning_coverage_epoch_archive learning_coverage_epoch_reason <<<"$epoch_probe"
  echo "learning_coverage_epoch_status=${learning_coverage_epoch_status:-INVALID}"
  echo "learning_coverage_epoch_boundary_utc=${learning_coverage_epoch_boundary_utc:-INVALID}"
  echo "learning_coverage_epoch_archive=${learning_coverage_epoch_archive:-INVALID}"
  echo "learning_coverage_epoch_reason=${learning_coverage_epoch_reason:-INVALID}"
else
  echo "learning_coverage_epoch_status=ABSENT_OR_UNAVAILABLE"
  echo "learning_coverage_epoch_boundary_utc=UNKNOWN"
  echo "learning_coverage_epoch_archive=UNKNOWN"
  echo "learning_coverage_epoch_reason=UNKNOWN"
fi

# ---------------------------------------------------------------------------
# Read-only production export observability.
#
# The recurring export can stop committing while production stays otherwise
# healthy, and committed-manifest age alone cannot separate "the scheduler
# stopped invoking the exporter" from "an exporter run is stuck holding a lock".
# These probes distinguish those cases using kernel-reported state only.
#
# Invariants, all deliberate:
#   * no lock is acquired, created, released, repaired or removed anywhere in
#     this block. Taking a lock to "test" it proves nothing about the holder and
#     could itself make a real export run skip, destroying the evidence;
#   * the exporter is never executed, and nothing is signalled, restarted,
#     stopped, deleted, moved, copied, or has its ownership or mode altered;
#   * lock ownership is derived from /proc/locks (via lslocks) and /proc
#     file-descriptor ownership, which report state without taking it;
#   * log and journal output is line- and byte-bounded and secret-redacted;
#   * every probe fails soft to UNKNOWN when its tool or path is unavailable, so
#     diagnostics stay available instead of becoming a new failure mode.
# ---------------------------------------------------------------------------

redact_export_secrets() {
  # Bound blast radius if an unexpected credential ever reaches a log line: keep
  # the key or header name for evidence, drop the value. The repository's
  # no-secrets-in-logs contract prohibits any credential form, so this covers
  # KEY=value assignments, HTTP Authorization headers, Bearer/basic tokens, and
  # common JSON credential fields.
  sed -E \
    -e 's/((API|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|SESSION|COOKIE|AUTH)[A-Z_]*)=[^[:space:]]*/\1=<redacted>/Ig' \
    -e 's/(authorization[[:space:]]*:[[:space:]]*)[^[:space:],;]+/\1<redacted>/Ig' \
    -e 's/(bearer[[:space:]]+)[A-Za-z0-9._~+\/=-]+/\1<redacted>/Ig' \
    -e 's/(basic[[:space:]]+)[A-Za-z0-9._~+\/=-]+/\1<redacted>/Ig' \
    -e 's/("?(access_?token|refresh_?token|id_?token|api_?key|secret|password|passwd|credential|session_?id|cookie|auth)"?[[:space:]]*[:=][[:space:]]*"?)[^"[:space:],;]+/\1<redacted>/Ig'
}

describe_pid() {
  # Report process provenance from /proc without touching the process.
  local prefix="$1"
  local pid="$2"
  local ppid="" start_ticks="" btime="" hz="" start_epoch="" elapsed=""
  local start_time="" comm="" exe=""
  if [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/stat" ]]; then
    ppid="$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null || true)"
    start_ticks="$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null || true)"
    if [[ "$start_ticks" =~ ^[0-9]+$ ]]; then
      btime="$(awk '/^btime / {print $2}' /proc/stat 2>/dev/null || true)"
      hz="$(getconf CLK_TCK 2>/dev/null || echo 100)"
      if [[ "$btime" =~ ^[0-9]+$ && "$hz" =~ ^[0-9]+$ && "$hz" -gt 0 ]]; then
        start_epoch=$((btime + start_ticks / hz))
        start_time="$(date -u -d "@$start_epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
        if [[ "$start_epoch" -le "$now_epoch" ]]; then
          elapsed="$((now_epoch - start_epoch))"
        fi
      fi
    fi
    if [[ -r "/proc/$pid/comm" ]]; then
      comm="$(head -c 64 "/proc/$pid/comm" 2>/dev/null | tr -d '\n' || true)"
    fi
    if [[ -L "/proc/$pid/exe" ]]; then
      # basename only: the directory layout is not diagnostic here.
      exe="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
      exe="${exe##*/}"
      exe="$(printf '%s' "$exe" | head -c 64)"
    fi
  fi
  echo "${prefix}_pid=${pid:-UNKNOWN}"
  echo "${prefix}_ppid=${ppid:-UNKNOWN}"
  echo "${prefix}_comm=${comm:-UNKNOWN}"
  echo "${prefix}_exe_basename=${exe:-UNKNOWN}"
  echo "${prefix}_start_time=${start_time:-UNKNOWN}"
  echo "${prefix}_elapsed_seconds=${elapsed:-UNKNOWN}"
}


pid_elapsed_seconds() {
  # Elapsed seconds for one pid, or empty when it cannot be proven.
  local pid="$1"
  local start_ticks btime hz start_epoch
  [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/stat" ]] || return 0
  start_ticks="$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null || true)"
  [[ "$start_ticks" =~ ^[0-9]+$ ]] || return 0
  btime="$(awk '/^btime / {print $2}' /proc/stat 2>/dev/null || true)"
  hz="$(getconf CLK_TCK 2>/dev/null || echo 100)"
  [[ "$btime" =~ ^[0-9]+$ && "$hz" =~ ^[0-9]+$ && "$hz" -gt 0 ]] || return 0
  start_epoch=$((btime + start_ticks / hz))
  (( start_epoch <= now_epoch )) || return 0
  printf '%s' "$((now_epoch - start_epoch))"
}


note_stall_evidence() {
  # Escalate monotonically: NO -> UNKNOWN -> YES.
  #
  # An instantaneous observation can never prove a stall on its own. A healthy
  # export is legitimately in flight for part of every cycle, so presence alone
  # is not evidence. Only a duration beyond the operational threshold may raise
  # the verdict, and an unavailable duration yields UNKNOWN rather than YES.
  local verdict="$1"
  case "$verdict" in
    YES) export_lock_stall_suspected="YES" ;;
    UNKNOWN)
      if [[ "$export_lock_stall_suspected" == "NO" ]]; then
        export_lock_stall_suspected="UNKNOWN"
      fi
      ;;
  esac
}


classify_duration_verdict() {
  # Duration-only verdict, for a process whose *identity* is already proven (a
  # pgrep-matched exporter). Only here is age alone meaningful evidence, and an
  # unavailable duration yields UNKNOWN rather than YES.
  local elapsed="$1"
  if [[ ! "$elapsed" =~ ^[0-9]+$ ]]; then
    printf 'UNKNOWN\n'
  elif (( elapsed > EXPORT_STALL_THRESHOLD_SECONDS )); then
    printf 'YES\n'
  else
    printf 'NO\n'
  fi
}


classify_lock_stall_verdict() {
  # Evidence-strength-aware verdict for one lock observation.
  #
  # The two evidence strengths are deliberately not interchangeable:
  #
  #   HELD               - the kernel reports that this process owns the lock,
  #                        so the holder is proven and its age is meaningful
  #                        evidence;
  #   OPENED_UNCONFIRMED - the fallback probe proved only that some process has
  #                        the file *open*. Opening a file is not owning the lock
  #                        on it, so no elapsed duration may upgrade this to YES.
  #
  # Required truth table:
  #   HELD               + elapsed >  threshold -> YES
  #   HELD               + elapsed <= threshold -> NO
  #   HELD               + elapsed unavailable  -> UNKNOWN
  #   OPENED_UNCONFIRMED + any elapsed          -> UNKNOWN
  #   NOT_HELD                                  -> NO
  #   ABSENT                                    -> NO
  #   anything else                             -> UNKNOWN
  local state="$1"
  local elapsed="$2"
  case "$state" in
    HELD) classify_duration_verdict "$elapsed" ;;
    OPENED_UNCONFIRMED) printf 'UNKNOWN\n' ;;
    NOT_HELD | ABSENT) printf 'NO\n' ;;
    *) printf 'UNKNOWN\n' ;;
  esac
}

observe_lock_owner() {
  # Observe a lock holder. Deliberately never takes the lock.
  local prefix="$1"
  local path="$2"
  local exists="MISSING"
  [[ -e "$path" ]] && exists="EXISTS"

  local pid="" how="NONE" state="UNKNOWN"
  if command -v lslocks >/dev/null 2>&1; then
    local holder
    holder="$(lslocks -n -o PID,PATH 2>/dev/null | awk -v p="$path" '$2 == p {print $1; exit}' || true)"
    if [[ "$holder" =~ ^[0-9]+$ ]]; then
      pid="$holder"
      how="LSLOCKS"
    fi
  fi
  if [[ -z "$pid" ]] && command -v fuser >/dev/null 2>&1; then
    # Without -k, fuser only lists openers: it sends no signal and takes no lock.
    local opener
    opener="$(fuser "$path" 2>/dev/null | awk '{print $1; exit}' || true)"
    if [[ "$opener" =~ ^[0-9]+$ ]]; then
      pid="$opener"
      how="FUSER_OPENERS"
    fi
  fi

  if [[ "$exists" == "MISSING" ]]; then
    state="ABSENT"
  elif [[ "$how" == "LSLOCKS" ]]; then
    state="HELD"
  elif [[ "$how" == "FUSER_OPENERS" ]]; then
    # Open but not listed as locked: reported, never asserted to be held.
    state="OPENED_UNCONFIRMED"
  elif command -v lslocks >/dev/null 2>&1; then
    state="NOT_HELD"
  fi

  echo "${prefix}_lock_file=$exists"
  echo "${prefix}_lock_state=$state"
  echo "${prefix}_lock_owner_source=$how"
  echo "${prefix}_lock_held_instantaneously=$([[ "$state" == "HELD" ]] && echo YES || echo NO)"
  # Ownership is proven only by kernel-reported lock ownership. An open file
  # descriptor is reported, but never treated as proof of owning the lock.
  echo "${prefix}_lock_ownership_proven=$([[ "$state" == "HELD" ]] && echo YES || echo NO)"
  describe_pid "${prefix}_lock_owner" "$pid"

  # Only proven kernel-reported ownership plus a duration past the threshold may
  # be called a stall. An unconfirmed opener is reported with its age but never
  # upgraded to YES by it.
  local verdict
  verdict="$(classify_lock_stall_verdict "$state" "$(pid_elapsed_seconds "$pid")")"
  echo "${prefix}_lock_stall_verdict=$verdict"
  note_stall_evidence "$verdict"
}

emit_export_entries() {
  # Bounded name+timestamp inventory. Lists only; never removes anything.
  local prefix="$1"
  local pattern="$2"
  local count=0
  local entries=""
  local entry stamp iso
  if [[ ! -d "$EXPORT_ROOT" ]]; then
    echo "${prefix}_count=UNKNOWN"
    echo "${prefix}_entries=UNKNOWN"
    return 0
  fi
  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    count=$((count + 1))
    if (( count <= 20 )); then
      stamp="$(stat -c '%Y' "$entry" 2>/dev/null || true)"
      iso="$(date -u -d "@${stamp:-0}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo UNKNOWN)"
      entries="${entries}${entries:+,}${entry##*/}@${iso}"
    fi
  done < <(find "$EXPORT_ROOT" -mindepth 1 -maxdepth 1 -name "$pattern" 2>/dev/null || true)
  echo "${prefix}_count=$count"
  echo "${prefix}_entries=${entries:-NONE}"
}

manifest_epoch="$(date -u -d "${exported_at:-}" +%s 2>/dev/null || true)"
[[ "$manifest_epoch" =~ ^[0-9]+$ ]] || manifest_epoch=""

echo "OPIP_EXPORT_OBSERVABILITY"

# 1A - is the cron daemon actually running and invoking anything?
#
# systemctl's is-active carries three distinct answers, and merging them
# discards evidence:
#   * active                             -> YES (the daemon is up)
#   * inactive / failed / deactivating   -> NO  (the daemon is proven not up)
#   * unknown / empty / anything else    -> UNKNOWN (systemctl cannot decide)
#
# "unknown" is not proof of inactivity: the unit may be unavailable to
# systemctl, the unit name may not resolve, or cron may be managed outside that
# unit. Collapsing this to NO would skip the pgrep fallback and could degrade
# diagnostics for a healthy daemon, so a UNKNOWN systemctl answer explicitly
# allows the observational fallback.
cron_daemon_active="UNKNOWN"
cron_daemon_state="UNKNOWN"
cron_daemon_pid="UNKNOWN"
cron_daemon_started_at="UNKNOWN"
cron_daemon_source="UNKNOWN"
if command -v systemctl >/dev/null 2>&1; then
  cron_daemon_source="SYSTEMCTL"
  cron_daemon_state="$(systemctl is-active cron 2>/dev/null || true)"
  cron_daemon_state="${cron_daemon_state:-UNKNOWN}"
  cron_daemon_pid="$(systemctl show -p MainPID --value cron 2>/dev/null || true)"
  cron_daemon_pid="${cron_daemon_pid:-UNKNOWN}"
  cron_daemon_started_at="$(systemctl show -p ExecMainStartTimestamp --value cron 2>/dev/null || true)"
  cron_daemon_started_at="${cron_daemon_started_at:-UNKNOWN}"
  case "$cron_daemon_state" in
    active) cron_daemon_active="YES" ;;
    inactive | failed | deactivating) cron_daemon_active="NO" ;;
    *) cron_daemon_active="UNKNOWN" ;;
  esac
fi
# Only when systemctl was inconclusive do we fall back to observing the
# process directly. A proven cron process upgrades UNKNOWN to YES, records that
# the answer came from pgrep, and reports state=PROCESS_PRESENT so the source
# of the YES is distinguishable from an authoritative systemctl "active".
if [[ "$cron_daemon_active" == "UNKNOWN" ]] && command -v pgrep >/dev/null 2>&1; then
  cron_fallback_pid="$(pgrep -x cron 2>/dev/null | head -n 1 || true)"
  if [[ "$cron_fallback_pid" =~ ^[0-9]+$ ]]; then
    cron_daemon_active="YES"
    cron_daemon_pid="$cron_fallback_pid"
    cron_daemon_source="PGREP"
    cron_daemon_state="PROCESS_PRESENT"
    if command -v ps >/dev/null 2>&1; then
      cron_daemon_started_at="$(ps -o lstart= -p "$cron_fallback_pid" 2>/dev/null | sed 's/^[[:space:]]*//' || true)"
      cron_daemon_started_at="${cron_daemon_started_at:-UNKNOWN}"
    fi
  fi
fi
echo "cron_daemon_active=$cron_daemon_active"
echo "cron_daemon_state=$cron_daemon_state"
echo "cron_daemon_pid=$cron_daemon_pid"
echo "cron_daemon_started_at=$cron_daemon_started_at"
echo "cron_daemon_source=$cron_daemon_source"

# 1B - installed cron artifact identity, and whether it matches this release.
export_cron_exists="NO"
export_cron_matches_release="UNKNOWN"
if [[ -e "$EXPORT_CRON" ]]; then
  export_cron_exists="YES"
  cron_meta="$(stat -c '%U|%G|%a|%s|%Y' "$EXPORT_CRON" 2>/dev/null || true)"
  IFS='|' read -r cron_owner cron_group cron_mode cron_size cron_mtime <<<"$cron_meta"
  cron_sha="$(sha256sum "$EXPORT_CRON" 2>/dev/null | awk '{print $1}' || true)"
  echo "export_cron_exists=YES"
  echo "export_cron_owner=${cron_owner:-UNKNOWN}"
  echo "export_cron_group=${cron_group:-UNKNOWN}"
  echo "export_cron_mode=${cron_mode:-UNKNOWN}"
  echo "export_cron_size_bytes=${cron_size:-UNKNOWN}"
  echo "export_cron_mtime_utc=$(date -u -d "@${cron_mtime:-0}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo UNKNOWN)"
  echo "export_cron_sha256=${cron_sha:-UNKNOWN}"
  cron_job="$(grep -v '=' "$EXPORT_CRON" 2>/dev/null | grep -Ev '^[[:space:]]*(#|$)' | head -n 2 | tr '\n' ';' | head -c 300 || true)"
  echo "export_cron_job_line=${cron_job:-UNKNOWN}"
  cron_src_sha="$(sha256sum "$EXPORT_CRON_SRC" 2>/dev/null | awk '{print $1}' || true)"
  if [[ -z "$cron_src_sha" ]]; then
    echo "export_cron_matches_release=UNKNOWN"
  elif [[ "$cron_src_sha" == "$cron_sha" ]]; then
    export_cron_matches_release="YES"
    echo "export_cron_matches_release=YES"
  else
    export_cron_matches_release="NO"
    echo "export_cron_matches_release=NO"
  fi
else
  echo "export_cron_exists=NO"
  echo "export_cron_matches_release=UNKNOWN"
fi

# 1C - export log metadata, lifetime counters, and post-manifest evidence.
#
# Two distinct things are reported and must not be conflated:
#
#   * lifetime counters - descriptive totals across the whole log;
#   * post-manifest evidence - only events whose own log-line timestamp proves
#     they occurred after the committed exported_at_utc.
#
# The exporter's echoed lines carry no timestamp (the cron wrapper appends them
# with a plain `>>` redirect), so on the current contract post-manifest event
# attribution is UNPROVABLE. File mtime is NOT a substitute for event time: it
# proves only that *something* was written, never which events. When no line
# carries a parseable timestamp the classification says so rather than inferring
# chronology, and the active incident is diagnosed from lock/process evidence.
export_log_exists="NO"
export_log_activity_class="UNKNOWN"
export_log_lifetime_skip_count="0"
export_log_lifetime_success_count="0"
export_log_lifetime_bundle_ok_count="0"
export_log_lifetime_failure_count="0"
export_log_timestamp_semantics="UNKNOWN"
export_log_post_manifest_skip_count="0"
export_log_post_manifest_success_count="0"
export_log_post_manifest_failure_count="0"
export_log_post_manifest_recognized_event_count="0"
export_log_post_manifest_unclassified_line_count="0"
export_log_post_manifest_evidence="UNKNOWN"
if [[ -f "$EXPORT_LOG" ]]; then
  export_log_exists="YES"
  log_meta="$(stat -c '%U|%G|%a|%s|%Y' "$EXPORT_LOG" 2>/dev/null || true)"
  IFS='|' read -r log_owner log_group log_mode log_size log_mtime <<<"$log_meta"
  echo "export_log_owner=${log_owner:-UNKNOWN}"
  echo "export_log_group=${log_group:-UNKNOWN}"
  echo "export_log_mode=${log_mode:-UNKNOWN}"
  echo "export_log_size_bytes=${log_size:-UNKNOWN}"
  # Reported as descriptive file metadata only; never used for event attribution.
  echo "export_log_mtime_utc=$(date -u -d "@${log_mtime:-0}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo UNKNOWN)"
  [[ "$log_mtime" =~ ^[0-9]+$ ]] || log_mtime=""
  [[ "$log_size" =~ ^[0-9]+$ ]] || log_size=""

  export_log_lifetime_skip_count="$(grep -c 'already active; skipping' "$EXPORT_LOG" 2>/dev/null || true)"
  export_log_lifetime_success_count="$(grep -c "learning evidence export: OK" "$EXPORT_LOG" 2>/dev/null || true)"
  export_log_lifetime_bundle_ok_count="$(grep -c 'canonical replica bundle OK' "$EXPORT_LOG" 2>/dev/null || true)"
  export_log_lifetime_failure_count="$(grep -cE 'canonical replica export FAILED|canonical replica FAILED|replica collision|Traceback|Permission denied' "$EXPORT_LOG" 2>/dev/null || true)"
  for counter in export_log_lifetime_skip_count export_log_lifetime_success_count export_log_lifetime_bundle_ok_count export_log_lifetime_failure_count; do
    [[ "${!counter}" =~ ^[0-9]+$ ]] || printf -v "$counter" '%s' 0
  done
  echo "export_log_lifetime_skip_count=$export_log_lifetime_skip_count"
  echo "export_log_lifetime_success_count=$export_log_lifetime_success_count"
  echo "export_log_lifetime_bundle_ok_count=$export_log_lifetime_bundle_ok_count"
  echo "export_log_lifetime_failure_count=$export_log_lifetime_failure_count"

  # Scan a bounded tail for lines that carry their own ISO-8601 instant.
  #
  # Two invariants matter here and are deliberately independent:
  #
  #   * a timestamp proves *when* a line was emitted;
  #   * only a recognized exporter event drives exporter-health classification.
  #
  # A line that carries a post-manifest timestamp but is not a recognized event
  # (e.g. an unrelated informational line written by another consumer of the
  # log) MUST NOT degrade diagnostics. It is counted descriptively for
  # observability and nothing more. Only recognized recognized-event counters
  # drive the classification and any resulting degrade.
  #
  # The tail is bounded so this stays cheap on a very large log, and it is
  # streamed directly: no temporary file is created, and the log is only ever
  # read.
  export_log_timestamped_line_count=0
  export_log_post_manifest_timestamped_count=0
  if [[ -n "$manifest_epoch" ]]; then
    while IFS= read -r log_line; do
      line_stamp="${log_line%% *}"
      case "$line_stamp" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*)
          line_epoch="$(date -u -d "${line_stamp%%.*}" +%s 2>/dev/null || true)"
          [[ "$line_epoch" =~ ^[0-9]+$ ]] || continue
          export_log_timestamped_line_count=$((export_log_timestamped_line_count + 1))
          if (( line_epoch > manifest_epoch )); then
            export_log_post_manifest_timestamped_count=$((export_log_post_manifest_timestamped_count + 1))
            case "$log_line" in
              *"already active; skipping"*)
                export_log_post_manifest_skip_count=$((export_log_post_manifest_skip_count + 1))
                export_log_post_manifest_recognized_event_count=$((export_log_post_manifest_recognized_event_count + 1))
                ;;
              *"canonical replica export FAILED"* | *"canonical replica FAILED"* | *"replica collision"* | *Traceback* | *"Permission denied"*)
                export_log_post_manifest_failure_count=$((export_log_post_manifest_failure_count + 1))
                export_log_post_manifest_recognized_event_count=$((export_log_post_manifest_recognized_event_count + 1))
                ;;
              *"learning evidence export: OK"*)
                export_log_post_manifest_success_count=$((export_log_post_manifest_success_count + 1))
                export_log_post_manifest_recognized_event_count=$((export_log_post_manifest_recognized_event_count + 1))
                ;;
              *)
                # Timestamped but not a recognized exporter event: descriptive
                # only, must not feed classification or degrade.
                export_log_post_manifest_unclassified_line_count=$((export_log_post_manifest_unclassified_line_count + 1))
                ;;
            esac
          fi
          ;;
      esac
    done < <(tail -n "$EXPORT_LOG_TAIL_LINES" "$EXPORT_LOG" 2>/dev/null \
      | head -c "$EXPORT_LOG_MAX_BYTES" || true)
  fi

  echo "export_log_tail_lines_scanned=$EXPORT_LOG_TAIL_LINES"
  echo "export_log_timestamped_line_count=$export_log_timestamped_line_count"
  echo "export_log_post_manifest_timestamped_count=$export_log_post_manifest_timestamped_count"
  echo "export_log_post_manifest_recognized_event_count=$export_log_post_manifest_recognized_event_count"
  echo "export_log_post_manifest_unclassified_line_count=$export_log_post_manifest_unclassified_line_count"
  echo "export_log_post_manifest_skip_count=$export_log_post_manifest_skip_count"
  echo "export_log_post_manifest_success_count=$export_log_post_manifest_success_count"
  echo "export_log_post_manifest_failure_count=$export_log_post_manifest_failure_count"

  if (( export_log_timestamped_line_count > 0 )); then
    export_log_timestamp_semantics="TIMESTAMPED"
  else
    export_log_timestamp_semantics="UNTIMESTAMPED"
  fi
  echo "export_log_timestamp_semantics=$export_log_timestamp_semantics"

  # Classification is driven ONLY by recognized post-manifest events. Unrelated
  # timestamped lines are counted but never contaminate the verdict, and
  # historical events (before the manifest) cannot appear here at all because
  # only line_epoch > manifest_epoch reaches these counters.
  if (( export_log_timestamped_line_count == 0 )); then
    export_log_activity_class="UNPROVABLE_FROM_UNTIMESTAMPED_LOG"
    export_log_post_manifest_evidence="UNPROVABLE_FROM_UNTIMESTAMPED_LOG"
  elif (( export_log_post_manifest_recognized_event_count == 0 )); then
    # Timestamps exist and some may fall after the manifest, but none is a
    # recognized exporter event, so no exporter-health verdict is claimed.
    export_log_activity_class="NO_POST_MANIFEST_RECOGNIZED_EXPORT_EVIDENCE"
    export_log_post_manifest_evidence="NO_POST_MANIFEST_RECOGNIZED_EXPORT_EVIDENCE"
  elif (( export_log_post_manifest_failure_count > 0 )); then
    export_log_activity_class="POST_MANIFEST_RUNS_FAIL"
    export_log_post_manifest_evidence="PROVEN"
  elif (( export_log_post_manifest_skip_count > 0 )); then
    export_log_activity_class="POST_MANIFEST_RUNS_SKIPPED_LOCK_HELD"
    export_log_post_manifest_evidence="PROVEN"
  elif (( export_log_post_manifest_success_count > 0 )); then
    export_log_activity_class="POST_MANIFEST_RUNS_SUCCEED"
    export_log_post_manifest_evidence="PROVEN"
  else
    # Belt-and-braces: recognized_event_count > 0 must have matched one of the
    # cases above. Reaching this branch would mean a recognized event was
    # counted without a matching sub-counter, which is a bug in the enum.
    export_log_activity_class="POST_MANIFEST_RECOGNIZED_ACTIVITY_UNCLASSIFIED"
    export_log_post_manifest_evidence="PROVEN"
  fi
  echo "export_log_activity_class=$export_log_activity_class"
  echo "export_log_post_manifest_evidence=$export_log_post_manifest_evidence"
  echo "export_log_tail_lines_requested=$EXPORT_LOG_TAIL_LINES"
  echo "export_log_tail_bytes_limit=$EXPORT_LOG_MAX_BYTES"
  echo "OPIP_EXPORT_LOG_TAIL"
  tail -n "$EXPORT_LOG_TAIL_LINES" "$EXPORT_LOG" 2>/dev/null | head -c "$EXPORT_LOG_MAX_BYTES" | redact_export_secrets || true
  echo "OPIP_EXPORT_LOG_TAIL_END"
else
  echo "export_log_exists=NO"
  echo "export_log_activity_class=NO_LOG_FILE"
fi

# 1D - holder observation for the three distinct export locks, plus live
#      wrapper/exporter process inventory. Nothing is acquired or signalled.
export_lock_stall_suspected="NO"
observe_lock_owner "outer_cron" "$EXPORT_WRAPPER_LOCK"
observe_lock_owner "internal_export" "$EXPORT_INTERNAL_LOCK"
observe_lock_owner "publish" "$EXPORT_PUBLISH_LOCK"

export_process_count="0"
export_process_present="NO"
export_process_max_elapsed_seconds="UNKNOWN"
export_processes=""
if command -v pgrep >/dev/null 2>&1; then
  # See EXPORT_PROCESS_PATTERN above: the pattern is the exporter script name,
  # deliberately narrower than the journal pattern so a `tail`/`less`/rotator of
  # the log or lock file cannot be misread as the exporter.
  export_process_count="$(pgrep -fc "$EXPORT_PROCESS_PATTERN" 2>/dev/null || true)"
  [[ "$export_process_count" =~ ^[0-9]+$ ]] || export_process_count=0
  while IFS= read -r live_pid; do
    [[ -n "$live_pid" ]] || continue
    # Safe metadata only: never raw argv. Identity and duration are sufficient to
    # recognise a stalled run, and command lines can carry credentials.
    live_elapsed="$(pid_elapsed_seconds "$live_pid")"
    live_comm=""
    [[ -r "/proc/$live_pid/comm" ]] \
      && live_comm="$(head -c 64 "/proc/$live_pid/comm" 2>/dev/null | tr -d '\n' || true)"
    if [[ "$live_elapsed" =~ ^[0-9]+$ ]]; then
      if [[ ! "$export_process_max_elapsed_seconds" =~ ^[0-9]+$ ]] \
        || (( live_elapsed > export_process_max_elapsed_seconds )); then
        export_process_max_elapsed_seconds="$live_elapsed"
      fi
    fi
    export_processes="${export_processes}${export_processes:+,}${live_pid}@${live_elapsed:-?}@${live_comm:-UNKNOWN}"
  done < <(pgrep -f "$EXPORT_PROCESS_PATTERN" 2>/dev/null || true)
fi
echo "export_process_count=$export_process_count"
echo "export_process_present=$([[ "$export_process_count" != "0" ]] && echo YES || echo NO)"
echo "export_process_max_elapsed_seconds=$export_process_max_elapsed_seconds"
echo "export_processes=${export_processes:-NONE}"
# Presence is not a stall, but unlike a lock opener the identity here IS proven:
# a pgrep match means this is the exporter. Only a duration past the threshold
# may raise the verdict, and an unproven age yields UNKNOWN rather than YES.
if [[ "$export_process_count" != "0" ]]; then
  note_stall_evidence "$(classify_duration_verdict "$export_process_max_elapsed_seconds")"
fi

# 1E - committed export state, the referenced replica directory and the inner
#      replica manifest (the real replica freshness contract).
manifest_schema_version="$(env_value "$MANIFEST" schema_version)"
manifest_deployed_sha="$(env_value "$MANIFEST" production_deployed_sha)"
manifest_replica_version="$(env_value "$MANIFEST" canonical_learning_replica_version)"
manifest_replica_dir="$(env_value "$MANIFEST" canonical_learning_replica_dir)"
manifest_replica_bytes="$(env_value "$MANIFEST" canonical_learning_replica_bytes)"
manifest_replica_tree_sha="$(env_value "$MANIFEST" canonical_learning_replica_sha256)"
manifest_sha="$(sha256sum "$MANIFEST" 2>/dev/null | awk '{print $1}' || true)"
echo "manifest_schema_version=${manifest_schema_version:-UNKNOWN}"
echo "manifest_production_deployed_sha=${manifest_deployed_sha:-UNKNOWN}"
echo "manifest_sha256=${manifest_sha:-UNKNOWN}"
echo "manifest_replica_marker_version=${manifest_replica_version:-ABSENT}"
echo "manifest_replica_dir=${manifest_replica_dir:-ABSENT}"
echo "manifest_replica_bytes=${manifest_replica_bytes:-ABSENT}"
echo "manifest_replica_sha256=${manifest_replica_tree_sha:-ABSENT}"
if [[ "$manifest_replica_version" == "1" ]]; then
  echo "manifest_replica_marker_present=YES"
else
  echo "manifest_replica_marker_present=NO"
fi

replica_dir_path=""
replica_dir_name_valid="NO"
if [[ -n "$manifest_replica_dir" ]]; then
  # Validate before use so a malformed or traversing name can never be stat'd.
  if [[ "$manifest_replica_dir" =~ $REPLICA_DIR_NAME_PATTERN ]]; then
    replica_dir_name_valid="YES"
    replica_dir_path="$EXPORT_ROOT/$manifest_replica_dir"
  fi
fi
echo "replica_dir_name_valid=$replica_dir_name_valid"
if [[ "$replica_dir_name_valid" == "YES" && -d "$replica_dir_path" ]]; then
  replica_meta="$(stat -c '%U|%G|%a|%Y' "$replica_dir_path" 2>/dev/null || true)"
  IFS='|' read -r replica_owner replica_group replica_mode replica_mtime <<<"$replica_meta"
  echo "replica_dir_exists=YES"
  echo "replica_dir_owner=${replica_owner:-UNKNOWN}"
  echo "replica_dir_group=${replica_group:-UNKNOWN}"
  echo "replica_dir_mode=${replica_mode:-UNKNOWN}"
  echo "replica_dir_mtime_utc=$(date -u -d "@${replica_mtime:-0}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo UNKNOWN)"
  if command -v du >/dev/null 2>&1; then
    replica_dir_bytes="$(du -sb "$replica_dir_path" 2>/dev/null | awk '{print $1}' || true)"
    echo "replica_dir_bytes=${replica_dir_bytes:-UNKNOWN}"
  else
    echo "replica_dir_bytes=UNKNOWN"
  fi

  replica_inner="$replica_dir_path/replica_manifest.json"
  if [[ -f "$replica_inner" ]]; then
    echo "replica_inner_manifest=EXISTS"
    inner_fields=""
    if command -v python3 >/dev/null 2>&1; then
      inner_fields="$(
        python3 -c '
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("INVALID||||")
    raise SystemExit(0)
if not isinstance(payload, dict):
    print("INVALID||||")
    raise SystemExit(0)
def text(key):
    value = payload.get(key)
    return value if isinstance(value, str) else ""
print(
    "%s|%s|%s|%s"
    % (
        text("generation_id"),
        text("source_release_sha"),
        text("snapshot_created_at_utc"),
        payload.get("replica_schema_version", ""),
    )
)
' "$replica_inner" 2>/dev/null || true
      )"
    fi
    IFS='|' read -r inner_generation inner_release inner_snapshot inner_schema <<<"${inner_fields:-}"
    echo "replica_inner_generation_id=${inner_generation:-UNKNOWN}"
    echo "replica_inner_source_release_sha=${inner_release:-UNKNOWN}"
    echo "replica_inner_snapshot_created_at_utc=${inner_snapshot:-UNKNOWN}"
    echo "replica_inner_schema_version=${inner_schema:-UNKNOWN}"
    if [[ -n "${inner_release:-}" && "$inner_release" == "$current_sha" ]]; then
      echo "replica_inner_release_matches_production=YES"
    else
      echo "replica_inner_release_matches_production=NO"
    fi
    inner_age="$(age_seconds "${inner_snapshot:-}" || true)"
    echo "replica_inner_age_seconds=${inner_age:-UNKNOWN}"
    if [[ "$inner_age" =~ ^[0-9]+$ ]]; then
      if (( inner_age > 1800 )); then
        echo "replica_contract_freshness_1800s=FAIL"
        degrade
      else
        echo "replica_contract_freshness_1800s=PASS"
      fi
    else
      echo "replica_contract_freshness_1800s=UNKNOWN"
      degrade
    fi
  else
    echo "replica_inner_manifest=MISSING"
    degrade
  fi
elif [[ "$replica_dir_name_valid" == "YES" ]]; then
  echo "replica_dir_exists=NO"
  degrade
else
  echo "replica_dir_exists=NOT_REFERENCED"
fi

# 1F - staging / orphan inventory (names and timestamps only, nothing removed).
emit_export_entries "replica_staging" ".canonical_learning_replica.staging.*"
emit_export_entries "replica_published" "canonical_learning_replica.*"
emit_export_entries "manifest_tmp" ".manifest.env.tmp.*"

# 1G - release receipt, and whether the export was committed after it.
if [[ -s "$EXPORT_RELEASE_RECEIPT" ]]; then
  receipt_sha="$(cat "$EXPORT_RELEASE_RECEIPT" 2>/dev/null | tr -d '[:space:]' || true)"
  receipt_mtime="$(stat -c '%Y' "$EXPORT_RELEASE_RECEIPT" 2>/dev/null || true)"
  echo "release_receipt_sha=${receipt_sha:-UNKNOWN}"
  echo "release_receipt_mtime_utc=$(date -u -d "@${receipt_mtime:-0}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo UNKNOWN)"
  if [[ ! "$receipt_mtime" =~ ^[0-9]+$ || -z "$manifest_epoch" ]]; then
    echo "export_committed_after_release_receipt=UNKNOWN"
  elif (( manifest_epoch > receipt_mtime )); then
    echo "export_committed_after_release_receipt=YES"
  else
    echo "export_committed_after_release_receipt=NO"
    degrade
  fi
else
  echo "release_receipt_sha=UNAVAILABLE"
  echo "export_committed_after_release_receipt=UNKNOWN"
fi

# Journal / syslog evidence that the daemon is actually invoking the job.
#
# Only O'Pip export-specific records are matched. A generic CRON match would pull
# in unrelated system jobs, and journal lines embed the full command line, so no
# raw line is emitted: only bounded counts and a boolean are reported.
export_journal_source="UNAVAILABLE"
export_journal_matched_lines="UNKNOWN"
export_journal_export_records_seen="UNKNOWN"
journal_raw=""
if command -v journalctl >/dev/null 2>&1; then
  journal_raw="$(journalctl --since '2 hours ago' --no-pager -n 500 2>/dev/null \
    | grep -Ei "$EXPORT_JOURNAL_PATTERN" \
    | tail -n "$EXPORT_JOURNAL_MAX_LINES" || true)"
  [[ -n "$journal_raw" ]] && export_journal_source="JOURNALCTL"
fi
if [[ "$export_journal_source" == "UNAVAILABLE" && -f /var/log/syslog ]]; then
  journal_raw="$(tail -n 2000 /var/log/syslog 2>/dev/null \
    | grep -Ei "$EXPORT_JOURNAL_PATTERN" \
    | tail -n "$EXPORT_JOURNAL_MAX_LINES" || true)"
  [[ -n "$journal_raw" ]] && export_journal_source="SYSLOG"
fi
if [[ "$export_journal_source" != "UNAVAILABLE" ]]; then
  export_journal_matched_lines="$(printf '%s\n' "$journal_raw" | grep -c '[^[:space:]]' || true)"
  [[ "$export_journal_matched_lines" =~ ^[0-9]+$ ]] || export_journal_matched_lines="UNKNOWN"
  if [[ "$export_journal_matched_lines" =~ ^[0-9]+$ ]] && (( export_journal_matched_lines > 0 )); then
    export_journal_export_records_seen="YES"
  else
    export_journal_export_records_seen="NO"
  fi
fi
echo "export_journal_source=$export_journal_source"
echo "export_journal_pattern_scoped=YES"
echo "export_journal_matched_lines=$export_journal_matched_lines"
echo "export_journal_export_records_seen=$export_journal_export_records_seen"

echo "export_stall_threshold_seconds=$EXPORT_STALL_THRESHOLD_SECONDS"
echo "export_lock_stall_suspected=$export_lock_stall_suspected"
if [[ "$export_lock_stall_suspected" == "YES" ]]; then
  degrade
fi
# Only PROVEN recognized post-manifest failure or lock-skipping degrades. An
# untimestamped log, timestamped-but-unrelated lines, and a genuinely proven
# success are all NOT degrading.
case "$export_log_activity_class" in
  POST_MANIFEST_RUNS_FAIL | POST_MANIFEST_RUNS_SKIPPED_LOCK_HELD)
    degrade
    ;;
esac
if [[ "$export_cron_exists" != "YES" ]]; then
  degrade
fi
if [[ "$cron_daemon_active" == "NO" ]]; then
  degrade
fi

if docker inspect ohm-trade-agent >/dev/null 2>&1; then
  core_running="$(docker inspect --format='{{.State.Running}}' ohm-trade-agent 2>/dev/null || true)"
  analytics=""
  analytics_rc=0
  if [[ "$core_running" != "true" ]]; then
    echo "production_validation_data=CORE_CONTAINER_STOPPED"
    status="FAIL"
  else
    analytics="$(
    timeout --signal=TERM --kill-after=5s 45 docker exec ohm-trade-agent python -c '
import json
import sys
try:
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
except Exception as exc:
    print("UNAVAILABLE:" + type(exc).__name__)
    sys.exit(1)
' 2>/dev/null
    )" || analytics_rc=$?
  fi
  if [[ "$core_running" == "true" && "$analytics" == UNAVAILABLE:* ]]; then
    echo "production_validation_data=$analytics"
    degrade
  elif [[ "$core_running" == "true" && ( "$analytics_rc" -ne 0 || -z "$analytics" ) ]]; then
    echo "production_validation_data=UNAVAILABLE:TIMEOUT_OR_EXEC"
    degrade
  elif [[ "$core_running" == "true" ]]; then
    echo "production_validation_data=$analytics"
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
from app.services.operations_analytics import SCAN_ACTIVITY_FILE
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

def bounded_scan_tail(path, max_bytes=1048576):
    if not path.exists():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            payload = handle.read(max_bytes)
    except OSError:
        return []
    if start > 0:
        separator = payload.find(b"\n")
        payload = b"" if separator < 0 else payload[separator + 1 :]
    rows = []
    for raw in payload.splitlines():
        try:
            item = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows

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
last_search_finished = parsed(state.get("last_search_finished_at"))
last_search_status = str(state.get("last_search_status") or "").strip().upper() or None
search_in_progress = last_search_status == "STARTED"
search_due = bool(
    search_allowed
    and (
        last_search_started is None
        or (now - last_search_started).total_seconds() >= interval
    )
)

rows = bounded_scan_tail(SCAN_ACTIVITY_FILE)
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
    "last_search_finished_at": (last_search_finished.isoformat() if last_search_finished else None),
    "last_search_status": last_search_status,
    "search_in_progress": search_in_progress,
    "scan_activity_tail_rows": len(rows),
    "scan_activity_rows_24h": len(recent),
    "scan_activity_read_limit_bytes": 1048576,
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

# Phase 18 zero-funnel clarity: Early Watch journeys are not the SEARCH
# qualification funnel producer. Keep counters distinct; no threshold changes.
echo "OPIP_ZERO_FUNNEL_CLARITY"
echo "note=early_watch_journeys_are_not_qualification_funnel_producer"
if [[ -n "${analytics:-}" && "$analytics" != UNAVAILABLE:* ]]; then
  echo "early_watch_journeys=$(printf '%s' "$analytics" | awk -F'[:,]' '
    /"early_watch_journeys"/ {
      for (i = 1; i <= NF; i++) {
        if ($i ~ /early_watch_journeys/) {
          gsub(/[^0-9]/, "", $(i + 1))
          if ($(i + 1) != "") { print $(i + 1); exit }
        }
      }
    }
  ')"
  echo "qualified_signals=$(printf '%s' "$analytics" | awk -F'[:,]' '
    /"qualified_signals"/ {
      for (i = 1; i <= NF; i++) {
        if ($i ~ /qualified_signals/) {
          gsub(/[^0-9]/, "", $(i + 1))
          if ($(i + 1) != "") { print $(i + 1); exit }
        }
      }
    }
  ')"
else
  echo "early_watch_journeys=UNKNOWN"
  echo "qualified_signals=UNKNOWN"
fi
echo "funnel_candidates_source=OPIP_QUALIFICATION_FUNNEL"
echo "search_mode_source=production_runtime_data.effective_mode"

# Export cron alone must not imply healthy learning compute.
if [[ "$status" == "OK" ]]; then
  if [[ "${release_compatibility_status:-}" == "RELEASE_DRIFT" \
     || "${release_compatibility_status:-}" == "UNVERIFIED" ]]; then
    degrade
  fi
fi

echo "diagnostics_status=$status"
[[ "$status" != "FAIL" ]]
