from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ml_evidence_cron_is_independent_from_unified_cycle_lock():
    cron = (ROOT / "deploy" / "cron.d" / "opip-ml-evidence").read_text(
        encoding="utf-8"
    )
    assert "app.jobs.run_opip_ml_capture" in cron
    assert "app.jobs.build_phase3c_forward_outcomes" in cron
    assert "/var/run/opip-ml-capture.lock" in cron
    assert "/var/run/opip-ml-outcomes.lock" in cron
    executable = "\n".join(
        line for line in cron.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert "/var/run/ohm-unified-cycle.lock" not in executable
    assert "* * * * * root" in cron
    assert "*/10 * * * * root" in cron


def test_unified_cycle_does_not_import_or_run_ml_capture():
    source = (ROOT / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    assert "opip_ml_evidence_capture" not in source
    assert "run_opip_ml_capture" not in source


def test_scheduler_reconcile_installs_and_validates_ml_evidence_cron():
    source = (
        ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
    ).read_text(encoding="utf-8")
    assert 'ML_EVIDENCE_DST="/etc/cron.d/opip-ml-evidence"' in source
    assert 'install -o root -g root -m 0644 "$ML_EVIDENCE_SRC" "$ML_EVIDENCE_DST"' in source
    assert "app.jobs.run_opip_ml_capture" in source
    assert "app.jobs.build_phase3c_forward_outcomes" in source


def test_deploy_probes_ml_capture_without_making_it_authoritative():
    source = (ROOT / "deploy" / "remote" / "ohm-deploy").read_text(
        encoding="utf-8"
    )
    assert "python -m app.jobs.run_opip_ml_capture" in source
    assert "if docker compose exec -T ohm-trade-agent" in source
    assert "production unaffected" in source
