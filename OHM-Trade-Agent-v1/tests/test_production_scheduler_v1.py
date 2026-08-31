from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_unified_scheduler_is_single_entrypoint():
    cron = (ROOT / "deploy/cron.d/ohm-unified-cycle").read_text(encoding="utf-8")
    assert "* * * * * root" in cron
    assert "flock -n /var/run/ohm-unified-cycle.lock" in cron
    assert "python -m app.jobs.run_cycle" in cron
    assert "app.jobs.scan_movers" not in cron
    assert "app.jobs.scan_opportunities" not in cron


def test_scheduler_reconciliation_removes_only_legacy_ohm_scheduler_paths():
    script = (ROOT / "deploy/remote/reconcile-scheduler.sh").read_text(encoding="utf-8")
    assert 'CANONICAL_DST="/etc/cron.d/ohm-unified-cycle"' in script
    assert 'LEGACY_MOVEMENT="/etc/cron.d/ohm-movement-discovery"' in script
    assert "app\\.jobs\\.(run_cycle|scan_movers|scan_opportunities|run_opip_ml_capture|build_phase3c_forward_outcomes)" in script
    assert 'grep -v -E' in script
    assert 'cp -a "$tmpdir/canonical.before" "$CANONICAL_DST"' in script
    assert 'crontab "$tmpdir/root.before"' in script
    assert "O'Pip scheduler reconciliation: OK" in script
    assert 'ML_EVIDENCE_DST="/etc/cron.d/opip-ml-evidence"' in script
    assert 'rm -f "$ML_EVIDENCE_DST"' in script
    assert 'LEARNING_EXPORT_DST="/etc/cron.d/opip-learning-export"' in script
    assert 'DEPLOY_SCRIPT_DST="/usr/local/sbin/ohm-deploy"' in script
    assert 'SSH_GATEWAY_DST="/usr/local/sbin/ohm-deploy-ssh"' in script
    assert 'LEARNING_READER_DST="/usr/local/sbin/opip-learning-read-export"' in script
    assert 'LEARNING_DIAGNOSTICS_DST="/usr/local/sbin/diagnose-opip-learning"' in script
    assert 'LEARNING_READER_STATE="/var/lib/opip-learning-reader"' in script


def test_remote_bootstrap_and_future_deploy_reconcile_scheduler():
    bootstrap = (ROOT / "deploy/remote/bootstrap-remote-ops.sh").read_text(encoding="utf-8")
    deploy = (ROOT / "deploy/remote/ohm-deploy").read_text(encoding="utf-8")
    assert 'SCHEDULER_RECONCILE_SRC="$APP_ROOT/deploy/remote/reconcile-scheduler.sh"' in bootstrap
    assert 'bash "$SCHEDULER_RECONCILE_SRC"' in bootstrap
    assert 'SCHEDULER_RECONCILE="$APP_ROOT/deploy/remote/reconcile-scheduler.sh"' in deploy
    assert 'bash "$SCHEDULER_RECONCILE"' in deploy


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_scheduler_shell_scripts_have_valid_bash_syntax():
    for relative in (
        "deploy/remote/reconcile-scheduler.sh",
        "deploy/remote/bootstrap-remote-ops.sh",
        "deploy/remote/ohm-deploy",
        "deploy/remote/ohm-deploy-ssh",
        "deploy/remote/diagnose-opip-learning.sh",
        "deploy/remote/opip-learning-read-export.sh",
    ):
        subprocess.run(
            ["bash", "-n", str(ROOT / relative)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_remote_gateway_keeps_diagnostics_bounded_and_read_only():
    gateway = (ROOT / "deploy/remote/ohm-deploy-ssh").read_text(encoding="utf-8")
    deploy = (ROOT / "deploy/remote/ohm-deploy").read_text(encoding="utf-8")
    diagnostics = (ROOT / "deploy/remote/diagnose-opip-learning.sh").read_text(
        encoding="utf-8"
    )

    assert '"diagnose-learning"' in gateway
    assert "sudo /usr/local/sbin/ohm-deploy diagnose-learning" in gateway
    assert 'if [[ "$TARGET_SHA" == "diagnose-learning" ]]' in deploy
    assert "build_dashboard_read_model" in diagnostics
    assert "production_validation_data=" in diagnostics
    assert "worker_compute_status=" in diagnostics
    assert "MAX_CAPTURE_AGE_SECONDS=900" in diagnostics
    assert "MAX_OUTCOMES_AGE_SECONDS=1800" in diagnostics
    assert "CAPTURE_STALE" in diagnostics
    assert "OUTCOMES_STALE" in diagnostics
    assert "docker exec ohm-trade-agent" in diagnostics
    assert "{{.State.Running}}" in diagnostics
    assert "CORE_CONTAINER_STOPPED" in diagnostics
    assert 'status="FAIL"' in diagnostics
    assert "docker rm" not in diagnostics
    assert "docker stop" not in diagnostics


def test_deploy_rollback_restores_installed_remote_ops():
    deploy = (ROOT / "deploy/remote/ohm-deploy").read_text(encoding="utf-8")
    assert 'DEPLOY_SCRIPT_DST="/usr/local/sbin/ohm-deploy"' in deploy
    assert 'SSH_GATEWAY_DST="/usr/local/sbin/ohm-deploy-ssh"' in deploy
    assert 'LEARNING_READER_DST="/usr/local/sbin/opip-learning-read-export"' in deploy
    assert 'LEARNING_DIAGNOSTICS_DST="/usr/local/sbin/diagnose-opip-learning"' in deploy
    assert "remote-op-ohm-deploy" in deploy
    assert "remote-op-ohm-deploy-ssh" in deploy
    assert "remote-op-learning-reader" in deploy
    assert "remote-op-learning-diagnostics" in deploy
    assert 'cp -a "$SCHEDULER_SNAPSHOT/$snapshot_name" "$target"' in deploy
