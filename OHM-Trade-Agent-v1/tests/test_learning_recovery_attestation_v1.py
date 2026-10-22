import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.learning_recovery_attestation import (
    attest_manifest,
    select_from_comments,
    validate,
    validate_manifest_attestation,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)
PRODUCTION = "a" * 40
TOOLING = "b" * 40
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


def _attestation() -> dict:
    return {
        "schema": "opip-learning-recovery-attestation/v1",
        "production_sha": PRODUCTION,
        "tooling_sha": TOOLING,
        "changed_paths": PATHS,
        "path_digests": HASHES,
        "diff_sha256": DIGEST,
        "owner_login": "Opr1130",
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


@pytest.mark.acceptance
def test_exact_owner_attestation_is_accepted():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: external exact-pair approval."""
    checks = validate(
        attestation=_attestation(),
        compatibility=_compat(),
        owner_login="Opr1130",
        now=NOW,
    )
    assert [check for check in checks if not check["passed"]] == []
    manifest = attest_manifest(
        _compat(), _attestation(), owner_login="Opr1130", now=NOW
    )
    assert manifest["status"] == "PASS"
    assert manifest["attestation_sha256"]
    assert validate_manifest_attestation(manifest, now=NOW)


@pytest.mark.acceptance
def test_attestation_rejects_wrong_pair_path_digest_and_owner():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: no wildcard or stale approval."""
    wrong = _attestation()
    wrong["tooling_sha"] = "c" * 40
    assert any(
        not check["passed"]
        for check in validate(
            attestation=wrong, compatibility=_compat(), owner_login="Opr1130", now=NOW
        )
    )
    wrong = _attestation()
    wrong["changed_paths"] = PATHS[:1]
    assert any(
        not check["passed"]
        for check in validate(
            attestation=wrong, compatibility=_compat(), owner_login="Opr1130", now=NOW
        )
    )
    wrong = _attestation()
    wrong["diff_sha256"] = "d" * 64
    assert any(
        not check["passed"]
        for check in validate(
            attestation=wrong, compatibility=_compat(), owner_login="Opr1130", now=NOW
        )
    )
    assert any(
        not check["passed"]
        for check in validate(
            attestation=_attestation(),
            compatibility=_compat(),
            owner_login="someone-else",
            now=NOW,
        )
    )

@pytest.mark.acceptance
def test_attestation_rejects_stale_and_missing_ci():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: freshness and exact CI."""
    stale = _attestation()
    stale["approved_at_utc"] = "2026-01-01T00:00:00+00:00"
    assert any(
        not check["passed"]
        for check in validate(
            attestation=stale, compatibility=_compat(), owner_login="Opr1130", now=NOW
        )
    )
    no_ci = _attestation()
    no_ci["ci"]["pytest"] = "failure"
    assert any(
        not check["passed"]
        for check in validate(
            attestation=no_ci, compatibility=_compat(), owner_login="Opr1130", now=NOW
        )
    )


@pytest.mark.acceptance
def test_attested_manifest_without_attestation_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: PASS alone is insufficient."""
    manifest = _compat()
    manifest["status"] = "PASS"
    checks = validate_manifest_attestation(manifest, now=NOW)
    assert any(not check["passed"] for check in checks)


@pytest.mark.acceptance
def test_comment_selection_uses_owner_and_latest_valid_attestation():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: authenticated record source."""
    body_old = (
        "<!-- opip-recovery-attestation:v1 "
        + json.dumps({**_attestation(), "diff_sha256": "e" * 64})
        + " -->"
    )
    body_new = "<!-- opip-recovery-attestation:v1 " + json.dumps(_attestation()) + " -->"
    comments = [
        {
            "user": {"login": "Opr1130"},
            "created_at": "2026-10-22T00:58:00Z",
            "body": body_old,
            "html_url": "https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/64#issuecomment-1",
        },
        {
            "user": {"login": "Opr1130"},
            "created_at": "2026-10-22T00:59:00Z",
            "body": body_new,
            "html_url": "https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/64#issuecomment-2",
        },
        {
            "user": {"login": "attacker"},
            "created_at": "2026-10-22T00:59:30Z",
            "body": body_new,
            "html_url": "https://github.com/attacker/x",
        },
    ]
    manifest = select_from_comments(
        comments, compatibility=_compat(), owner_login="Opr1130", now=NOW
    )
    assert manifest["status"] == "PASS"
    assert manifest["attestation"]["record_identity"].endswith("issuecomment-2")


@pytest.mark.acceptance
def test_recovery_workflow_authenticates_issue64_attestation():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: no in-commit self-reference."""
    workflow = (
        ROOT.parent / ".github/workflows/deploy-learning-recovery.yml"
    ).read_text(encoding="utf-8")
    assert "issues/64/comments" in workflow
    assert "learning_recovery_attestation select" in workflow
    assert "learning_recovery_compatibility verify" in workflow
    assert workflow.index("learning_recovery_attestation select") < workflow.index(
        "Upload exact tooling tree"
    )
