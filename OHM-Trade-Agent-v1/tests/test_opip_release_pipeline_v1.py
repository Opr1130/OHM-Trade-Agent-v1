"""Release Pipeline v1: allowlisted release profiles and bounded governance supersession.

Proves the release-profile contract is the explicit, fail-closed activation
authority, that the OWNER-authorized EVIDENCE_SHADOW profile activates the
evidence plane while history is preserved, and that stale `.env` and unapproved
weakening cannot elevate authority.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.services.release_profiles import (
    RELEASE_PROFILES,
    evaluate_architecture_gate,
    get_release_profiles,
    render_profile_environment,
    resolve_release_profile,
    validate_profile_contract,
)

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
COMPOSE = APP_ROOT / "docker-compose.yml"
CONTRACTS = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CORE = "ohm-trade-agent"

_PROFILE_KEYS = (
    "OPIP_FEATURE_BUS_MODE",
    "OPIP_CANONICAL_WRITER_MODE",
    "OPIP_TARGET_SPINE_MODE",
    "OPIP_PAPER_V2_MODE",
    "OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD",
)


def _core_env() -> dict:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    env = document["services"][CORE]["environment"]
    assert isinstance(env, dict)
    return env


@pytest.mark.acceptance
def test_ac_001_profile_allowlist_and_exact_modes() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-001: the profile allowlist resolves to exact modes and TARGET_PAPER is blocked."""
    profiles = get_release_profiles()
    assert set(profiles) == {"SAFE_BASELINE", "EVIDENCE_SHADOW", "TARGET_PAPER"}

    baseline = resolve_release_profile("SAFE_BASELINE")
    assert baseline["allowed_modes"] == {
        "OPIP_FEATURE_BUS_MODE": "off",
        "OPIP_CANONICAL_WRITER_MODE": "off",
        "OPIP_TARGET_SPINE_MODE": "off",
        "OPIP_PAPER_V2_MODE": "off",
        "OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD": "0.0",
    }
    evidence = resolve_release_profile("EVIDENCE_SHADOW")
    assert evidence["allowed_modes"] == {
        "OPIP_FEATURE_BUS_MODE": "shadow",
        "OPIP_CANONICAL_WRITER_MODE": "shadow",
        "OPIP_TARGET_SPINE_MODE": "shadow",
        "OPIP_PAPER_V2_MODE": "off",
        "OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD": "1000.0",
    }
    assert resolve_release_profile("TARGET_PAPER")["status"] == "BLOCKED"

    ok, issues = validate_profile_contract("TARGET_PAPER")
    assert not ok
    assert any("TARGET_PAPER remains blocked" in issue for issue in issues)


@pytest.mark.acceptance
def test_ac_001_unknown_profile_fails_closed() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-001: an unknown, cased or padded profile name fails closed."""
    for bad in ("NOT_A_PROFILE", "evidence_shadow", "EVIDENCE_SHADOW ", " EVIDENCE_SHADOW", ""):
        with pytest.raises(ValueError):
            resolve_release_profile(bad)
    # Only the two ACTIVE profiles render an environment; the blocked one refuses.
    assert set(render_profile_environment("EVIDENCE_SHADOW")) == set(_PROFILE_KEYS)
    with pytest.raises(ValueError):
        render_profile_environment("TARGET_PAPER")


@pytest.mark.acceptance
def test_ac_002_profile_resolves_compose_and_gate_passes() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-002: the selected profile resolves to the exact core-service literals and the architecture gate passes."""
    env = _core_env()
    rendered = render_profile_environment("EVIDENCE_SHADOW")
    for key in ("OPIP_FEATURE_BUS_MODE", "OPIP_CANONICAL_WRITER_MODE", "OPIP_TARGET_SPINE_MODE"):
        # The core service literal equals the profile's exact mode.
        assert env[key] == rendered[key]
    # Paper-v2 is intentionally unset (rendered "off").
    assert "OPIP_PAPER_V2_MODE" not in env
    assert rendered["OPIP_PAPER_V2_MODE"] == "off"

    verdict = evaluate_architecture_gate("EVIDENCE_SHADOW", repo_root=APP_ROOT)
    assert verdict["status"] == "PASS"
    assert verdict["checks"]["CURRENT_RUNTIME_POSTURE_CONSISTENT"] is True
    assert verdict["checks"]["PAPER_V2_REMAINS_OFF"] is True
    assert verdict["new_entry_authority"] == "LEGACY_ONLY"


@pytest.mark.acceptance
def test_ac_002_stale_env_cannot_elevate_baseline() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-002: a stale `.env` value cannot elevate SAFE_BASELINE to EVIDENCE_SHADOW, and the pins are literals."""
    # SAFE_BASELINE forbids `shadow`; a `.env`-style request to widen it fails.
    ok, issues = validate_profile_contract(
        "SAFE_BASELINE",
        requested_modes={
            "OPIP_FEATURE_BUS_MODE": "shadow",
            "OPIP_CANONICAL_WRITER_MODE": "shadow",
        },
    )
    assert not ok
    assert any("OPIP_FEATURE_BUS_MODE" in issue for issue in issues)

    # The core service literals carry no variable expansion, so a stale `.env`
    # cannot drive them.
    lines = [line.strip() for line in COMPOSE.read_text(encoding="utf-8").splitlines()]
    for key in ("OPIP_FEATURE_BUS_MODE", "OPIP_CANONICAL_WRITER_MODE", "OPIP_TARGET_SPINE_MODE"):
        pinned = next(line for line in lines if line.startswith(f"{key}:"))
        value = pinned.split(":", 1)[1].strip()
        assert value in ('"shadow"', '"off"'), (key, value)
        assert "${" not in value


@pytest.mark.acceptance
def test_ac_002_arbitrary_and_injection_overrides_fail() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-002: arbitrary mode keys, whitespace/case injection and unexpected values fail closed."""
    # An unexpected mode key is refused.
    ok, issues = validate_profile_contract(
        "EVIDENCE_SHADOW",
        requested_modes={"OPIP_PAPER_V2_MODE": "active"},
    )
    assert not ok and any("OPIP_PAPER_V2_MODE" in issue for issue in issues)

    # Whitespace-padded or cased values are not accepted.
    for bad in (" shadow", "shadow ", "Shadow", "SHADOW"):
        ok, _ = validate_profile_contract(
            "EVIDENCE_SHADOW",
            requested_modes={"OPIP_FEATURE_BUS_MODE": bad},
        )
        assert not ok, bad

    # A non-string value is refused.
    ok, _ = validate_profile_contract(
        "EVIDENCE_SHADOW", requested_modes={"OPIP_FEATURE_BUS_MODE": True}
    )
    assert not ok

    # Feature Bus `active` (authority widening) is refused.
    ok, issues = validate_profile_contract(
        "EVIDENCE_SHADOW", requested_modes={"OPIP_FEATURE_BUS_MODE": "active"}
    )
    assert not ok and any("OPIP_FEATURE_BUS_MODE" in issue for issue in issues)


@pytest.mark.acceptance
def test_ac_003_history_preserved_and_superseded() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-003: older contracts keep their historical `off` posture and a later OWNER supersession authorizes the current posture."""
    # HISTORICAL: the older contracts still record the Feature Bus `off` at their increment.
    b0 = (CONTRACTS / "ATDD-R4-B0-spine-contract-closure.md").read_text(encoding="utf-8")
    b1 = (CONTRACTS / "ATDD-R4-B1-contract-freeze.md").read_text(encoding="utf-8")
    f3i = (CONTRACTS / "ATDD-R3-F3-ignition-implementation.md").read_text(encoding="utf-8")
    assert "off" in b0 and "OPIP_FEATURE_BUS_MODE" in b0
    assert "OPIP_FEATURE_BUS_MODE=off" in b1
    assert "remains `off`" in f3i

    # CURRENT: the owner-authorized supersession contract exists and is explicit.
    supersession = (CONTRACTS / "ATDD-RELEASE-PIPELINE-v1.md").read_text(encoding="utf-8")
    assert "ATDD-RELEASE-PIPELINE-v1" in supersession
    assert "EVIDENCE_SHADOW" in supersession
    assert "supersed" in supersession.lower()
    assert "OPIP_FEATURE_BUS_MODE=shadow" in supersession or "Feature Bus `shadow`" in supersession
    # The current repository posture is the profile's, not a permanent prohibition.
    assert _core_env()["OPIP_FEATURE_BUS_MODE"] == "shadow"


@pytest.mark.acceptance
def test_ac_004_bridge_guard_allows_authorized_supersession() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-004: the bridge no-weakening guard no longer pins a historical runtime literal and still requires the substantive isolation assertions."""
    bridge_test = (APP_ROOT / "tests" / "test_local_agent_bridge.py").read_text(encoding="utf-8")
    # It no longer converts the historical runtime literal into a permanent ban:
    # the required-guard tuple no longer demands the old `"off"` token (the phrase
    # survives only inside an explanatory comment).
    assert "'OPIP_FEATURE_BUS_MODE: \"off\"'," not in bridge_test
    # It retains the substantive guard tokens and the adversarial check.
    for token in ("FORBIDDEN_MODULE_PREFIXES", "AUTHORITY_TOKENS", "run_cycle.py"):
        assert token in bridge_test
    assert "test_bridge_guard_still_fails_unapproved_weakening" in bridge_test


@pytest.mark.acceptance
def test_ac_004_bridge_guard_still_fails_unapproved_weakening() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-004: the guard is non-vacuous - deleting a substantive isolation token would be detected."""
    guards = (
        "FORBIDDEN_MODULE_PREFIXES",
        "AUTHORITY_TOKENS",
        "run_cycle.py",
        "assert imported_modules(source).isdisjoint(",
    )
    mode_re = re.compile(r'OPIP_FEATURE_BUS_MODE: "(?:off|shadow)"')

    def guard_ok(source: str) -> bool:
        return all(token in source for token in guards) and bool(mode_re.search(source))

    real = (APP_ROOT / "tests" / "test_opip_r3_f3_ignition_detector.py").read_text(
        encoding="utf-8"
    )
    assert guard_ok(real) is True
    # An unapproved deletion of any substantive guard is detected.
    for token in guards:
        assert guard_ok(real.replace(token, "")) is False, token
    # Removing the Feature Bus mode assertion entirely is detected.
    assert guard_ok(re.sub(r'OPIP_FEATURE_BUS_MODE: "(?:off|shadow)"', "", real)) is False
    # An unapproved widening to a non-allowlisted mode (`active`) is also detected.
    assert guard_ok(real.replace('OPIP_FEATURE_BUS_MODE: "shadow"', 'OPIP_FEATURE_BUS_MODE: "active"')) is False


@pytest.mark.acceptance
def test_ac_005_gate_fails_closed_and_receipt() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-005: the gate fails closed on an inconsistent posture and renders a concise receipt."""
    from app.services.release_profiles import render_release_verdict

    passing = evaluate_architecture_gate("EVIDENCE_SHADOW", repo_root=APP_ROOT)
    assert passing["status"] == "PASS"
    for name in (
        "ONE_CANONICAL_WRITER",
        "FUNDED_AUTHORITY_ABSENT",
        "FUNDED_CREDENTIAL_PATH_ABSENT_FROM_PAPER",
        "PAPER_V2_REMAINS_OFF",
        "PROTECTION_INDEPENDENT",
        "STALE_ENV_CANNOT_ACTIVATE_DORMANT_AUTHORITY",
    ):
        assert passing["checks"][name] is True, name

    # Authority widening (Paper-v2 active) fails the gate.
    failing = evaluate_architecture_gate(
        "EVIDENCE_SHADOW",
        repo_root=APP_ROOT,
        environment={
            "OPIP_FEATURE_BUS_MODE": "shadow",
            "OPIP_CANONICAL_WRITER_MODE": "shadow",
            "OPIP_TARGET_SPINE_MODE": "shadow",
            "OPIP_PAPER_V2_MODE": "active",
        },
    )
    assert failing["status"] == "FAIL"
    assert failing["checks"]["PAPER_V2_REMAINS_OFF"] is False

    receipt = render_release_verdict(passing)
    assert "ARCHITECTURE_GATE=PASS" in receipt
    assert "PROFILE=EVIDENCE_SHADOW" in receipt
    assert "NEW_ENTRY_AUTHORITY=LEGACY_ONLY" in receipt
    assert "PAPER_V2=OFF" in receipt

    # The gate is hermetic: it derives every check from the supplied mapping, so a
    # funded credential name in the ambient process environment cannot change the
    # verdict (and no secret value is ever read).
    import os

    saved = os.environ.get("KRAKEN_API_KEY")
    os.environ["KRAKEN_API_KEY"] = "not-a-real-secret"
    try:
        assert evaluate_architecture_gate("EVIDENCE_SHADOW", repo_root=APP_ROOT)["status"] == "PASS"
    finally:
        if saved is None:
            os.environ.pop("KRAKEN_API_KEY", None)
        else:
            os.environ["KRAKEN_API_KEY"] = saved

    # A declared funded credential name in the reviewed environment fails the gate.
    funded = evaluate_architecture_gate(
        "EVIDENCE_SHADOW",
        repo_root=APP_ROOT,
        environment={
            "OPIP_FEATURE_BUS_MODE": "shadow",
            "OPIP_CANONICAL_WRITER_MODE": "shadow",
            "OPIP_TARGET_SPINE_MODE": "shadow",
            "OPIP_PAPER_V2_MODE": "off",
            "KRAKEN_API_KEY": "x",
        },
    )
    assert funded["status"] == "FAIL"
    assert funded["checks"]["FUNDED_AUTHORITY_ABSENT"] is False

    # The Committee check is not vacuous: a non-off committee mode fails closed.
    committee = evaluate_architecture_gate(
        "EVIDENCE_SHADOW",
        repo_root=APP_ROOT,
        environment={
            "OPIP_FEATURE_BUS_MODE": "shadow",
            "OPIP_CANONICAL_WRITER_MODE": "shadow",
            "OPIP_TARGET_SPINE_MODE": "shadow",
            "OPIP_PAPER_V2_MODE": "off",
            "OPIP_COMMITTEE_MODE": "on",
        },
    )
    assert committee["checks"]["COMMITTEE_RUNTIME_AUTHORITY_ABSENT"] is False
    assert committee["status"] == "FAIL"


@pytest.mark.acceptance
def test_ac_006_paper_v2_off_legacy_sole_authority() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-006: the activated evidence plane leaves Paper-v2 off, the legacy path the sole new-entry authority, and no authority widened."""
    env = _core_env()
    assert "OPIP_PAPER_V2_MODE" not in env
    assert "OPIP_COMMITTEE_MODE:" not in COMPOSE.read_text(encoding="utf-8")

    from app.services.target_spine_cycle import TARGET_SPINE_MODES

    assert TARGET_SPINE_MODES == frozenset({"off", "shadow"})
    assert "active" not in TARGET_SPINE_MODES

    # Capture is never invoked from inside the protected unified cycle.
    run_cycle = (APP_ROOT / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    assert "capture_feature_bus_shadow" not in run_cycle
    assert "capture_feasibility_evidence_shadow" not in run_cycle

    # The profile contract declares no widened authority.
    for name in ("SAFE_BASELINE", "EVIDENCE_SHADOW"):
        assert RELEASE_PROFILES[name]["expected_new_entry_authority"] == "LEGACY_ONLY"


@pytest.mark.acceptance
def test_ac_007_evidence_notional_is_repo_controlled() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-007: the F5 validation notional is a fixed repo-controlled EVIDENCE_SHADOW constant (1000.0); SAFE_BASELINE keeps capture disabled; arbitrary overrides are rejected."""
    baseline = render_profile_environment("SAFE_BASELINE")
    evidence = render_profile_environment("EVIDENCE_SHADOW")
    assert baseline["OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD"] == "0.0"
    assert evidence["OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD"] == "1000.0"

    # The core service pins the exact profile value as a repo-controlled literal.
    env = _core_env()
    assert env["OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD"] == "1000.0"
    assert 'OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD: "1000.0"' in COMPOSE.read_text(
        encoding="utf-8"
    )

    # An arbitrary/free-form notional is refused for EVIDENCE_SHADOW...
    ok, issues = validate_profile_contract(
        "EVIDENCE_SHADOW",
        requested_modes={"OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD": "5000.0"},
    )
    assert not ok
    assert any("OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD" in issue for issue in issues)
    # ...and any non-zero notional is refused for SAFE_BASELINE (capture disabled).
    ok, issues = validate_profile_contract(
        "SAFE_BASELINE",
        requested_modes={"OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD": "1000.0"},
    )
    assert not ok

    # The architecture gate passes with the fixed constant and fails on drift.
    assert evaluate_architecture_gate("EVIDENCE_SHADOW", repo_root=APP_ROOT)["status"] == "PASS"
    drifted = evaluate_architecture_gate(
        "EVIDENCE_SHADOW",
        repo_root=APP_ROOT,
        environment={
            "OPIP_FEATURE_BUS_MODE": "shadow",
            "OPIP_CANONICAL_WRITER_MODE": "shadow",
            "OPIP_TARGET_SPINE_MODE": "shadow",
            "OPIP_PAPER_V2_MODE": "off",
            "OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD": "5000.0",
        },
    )
    assert drifted["status"] == "FAIL"
    assert drifted["checks"]["CURRENT_RUNTIME_POSTURE_CONSISTENT"] is False


@pytest.mark.acceptance
def test_ac_007_capture_refuses_a_free_form_notional_override() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-007: the capture resolves the configured notional and refuses any --notional-usd override that differs from it."""
    from types import SimpleNamespace

    from app.jobs.capture_feasibility_evidence_shadow import resolve_capture_notional

    configured = SimpleNamespace(opip_feasibility_capture_notional_usd=1000.0)
    disabled = SimpleNamespace(opip_feasibility_capture_notional_usd=0.0)

    # The configured value is used when no override is given.
    assert resolve_capture_notional(configured) == (1000.0, None)
    # A matching override is accepted.
    assert resolve_capture_notional(configured, override=1000.0) == (1000.0, None)
    # A differing override is refused.
    assert resolve_capture_notional(configured, override=5000.0) == (
        None,
        "arbitrary notional override rejected",
    )
    # A disabled (SAFE_BASELINE) configuration refuses regardless of override.
    assert resolve_capture_notional(disabled) == (None, "notional not configured")
    assert resolve_capture_notional(disabled, override=1000.0) == (
        None,
        "arbitrary notional override rejected",
    )
    # A non-finite override is refused.
    assert resolve_capture_notional(configured, override=float("nan"))[0] is None
    assert resolve_capture_notional(configured, override=float("inf"))[0] is None
    # A non-numeric override is refused, not raised.
    assert resolve_capture_notional(configured, override="not-a-number")[0] is None
    # A non-finite or non-numeric *configured* value is refused (fail closed).
    assert resolve_capture_notional(
        SimpleNamespace(opip_feasibility_capture_notional_usd=float("inf"))
    ) == (None, "notional not configured")
    assert resolve_capture_notional(
        SimpleNamespace(opip_feasibility_capture_notional_usd="bogus")
    )[0] is None


@pytest.mark.acceptance
def test_ac_007_read_only_canonical_evidence_verifier() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-007: the diagnostics probe reports canonical evidence counters read-only, without touching the live store."""
    script = (
        APP_ROOT / "deploy" / "remote" / "diagnose-opip-learning.sh"
    ).read_text(encoding="utf-8")
    # The verifier reports the two prospective evidence families and the notional.
    assert "OPIP_CANONICAL_EVIDENCE_COUNTS" in script
    assert "feature_snapshot_recorded_count=" in script
    assert "feasibility_evidence_recorded_count=" in script
    assert "canonical_max_local_sequence=" in script
    assert "latest_feasibility_validation_notional_usd=" in script
    # It reads the exported replica read-only and never the live store.
    assert "mode=ro" in script
    assert "canonical_evidence_counts=UNAVAILABLE" in script
    # It never mutates: no SQLite write verbs appear near the counter block.
    block = script.split("OPIP_CANONICAL_EVIDENCE_COUNTS", 1)[1].split(
        "OPIP_CANONICAL_EVIDENCE_COUNTS_END", 1
    )[0]
    for forbidden in ("INSERT", "UPDATE ", "DELETE", "DROP ", "ATTACH"):
        assert forbidden not in block, forbidden
