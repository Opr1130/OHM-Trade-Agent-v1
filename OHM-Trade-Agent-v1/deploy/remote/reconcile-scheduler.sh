#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
CANONICAL_SRC="$APP_ROOT/deploy/cron.d/ohm-unified-cycle"
CANONICAL_DST="/etc/cron.d/ohm-unified-cycle"
LEARNING_EXPORT_SRC="$APP_ROOT/deploy/cron.d/opip-learning-export"
LEARNING_EXPORT_DST="/etc/cron.d/opip-learning-export"
ML_EVIDENCE_DST="/etc/cron.d/opip-ml-evidence"
LEGACY_MOVEMENT="/etc/cron.d/ohm-movement-discovery"
STREAM_RECONCILE="$APP_ROOT/deploy/remote/reconcile-stream-worker.sh"
LEARNING_EXPORTER="$APP_ROOT/deploy/remote/export-opip-learning-evidence.sh"
DEPLOY_SCRIPT_SRC="$APP_ROOT/deploy/remote/ohm-deploy"
SSH_GATEWAY_SRC="$APP_ROOT/deploy/remote/ohm-deploy-ssh"
LEARNING_READER_SRC="$APP_ROOT/deploy/remote/opip-learning-read-export.sh"
LEARNING_DIAGNOSTICS_SRC="$APP_ROOT/deploy/remote/diagnose-opip-learning.sh"
DEPLOY_SCRIPT_DST="/usr/local/sbin/ohm-deploy"
SSH_GATEWAY_DST="/usr/local/sbin/ohm-deploy-ssh"
LEARNING_READER_DST="/usr/local/sbin/opip-learning-read-export"
LEARNING_READER_STATE="/var/lib/opip-learning-reader"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run this scheduler reconciliation with sudo" >&2
  exit 77
fi

for cmd in install crontab grep awk mktemp cp rm; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required command: $cmd" >&2
    exit 69
  }
done

for required in \
  "$CANONICAL_SRC" \
  "$LEARNING_EXPORT_SRC" \
  "$STREAM_RECONCILE" \
  "$LEARNING_EXPORTER" \
  "$DEPLOY_SCRIPT_SRC" \
  "$SSH_GATEWAY_SRC" \
  "$LEARNING_READER_SRC" \
  "$LEARNING_DIAGNOSTICS_SRC"; do
  if [[ ! -s "$required" ]]; then
    echo "required production scheduler artifact missing: $required" >&2
    exit 69
  fi
done

tmpdir="$(mktemp -d)"
had_canonical=0
had_learning_export=0
had_ml_evidence=0
had_legacy=0
had_root_crontab=0

snapshot_file() {
  local path="$1"
  local name="$2"
  if [[ -e "$path" ]]; then
    cp -a "$path" "$tmpdir/$name.before"
    printf -v "had_$name" '%s' 1
  fi
}

if [[ -e "$CANONICAL_DST" ]]; then
  cp -a "$CANONICAL_DST" "$tmpdir/canonical.before"
  had_canonical=1
fi
if [[ -e "$LEARNING_EXPORT_DST" ]]; then
  cp -a "$LEARNING_EXPORT_DST" "$tmpdir/learning-export.before"
  had_learning_export=1
fi
if [[ -e "$ML_EVIDENCE_DST" ]]; then
  cp -a "$ML_EVIDENCE_DST" "$tmpdir/ml-evidence.before"
  had_ml_evidence=1
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
  if [[ "$had_learning_export" == "1" ]]; then
    cp -a "$tmpdir/learning-export.before" "$LEARNING_EXPORT_DST"
  else
    rm -f "$LEARNING_EXPORT_DST"
  fi
  if [[ "$had_ml_evidence" == "1" ]]; then
    cp -a "$tmpdir/ml-evidence.before" "$ML_EVIDENCE_DST"
  else
    rm -f "$ML_EVIDENCE_DST"
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
install -o root -g root -m 0644 "$LEARNING_EXPORT_SRC" "$LEARNING_EXPORT_DST"

# Refresh forced-command remote operations from the exact deployed SHA. This
# keeps the production deploy gateway and read-only learning observability in
# lockstep with the checked-out release without relaxing SSH authority.
install -o root -g root -m 0755 "$DEPLOY_SCRIPT_SRC" "$DEPLOY_SCRIPT_DST"
install -o root -g root -m 0755 "$SSH_GATEWAY_SRC" "$SSH_GATEWAY_DST"
install -o root -g root -m 0755 "$LEARNING_READER_SRC" "$LEARNING_READER_DST"
if id opiplearn >/dev/null 2>&1; then
  install -d -o opiplearn -g opiplearn -m 0750 "$LEARNING_READER_STATE"
fi

# Learning compute is no longer permitted on the production droplet.
rm -f "$ML_EVIDENCE_DST"
rm -f "$LEGACY_MOVEMENT"

# Remove legacy direct scheduler lines while preserving unrelated root jobs.
grep -v -E 'app\.jobs\.(run_cycle|scan_movers|scan_opportunities|run_opip_ml_capture|build_phase3c_forward_outcomes)' \
  "$tmpdir/root.before" > "$tmpdir/root.after" || true
crontab "$tmpdir/root.after"

grep -q 'app.jobs.run_cycle' "$CANONICAL_DST"
grep -q 'export-opip-learning-evidence.sh' "$LEARNING_EXPORT_DST"

if [[ -e "$ML_EVIDENCE_DST" ]]; then
  echo "production ML evidence cron still exists" >&2
  false
fi
if [[ -e "$LEGACY_MOVEMENT" ]]; then
  echo "legacy movement scheduler still exists" >&2
  false
fi
if crontab -l 2>/dev/null | grep -Eq 'app\.jobs\.(run_cycle|scan_movers|scan_opportunities|run_opip_ml_capture|build_phase3c_forward_outcomes)'; then
  echo "legacy O'Pip scheduler line remains in root crontab" >&2
  false
fi

if bash "$STREAM_RECONCILE"; then
  echo "O'Pip stream worker reconciliation: healthy"
else
  stream_rc=$?
  echo "O'Pip stream worker reconciliation: degraded (rc=$stream_rc); shadow evidence unavailable or incomplete; production core unaffected" >&2
fi

trap - ERR
rm -rf "$tmpdir"

echo "O'Pip scheduler reconciliation: OK"
echo "canonical=$CANONICAL_DST"
echo "core_cadence=1 minute"
echo "core_entrypoint=app.jobs.run_cycle"
echo "learning_compute=REMOTE_ONLY"
echo "local_ml_evidence_cron=ABSENT"
echo "learning_export=$LEARNING_EXPORT_DST"
echo "learning_export_cadence=2 minutes + 40s offset"
echo "learning_reader_observability=ENABLED"
