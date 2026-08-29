#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${OPIP_APP_ROOT:-/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1}"
MEM_WARN="${OPIP_MEM_WARN_PCT:-75}"
MEM_CRIT="${OPIP_MEM_CRIT_PCT:-90}"
SWAP_WARN="${OPIP_SWAP_WARN_PCT:-35}"
SWAP_CRIT="${OPIP_SWAP_CRIT_PCT:-70}"
DISK_WARN="${OPIP_DISK_WARN_PCT:-75}"
DISK_CRIT="${OPIP_DISK_CRIT_PCT:-90}"

for value in "$MEM_WARN" "$MEM_CRIT" "$SWAP_WARN" "$SWAP_CRIT" "$DISK_WARN" "$DISK_CRIT"; do
  [[ "$value" =~ ^[0-9]+$ ]] || {
    echo "invalid percentage threshold: $value" >&2
    exit 64
  }
done

if (( MEM_WARN > MEM_CRIT || SWAP_WARN > SWAP_CRIT || DISK_WARN > DISK_CRIT )); then
  echo "warning threshold cannot exceed critical threshold" >&2
  exit 64
fi

pct_used() {
  local total="$1"
  local available="$2"
  if (( total <= 0 )); then
    echo 0
    return
  fi
  echo $(( (100 * (total - available)) / total ))
}

MEM_TOTAL_KB="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
MEM_AVAILABLE_KB="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
SWAP_TOTAL_KB="$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)"
SWAP_FREE_KB="$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)"

MEM_USED_PCT="$(pct_used "$MEM_TOTAL_KB" "$MEM_AVAILABLE_KB")"
SWAP_USED_PCT="$(pct_used "$SWAP_TOTAL_KB" "$SWAP_FREE_KB")"

if [[ -d "$APP_ROOT" ]]; then
  DISK_USED_PCT="$(df -P "$APP_ROOT" | awk 'NR==2 {gsub("%","",$5); print $5}')"
else
  DISK_USED_PCT="$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')"
fi

status="HEALTHY"
critical=0

evaluate_metric() {
  local name="$1"
  local actual="$2"
  local warn="$3"
  local crit="$4"
  if (( actual >= crit )); then
    echo "CRITICAL $name=${actual}% threshold=${crit}%"
    status="CRITICAL"
    critical=1
  elif (( actual >= warn )); then
    echo "WARN $name=${actual}% threshold=${warn}%"
    if [[ "$status" == "HEALTHY" ]]; then
      status="WARN"
    fi
  else
    echo "OK $name=${actual}%"
  fi
}

echo "===== O'Pip Platform Check ====="
evaluate_metric "memory_used" "$MEM_USED_PCT" "$MEM_WARN" "$MEM_CRIT"
if (( SWAP_TOTAL_KB > 0 )); then
  evaluate_metric "swap_used" "$SWAP_USED_PCT" "$SWAP_WARN" "$SWAP_CRIT"
else
  echo "OK swap_used=0% (no swap configured)"
fi
evaluate_metric "disk_used" "$DISK_USED_PCT" "$DISK_WARN" "$DISK_CRIT"

if ! command -v docker >/dev/null 2>&1; then
  echo "CRITICAL docker=unavailable"
  status="CRITICAL"
  critical=1
else
  echo "OK docker=available"
  if [[ -d "$APP_ROOT" ]]; then
    mapfile -t running_services < <(
      cd "$APP_ROOT" &&
      docker compose ps --status running --services 2>/dev/null || true
    )
    if printf '%s\n' "${running_services[@]:-}" | grep -qx 'ohm-trade-agent'; then
      echo "OK core_container=running"
    else
      echo "CRITICAL core_container=not_running"
      status="CRITICAL"
      critical=1
    fi
  else
    echo "CRITICAL app_root=missing path=$APP_ROOT"
    status="CRITICAL"
    critical=1
  fi
fi

if command -v curl >/dev/null 2>&1 && curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/health | grep -q '"status":"ok"'; then
  echo "OK application_health=healthy"
else
  echo "CRITICAL application_health=unhealthy"
  status="CRITICAL"
  critical=1
fi

echo "STATUS=$status"

if (( critical )); then
  exit 2
fi
exit 0
