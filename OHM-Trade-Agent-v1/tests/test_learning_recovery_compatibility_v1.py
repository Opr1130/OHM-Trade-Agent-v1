import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.learning_recovery_compatibility import (
    evaluate,
    prepare,
    verify,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git required")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    learning = repo / "app" / "opip" / "learning" / "consumer.py"
    learning.parent.mkdir(parents=True)
    learning.write_text("VERSION = 1\n")
    exporter = repo / "deploy" / "remote" / "export-opip-learning-evidence.sh"
    exporter.parent.mkdir(parents=True)
    exporter.write_text("#!/bin/bash\necho v1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "production")
    production = _git(repo, "rev-parse", "HEAD")

    learning.write_text("VERSION = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "tooling")
    tooling = _git(repo, "rev-parse", "HEAD")
    return repo, production, tooling


@pytest.mark.acceptance
def test_no_critical_changes_pass():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-014: contract criterion."""
    passed, unreviewed = evaluate(
        production_sha="a" * 40,
        tooling_sha="b" * 40,
        changed_critical_paths=[],
        approved_paths=set(),
    )
    assert passed is True
    assert unreviewed == []


@pytest.mark.acceptance
def test_unreviewed_critical_change_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-014: contract criterion."""
    passed, unreviewed = evaluate(
        production_sha="a" * 40,
        tooling_sha="b" * 40,
        changed_critical_paths=["app/opip/learning/consumer.py"],
        approved_paths=set(),
    )
    assert passed is False
    assert unreviewed == ["app/opip/learning/consumer.py"]


@pytest.mark.acceptance
def test_exact_pair_exception_is_accepted_but_wrong_pair_is_not(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-014: contract criterion."""
    repo, production, tooling = _repo(tmp_path)
    exceptions = tmp_path / "exceptions.json"
    exceptions.write_text(
        json.dumps(
            {
                "pairs": {
                    f"{production}..{tooling}": {
                        "paths": ["app/opip/learning/consumer.py"],
                        "rationale": "reviewed additive consumer change",
                    }
                }
            }
        )
    )
    out = tmp_path / "compatibility.json"
    payload = prepare(
        production_sha=production,
        tooling_sha=tooling,
        repo_root=repo,
        exceptions_file=exceptions,
    )
    assert payload["status"] == "PASS"
    assert payload["changed_critical_paths"] == ["app/opip/learning/consumer.py"]
    out.write_text(json.dumps(payload))

    checks = verify(
        manifest=json.loads(out.read_text()),
        source_app=repo,
        production_sha=production,
        tooling_sha=tooling,
    )
    assert all(check["passed"] for check in checks)

    wrong = prepare(
        production_sha=production,
        tooling_sha=tooling,
        repo_root=repo,
        exceptions_file=tmp_path / "missing.json",
    )
    assert wrong["status"] == "REFUSED"
    assert "app/opip/learning/consumer.py" in wrong["unreviewed_changes"]


@pytest.mark.acceptance
def test_tooling_hash_mismatch_is_refused(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-014: contract criterion."""
    repo, production, tooling = _repo(tmp_path)
    exceptions = tmp_path / "exceptions.json"
    exceptions.write_text(
        json.dumps(
            {
                "pairs": {
                    f"{production}..{tooling}": {
                        "paths": ["app/opip/learning/consumer.py"],
                        "rationale": "reviewed",
                    }
                }
            }
        )
    )
    payload = prepare(
        production_sha=production,
        tooling_sha=tooling,
        repo_root=repo,
        exceptions_file=exceptions,
    )
    checks = verify(
        manifest=payload,
        source_app=repo,
        production_sha=production,
        tooling_sha=tooling,
    )
    assert all(check["passed"] for check in checks)

    (repo / "app" / "opip" / "learning" / "consumer.py").write_text("TAMPERED\n")
    checks = verify(
        manifest=payload,
        source_app=repo,
        production_sha=production,
        tooling_sha=tooling,
    )
    assert any(
        check["name"] == "compatibility_hashes_match" and not check["passed"]
        for check in checks
    )


@pytest.mark.acceptance
def test_unreviewed_change_list_in_manifest_is_refused(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-014: contract criterion."""
    repo, production, tooling = _repo(tmp_path)
    payload = prepare(
        production_sha=production,
        tooling_sha=tooling,
        repo_root=repo,
        exceptions_file=tmp_path / "missing.json",
    )
    payload["status"] = "PASS"  # simulate a forged/incorrect manifest status
    checks = verify(
        manifest=payload,
        source_app=repo,
        production_sha=production,
        tooling_sha=tooling,
    )
    assert any(
        check["name"] == "no_unreviewed_critical_changes" and not check["passed"]
        for check in checks
    )


@pytest.mark.acceptance
def test_recovery_workflow_revalidates_main_ci_and_ships_compatibility():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-014: contract criterion."""
    root = Path(__file__).resolve().parents[2]
    workflow = (
        root / ".github" / "workflows" / "deploy-learning-recovery.yml"
    ).read_text(encoding="utf-8")
    assert "Revalidate main and exact-SHA CI before mutation" in workflow
    assert "RECOVERY_COMPATIBILITY.json" in workflow
    assert "learning_recovery_compatibility prepare" in workflow


@pytest.mark.acceptance
def test_recovery_script_requires_compatibility_transaction_and_archive():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-014: contract criterion."""
    root = Path(__file__).resolve().parents[1]
    script = (
        root / "deploy" / "learning" / "run-recovery-learning-deploy.sh"
    ).read_text(encoding="utf-8")
    assert "learning_recovery_compatibility" in script
    assert "RECOVERY_COMPATIBILITY.json" in script
    assert "learning_recovery_transaction" in script
    assert "learning_lineage_archive" in script
    assert "opip-learning-recovery.lock" in script
    assert "collect_failure" in script


@pytest.mark.acceptance
def test_critical_scope_covers_actual_learning_job_entrypoints():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-014: contract criterion."""
    from app.services.learning_recovery_compatibility import critical_paths

    paths = set(critical_paths())
    assert "app/jobs/run_opportunity_intelligence_cycle.py" in paths
    assert "app/jobs/build_discovery_forward_outcomes.py" in paths
    assert "app/services/opportunity_accountability.py" in paths
    assert "app/jobs/reconcile_discovery_accountability_evidence.py" in paths
    assert "app/services/opip_ml_evidence_capture.py" in paths
    assert "app/opip/discovery" in paths


@pytest.mark.acceptance
def test_actual_outcomes_consumer_change_fails_closed_without_exception(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-014: contract criterion."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    consumer = repo / "app" / "services" / "opportunity_accountability.py"
    consumer.parent.mkdir(parents=True)
    consumer.write_text("VERSION = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "production")
    production = _git(repo, "rev-parse", "HEAD")
    consumer.write_text("VERSION = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "tooling")
    tooling = _git(repo, "rev-parse", "HEAD")

    blocked = prepare(
        production_sha=production,
        tooling_sha=tooling,
        repo_root=repo,
        exceptions_file=tmp_path / "missing.json",
    )
    assert blocked["status"] == "REFUSED"
    assert blocked["unreviewed_changes"] == [
        "app/services/opportunity_accountability.py"
    ]

    exceptions = tmp_path / "exceptions.json"
    exceptions.write_text(
        json.dumps(
            {
                "pairs": {
                    f"{production}..{tooling}": {
                        "paths": ["app/services/opportunity_accountability.py"],
                        "rationale": "owner-approved outcomes consumer change",
                    }
                }
            }
        )
    )
    approved = prepare(
        production_sha=production,
        tooling_sha=tooling,
        repo_root=repo,
        exceptions_file=exceptions,
    )
    assert approved["status"] == "PASS"
    checks = verify(
        manifest=approved,
        source_app=repo,
        production_sha=production,
        tooling_sha=tooling,
    )
    assert all(check["passed"] for check in checks)
