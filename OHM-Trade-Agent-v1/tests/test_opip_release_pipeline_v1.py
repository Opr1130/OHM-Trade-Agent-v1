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
    "OPIP_COMMITTEE_MODE",
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
        "OPIP_COMMITTEE_MODE": "off",
    }
    evidence = resolve_release_profile("EVIDENCE_SHADOW")
    assert evidence["allowed_modes"] == {
        "OPIP_FEATURE_BUS_MODE": "shadow",
        "OPIP_CANONICAL_WRITER_MODE": "shadow",
        "OPIP_TARGET_SPINE_MODE": "shadow",
        "OPIP_PAPER_V2_MODE": "off",
        "OPIP_COMMITTEE_MODE": "off",
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
    # Paper-v2 is pinned off to block stale `.env` activation.
    assert env["OPIP_PAPER_V2_MODE"] == "off"
    assert rendered["OPIP_PAPER_V2_MODE"] == "off"
    assert env["OPIP_RELEASE_PROFILE"] == "EVIDENCE_SHADOW"

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
            "OPIP_PAPER_V2_MODE": "active",
        },
    )
    assert not ok
    assert any("OPIP_FEATURE_BUS_MODE" in issue for issue in issues)
    assert any("OPIP_PAPER_V2_MODE" in issue for issue in issues)

    # The core service literals carry no variable expansion, so a stale `.env`
    # cannot drive them.
    lines = [line.strip() for line in COMPOSE.read_text(encoding="utf-8").splitlines()]
    for key in (
        "OPIP_FEATURE_BUS_MODE",
        "OPIP_CANONICAL_WRITER_MODE",
        "OPIP_TARGET_SPINE_MODE",
        "OPIP_PAPER_V2_MODE",
    ):
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
    assert "COMMITTEE_MODE=off" in receipt
    assert "FEATURE_BUS=shadow" in receipt
    assert "CANONICAL_WRITER=shadow" in receipt
    assert "TARGET_SPINE=shadow" in receipt

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
            "OPIP_RELEASE_PROFILE": "EVIDENCE_SHADOW",
            "OPIP_COMMITTEE_MODE": "on",
        },
    )
    assert committee["checks"]["COMMITTEE_RUNTIME_AUTHORITY_ABSENT"] is False
    assert committee["status"] == "FAIL"


@pytest.mark.acceptance
def test_ac_006_paper_v2_off_legacy_sole_authority() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-006: the activated evidence plane leaves Paper-v2 off, the legacy path the sole new-entry authority, and no authority widened."""
    env = _core_env()
    assert env["OPIP_PAPER_V2_MODE"] == "off"
    assert env["OPIP_COMMITTEE_MODE"] == "off"

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
def test_ac_007_ci_gate_and_main_candidate_are_non_deploying() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-007: PR/main CI evaluates the pinned architecture and emits a candidate without crossing deployment authority."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "pytest.yml").read_text(
        encoding="utf-8"
    )
    assert "architecture-gate:" in workflow
    assert "python -m app.services.release_profiles --profile EVIDENCE_SHADOW" in workflow
    for receipt in (
        "ARCHITECTURE_GATE=PASS",
        "PROFILE=EVIDENCE_SHADOW",
        "NEW_ENTRY_AUTHORITY=LEGACY_ONLY",
        "FUNDED_AUTHORITY=ABSENT",
        "PAPER_V2=OFF",
        "COMMITTEE_MODE=off",
    ):
        assert receipt in workflow
    assert "release-candidate:" in workflow
    assert "needs: [test, atdd-scope, architecture-gate, security-gate]" in workflow
    assert "DEPLOY_RESULT=NOT_REQUESTED" in workflow
    assert "ssh " not in workflow


@pytest.mark.acceptance
def test_ac_008_profile_approval_is_owner_only_exact_sha_and_gated() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-008: issue #64 accepts one strict allowlisted profile command only from the repository owner and gates deployment on exact-main CI and profile architecture."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-production.yml").read_text(
        encoding="utf-8"
    )
    assert "github.event.issue.number == 64" in workflow
    assert "github.event.comment.user.login == github.repository_owner" in workflow
    assert "github.event.comment.author_association == 'OWNER'" in workflow
    assert "github.actor == github.repository_owner" in workflow
    assert "manual dispatch is repository-owner-only" in workflow
    assert "^/deploy-profile[[:space:]]+(EVIDENCE_SHADOW|TARGET_PAPER)" in workflow
    assert "SAFE_BASELINE is rollback-only" in workflow
    assert "gh api \"repos/$GITHUB_REPOSITORY/commits/main\"" in workflow
    assert "workflow pytest.yml" in workflow
    assert "Checkout and validate the exact-SHA release profile" in workflow
    assert "python -m app.services.release_profiles --profile \"$PROFILE\"" in workflow
    assert '"deploy $TARGET_SHA"' in workflow
    assert "environment: production" in workflow
    deploy = (APP_ROOT / "deploy" / "remote" / "ohm-deploy").read_text(
        encoding="utf-8"
    )
    assert "UNTRACKED_APP_FILES=" in deploy
    assert "IGNORED_APP_FILES=" in deploy
    assert "grep -Ev '\\.py[cod]$'" in deploy
    dockerignore = (APP_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "**/__pycache__/" in dockerignore
    assert "**/*.py[cod]" in dockerignore
    assert "**/.env" in dockerignore
    # The legacy spelling remains only as an EVIDENCE_SHADOW alias and passes
    # through the same exact-SHA, owner, CI, and architecture gates.
    assert 'PROFILE="EVIDENCE_SHADOW"' in workflow


@pytest.mark.acceptance
def test_ac_009_runtime_verifier_precedes_commit_and_rolls_back_to_baseline() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-009: bounded prospective runtime evidence is a rollback-sensitive gate."""
    deploy = (APP_ROOT / "deploy" / "remote" / "ohm-deploy").read_text(
        encoding="utf-8"
    )
    verifier_call = deploy.index("python -m app.services.release_runtime_verifier")
    commit_point = deploy.rfind('"$TARGET_SHA" > "$LAST_GOOD_FILE"')
    trap_disabled = deploy.rfind("trap - ERR")
    assert verifier_call < commit_point < trap_disabled
    assert "capture_release_evidence_baseline()" in deploy
    assert "mode=ro" in deploy
    assert "validate_candidate_container_identity" in deploy
    assert 'EVIDENCE_SHADOW)' in deploy
    assert 'echo "OPIP_RELEASE_PROFILE=$profile"' in deploy
    assert "validate_release_scheduler_contract" in deploy
    assert "write_safe_baseline_override" in deploy
    assert 'OPIP_RELEASE_PROFILE: "SAFE_BASELINE"' in deploy
    assert 'org.opencontainers.image.revision: "$PREVIOUS_SHA"' in deploy
    assert 'echo "OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS"' in deploy
    assert "--timeout-seconds \"$REMAINING_VERIFY_SECONDS\"" in deploy
    assert "DEPLOY_VERIFY_DEADLINE=$((SECONDS + 360))" in deploy
    assert "wait_unified_cycle_success" in deploy
    cycle = (APP_ROOT / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    assert "OPIP_UNIFIED_CYCLE_STATUS=" in cycle

    dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG OPIP_RELEASE_SHA=UNPINNED" in dockerfile
    assert 'LABEL org.opencontainers.image.revision="${OPIP_RELEASE_SHA}"' in dockerfile


@pytest.mark.acceptance
def test_ac_009_deployment_receipt_requires_runtime_verifier_and_baseline_rollback() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-009: the receipt requires exact-SHA runtime proof."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-production.yml").read_text(
        encoding="utf-8"
    )
    assert '"$RUNTIME_VERIFICATION" == "PASS"' in workflow
    assert "OPIP_RELEASE_RUNTIME_SHA=$TARGET_SHA" in workflow
    assert "OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS" in workflow
    assert 'if [[ "$legacy_allowed" == "1" && "$CORE_MARKER" == "ABSENT" ]]; then' in workflow
    assert "RUNTIME_VERIFICATION=" in workflow
    assert "SAFE_BASELINE_ROLLBACK=" in workflow
    assert 'APPROVED_PROFILE" == "EVIDENCE_SHADOW"' in workflow
    assert 'DEPLOYED_PROFILE" == "$APPROVED_PROFILE"' in workflow
    assert '&& [[ "$UNIFIED_CYCLE" == "HEALTHY" ]]' in workflow


@pytest.mark.acceptance
def test_ac_010_rollback_success_requires_verified_safe_baseline_modes() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-010: rollback success requires verified baseline modes and health."""
    deploy = (APP_ROOT / "deploy" / "remote" / "ohm-deploy").read_text(
        encoding="utf-8"
    )
    validation = deploy.index("if ! validate_safe_baseline_modes")
    rollback_marker = deploy.index('echo "OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS"')
    health_proof = deploy.index("rollback health and paper checks passed")
    assert validation < rollback_marker < health_proof

    workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-production.yml").read_text(
        encoding="utf-8"
    )
    assert "rollback health and paper checks passed" in workflow
    assert "grep -Fxq 'OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS'" in workflow
