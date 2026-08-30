#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
MODULE="${1:-}"

case "$MODULE" in
  app.jobs.run_opip_ml_capture)
    MEMORY_LIMIT="192m"
    MIN_AVAILABLE_KB=$((512 * 1024))
    JOB_TIMEOUT_SECONDS=55
    JOB_SLUG="ml-capture"
    ;;
  app.jobs.build_phase3c_forward_outcomes)
    MEMORY_LIMIT="256m"
    MIN_AVAILABLE_KB=$((768 * 1024))
    JOB_TIMEOUT_SECONDS=420
    JOB_SLUG="phase3c-outcomes"
    ;;
  *)
    echo "unsupported O'Pip background module: $MODULE" >&2
    exit 64
    ;;
esac

for cmd in /usr/bin/docker /usr/bin/timeout awk sed tail tr; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required background-runner command: $cmd" >&2
    exit 69
  }
done

MEM_AVAILABLE_KB="$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo)"
if [[ ! "$MEM_AVAILABLE_KB" =~ ^[0-9]+$ ]]; then
  echo "unable to read host MemAvailable; refusing non-authoritative background job" >&2
  exit 0
fi
if (( MEM_AVAILABLE_KB < MIN_AVAILABLE_KB )); then
  echo "O'Pip background job skipped for host safety: module=$MODULE available_kb=$MEM_AVAILABLE_KB required_kb=$MIN_AVAILABLE_KB" >&2
  exit 0
fi

cd "$APP_ROOT"

CORE_ID="$(/usr/bin/docker compose ps -q ohm-trade-agent)"
if [[ -z "$CORE_ID" ]]; then
  echo "O'Pip core container is not running" >&2
  exit 69
fi

CORE_IMAGE="$(/usr/bin/docker inspect --format '{{.Image}}' "$CORE_ID")"
if [[ -z "$CORE_IMAGE" ]]; then
  echo "unable to resolve O'Pip core image" >&2
  exit 69
fi

# Do not inject the production .env into evidence-only containers. The capture
# path needs only its enable flag; normalize that one non-secret setting and
# leave exchange/API credentials outside this process entirely.
P1_RAW="$(
  sed -n 's/^[[:space:]]*P1_SHADOW_OUTBOX_ENABLED[[:space:]]*=[[:space:]]*//p' \
    "$APP_ROOT/.env" | tail -n 1
)"
P1_NORMALIZED="$(
  printf '%s' "$P1_RAW" | tr '[:upper:]' '[:lower:]' | tr -d '"'"'[:space:]'
)"
case "$P1_NORMALIZED" in
  true|1|yes|on)
    P1_SHADOW_OUTBOX_ENABLED=true
    ;;
  *)
    P1_SHADOW_OUTBOX_ENABLED=false
    ;;
esac

CONTAINER_NAME="opip-background-${JOB_SLUG}-$$"
cleanup() {
  /usr/bin/docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# Evidence jobs are non-authoritative and may be memory-heavy as ledgers grow.
# They run in an isolated, networkless cgroup, only when the host has a safe
# MemAvailable floor. A hard timeout prevents a hung pass from retaining the
# host-level scheduler flock indefinitely.
set +e
/usr/bin/timeout --signal=TERM --kill-after=20s "$JOB_TIMEOUT_SECONDS" \
  /usr/bin/docker run --rm \
    --name "$CONTAINER_NAME" \
    --network none \
    --memory "$MEMORY_LIMIT" \
    --memory-swap "$MEMORY_LIMIT" \
    --cpus 0.20 \
    --pids-limit 128 \
    --oom-score-adj 800 \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --tmpfs /tmp:rw,noexec,nosuid,size=32m \
    --tmpfs /var/run:rw,noexec,nosuid,size=16m \
    -e P1_SHADOW_OUTBOX_ENABLED="$P1_SHADOW_OUTBOX_ENABLED" \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$APP_ROOT/data:/app/data" \
    -v "$APP_ROOT/data/opip/streaming:/app/data/opip/streaming:ro" \
    -v "$APP_ROOT/data/freqtrade/state:/app/freqtrade_paper:ro" \
    "$CORE_IMAGE" \
    python -m "$MODULE"
rc=$?
set -e

if [[ "$rc" == "124" || "$rc" == "137" ]]; then
  echo "O'Pip background job exceeded bounded runtime/memory envelope: module=$MODULE rc=$rc" >&2
fi
exit "$rc"
