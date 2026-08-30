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
    ):
        subprocess.run(
            ["bash", "-n", str(ROOT / relative)],
            check=True,
            capture_output=True,
            text=True,
        )
