#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="/etc/opip-learning.env"
LOCK_FILE="/var/lock/opip-learning-plane.lock"
DATA_ROOT="/var/lib/opip-learning/data"
STATE_ROOT="/var/lib/opip-learning/state"
JOB="${1:-}"

[[ -r "$ENV_FILE" ]] || {
  echo "missing O'Pip learning environment: $ENV_FILE" >&2
  exit 78
}
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${OPIP_LEARNING_IMAGE:?OPIP_LEARNING_IMAGE is required}"

case "$JOB" in
  capture)
    MODULE="app.jobs.run_opip_ml_capture"
    MEMORY_LIMIT="384m"
    CPU_LIMIT="0.60"
    MIN_AVAILABLE_KB=$((512 * 1024))
    TIMEOUT_SECONDS=180
    ;;
  outcomes)
    MODULE="app.jobs.build_phase3c_forward_outcomes"
    MEMORY_LIMIT="512m"
    CPU_LIMIT="0.70"
    MIN_AVAILABLE_KB=$((640 * 1024))
    TIMEOUT_SECONDS=480
    ;;
  *)
    echo "unsupported O'Pip learning job: $JOB" >&2
    exit 64
    ;;
esac

for cmd in docker flock timeout awk install date; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing learning-runner command: $cmd" >&2
    exit 69
  }
done

install -d -o root -g root -m 0755 "$DATA_ROOT" "$STATE_ROOT"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "O'Pip learning plane busy; $JOB skipped"
  exit 0
fi

MEM_AVAILABLE_KB="$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo)"
if [[ ! "$MEM_AVAILABLE_KB" =~ ^[0-9]+$ ]] || (( MEM_AVAILABLE_KB < MIN_AVAILABLE_KB )); then
  echo "O'Pip learning job skipped for memory safety: job=$JOB available_kb=$MEM_AVAILABLE_KB required_kb=$MIN_AVAILABLE_KB" >&2
  exit 0
fi

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
timeout --signal=TERM --kill-after=20s "$TIMEOUT_SECONDS"   docker run --rm     --name "$CONTAINER_NAME"     --label "$LABEL"     --network none     --memory "$MEMORY_LIMIT"     --memory-swap "$MEMORY_LIMIT"     --cpus "$CPU_LIMIT"     --pids-limit 128     --oom-score-adj 700     --read-only     --cap-drop ALL     --security-opt no-new-privileges:true     --tmpfs /tmp:rw,noexec,nosuid,size=48m     --tmpfs /var/run:rw,noexec,nosuid,size=16m     -e P1_SHADOW_OUTBOX_ENABLED=true     -e PYTHONDONTWRITEBYTECODE=1     -v "$DATA_ROOT:/app/data"     "$OPIP_LEARNING_IMAGE"     python -m "$MODULE"
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
EOF
mv -f "$STATE_ROOT/$JOB.last.env.tmp" "$STATE_ROOT/$JOB.last.env"

if [[ "$rc" == "124" || "$rc" == "137" ]]; then
  echo "O'Pip learning job exceeded bounded envelope: job=$JOB rc=$rc" >&2
fi
exit "$rc"
