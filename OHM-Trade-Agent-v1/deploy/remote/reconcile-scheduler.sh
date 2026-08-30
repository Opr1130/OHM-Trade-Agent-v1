#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
CANONICAL_SRC="$APP_ROOT/deploy/cron.d/ohm-unified-cycle"
CANONICAL_DST="/etc/cron.d/ohm-unified-cycle"
LEGACY_MOVEMENT="/etc/cron.d/ohm-movement-discovery"
STREAM_RECONCILE="$APP_ROOT/deploy/remote/reconcile-stream-worker.sh"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run this scheduler reconciliation with sudo" >&2
  exit 77
fi

for cmd in install crontab grep mktemp cp rm; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required command: $cmd" >&2
    exit 69
  }
done

if [[ ! -s "$CANONICAL_SRC" ]]; then
  echo "canonical scheduler missing: $CANONICAL_SRC" >&2
  exit 69
fi
if [[ ! -s "$STREAM_RECONCILE" ]]; then
  echo "stream worker reconciliation missing: $STREAM_RECONCILE" >&2
  exit 69
fi

tmpdir="$(mktemp -d)"
had_canonical=0
had_legacy=0
had_root_crontab=0

if [[ -e "$CANONICAL_DST" ]]; then
  cp -a "$CANONICAL_DST" "$tmpdir/canonical.before"
  had_canonical=1
fi
if [[ -e "$LEGACY_MOVEMENT" ]]; then
  cp -a "$LEGACY_MOVEMENT" "$tmpdir/legacy.before"
  had_legacy=1
fi
if crontab -l > "$tmpdir/root.before" 2>/dev/null; then
  had_root_crontab=1
else
  : > "$tmpdir/root.before"
fi

rollback() {
  rc=$?
  trap - ERR
  if [[ "$had_canonical" == "1" ]]; then
    cp -a "$tmpdir/canonical.before" "$CANONICAL_DST"
  else
    rm -f "$CANONICAL_DST"
  fi
  if [[ "$had_legacy" == "1" ]]; then
    cp -a "$tmpdir/legacy.before" "$LEGACY_MOVEMENT"
  else
    rm -f "$LEGACY_MOVEMENT"
  fi
  if [[ "$had_root_crontab" == "1" ]]; then
    crontab "$tmpdir/root.before"
  else
    crontab -r 2>/dev/null || true
  fi
  rm -rf "$tmpdir"
  exit "$rc"
}
trap rollback ERR

install -o root -g root -m 0644 "$CANONICAL_SRC" "$CANONICAL_DST"
rm -f "$LEGACY_MOVEMENT"

# Remove only legacy O'Pip direct/unified scheduler lines from root's personal
# crontab. Preserve every unrelated root cron entry exactly as-is.
grep -v -E 'app\.jobs\.(run_cycle|scan_movers|scan_opportunities)'   "$tmpdir/root.before" > "$tmpdir/root.after" || true
crontab "$tmpdir/root.after"

grep -q 'app.jobs.run_cycle' "$CANONICAL_DST"
if [[ -e "$LEGACY_MOVEMENT" ]]; then
  echo "legacy movement scheduler still exists" >&2
  false
fi
if crontab -l 2>/dev/null | grep -Eq 'app\.jobs\.(run_cycle|scan_movers|scan_opportunities)'; then
  echo "legacy O'Pip scheduler line remains in root crontab" >&2
  false
fi

bash "$STREAM_RECONCILE"

trap - ERR
rm -rf "$tmpdir"

echo "OHM scheduler reconciliation: OK"
echo "canonical=$CANONICAL_DST"
echo "cadence=1 minute"
echo "entrypoint=app.jobs.run_cycle"
