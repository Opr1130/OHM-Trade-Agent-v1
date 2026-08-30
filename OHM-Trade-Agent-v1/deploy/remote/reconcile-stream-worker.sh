#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
STREAM_DATA="$APP_ROOT/data/opip/streaming"
SERVICE="opip-stream-worker"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run stream-worker reconciliation with sudo" >&2
  exit 77
fi

for cmd in docker install grep; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required command: $cmd" >&2
    exit 69
  }
done

cd "$APP_ROOT"
docker compose config --services | grep -qx "$SERVICE" || {
  echo "stream worker service missing from compose" >&2
  exit 69
}

install -d -m 0755 "$STREAM_DATA"

docker compose build "$SERVICE"
docker compose up -d "$SERVICE"

healthy=0
for _ in $(seq 1 45); do
  status="$(
    docker inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
      "$SERVICE" 2>/dev/null || true
  )"
  if [[ "$status" == "healthy" ]]; then
    healthy=1
    break
  fi
  if [[ "$status" == "unhealthy" ]]; then
    break
  fi
  sleep 2
done

if [[ "$healthy" != "1" ]]; then
  echo "O'Pip stream worker health check failed" >&2
  docker compose ps "$SERVICE" >&2 || true
  docker compose logs --tail=120 "$SERVICE" >&2 || true
  exit 1
fi

docker exec "$SERVICE" python -m app.opip.streaming.healthcheck

echo "OPIP stream worker reconciliation: OK"
docker compose ps "$SERVICE"
cat "$STREAM_DATA/health.json"
echo
