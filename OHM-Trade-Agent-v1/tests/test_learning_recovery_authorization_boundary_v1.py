"""ATDD: authorization time-of-check/time-of-use boundary and workflow guards."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from app.services.learning_recovery_attestation import (
    AttestationError,
    select_from_comments,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT.parent / ".github" / "workflows" / "deploy-learning-recovery.yml"
NOW = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)
PRODUCTION = "a" * 40
TOOLING = "b" * 40
OWNER = "Opr1130"
PATHS = [
    "app/jobs/build_phase3c_forward_outcomes.py",
    "app/opip/discovery/maturation.py",
    "app/services/opportunity_accountability.py",
]
HASHES = {path: hashlib.sha256(path.encode()).hexdigest() for path in PATHS}
DIGEST = hashlib.sha256(
    "\n".join(f"{path}:{HASHES[path]}" for path in sorted(PATHS)).encode()
).hexdigest()


def _compat() -> dict:
    return {
        "schema": "opip-learning-recovery-compatibility/v1",
        "status": "REFUSED",
        "production_sha": PRODUCTION,
        "tooling_sha": TOOLING,
        "changed_critical_paths": PATHS,
        "unreviewed_changes": PATHS,
        "tooling_sha256": HASHES,
    }


def _approval_body() -> str:
    payload = {
        "schema": "opip-learning-recovery-attestation/v1",
        "production_sha": PRODUCTION,
        "tooling_sha": TOOLING,
        "changed_paths": PATHS,
        "path_digests": HASHES,
        "diff_sha256": DIGEST,
        "owner_login": OWNER,
        "approved_at_utc": "2026-10-22T00:59:00+00:00",
        "record_identity": "https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/64#issuecomment-1",
        "rationale": "Reviewed lineage-bound consumer guard; no trading authority change.",
        "ci": {
            "commit_sha": TOOLING,
            "pytest": "success",
            "quality_security": "success",
            "semgrep": "success",
        },
    }
    return "<!-- opip-recovery-attestation:v1 " + json.dumps(payload) + " -->"


def _comment(login, body, created_at, number):
    return {
        "user": {"login": login},
        "body": body,
        "created_at": created_at,
        "html_url": f"https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/64#issuecomment-{number}",
    }


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _workflow()["jobs"]["deploy"]["steps"]


def _index(name: str) -> int:
    for position, step in enumerate(_steps()):
        if step.get("name") == name:
            return position
    raise AssertionError(f"missing workflow step: {name}")


@pytest.mark.acceptance
def test_t13_authorization_revoked_between_check_and_mutation_blocks():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T13: the same resolver run again after a revocation must refuse."""
    comments = [_comment(OWNER, _approval_body(), "2026-10-22T00:59:00Z", 1)]
    first = select_from_comments(
        comments, compatibility=_compat(), owner_login=OWNER, now=NOW
    )
    assert first["status"] == "PASS"

    comments = comments + [
        _comment(OWNER, "REVOKED: do not proceed", "2026-10-22T00:59:40Z", 2)
    ]
    with pytest.raises(AttestationError, match="revoked"):
        select_from_comments(
            comments, compatibility=_compat(), owner_login=OWNER, now=NOW
        )


@pytest.mark.acceptance
def test_t13b_workflow_revalidates_authorization_before_mutation():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T13b: a fresh fetch+select step must precede the mutating step."""
    revalidate = _index("Revalidate owner authorization immediately before mutation")
    mutate = _index("Run recovery worker update")
    assert revalidate < mutate
    script = _steps()[revalidate]["run"]
    assert "learning_recovery_attestation fetch-comments" in script
    assert "learning_recovery_attestation select" in script
    assert "learning_recovery_compatibility verify" in script
    assert "commits/main" in script
    assert _steps()[revalidate]["if"] == "steps.compat.outputs.attested == 'true'"


@pytest.mark.acceptance
def test_t27_main_movement_is_guarded_before_mutation():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-018: T27: main is re-resolved after certification and before mutation."""
    revalidate = _index("Revalidate main and exact-SHA CI before mutation")
    mutate = _index("Run recovery worker update")
    assert revalidate < mutate
    assert "main moved after certification" in _steps()[revalidate]["run"]


@pytest.mark.acceptance
def test_t28_concurrent_recovery_cannot_overlap():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-009: T28: the learning workflow is serialized by a shared concurrency group."""
    workflow = _workflow()
    assert workflow["concurrency"]["group"] == "opip-learning"
    assert workflow["concurrency"]["cancel-in-progress"] is False


@pytest.mark.acceptance
def test_t29_protected_environment_remains_enforced():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-009: T29: the learning-production environment gate is still declared."""
    assert _workflow()["jobs"]["deploy"]["environment"] == "learning-production"


@pytest.mark.acceptance
def test_t30_recovery_never_enables_learning_timers():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-009: T30: the recovery workflow must not enable timers."""
    text = WORKFLOW.read_text(encoding="utf-8")
    enabling = [
        line
        for line in text.splitlines()
        if "systemctl" in line and "enable" in line and not line.lstrip().startswith("#")
    ]
    assert enabling == []
    assert "Timers remain disabled" in text


@pytest.mark.acceptance
def test_t31_no_core_deployment_path_is_reachable():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-009: T31: the recovery workflow can never invoke the core deploy."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "deploy-production" not in text
    assert "ohm-deploy" not in text
    assert "run-gated-learning-deploy.sh" not in text
    assert "run-recovery-learning-deploy.sh" in text


@pytest.mark.acceptance
def test_t32_no_authority_flags_are_touched():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-009: T32: recovery may not flip Paper V2, Committee, funded or AI authority."""
    text = WORKFLOW.read_text(encoding="utf-8")
    for forbidden in (
        "OPIP_PAPER_V2_MODE",
        "OPIP_COMMITTEE",
        "FUNDED",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        assert forbidden not in text

