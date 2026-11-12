"""Owner-disabled paper/learning must be proven, never inferred from failed jobs."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_opip_deployment_transaction_boundary_v1 import (
    CORE_OK_LOG,
    _classify,
    requires_bash,
)

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "deploy/remote/ohm-deploy").read_text(encoding="utf-8")
WORKFLOW = (ROOT.parent / ".github/workflows/deploy-production.yml").read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = DEPLOY.index(f"{name}() {{")
    end = DEPLOY.index("\n}\n", start)
    return DEPLOY[start:end + 3]


def _read_owner_mode(control: Path) -> subprocess.CompletedProcess:
    function = _function("paper_deploy_mode")
    shell = (
        f"PYTHON_BIN={sys.executable!r}; PAPER_CONTROL_FILE={str(control)!r};\n"
        + function
        + "\npaper_deploy_mode"
    )
    return subprocess.run(
        ["bash", "-c", shell], capture_output=True, text=True,
        check=False, timeout=10,
    )


@pytest.mark.acceptance
def test_owner_paper_control_off_on_and_unknown_are_distinct(tmp_path):
    """ATDD-OPIP-OWNER-OFF-RELEASE-v1/AC-001: release refuses malformed or absent operator paper control before mutation."""
    control = tmp_path / "control.json"
    for enabled, expected in ((False, "OFF"), (True, "ON")):
        control.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
        result = _read_owner_mode(control)
        assert result.returncode == 0 and result.stdout.strip() == expected
    for raw in ('{"enabled": 0}', '{"enabled": "false"}', "{unparseable"):
        control.write_text(raw, encoding="utf-8")
        assert _read_owner_mode(control).returncode != 0
    control.unlink()
    assert _read_owner_mode(control).returncode != 0
    assert DEPLOY.index('PAPER_DEPLOY_MODE="$(paper_deploy_mode)"') < DEPLOY.index("trap rollback ERR")
    assert "OPIP_OWNER_RUNTIME_PREFLIGHT=BLOCKED_PAPER_CONTROL_UNPROVEN" in DEPLOY


@pytest.mark.acceptance
def test_owner_disabled_paper_never_restarts_in_forward_or_rollback():
    """ATDD-OPIP-OWNER-OFF-RELEASE-v1/AC-002: preserve OFF across deployment and rollback while retaining ON checks."""
    assert "verify_paper_disabled() {" in DEPLOY
    assert 'if [[ "$PAPER_DEPLOY_MODE" == "ON" ]]; then\n  start_paper_stack' in DEPLOY
    assert 'elif [[ "$PAPER_DEPLOY_MODE" == "OFF" ]]; then\n  verify_paper_disabled' in DEPLOY
    assert "OPIP_PAPER_STACK=OWNER_OFF_VERIFIED" in DEPLOY
    assert "OPIP_ROLLBACK_PAPER=OWNER_OFF_VERIFIED" in DEPLOY
    assert 'if ! start_paper_stack || ! wait_paper_health; then' in DEPLOY
    assert '"$name" == "ohm-freqtrade-paper" || "$name" == "ohm-freqtrade-paper-usdt"' in DEPLOY
    assert "opip-canonical-writer|134217728|134217728" in DEPLOY
    assert '[[ "$PAPER_DEPLOY_MODE" == "OFF" ]]' in DEPLOY


@pytest.mark.acceptance
@requires_bash
def test_learning_off_release_passes_only_with_verified_paper(tmp_path):
    """ATDD-OPIP-OWNER-OFF-RELEASE-v1/AC-003: owner-off is distinct from a READY export and requires explicit paper evidence."""
    base = "\n".join((
        CORE_OK_LOG, "OPIP_LEARNING_POLICY=OWNER_OFF",
        "OPIP_LEARNING_EXPORT_STATUS=SKIPPED",
        "OPIP_LEARNING_READINESS=NOT_APPLICABLE",
        "OPIP_PAPER_CONTROL_PREFLIGHT=OFF",
        "OPIP_PAPER_STACK=OWNER_OFF_VERIFIED",
        "O'Pip deployment succeeded",
    ))
    ok = _classify(tmp_path / "owner_off", base, rc=0, legacy_allowed=0)
    assert ok["RESULT"] == "SUCCESS" and ok["GATE"] == "PASS"
    # Do not borrow a READY export, fake paper health or an unknown operator mode.
    contradictions = (
        ("OPIP_LEARNING_POLICY=OWNER_OFF", "OPIP_LEARNING_POLICY=UNPROVEN"),
        ("OPIP_LEARNING_EXPORT_STATUS=SKIPPED", "OPIP_LEARNING_EXPORT_STATUS=SUCCESS"),
        ("OPIP_LEARNING_READINESS=NOT_APPLICABLE", "OPIP_LEARNING_READINESS=READY"),
        ("OPIP_PAPER_STACK=OWNER_OFF_VERIFIED", "OPIP_PAPER_STACK=DRY_RUN_HEALTHY"),
        ("OPIP_PAPER_CONTROL_PREFLIGHT=OFF", "OPIP_PAPER_CONTROL_PREFLIGHT=UNKNOWN"),
    )
    for i, (old, new) in enumerate(contradictions):
        case = _classify(tmp_path / f"contradiction_{i}", base.replace(old, new),
                         rc=0, legacy_allowed=0)
        assert case["GATE"] == "FAIL", (old, new)
    assert 'if bash "$LEARNING_EXPORTER"; then' in DEPLOY
    assert 'learning_readiness="NOT_APPLICABLE"' in DEPLOY
    assert 'learning_policy="OWNER_OFF"' in DEPLOY
    assert "verify-export" in DEPLOY
    assert "OPIP_F11_READONLY_FINGERPRINT_PARITY=MATCH" in DEPLOY


@pytest.mark.acceptance
@requires_bash
def test_enabled_learning_still_requires_original_exact_sha_evidence(tmp_path):
    """ATDD-OPIP-OWNER-OFF-RELEASE-v1/AC-003: owner-enabled learning never substitutes skipped export for a proven replica."""
    base = "\n".join((
        CORE_OK_LOG, "OPIP_LEARNING_POLICY=ENABLED",
        "OPIP_PAPER_CONTROL_PREFLIGHT=ON",
        "OPIP_PAPER_STACK=DRY_RUN_HEALTHY",
        "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
        "OPIP_LEARNING_READINESS=READY",
        "O'Pip deployment succeeded",
    ))
    assert _classify(tmp_path / "ready", base, rc=0, legacy_allowed=0)["GATE"] == "PASS"
    for i, (key, value) in enumerate((
        ("OPIP_LEARNING_EXPORT_STATUS=SUCCESS", "OPIP_LEARNING_EXPORT_STATUS=SKIPPED"),
        ("OPIP_LEARNING_READINESS=READY", "OPIP_LEARNING_READINESS=BLOCKED"),
        ("OPIP_PAPER_STACK=DRY_RUN_HEALTHY", "OPIP_PAPER_STACK=UNPROVEN"),
    )):
        invalid = base.replace(key, value)
        assert _classify(tmp_path / f"invalid_{i}", invalid, rc=0, legacy_allowed=0)["GATE"] == "FAIL"


@pytest.mark.acceptance
def test_release_controller_shell_syntax_and_non_deploying_ui_path():
    """ATDD-OPIP-OWNER-OFF-RELEASE-v1/AC-002: changed controller parses and UI-only short circuit remains before paper logic."""
    command = subprocess.run(
        ["bash", "-n", str(ROOT / "deploy/remote/ohm-deploy")],
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert command.returncode == 0, command.stderr
    assert DEPLOY.index('if [[ -n "$UI_MODE" ]]; then') < DEPLOY.index("paper_deploy_mode() {")
    assert 'opip_f11_release_credential_gate EVIDENCE_SHADOW' in DEPLOY
    assert 'OPIP_RELEASE_RUNTIME_VERIFIER_ATTEMPTED' not in WORKFLOW
