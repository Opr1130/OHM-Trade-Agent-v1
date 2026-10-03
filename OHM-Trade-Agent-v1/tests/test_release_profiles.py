from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.services.release_profiles import (
    evaluate_architecture_gate,
    get_release_profiles,
    resolve_release_profile,
    validate_profile_contract,
)

APP_ROOT = Path(__file__).resolve().parents[1]


def test_release_profiles_include_safe_and_evidence_shadow():
    profiles = get_release_profiles()
    assert set({"SAFE_BASELINE", "EVIDENCE_SHADOW", "TARGET_PAPER"}).issubset(profiles)
    assert profiles["EVIDENCE_SHADOW"]["allowed_modes"]["OPIP_FEATURE_BUS_MODE"] == "shadow"
    assert profiles["EVIDENCE_SHADOW"]["allowed_modes"]["OPIP_CANONICAL_WRITER_MODE"] == "shadow"
    assert profiles["EVIDENCE_SHADOW"]["allowed_modes"]["OPIP_TARGET_SPINE_MODE"] == "shadow"
    assert profiles["EVIDENCE_SHADOW"]["allowed_modes"]["OPIP_PAPER_V2_MODE"] == "off"
    assert profiles["EVIDENCE_SHADOW"]["allowed_modes"]["OPIP_COMMITTEE_MODE"] == "off"


def test_release_profile_results_are_deeply_isolated():
    profiles = get_release_profiles()
    profiles["EVIDENCE_SHADOW"]["allowed_modes"]["OPIP_FEATURE_BUS_MODE"] = "active"
    assert resolve_release_profile("EVIDENCE_SHADOW")["allowed_modes"][
        "OPIP_FEATURE_BUS_MODE"
    ] == "shadow"


def test_invalid_profile_names_fail():
    try:
        resolve_release_profile("NOT_A_PROFILE")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_profile_contract_rejects_unexpected_mode_values():
    ok, issues = validate_profile_contract(
        "EVIDENCE_SHADOW",
        requested_modes={
            "OPIP_FEATURE_BUS_MODE": "active",
            "OPIP_CANONICAL_WRITER_MODE": "shadow",
            "OPIP_TARGET_SPINE_MODE": "shadow",
            "OPIP_PAPER_V2_MODE": "off",
        },
    )
    assert not ok
    assert any("OPIP_FEATURE_BUS_MODE" in issue for issue in issues)


def test_architecture_gate_accepts_current_runtime_posture():
    verdict = evaluate_architecture_gate("EVIDENCE_SHADOW")
    assert verdict["status"] == "PASS"
    assert verdict["profile"] == "EVIDENCE_SHADOW"
    assert verdict["paper_v2"] == "OFF"


def test_architecture_gate_rejects_authority_widening():
    verdict = evaluate_architecture_gate(
        "EVIDENCE_SHADOW",
        environment={
            "OPIP_FEATURE_BUS_MODE": "active",
            "OPIP_CANONICAL_WRITER_MODE": "shadow",
            "OPIP_TARGET_SPINE_MODE": "shadow",
            "OPIP_PAPER_V2_MODE": "off",
        },
    )
    assert verdict["status"] == "FAIL"
    assert verdict["checks"]["CURRENT_RUNTIME_POSTURE_CONSISTENT"] is False


def test_architecture_gate_accepts_only_exact_profile_compose_modes():
    baseline = evaluate_architecture_gate(
        "SAFE_BASELINE",
        environment={
            "OPIP_RELEASE_PROFILE": "SAFE_BASELINE",
            "OPIP_FEATURE_BUS_MODE": "off",
            "OPIP_CANONICAL_WRITER_MODE": "off",
            "OPIP_TARGET_SPINE_MODE": "off",
            "OPIP_PAPER_V2_MODE": "off",
            "OPIP_COMMITTEE_MODE": "off",
        },
    )
    assert baseline["status"] == "PASS"

    mismatch = evaluate_architecture_gate(
        "SAFE_BASELINE",
        environment={
            "OPIP_RELEASE_PROFILE": "EVIDENCE_SHADOW",
            "OPIP_FEATURE_BUS_MODE": "off",
            "OPIP_CANONICAL_WRITER_MODE": "off",
            "OPIP_TARGET_SPINE_MODE": "off",
            "OPIP_PAPER_V2_MODE": "off",
            "OPIP_COMMITTEE_MODE": "off",
        },
    )
    assert mismatch["status"] == "FAIL"
    assert mismatch["checks"]["COMPOSE_PROFILE_EXPLICIT"] is False


def test_architecture_gate_fails_closed_on_missing_compose_profile():
    verdict = evaluate_architecture_gate(
        "EVIDENCE_SHADOW",
        environment={
            "OPIP_FEATURE_BUS_MODE": "shadow",
            "OPIP_CANONICAL_WRITER_MODE": "shadow",
            "OPIP_TARGET_SPINE_MODE": "shadow",
            "OPIP_PAPER_V2_MODE": "off",
        },
    )
    assert verdict["status"] == "FAIL"
    assert verdict["checks"]["COMPOSE_PROFILE_EXPLICIT"] is False


def test_cli_emits_fail_closed_receipt_for_unknown_profile():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.services.release_profiles",
            "--profile",
            "EVIDENCE_SHADOW\nARCHITECTURE_GATE=PASS",
        ],
        cwd=APP_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout.startswith("ARCHITECTURE_GATE=FAIL\n")
    assert "ARCHITECTURE_GATE=PASS" not in result.stdout
    assert "PROFILE=INVALID" in result.stdout


def test_target_paper_remains_blocked_by_default():
    ok, issues = validate_profile_contract("TARGET_PAPER")
    assert not ok
    assert any("TARGET_PAPER remains blocked" in issue for issue in issues)
