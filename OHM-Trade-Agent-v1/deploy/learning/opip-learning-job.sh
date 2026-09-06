#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${OPIP_LEARNING_ENV_FILE:-/etc/opip-learning.env}"
LOCK_FILE="${OPIP_LEARNING_LOCK_FILE:-/var/lock/opip-learning-plane.lock}"
DATA_ROOT="${OPIP_LEARNING_DATA_ROOT:-/var/lib/opip-learning/data}"
STATE_ROOT="${OPIP_LEARNING_STATE_ROOT:-/var/lib/opip-learning/state}"
MANIFEST="$DATA_ROOT/manifest.env"
JOB="${1:-}"

[[ -r "$ENV_FILE" ]] || {
  echo "missing O'Pip learning environment: $ENV_FILE" >&2
  exit 78
}
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${OPIP_LEARNING_IMAGE:?OPIP_LEARNING_IMAGE is required}"
: "${OPIP_DEPLOYED_SHA:?OPIP_DEPLOYED_SHA is required}"

case "$JOB" in
  capture)
    MODULE="app.jobs.run_opip_ml_capture"
    MEMORY_LIMIT="384m"
    CPU_LIMIT="0.60"
    MIN_AVAILABLE_KB=$((512 * 1024))
    TIMEOUT_SECONDS=180
    ;;
  outcomes)
    MODULE="app.jobs.run_opportunity_intelligence_cycle"
    # Bounded queue/window processing keeps the Python working set below this
    # hard cap; preserve host headroom on the 1 GiB learning worker.
    MEMORY_LIMIT="384m"
    CPU_LIMIT="0.70"
    MIN_AVAILABLE_KB=$((512 * 1024))
    TIMEOUT_SECONDS=480
    ;;
  *)
    echo "unsupported O'Pip learning job: $JOB" >&2
    exit 64
    ;;
esac

for cmd in flock awk install date mv; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing learning-runner command: $cmd" >&2
    exit 69
  }
done

install -d -o root -g root -m 0755 "$DATA_ROOT" "$STATE_ROOT" 2>/dev/null || install -d -m 0755 "$DATA_ROOT" "$STATE_ROOT"

manifest_value() {
  local key="$1"
  awk -F= -v k="$key" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$MANIFEST" 2>/dev/null || true
}

# Exact-SHA equality with production last-good / export expected SHA.
classify_release_compatibility() {
  local worker="$1"
  local expected="$2"
  if [[ ! "$worker" =~ ^[0-9a-f]{40}$ || ! "$expected" =~ ^[0-9a-f]{40}$ ]]; then
    printf 'UNVERIFIED\n'
  elif [[ "$worker" == "$expected" ]]; then
    printf 'CURRENT\n'
  else
    printf 'RELEASE_DRIFT\n'
  fi
}

write_disposition() {
  local disposition="$1"
  local release_status="$2"
  local expected_sha="$3"
  local exit_code="${4:-}"
  local detail="${5:-}"
  local recorded
  recorded="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local tmp="$STATE_ROOT/$JOB.disposition.env.tmp"
  cat > "$tmp" <<EOF
job=$JOB
disposition=$disposition
recorded_at_utc=$recorded
release_compatibility_status=$release_status
worker_sha=$OPIP_DEPLOYED_SHA
expected_sha=$expected_sha
exit_code=$exit_code
detail=$detail
measurement_only=true
trade_authority_changed=false
policy_change_authorized=false
EOF
  mv -f "$tmp" "$STATE_ROOT/$JOB.disposition.env"
}

EXPECTED_SHA="$(manifest_value production_deployed_sha)"
RELEASE_STATUS="$(classify_release_compatibility "$OPIP_DEPLOYED_SHA" "$EXPECTED_SHA")"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "O'Pip learning plane busy; $JOB skipped"
  write_disposition "SKIPPED_BUSY" "$RELEASE_STATUS" "$EXPECTED_SHA" "" "learning_plane_lock_busy"
  exit 0
fi

MEM_AVAILABLE_KB="$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo)"
if [[ ! "$MEM_AVAILABLE_KB" =~ ^[0-9]+$ ]] || (( MEM_AVAILABLE_KB < MIN_AVAILABLE_KB )); then
  echo "O'Pip learning job skipped for memory safety: job=$JOB available_kb=$MEM_AVAILABLE_KB required_kb=$MIN_AVAILABLE_KB" >&2
  write_disposition "SKIPPED_CAPACITY" "$RELEASE_STATUS" "$EXPECTED_SHA" "" "mem_available_kb=$MEM_AVAILABLE_KB"
  exit 0
fi

if [[ "$RELEASE_STATUS" != "CURRENT" ]]; then
  echo "O'Pip learning job blocked for release compatibility: job=$JOB status=$RELEASE_STATUS worker=$OPIP_DEPLOYED_SHA expected=${EXPECTED_SHA:-MISSING}" >&2
  write_disposition "BLOCKED_RELEASE_DRIFT" "$RELEASE_STATUS" "$EXPECTED_SHA" "75" "exact_sha_admission_failed"
  exit 75
fi

# The legacy P1 shadow outbox has been owner-retired. A capture timer firing
# after schema-4 sync is still a governed consumption event: record a durable
# empty disposition instead of launching a container that expects a deleted
# source. Historical evidence already in the ledger remains available to the
# outcomes job and is not discarded here.
P1_SHADOW_OUTBOX_RETIRED="$(manifest_value p1_shadow_outbox_retired)"
if [[ "$JOB" == "capture" && "$P1_SHADOW_OUTBOX_RETIRED" == "1" ]]; then
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  finished_at="$started_at"
  cat > "$STATE_ROOT/$JOB.last.env.tmp" <<EOF
job=$JOB
module=RETIRED_P1_SHADOW_OUTBOX
started_at_utc=$started_at
finished_at_utc=$finished_at
exit_code=0
memory_limit=NONE
cpu_limit=NONE
release_compatibility_status=$RELEASE_STATUS
EOF
  mv -f "$STATE_ROOT/$JOB.last.env.tmp" "$STATE_ROOT/$JOB.last.env"
  write_disposition "CONSUMED_EMPTY" "$RELEASE_STATUS" "$EXPECTED_SHA" "0" "p1_shadow_outbox_retired"
  echo "O'Pip learning capture: CONSUMED_EMPTY retired P1 shadow outbox"
  exit 0
fi

for cmd in docker timeout; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing learning-runner command: $cmd" >&2
    exit 69
  }
done

LABEL="com.opip.learning.job=$JOB"

# Clean entry: because the global learning-plane flock is held, any matching
# container is stale from an interrupted prior invocation and is safe to reap.
mapfile -t stale_ids < <(docker ps -aq --filter "label=$LABEL")
if (( ${#stale_ids[@]} > 0 )); then
  docker rm -f "${stale_ids[@]}" >/dev/null
fi

CONTAINER_NAME="opip-learning-$JOB-$$"
cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
set +e
timeout --signal=TERM --kill-after=20s "$TIMEOUT_SECONDS" \
  docker run --rm \
    --name "$CONTAINER_NAME" \
    --label "$LABEL" \
    --network none \
    --memory "$MEMORY_LIMIT" \
    --memory-swap "$MEMORY_LIMIT" \
    --cpus "$CPU_LIMIT" \
    --pids-limit 128 \
    --oom-score-adj 700 \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --tmpfs /tmp:rw,noexec,nosuid,size=48m \
    --tmpfs /var/run:rw,noexec,nosuid,size=16m \
    -e OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR=true \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$DATA_ROOT:/app/data" \
    "$OPIP_LEARNING_IMAGE" \
    python -m "$MODULE"
rc=$?
set -e

cleanup
trap - EXIT INT TERM

# Clean exit: no O'Pip job container may survive the invocation.
mapfile -t remaining_ids < <(docker ps -aq --filter "label=$LABEL")
if (( ${#remaining_ids[@]} > 0 )); then
  docker rm -f "${remaining_ids[@]}" >/dev/null 2>&1 || true
  echo "O'Pip learning runner reaped orphan containers after $JOB" >&2
fi

finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$STATE_ROOT/$JOB.last.env.tmp" <<EOF
job=$JOB
module=$MODULE
started_at_utc=$started_at
finished_at_utc=$finished_at
exit_code=$rc
memory_limit=$MEMORY_LIMIT
cpu_limit=$CPU_LIMIT
release_compatibility_status=$RELEASE_STATUS
EOF
mv -f "$STATE_ROOT/$JOB.last.env.tmp" "$STATE_ROOT/$JOB.last.env"

if [[ "$rc" == "0" ]]; then
  disposition="CONSUMED_OK"
  detail="job_completed"
  if [[ "$JOB" == "outcomes" ]]; then
    summary_disp="$(
      awk -F'[:,]' '
        /"disposition"/ {
          for (i = 1; i <= NF; i++) {
            if ($i ~ /"disposition"/) {
              gsub(/[^A-Za-z0-9_]/, "", $(i + 1))
              if ($(i + 1) != "") { print $(i + 1); exit }
            }
          }
        }
      ' "$DATA_ROOT/.learning_consumption/outcomes.json" 2>/dev/null || true
    )"
    if [[ "$summary_disp" == "CONSUMED_EMPTY" || "$summary_disp" == "CONSUMED_OK" ]]; then
      disposition="$summary_disp"
    fi
    pending_count="$(
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
    detail="job_completed"
    write_disposition "$disposition" "$RELEASE_STATUS" "$EXPECTED_SHA" "$rc" "$detail"
    if [[ "$pending_count" =~ ^[0-9]+$ ]]; then
      printf 'accountability_pending_count=%s\n' "$pending_count" \
        >> "$STATE_ROOT/$JOB.disposition.env"
    fi
  else
    write_disposition "$disposition" "$RELEASE_STATUS" "$EXPECTED_SHA" "$rc" "$detail"
  fi
elif [[ "$rc" == "124" || "$rc" == "137" ]]; then
  echo "O'Pip learning job exceeded bounded envelope: job=$JOB rc=$rc" >&2
  write_disposition "FAILED_RETRYABLE" "$RELEASE_STATUS" "$EXPECTED_SHA" "$rc" "timeout_or_oom"
else
  write_disposition "FAILED_TERMINAL" "$RELEASE_STATUS" "$EXPECTED_SHA" "$rc" "job_failed"
fi
exit "$rc"
