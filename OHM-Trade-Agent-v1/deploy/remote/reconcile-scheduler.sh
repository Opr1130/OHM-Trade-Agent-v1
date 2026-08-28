#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
CANONICAL_SRC="$APP_ROOT/deploy/cron.d/ohm-unified-cycle"
CANONICAL_DST="/etc/cron.d/ohm-unified-cycle"
LEGACY_MOVEMENT="/etc/cron.d/ohm-movement-discovery"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run this scheduler reconciliation with sudo" >&2
  exit 77
fi

for cmd in install crontab grep mktemp; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required command: $cmd" >&2
    exit 69
  }
done

if [[ ! -s "$CANONICAL_SRC" ]]; then
  echo "canonical scheduler missing: $CANONICAL_SRC" >&2
  exit 69
fi

install -o root -g root -m 0644 "$CANONICAL_SRC" "$CANONICAL_DST"
rm -f "$LEGACY_MOVEMENT"

tmp="$(mktemp)"
current="${tmp}.current"
trap 'rm -f "$tmp" "$current"' EXIT

# Remove only legacy O'Pip direct/unified scheduler lines from root's personal
# crontab. Preserve every unrelated root cron entry exactly as-is.
if crontab -l >"$current" 2>/dev/null; then
  grep -v -E 'app\.jobs\.(run_cycle|scan_movers|scan_opportunities)'     "$current" > "$tmp" || true
  crontab "$tmp"
  rm -f "$current"
fi

grep -q 'app.jobs.run_cycle' "$CANONICAL_DST"
if [[ -e "$LEGACY_MOVEMENT" ]]; then
  echo "legacy movement scheduler still exists" >&2
  exit 1
fi
if crontab -l 2>/dev/null | grep -Eq 'app\.jobs\.(run_cycle|scan_movers|scan_opportunities)'; then
  echo "legacy O'Pip scheduler line remains in root crontab" >&2
  exit 1
fi

echo "OHM scheduler reconciliation: OK"
echo "canonical=$CANONICAL_DST"
echo "cadence=1 minute"
echo "entrypoint=app.jobs.run_cycle"
