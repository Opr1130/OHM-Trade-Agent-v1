"""Learning worker release admission, dispositions, and consumption visibility."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.opip.learning import job_disposition as jd


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
LEARNING = ROOT / "deploy" / "learning"


def test_release_compatibility_exact_sha_classifier():
    sha = "9e17cbf01c62d4236af011a2f8e4567973aaa271"
    assert jd.classify_release_compatibility(sha, sha) == jd.RELEASE_CURRENT
    assert (
        jd.classify_release_compatibility(sha, "a" * 40) == jd.RELEASE_DRIFT
    )
    assert jd.classify_release_compatibility(sha, "") == jd.RELEASE_UNVERIFIED
    assert jd.classify_release_compatibility("short", sha) == jd.RELEASE_UNVERIFIED


def test_disposition_env_roundtrip_and_terminal_vocab(tmp_path: Path):
    path = jd.write_disposition_env(
        tmp_path,
        job="capture",
        disposition=jd.BLOCKED_RELEASE_DRIFT,
        release_compatibility_status=jd.RELEASE_DRIFT,
        worker_sha="a" * 40,
        expected_sha="b" * 40,
        exit_code=75,
        detail="exact_sha_admission_failed",
    )
    assert path.name == "capture.disposition.env"
    payload = jd.read_disposition_env(tmp_path, "capture")
    assert payload["disposition"] == jd.BLOCKED_RELEASE_DRIFT
    assert payload["release_compatibility_status"] == jd.RELEASE_DRIFT
    assert payload["policy_change_authorized"] == "false"
    assert payload["measurement_only"] == "true"


def test_consumption_summary_json_is_durable(tmp_path: Path):
    path = jd.write_consumption_summary(
        tmp_path,
        job="outcomes",
        disposition=jd.CONSUMED_OK,
        payload={
            "accountability_pending_count": 2,
            "accountability_handoff_acknowledged": 1,
        },
    )
    assert path.is_file()
    body = jd.read_consumption_summary(tmp_path, "outcomes")
    assert body is not None
    assert body["disposition"] == jd.CONSUMED_OK
    assert body["accountability_pending_count"] == 2
    assert body["policy_change_authorized"] is False


def test_learning_job_script_fail_closed_and_records_skips():
    runner = LEARNING / "opip-learning-job.sh"
    source = runner.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(runner)], check=True)
    assert "BLOCKED_RELEASE_DRIFT" in source
    assert "exact_sha_admission_failed" in source
    assert "exit 75" in source
    assert '[[ "$RELEASE_STATUS" != "CURRENT" ]]' in source
    assert "SKIPPED_BUSY" in source
    assert "SKIPPED_CAPACITY" in source
    assert "production_deployed_sha" in source
    assert "CONSUMED_OK" in source
    assert "FAILED_RETRYABLE" in source
    assert "FAILED_TERMINAL" in source
    assert "policy_change_authorized=false" in source
    # Busy/capacity remain exit 0 but must write dispositions first.
    busy_idx = source.index("SKIPPED_BUSY")
    assert source.index("exit 0", busy_idx) > busy_idx


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="Git Bash fork is unreliable on Windows; Linux CI executes this path",
)
def test_learning_job_shell_admission_writes_blocked_disposition(tmp_path: Path):
    """Invoke real opip-learning-job.sh with path overrides under RELEASE_DRIFT."""
    state = tmp_path / "state"
    data = tmp_path / "data"
    lock = tmp_path / "plane.lock"
    env_file = tmp_path / "opip-learning.env"
    state.mkdir()
    data.mkdir()
    worker = "a" * 40
    expected = "b" * 40
    env_file.write_bytes(
        (
            f"OPIP_LEARNING_IMAGE=opip-learning:{worker}\n"
            f"OPIP_DEPLOYED_SHA={worker}\n"
        ).encode("utf-8")
    )
    (data / "manifest.env").write_bytes(
        f"schema_version=3\nproduction_deployed_sha={expected}\n".encode("utf-8")
    )
    env = {
        **dict(__import__("os").environ),
        "OPIP_LEARNING_ENV_FILE": str(env_file),
        "OPIP_LEARNING_LOCK_FILE": str(lock),
        "OPIP_LEARNING_DATA_ROOT": str(data),
        "OPIP_LEARNING_STATE_ROOT": str(state),
    }
    result = subprocess.run(
        ["bash", str(LEARNING / "opip-learning-job.sh"), "capture"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 75, (result.stdout, result.stderr)
    payload = jd.read_disposition_env(state, "capture")
    assert payload["disposition"] == jd.BLOCKED_RELEASE_DRIFT
    assert payload["release_compatibility_status"] == jd.RELEASE_DRIFT

    (data / "manifest.env").write_bytes(
        f"schema_version=3\nproduction_deployed_sha={worker}\n".encode("utf-8")
    )
    # Exact SHA admits past release gate; without docker the runner exits 69.
    ok = subprocess.run(
        ["bash", str(LEARNING / "opip-learning-job.sh"), "capture"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert ok.returncode == 69, (ok.stdout, ok.stderr)
    assert "missing learning-runner command: docker" in ok.stderr or (
        "missing learning-runner command: timeout" in ok.stderr
    )


def test_export_manifest_includes_production_deployed_sha():
    export = (
        ROOT / "deploy" / "remote" / "export-opip-learning-evidence.sh"
    ).read_text(encoding="utf-8")
    assert "production_deployed_sha=" in export
    assert "last-good-sha" in export
    assert "rev-parse HEAD" not in export


def test_deploy_learning_workflow_owner_gated_exact_main_pytest():
    workflow = (REPO_ROOT / ".github/workflows/deploy-learning.yml").read_text(
        encoding="utf-8"
    )
    assert "github.event.comment.user.login == github.repository_owner" in workflow
    assert "github.event.comment.author_association == 'OWNER'" in workflow
    assert "Require target to equal current main" in workflow
    assert "Require successful exact-SHA CI" in workflow
    assert "/deploy-learning" in workflow
    assert "run-gated-learning-deploy.sh" in workflow
    assert "does not merge, trade" in workflow
    assert "persist-credentials: false" in workflow
    assert "Kraken/Telegram" in workflow or "Kraken or Telegram" in workflow


def test_gated_learning_deploy_script_no_trading_creds():
    script = LEARNING / "run-gated-learning-deploy.sh"
    source = script.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert "OPIP_DEPLOYED_SHA=" in source
    assert "OPIP_LEARNING_IMAGE=" in source
    assert "KRAKEN" not in source
    assert "TELEGRAM" not in source
    assert "policy_change_authorized=false" in source


def test_diagnose_surfaces_release_consumption_and_zero_funnel():
    diagnostics = (
        ROOT / "deploy" / "remote" / "diagnose-opip-learning.sh"
    ).read_text(encoding="utf-8")
    subprocess.run(
        ["bash", "-n", str(ROOT / "deploy" / "remote" / "diagnose-opip-learning.sh")],
        check=True,
    )
    assert "release_compatibility_status=" in diagnostics
    assert "capture_disposition=" in diagnostics
    assert "outcomes_disposition=" in diagnostics
    assert "accountability_pending_count=" in diagnostics
    assert "consumption_lag_vs_export_seconds=" in diagnostics
    assert "OPIP_ZERO_FUNNEL_CLARITY" in diagnostics
    assert "early_watch_journeys_are_not_qualification_funnel_producer" in diagnostics
    assert "early_watch_journeys=" in diagnostics
    assert "qualified_signals=" in diagnostics
    assert "funnel_candidates_source=OPIP_QUALIFICATION_FUNNEL" in diagnostics
    # Must not treat export cron alone as healthy under drift.
    assert 'RELEASE_DRIFT' in diagnostics
    assert "worker_compute_status=RELEASE_DRIFT" in diagnostics
    assert "worker_compute_status=BLOCKED_RELEASE_DRIFT" in diagnostics


def test_learning_consumption_guardrail_present():
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    rule = (
        REPO_ROOT / ".cursor" / "rules" / "opip-learning-consumption.mdc"
    ).read_text(encoding="utf-8")
    needle = "Every eligible persisted learning artifact must reach a traceable consumption disposition"
    assert needle in agents
    assert needle in rule
    assert "fail closed" in rule.lower() or "fail-closed" in rule.lower()
    assert "/deploy-learning" in agents


def test_outcomes_cycle_writes_consumption_summary(tmp_path: Path, monkeypatch):
    import app.jobs.run_opportunity_intelligence_cycle as cycle

    monkeypatch.setattr(cycle, "_DEFAULT_DATA_ROOT", tmp_path)

    def _pending():
        return []

    def _build():
        return []

    def _incremental(outcomes, replica_mode=True):
        return {"population": {}, "opportunity_capture_rate_pct": None}

    def _resolved(outcomes):
        return []

    def _ack(resolved):
        return 0

    monkeypatch.setattr(cycle, "pending_accountability_outcomes", _pending)
    monkeypatch.setattr(cycle, "build_outcomes_bounded", _build)
    monkeypatch.setattr(cycle, "build_incremental_from_outcomes", _incremental)
    monkeypatch.setattr(cycle, "resolved_accountability_outcomes", _resolved)
    monkeypatch.setattr(cycle, "acknowledge_accountability_outcomes", _ack)

    cycle.main()
    summary = jd.read_consumption_summary(tmp_path, "outcomes")
    assert summary is not None
    assert summary["disposition"] == jd.CONSUMED_EMPTY
    assert summary["accountability_pending_count"] == 0
    assert summary["policy_change_authorized"] is False


def test_outcomes_handoff_ack_idempotent_on_replay(tmp_path: Path, monkeypatch):
    """Replay drains pending once; second ack does not double-apply."""
    import app.jobs.run_opportunity_intelligence_cycle as cycle

    monkeypatch.setattr(cycle, "_DEFAULT_DATA_ROOT", tmp_path)
    calls = {"ack": 0, "pending_rounds": 0}
    handoff = [{"snapshot_id": "s1", "outcome_revision": 1}]

    def _pending():
        calls["pending_rounds"] += 1
        # First drain returns the row; after ack, empty.
        if calls["ack"] == 0 and calls["pending_rounds"] <= 2:
            return list(handoff)
        return []

    def _build():
        return []

    def _incremental(outcomes, replica_mode=True):
        assert outcomes == handoff
        return {"population": {"n": 1}, "opportunity_capture_rate_pct": 0.0}

    def _resolved(outcomes):
        return list(outcomes)

    def _ack(resolved):
        calls["ack"] += 1
        assert resolved == handoff
        return len(resolved)

    monkeypatch.setattr(cycle, "pending_accountability_outcomes", _pending)
    monkeypatch.setattr(cycle, "build_outcomes_bounded", _build)
    monkeypatch.setattr(cycle, "build_incremental_from_outcomes", _incremental)
    monkeypatch.setattr(cycle, "resolved_accountability_outcomes", _resolved)
    monkeypatch.setattr(cycle, "acknowledge_accountability_outcomes", _ack)

    cycle.main()
    assert calls["ack"] == 1
    summary = jd.read_consumption_summary(tmp_path, "outcomes")
    assert summary["disposition"] == jd.CONSUMED_OK
    assert summary["accountability_handoff_acknowledged"] == 1
    assert summary["replayed_handoff"] is True


def test_producer_to_disposition_e2e_jsonl_path(tmp_path: Path):
    """Producer persistence → disposition write → diagnose-readable state."""
    data = tmp_path / "data"
    state = tmp_path / "state"
    data.mkdir()
    state.mkdir()
    evidence = data / "intelligence_learning" / "events.jsonl"
    evidence.parent.mkdir(parents=True)
    row = {
        "event_type": "learning_evidence",
        "observed_at_utc": "2026-09-05T00:00:00Z",
        "measurement_only": True,
    }
    evidence.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert evidence.is_file()

    jd.write_disposition_env(
        state,
        job="outcomes",
        disposition=jd.CONSUMED_OK,
        release_compatibility_status=jd.RELEASE_CURRENT,
        extra={"accountability_pending_count": 0},
    )
    jd.write_consumption_summary(
        data,
        job="outcomes",
        disposition=jd.CONSUMED_OK,
        payload={"accountability_pending_count": 0, "source_bytes": evidence.stat().st_size},
    )
    disp = jd.read_disposition_env(state, "outcomes")
    summary = jd.read_consumption_summary(data, "outcomes")
    assert disp["disposition"] == jd.CONSUMED_OK
    assert summary is not None
    assert summary["source_bytes"] == evidence.stat().st_size
    # Diagnose contract keys exist in disposition env format.
    assert "disposition=" in (state / "outcomes.disposition.env").read_text(
        encoding="utf-8"
    )


def test_learning_sync_heartbeats_consumption_fields():
    sync = (LEARNING / "opip-learning-sync.sh").read_text(encoding="utf-8")
    reader = (
        ROOT / "deploy" / "remote" / "opip-learning-read-export.sh"
    ).read_text(encoding="utf-8")
    assert "capture_disposition=" in sync
    assert "outcomes_disposition=" in sync
    assert "release_compatibility=" in sync
    assert "outcomes_pending_ack=" in sync
    assert "capture_disposition=" in reader
    assert "outcomes_pending_ack=" in reader
    # Sync allowed under drift; compute blocked separately.
    assert "sync allowed; compute blocked" in sync
