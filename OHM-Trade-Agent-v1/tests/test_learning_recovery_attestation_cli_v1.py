import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.learning_recovery_attestation import attest_manifest

APP_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = "a" * 40
TOOLING = "b" * 40
PATH = "app/services/opportunity_accountability.py"
HASH = "c" * 64


def _manifest() -> dict:
    compatibility = {
        "schema": "opip-learning-recovery-compatibility/v1",
        "status": "REFUSED",
        "production_sha": PRODUCTION,
        "tooling_sha": TOOLING,
        "changed_critical_paths": [PATH],
        "unreviewed_changes": [PATH],
        "tooling_sha256": {PATH: HASH},
    }
    digest = __import__("hashlib").sha256(f"{PATH}:{HASH}".encode()).hexdigest()
    attestation = {
        "schema": "opip-learning-recovery-attestation/v1",
        "production_sha": PRODUCTION,
        "tooling_sha": TOOLING,
        "changed_paths": [PATH],
        "path_digests": {PATH: HASH},
        "diff_sha256": digest,
        "owner_login": "Opr1130",
        "approved_at_utc": datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat(),
        "record_identity": (
            "https://github.com/Opr1130/OHM-Trade-Agent-v1/"
            "issues/64#issuecomment-1"
        ),
        "rationale": "Reviewed exact critical path and its evidence.",
        "ci": {
            "commit_sha": TOOLING,
            "pytest": "success",
            "quality_security": "success",
            "semgrep": "success",
        },
    }
    return attest_manifest(compatibility, attestation, owner_login="Opr1130")


def _run(manifest_path: Path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(APP_ROOT)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "app.services.learning_recovery_attestation",
            "verify-manifest",
            "--manifest",
            str(manifest_path),
        ],
        cwd=APP_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.acceptance
def test_verify_manifest_cli_accepts_valid_attestation(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: real verify-manifest CLI."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")
    result = _run(path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["failures"] == []


@pytest.mark.acceptance
def test_verify_manifest_cli_rejects_missing_attestation(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: missing attestation fails."""
    manifest = _manifest()
    manifest.pop("attestation")
    manifest.pop("attestation_sha256", None)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _run(path)
    assert result.returncode == 2
    assert "attestation_present" in result.stdout


@pytest.mark.acceptance
def test_verify_manifest_cli_rejects_tampered_attestation(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-017: tampered attestation fails."""
    manifest = _manifest()
    manifest["attestation"]["path_digests"][PATH] = "d" * 64
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _run(path)
    assert result.returncode == 2
    assert "attestation_path_digests" in result.stdout
