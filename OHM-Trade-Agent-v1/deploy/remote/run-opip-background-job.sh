#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
MODULE="${1:-}"

case "$MODULE" in
  app.jobs.run_opip_ml_capture|app.jobs.build_phase3c_forward_outcomes)
    ;;
  *)
    echo "unsupported O'Pip background module: $MODULE" >&2
    exit 64
    ;;
esac

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

# Evidence jobs are non-authoritative and may be memory-heavy as ledgers grow.
# Run them in an isolated, networkless container so a pathological evidence
# pass is killed before it can starve the core or Freqtrade paper workers.
exec /usr/bin/docker run --rm \
  --network none \
  --memory 512m \
  --memory-swap 512m \
  --cpus 0.25 \
  --pids-limit 128 \
  --oom-score-adj 800 \
  --read-only \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --tmpfs /var/run:rw,noexec,nosuid,size=16m \
  --env-file "$APP_ROOT/.env" \
  -e OPIP_EVENT_STORE_ENABLED=true \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$APP_ROOT/data:/app/data" \
  -v "$APP_ROOT/data/opip/streaming:/app/data/opip/streaming:ro" \
  -v "$APP_ROOT/data/freqtrade/state:/app/freqtrade_paper:ro" \
  "$CORE_IMAGE" \
  python -m "$MODULE"
