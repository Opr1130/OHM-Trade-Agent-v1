#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
CANONICAL_SRC="$APP_ROOT/deploy/cron.d/ohm-unified-cycle"
CANONICAL_DST="/etc/cron.d/ohm-unified-cycle"
LEARNING_EXPORT_SRC="$APP_ROOT/deploy/cron.d/opip-learning-export"
LEARNING_EXPORT_DST="/etc/cron.d/opip-learning-export"
ML_EVIDENCE_DST="/etc/cron.d/opip-ml-evidence"
# R4-B2 bounded Feature Bus SHADOW evidence capture (dedicated, non-overlapping).
CAPTURE_SRC="$APP_ROOT/deploy/cron.d/opip-feature-bus-capture"
CAPTURE_DST="/etc/cron.d/opip-feature-bus-capture"
# R4-B2 bounded SHADOW feasibility-evidence capture (dedicated, non-overlapping).
FEV_CAPTURE_SRC="$APP_ROOT/deploy/cron.d/opip-feasibility-evidence-capture"
FEV_CAPTURE_DST="/etc/cron.d/opip-feasibility-evidence-capture"
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
LEARNING_DIAGNOSTICS_DST="/usr/local/sbin/diagnose-opip-learning"
LEARNING_READER_STATE="/var/lib/opip-learning-reader"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run this scheduler reconciliation with sudo" >&2
  exit 77
fi

for cmd in install crontab grep awk mktemp cp mv rm id; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required command: $cmd" >&2
    exit 69
  }
done

for required in \
  "$CANONICAL_SRC" \
  "$CAPTURE_SRC" \
  "$FEV_CAPTURE_SRC" \
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

# The deploy controller snapshots /usr/local/sbin/ohm-deploy before this script
# runs, then keeps executing the old controller. A later rollback restores that
# snapshot and would otherwise put the pre-deploy controller back. When a
# transaction snapshot is present, replace only its deploy-controller file with
# the checked-out target after the proofs below. No snapshot means this is not
# an in-flight deploy (initial host bootstrap); do not invent one.
preserve_target_deploy_controller_snapshot() {
  local app_root="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
  local state_dir="/var/lib/ohm-deploy"
  if [[ "${OPIP_DEPLOY_TEST_SEAMS:-0}" == "1" ]]; then
    if [[ -n "${OPIP_BOOTSTRAP_APP_ROOT:-}" ]]; then
      app_root="$OPIP_BOOTSTRAP_APP_ROOT"
    fi
    if [[ -n "${OPIP_BOOTSTRAP_STATE_DIR:-}" ]]; then
      state_dir="$OPIP_BOOTSTRAP_STATE_DIR"
    fi
  fi

  command -v git >/dev/null 2>&1 || {
    echo "deploy-controller bootstrap: git is required" >&2
    return 1
  }
  command -v cmp >/dev/null 2>&1 || {
    echo "deploy-controller bootstrap: cmp is required" >&2
    return 1
  }

  local head_sha target_controller
  head_sha="$(git -c safe.directory='*' -C "$app_root" rev-parse --verify HEAD 2>/dev/null || true)"
  [[ "$head_sha" =~ ^[0-9a-f]{40}$ ]] || {
    echo "deploy-controller bootstrap: repository HEAD is not a 40-char SHA" >&2
    return 1
  }

  target_controller="$app_root/deploy/remote/ohm-deploy"
  [[ -f "$target_controller" && ! -L "$target_controller" && -s "$target_controller" ]] || {
    echo "deploy-controller bootstrap: target controller is not a regular file" >&2
    return 1
  }
  if ! bash -n "$target_controller"; then
    echo "deploy-controller bootstrap: target controller failed bash -n" >&2
    return 1
  fi

  local matches=() dirs=() candidate snapshot staged
  shopt -s nullglob
  matches=("$state_dir"/scheduler-before.*)
  shopt -u nullglob
  if [[ "${#matches[@]}" -gt 0 ]]; then
    for candidate in "${matches[@]}"; do
      if [[ -d "$candidate" && ! -L "$candidate" ]]; then
        dirs+=("$candidate")
      fi
    done
  fi
  if [[ "${#dirs[@]}" -ne 1 ]]; then
    echo "deploy-controller bootstrap: expected exactly one scheduler-before transaction snapshot" >&2
    return 1
  fi
  snapshot="${dirs[0]}"

  [[ -f "$snapshot/remote-op-ohm-deploy.present" && ! -L "$snapshot/remote-op-ohm-deploy.present" ]] || {
    echo "deploy-controller bootstrap: snapshot controller present marker is missing" >&2
    return 1
  }
  [[ -f "$snapshot/remote-op-ohm-deploy" && ! -L "$snapshot/remote-op-ohm-deploy" && -s "$snapshot/remote-op-ohm-deploy" ]] || {
    echo "deploy-controller bootstrap: snapshot controller is not a regular file" >&2
    return 1
  }

  if cmp -s -- "$snapshot/remote-op-ohm-deploy" "$target_controller"; then
    echo "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=NOT_NEEDED"
    echo "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP_SHA=$head_sha"
    return 0
  fi

  staged="$(mktemp "$snapshot/remote-op-ohm-deploy.bootstrap.XXXXXX")"
  rm -f -- "$staged"
  # Restore copies this file with cp -a, so it must be executable or the next
  # deploy cannot start the preserved controller.
  install -m 0755 -- "$target_controller" "$staged"
  mv -f -- "$staged" "$snapshot/remote-op-ohm-deploy"
  echo "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=ARMED"
  echo "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP_SHA=$head_sha"
}

preserve_target_deploy_controller_snapshot_if_transaction_present() {
  local state_dir="/var/lib/ohm-deploy"
  if [[ "${OPIP_DEPLOY_TEST_SEAMS:-0}" == "1" && -n "${OPIP_BOOTSTRAP_STATE_DIR:-}" ]]; then
    state_dir="$OPIP_BOOTSTRAP_STATE_DIR"
  fi
  local matches=() real=0 other=0 candidate
  shopt -s nullglob
  matches=("$state_dir"/scheduler-before.*)
  shopt -u nullglob
  if [[ "${#matches[@]}" -gt 0 ]]; then
    for candidate in "${matches[@]}"; do
      if [[ -d "$candidate" && ! -L "$candidate" ]]; then
        real=$((real + 1))
      else
        other=$((other + 1))
      fi
    done
  fi
  if [[ "$real" -eq 0 && "$other" -eq 0 ]]; then
    return 0
  fi
  preserve_target_deploy_controller_snapshot
}

preserve_target_deploy_controller_snapshot_if_transaction_present

tmpdir="$(mktemp -d)"
had_canonical=0
had_learning_export=0
had_ml_evidence=0
had_legacy=0
had_capture=0
had_fev_capture=0
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
if [[ -e "$CAPTURE_DST" ]]; then
  cp -a "$CAPTURE_DST" "$tmpdir/capture.before"
  had_capture=1
fi
if [[ -e "$FEV_CAPTURE_DST" ]]; then
  cp -a "$FEV_CAPTURE_DST" "$tmpdir/fev-capture.before"
  had_fev_capture=1
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

install_executable_atomically() {
  local source="$1"
  local target="$2"
  local tmp
  tmp="$(mktemp "${target}.install.XXXXXX")"
  rm -f "$tmp"
  install -o root -g root -m 0755 "$source" "$tmp"
  mv -f "$tmp" "$target"
}

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
  if [[ "$had_capture" == "1" ]]; then
    cp -a "$tmpdir/capture.before" "$CAPTURE_DST"
  else
    rm -f "$CAPTURE_DST"
  fi
  if [[ "$had_fev_capture" == "1" ]]; then
    cp -a "$tmpdir/fev-capture.before" "$FEV_CAPTURE_DST"
  else
    rm -f "$FEV_CAPTURE_DST"
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
install -o root -g root -m 0644 "$CAPTURE_SRC" "$CAPTURE_DST"
install -o root -g root -m 0644 "$FEV_CAPTURE_SRC" "$FEV_CAPTURE_DST"

# Refresh forced-command remote operations from the exact deployed SHA. This
# keeps the production deploy gateway and read-only learning observability in
# lockstep with the checked-out release without relaxing SSH authority.
install_executable_atomically "$DEPLOY_SCRIPT_SRC" "$DEPLOY_SCRIPT_DST"
install_executable_atomically "$SSH_GATEWAY_SRC" "$SSH_GATEWAY_DST"
install_executable_atomically "$LEARNING_READER_SRC" "$LEARNING_READER_DST"
install_executable_atomically "$LEARNING_DIAGNOSTICS_SRC" "$LEARNING_DIAGNOSTICS_DST"
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
grep -q 'capture_feasibility_evidence_shadow' "$FEV_CAPTURE_DST"

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
echo "fev_capture=$FEV_CAPTURE_DST"
echo "fev_capture_cadence=1 minute (dual-gated SHADOW; inert unless Feature Bus AND writer are shadow)"
