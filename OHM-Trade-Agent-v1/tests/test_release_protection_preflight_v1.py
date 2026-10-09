"""AC-024: read-only protection preflight refuses a release before mutation.

The preflight reuses the existing protection report, materializes the candidate
SHA into a temporary tree so the live checkout is never changed before PASS, and
surfaces the exact blockers through bounded receipt markers. These tests do not
call Kraken and do not mutate a registry.
"""

from __future__ import annotations

import json
import re
import subprocess
import types
from pathlib import Path
import shutil
import sys

import pytest

from app.jobs import preflight_protection_health as preflight
from app.jobs.preflight_protection_health import (
    build_preflight_document,
    format_incident_health,
    format_marker_codes,
    format_marker_incidents,
    format_marker_symbols,
)
from app.jobs.report_protection_health import (
    ProtectionObservation,
    build_report as _build_report,
)
from app.services.evidence_shadow_readiness import (
    ADVISORY_CANDIDATE_RECOVERABLE_INCIDENT,
    BLOCK_COVERAGE_INCOMPLETE,
    BLOCK_INCIDENT_UNREADABLE,
    STATE_BLOCKED,
    STATE_READY,
    evaluate_evidence_shadow_readiness,
)
from app.services.protection_health import (
    REASON_UNMANAGED_EXPOSURE,
    STATE_HEALTHY,
    STATE_UNAVAILABLE,
    STATE_UNSAFE,
    evaluate_protection_health,
)
from tests.test_opip_deployment_transaction_boundary_v1 import (
    RELEASE_SHA,
    _classify,
    requires_bash,
)
from tests.test_opip_canonical_single_writer_feasibility_v1 import (
    _bash,
    _is_fork_failure,
    _run_bash_script,
)

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "deploy" / "remote" / "ohm-deploy").read_text(encoding="utf-8")
SSH = (ROOT / "deploy" / "remote" / "ohm-deploy-ssh").read_text(encoding="utf-8")
PREFLIGHT = (
    ROOT / "app" / "jobs" / "preflight_protection_health.py"
).read_text(encoding="utf-8")
REPORT = (
    ROOT / "app" / "jobs" / "report_protection_health.py"
).read_text(encoding="utf-8")
WORKFLOW = (
    ROOT.parent / ".github" / "workflows" / "deploy-production.yml"
).read_text(encoding="utf-8")
SHA = "9da2fc4a70340b5a6cf158e539ef55ffa25971f8"
WHEN = "2026-10-08T02:30:00Z"

MARKER_NAMES = (
    "UNMANAGED_EXPOSURES",
    "UNCERTAIN_EXPOSURES",
    "SILENT_HOLDINGS",
    "INCIDENT_HEALTH",
)


def _report(**overrides):
    report = {
        "state": "HEALTHY",
        "admissions_suspended": False,
        "coverage_complete": True,
        "reason_codes": ["PROTECTION_PROVEN"],
        "silent_holdings": [],
        "geometry_invalid_exposures": [],
        "unmanaged_exposures": [],
        "uncertain_exposures": [],
        "resolution_reason": "",
    }
    report.update(overrides)
    return report


def _shadow(
    *,
    exposures=(),
    coverage_complete=True,
    open_incidents=(),
    degraded_scopes=frozenset(),
):
    return evaluate_evidence_shadow_readiness(
        exposures,
        coverage_complete=coverage_complete,
        open_incidents=open_incidents,
        current_degraded_scopes=degraded_scopes,
    )


def _observation(report, incidents=True, *, shadow=None):
    """Build a coherent observation for a synthetic strict report.

    When no explicit shadow result is supplied, derive a plausible one from the
    report's coverage and the supplied incident verdict, so a legacy strict-field
    assertion also exercises the AC-026 verdict projection.
    """
    if shadow is None:
        if report.get("coverage_complete") is not True:
            shadow = _shadow(coverage_complete=False)
        elif incidents is not True:
            shadow = _shadow(open_incidents=None)
        else:
            shadow = _shadow(coverage_complete=True)
    return ProtectionObservation(
        report=report,
        incidents_healthy=incidents,
        shadow=shadow,
    )


def _document(report, incidents=True, *, shadow=None):
    return build_preflight_document(
        _observation(report, incidents, shadow=shadow),
        candidate_sha=SHA,
        release_profile="EVIDENCE_SHADOW",
        checked_at_utc=WHEN,
    )


def _managed(symbol, *, stop=95.0, entry=100.0, direction="LONG"):
    return types.SimpleNamespace(
        status="VERIFIED_MANAGED",
        symbol=symbol,
        trade=types.SimpleNamespace(
            direction=direction, entry_price=entry, stop_price=stop
        ),
    )


def _unmanaged(symbol):
    return types.SimpleNamespace(status="VERIFIED_UNMANAGED", symbol=symbol, trade=None)


def _degraded(symbol):
    return types.SimpleNamespace(status="DEGRADED", symbol=symbol, trade=None)


def _incident_row(scope, state="OPEN"):
    return {"scope": scope, "state": state, "incident_key": f"SYSTEM_HEALTH:{scope}"}


def _run_preflight(
    monkeypatch,
    *,
    exposures=(),
    coverage_complete=True,
    degraded_scopes=frozenset(),
    incidents=None,
    resolver_error=None,
    candidate_sha=SHA,
    release_profile="EVIDENCE_SHADOW",
):
    """Run the real composition with an injected observation, read-only.

    Counts are recorded so a test can assert one resolver call and one incident
    read per decision.
    """
    import app.jobs.report_protection_health as reporter

    calls = {"resolver": 0, "incident_reads": 0}

    if resolver_error is not None:
        def _resolve():
            raise RuntimeError(resolver_error)
    else:
        resolution = types.SimpleNamespace(
            exposures=tuple(exposures),
            coverage_complete=coverage_complete,
            reason="",
            degraded_scopes=degraded_scopes,
        )

        def _resolve():
            calls["resolver"] += 1
            return resolution

    monkeypatch.setattr(
        reporter,
        "_read_only_resolver",
        lambda: types.SimpleNamespace(resolve=_resolve),
    )

    payload = incidents if incidents is not None else {"incidents": {}}

    def _read_incidents(path):
        calls["incident_reads"] += 1
        return payload

    monkeypatch.setattr(reporter, "read_json_without_quarantine", _read_incidents)

    document = preflight.evaluate_preflight(
        candidate_sha=candidate_sha, release_profile=release_profile
    )
    return document, calls


def _without_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _function_body() -> str:
    return DEPLOY.split("run_protection_preflight() {", 1)[1].split(
        "\n# AC-024 read-only protection boundary.", 1
    )[0]


def _function_code() -> str:
    return _without_comments(_function_body())


def _boundary_block() -> str:
    start = DEPLOY.index("# AC-024 read-only protection boundary.")
    end = DEPLOY.index("# Stop paper workers during the build/recreate window.", start)
    return DEPLOY[start:end]


def _boundary_code() -> str:
    return _without_comments(_boundary_block())


@pytest.mark.acceptance
def test_ac_024_healthy_preflight_is_ready():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: a HEALTHY complete non-suspended strict protection decision with READY EVIDENCE_SHADOW readiness is ready to cross the mutable release boundary."""
    document = _document(_report())
    assert document["read_only"] is True
    assert document["verdict"]["ready"] is True
    assert document["protection"]["reason_codes"] == ["PROTECTION_PROVEN"]
    assert document["strict_f11"]["state"] == STATE_HEALTHY
    assert document["strict_f11"]["healthy"] is True
    assert document["evidence_shadow"]["state"] == STATE_READY
    assert document["markers"]["strict_f11_state"] == "HEALTHY"
    assert document["markers"]["evidence_shadow_readiness"] == "READY"
    assert document["incidents"]["open_incident_count"] is None
    assert document["incidents"]["open_incidents"] is None
    assert document["markers"]["incident_health"] == "true"


@pytest.mark.acceptance
@pytest.mark.parametrize(
    ("report", "incidents", "shadow", "code", "expected_health"),
    [
        (
            _report(
                state="UNAVAILABLE",
                admissions_suspended=True,
                coverage_complete=False,
                reason_codes=["EXPOSURE_COVERAGE_INCOMPLETE", "UNAVAILABLE"],
                resolution_reason="USD/stable-quote pricing unavailable for held assets: ADA.Z",
                unmanaged_exposures=["ADA.Z"],
            ),
            True,
            _shadow(coverage_complete=False),
            "EXPOSURE_COVERAGE_INCOMPLETE",
            "true",
        ),
        (
            _report(
                state="UNAVAILABLE",
                admissions_suspended=True,
                reason_codes=["PROTECTION_INCIDENT_OPEN"],
            ),
            False,
            _shadow(open_incidents=None),
            "PROTECTION_INCIDENT_OPEN",
            "false",
        ),
        (
            _report(
                state="UNAVAILABLE",
                admissions_suspended=True,
                coverage_complete=False,
                reason_codes=["UNAVAILABLE"],
            ),
            None,
            _shadow(coverage_complete=False, open_incidents=None),
            "UNAVAILABLE",
            "UNPROVEN",
        ),
        (
            _report(
                state="UNSAFE",
                admissions_suspended=True,
                reason_codes=["SILENT_HOLDING_UNPROTECTED_EXPOSURE"],
                silent_holdings=["BTCUSD"],
            ),
            True,
            _shadow(exposures=[_managed("BTCUSD", stop=0.0)]),
            "SILENT_HOLDING_UNPROTECTED_EXPOSURE",
            "true",
        ),
    ],
)
def test_ac_024_non_ready_protection_is_refused(
    report, incidents, shadow, code, expected_health
):
    """ATDD-RELEASE-PIPELINE-v1/AC-024: incomplete coverage, an open/unproven incident and a silent managed holding all refuse before mutation, and the strict incident verdict is carried through unchanged."""
    document = _document(report, incidents, shadow=shadow)
    assert document["verdict"]["ready"] is False
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert code in document["protection"]["reason_codes"]
    assert document["protection"]["admissions_suspended"] is True
    assert document["incidents"]["health"] is incidents
    assert document["markers"]["incident_health"] == expected_health


@pytest.mark.acceptance
@pytest.mark.parametrize(
    ("incidents_healthy", "expected_marker", "expected_code"),
    [
        (True, "true", None),
        (False, "false", "PROTECTION_INCIDENT_OPEN"),
        (None, "UNPROVEN", "PROTECTION_INCIDENT_HEALTH_UNPROVEN"),
    ],
)
def test_ac_024_incident_health_consistent_with_classification(
    incidents_healthy, expected_marker, expected_code
):
    """ATDD-RELEASE-PIPELINE-v1/AC-024: incidents.health is the same verdict that drives PROTECTION_INCIDENT_OPEN (false) and PROTECTION_INCIDENT_HEALTH_UNPROVEN (null); a proved-clear verdict stays HEALTHY/true."""
    report = evaluate_protection_health(
        (), coverage_complete=True, incidents_healthy=incidents_healthy
    ).to_dict()
    document = _document(report, incidents_healthy)
    assert document["incidents"]["health"] is incidents_healthy
    assert document["markers"]["incident_health"] == expected_marker
    if expected_code is None:
        assert document["protection"]["reason_codes"] == ["PROTECTION_PROVEN"]
        assert document["verdict"]["ready"] is True
    else:
        assert expected_code in document["protection"]["reason_codes"]
        assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_024_one_incident_observation_drives_report_and_marker(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-024: the preflight observes the incident store exactly once and reuses that verdict for both protection classification and the incident-health marker."""
    document, calls = _run_preflight(
        monkeypatch,
        exposures=[_managed("XBTUSD")],
        incidents={
            "incidents": {
                "KRAKEN:RATE_LIMIT": _incident_row("KRAKEN:RATE_LIMIT"),
            }
        },
    )
    assert calls["incident_reads"] == 1
    assert calls["resolver"] == 1
    assert document["incidents"]["health"] is False
    assert "PROTECTION_INCIDENT_OPEN" in document["protection"]["reason_codes"]
    assert document["markers"]["incident_health"] == "false"


@pytest.mark.acceptance
def test_ac_024_preflight_uses_the_existing_read_only_report():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: candidate preflight calls the shared non-mutating protection observation, observes incidents once, and never recovers, repairs, or trades."""
    assert "build_observation()" in PREFLIGHT
    assert "protection_incidents_healthy" not in PREFLIGHT
    assert "build_report_with_incidents" in REPORT
    assert "build_observation" in REPORT
    assert "read_active_trades_without_mutation" in REPORT
    assert "minimum_unmanaged_notional_usd=0.0" in REPORT
    assert "minimum_unmanaged_notional_usd" not in PREFLIGHT
    for forbidden in (
        "observe_recovery",
        "repair",
        "quarantine",
        "AddOrder",
        "add_order",
        "cancel_order",
        "create_order",
    ):
        assert forbidden not in PREFLIGHT
    assert _build_report.__module__ == "app.jobs.report_protection_health"


@pytest.mark.acceptance
def test_ac_024_marker_fields_are_bounded_and_sanitized():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: the receipt markers dedupe, drop anything that is not a plain exposure symbol, and are explicitly bounded in count and length."""
    assert format_marker_symbols([]) == "NONE"
    assert format_marker_symbols(None) == "NONE"
    assert format_marker_symbols(["ADAUSD", "ADAUSD", "SEIUSD"]) == "ADAUSD,SEIUSD"
    assert format_marker_symbols(["bad symbol", "SEI.B", "SUI.B", "TAO.B"]) == "SEI.B,SUI.B,TAO.B"
    assert format_marker_symbols(["A" * 30, "ADAUSD"]) == "ADAUSD"
    assert format_marker_symbols(["ADAUSD", {"bad": 1}]) == "ADAUSD"
    encoded = format_marker_symbols([f"SYM{i}" for i in range(50)])
    assert encoded.count(",") <= 19
    assert len(encoded) <= 400
    assert " " not in encoded
    assert format_incident_health(True) == "true"
    assert format_incident_health(False) == "false"
    assert format_incident_health(None) == "UNPROVEN"


@pytest.mark.acceptance
def test_ac_024_candidate_code_comes_from_a_temporary_target_sha_tree():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: candidate Python comes from a temporary detached worktree of TARGET_SHA, mounted read-only, with the running image used only for dependencies."""
    body = _function_body()
    code = _function_code()
    assert "worktree add --detach" in body
    assert '"$TARGET_SHA"' in body
    assert "worktree remove --force" in body
    assert "worktree prune" in body
    assert '-v "$candidate_app:/app/app:ro"' in body
    assert '-v "$APP_ROOT/data:/app/data:ro"' in body
    assert "python -m app.jobs.preflight_protection_health" in body
    assert "--read-only" in body
    assert "docker compose" not in code
    assert "--publish" not in code
    assert "stop_paper_stack" not in code
    assert "LAST_GOOD_FILE" not in code
    assert "reconcile-scheduler" not in code
    assert "checkout -f main" not in code
    assert "reset --hard" not in code
    assert "cleanup_snapshot" not in code


@pytest.mark.acceptance
def test_ac_024_live_checkout_is_unchanged_until_preflight_pass():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: the live repository checkout is not changed before the preflight PASS; only then does checkout/reset and the rollback trap happen."""
    start = DEPLOY.index("# AC-024 read-only protection boundary.")
    invocation = DEPLOY.index("if ! run_protection_preflight; then", start)
    checkout = DEPLOY.index('checkout -f main', start)
    reset = DEPLOY.index('reset --hard "$TARGET_SHA"', start)
    mutation = DEPLOY.index("\nstop_paper_stack\n", reset)
    trap = DEPLOY.index("\ntrap rollback ERR\n")
    assert trap < invocation < checkout < reset < mutation
    boundary = _without_comments(DEPLOY[invocation:checkout])
    assert "cleanup_snapshot" in boundary
    assert "trap - ERR" in boundary
    assert "stop_paper_stack" not in boundary
    assert "docker compose" not in boundary
    assert "LAST_GOOD_FILE" not in boundary


@pytest.mark.acceptance
def test_ac_024_preflight_refusal_precedes_every_mutation():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: no service stop, build, recreate, scheduler reconcile, writer activation, or last-good write can run before a PASS."""
    code = _function_code()
    boundary = _boundary_code()
    for forbidden in (
        "docker compose",
        "stop_paper_stack",
        'bash "$SCHEDULER_RECONCILE"',
        "LAST_GOOD_FILE",
    ):
        assert forbidden not in code
        assert forbidden not in boundary
    after = _without_comments(
        DEPLOY[DEPLOY.index("\nstop_paper_stack\n") :]
    )
    assert after.index("stop_paper_stack") < after.index(
        'docker compose build --build-arg "OPIP_RELEASE_SHA=$TARGET_SHA"'
    )
    assert after.index(
        "docker compose up -d --remove-orphans opip-canonical-writer"
    ) < after.index('bash "$SCHEDULER_RECONCILE"')


@pytest.mark.acceptance
def test_ac_024_preflight_block_does_not_claim_rollback():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: a preflight refusal reports mutation not started and does not invoke rollback."""
    body = _function_body()
    assert "PROTECTION PREFLIGHT BLOCKED" in body
    assert "production_mutation_started=false" in body
    assert "rollback_required=false" in body
    assert "OPIP_PRODUCTION_MUTATION=NOT_STARTED" in body
    assert "OPIP_CORE_DEPLOY_STATUS=NOT_STARTED" in body
    assert "rollback()" not in body
    block = _boundary_block()
    assert "OPIP_PROTECTION_PREFLIGHT_ABORT=REFUSED_BEFORE_MUTATION" in block
    assert "exit 77" in block


@pytest.mark.acceptance
def test_ac_024_markers_are_wired_through_the_receipt():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: unmanaged, uncertain and silent exposure symbols and incident health reach the deploy parser, the GitHub outputs, the receipt and the failure summary."""
    for name in MARKER_NAMES:
        assert f"OPIP_PROTECTION_PREFLIGHT_{name}=" in DEPLOY
        assert f"OPIP_PROTECTION_PREFLIGHT_{name}=" in WORKFLOW
        assert f"OPIP_PROTECTION_PREFLIGHT_{name}=" in _function_body()
    assert "protection_preflight_unmanaged_exposures=" in WORKFLOW
    assert "protection_preflight_uncertain_exposures=" in WORKFLOW
    assert "protection_preflight_silent_holdings=" in WORKFLOW
    assert "protection_preflight_incident_health=" in WORKFLOW
    assert "PROTECTION_UNMANAGED_EXPOSURES=" in WORKFLOW
    assert "PROTECTION_UNCERTAIN_EXPOSURES=" in WORKFLOW
    assert "PROTECTION_SILENT_HOLDINGS=" in WORKFLOW
    assert "PROTECTION_INCIDENT_HEALTH=" in WORKFLOW
    assert "Unmanaged exposures:" in WORKFLOW
    assert "Uncertain exposures:" in WORKFLOW
    assert "Silent holdings:" in WORKFLOW
    assert "Incident health:" in WORKFLOW


@pytest.mark.acceptance
def test_ac_024_ssh_gateway_keeps_exactly_two_commands():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: the forced command remains deploy <sha> and diagnose-learning, with no shell passthrough."""
    assert "=~ ^deploy[[:space:]]+([0-9a-f]{40})$" in SSH
    assert '== "diagnose-learning"' in SSH
    assert "refusing command" in SSH
    assert "eval " not in SSH
    assert "bash -c" not in SSH
    assert SSH.count("exec sudo") == 2


@pytest.mark.acceptance
def test_ac_024_unknown_decoration_class_stays_a_coverage_block():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: an unpriced held asset remains a coverage failure, stays visible, and is encoded into the marker."""
    document = _document(
        _report(
            state="UNAVAILABLE",
            admissions_suspended=True,
            coverage_complete=False,
            reason_codes=[
                "EXPOSURE_COVERAGE_INCOMPLETE",
                "UNMANAGED_EXPOSURE_REQUIRES_REVIEW",
            ],
            resolution_reason="USD/stable-quote pricing unavailable for held assets: ADA.Z",
            unmanaged_exposures=["ADA.Z"],
        )
    )
    assert document["verdict"]["ready"] is False
    assert "ADA.Z" in document["protection"]["unmanaged_exposures"]
    assert document["markers"]["unmanaged_exposures"] == "ADA.Z"
    assert "PRICING_OR_COVERAGE" in preflight.diagnostic_classes(
        document["protection"]
    )
    assert "UNMANAGED_EXPOSURE" in preflight.diagnostic_classes(
        document["protection"]
    )


# ---------------------------------------------------------------------------
# AC-026: EVIDENCE_SHADOW readiness drives the release decision, while strict
# F11 stays unchanged. Every case below runs the REAL read-only composition with
# an injected observation, so it exercises the same one-resolver/one-incident
# composition the deploy preflight uses.
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_026_unmanaged_only_shadow_case(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: a complete fresh observation with only a VERIFIED_UNMANAGED external holding keeps strict F11 non-HEALTHY and suspended, yet EVIDENCE_SHADOW is READY and the preflight PASSES."""
    document, calls = _run_preflight(
        monkeypatch,
        exposures=[_unmanaged("XBTUSD")],
        coverage_complete=True,
    )
    assert calls["resolver"] == 1
    assert calls["incident_reads"] == 1
    # Strict F11 unchanged.
    assert document["strict_f11"]["state"] == STATE_UNSAFE
    assert document["strict_f11"]["healthy"] is False
    assert document["protection"]["admissions_suspended"] is True
    assert REASON_UNMANAGED_EXPOSURE in document["protection"]["reason_codes"]
    assert document["markers"]["unmanaged_exposures"] == "XBTUSD"
    # Shadow readiness is a different decision.
    assert document["evidence_shadow"]["state"] == STATE_READY
    assert document["evidence_shadow"]["ready"] is True
    assert document["evidence_shadow"]["unmanaged_exposures"] == ["XBTUSD"]
    assert document["evidence_shadow"]["blocking_reason_codes"] == []
    assert document["markers"]["evidence_shadow_readiness"] == "READY"
    assert document["markers"]["evidence_shadow_unmanaged_exposures"] == "XBTUSD"
    assert document["verdict"]["ready"] is True


@pytest.mark.acceptance
def test_ac_026_production_shaped_case_is_shadow_ready_but_strict_non_healthy(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: VERIFIED_UNMANAGED holdings plus durable HELD_ASSET_PRICING and POSITION_VERIFICATION incidents whose current candidate predicate is proven leave strict F11 non-HEALTHY but make EVIDENCE_SHADOW READY, with both incidents visible as candidate-recoverable."""
    pricing = "KRAKEN:HELD_ASSET_PRICING"
    position = "KRAKEN:POSITION_VERIFICATION"
    document, calls = _run_preflight(
        monkeypatch,
        exposures=[_unmanaged("XBTUSD")],
        coverage_complete=True,
        degraded_scopes=frozenset(),
        incidents={
            "incidents": {
                "SYSTEM_HEALTH:" + pricing: _incident_row(pricing),
                "SYSTEM_HEALTH:" + position: _incident_row(position),
            }
        },
    )
    assert calls["resolver"] == 1
    assert calls["incident_reads"] == 1
    # Strict F11: the durable incidents remain unresolved strict concerns.
    assert document["strict_f11"]["state"] == STATE_UNAVAILABLE
    assert document["strict_f11"]["healthy"] is False
    assert document["incidents"]["health"] is False
    assert "PROTECTION_INCIDENT_OPEN" in document["protection"]["reason_codes"]
    assert REASON_UNMANAGED_EXPOSURE in document["protection"]["reason_codes"]
    # Shadow: candidate-recoverable, not "recovered".
    shadow = document["evidence_shadow"]
    assert shadow["state"] == STATE_READY
    assert shadow["candidate_recoverable_incidents"] == [
        f"SYSTEM_HEALTH:{pricing}",
        f"SYSTEM_HEALTH:{position}",
    ]
    assert shadow["advisory_reason_codes"] == [
        ADVISORY_CANDIDATE_RECOVERABLE_INCIDENT,
        "UNMANAGED_EXPOSURE",
    ]
    assert shadow["blocking_incidents"] == []
    assert document["markers"]["evidence_shadow_candidate_recoverable_incidents"] == (
        f"SYSTEM_HEALTH:{pricing},SYSTEM_HEALTH:{position}"
    )
    assert document["markers"]["evidence_shadow_blocking_reason_codes"] == "NONE"
    assert document["verdict"]["ready"] is True


@pytest.mark.acceptance
def test_ac_026_held_asset_pricing_currently_degraded_blocks(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: a current held-asset pricing degradation keeps EVIDENCE_SHADOW BLOCKED and refuses the preflight, even when a pricing incident exists."""
    pricing = "KRAKEN:HELD_ASSET_PRICING"
    document, _ = _run_preflight(
        monkeypatch,
        exposures=[_unmanaged("DOGE")],
        coverage_complete=False,
        degraded_scopes=frozenset({pricing}),
        incidents={"incidents": {"SYSTEM_HEALTH:" + pricing: _incident_row(pricing)}},
    )
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert BLOCK_COVERAGE_INCOMPLETE in document["evidence_shadow"]["blocking_reason_codes"]
    assert "PRICING_GAP" in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["evidence_shadow"]["candidate_recoverable_incidents"] == []
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_position_verification_currently_degraded_blocks(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: a current position-verification degradation keeps EVIDENCE_SHADOW BLOCKED and refuses the preflight."""
    position = "KRAKEN:POSITION_VERIFICATION"
    document, _ = _run_preflight(
        monkeypatch,
        exposures=[_managed("XBTUSD")],
        coverage_complete=True,
        degraded_scopes=frozenset({position}),
        incidents={"incidents": {"SYSTEM_HEALTH:" + position: _incident_row(position)}},
    )
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert "POSITION_VERIFICATION_GAP" in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["evidence_shadow"]["blocking_incidents"] == [
        f"SYSTEM_HEALTH:{position}"
    ]
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_current_scope_status_unproven_fails_closed(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: an existing coverage incident with missing/unproven same-cycle scope evidence stays blocking."""
    pricing = "KRAKEN:HELD_ASSET_PRICING"
    document, _ = _run_preflight(
        monkeypatch,
        exposures=[],
        coverage_complete=True,
        degraded_scopes=None,
        incidents={"incidents": {"SYSTEM_HEALTH:" + pricing: _incident_row(pricing)}},
    )
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert "PRICING_GAP" in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["evidence_shadow"]["candidate_recoverable_incidents"] == []
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_coverage_incomplete_fails_closed(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: incomplete coverage blocks EVIDENCE_SHADOW."""
    document, _ = _run_preflight(monkeypatch, coverage_complete=False)
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert BLOCK_COVERAGE_INCOMPLETE in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_uncertain_exposure_fails_closed(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: an uncertain exposure blocks EVIDENCE_SHADOW."""
    document, _ = _run_preflight(monkeypatch, exposures=[_degraded("XBTUSD")])
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert "EXPOSURE_UNCERTAIN" in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["evidence_shadow"]["uncertain_exposures"] == ["XBTUSD"]
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_silent_managed_holding_fails_closed(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: a silent managed holding blocks EVIDENCE_SHADOW."""
    document, _ = _run_preflight(
        monkeypatch, exposures=[_managed("XBTUSD", stop=0.0)]
    )
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert "SILENT_HOLDING" in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["evidence_shadow"]["silent_holdings"] == ["XBTUSD"]
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_invalid_managed_geometry_fails_closed(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: an invalid managed protection geometry blocks EVIDENCE_SHADOW."""
    document, _ = _run_preflight(
        monkeypatch, exposures=[_managed("XBTUSD", entry=100.0, stop=105.0)]
    )
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert "GEOMETRY_INVALID" in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["evidence_shadow"]["geometry_invalid_exposures"] == ["XBTUSD"]
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
@pytest.mark.parametrize(
    "scope",
    [
        "KRAKEN:READ_ONLY_AUTH",
        "KRAKEN:PUBLIC_CONNECTIVITY",
        "KRAKEN:READ_ONLY_CONNECTIVITY",
        "KRAKEN:RATE_LIMIT",
    ],
)
def test_ac_026_non_coverage_incidents_fail_closed(monkeypatch, scope):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: an auth, connectivity or rate-limit incident is a known non-coverage blocker, never candidate-recoverable."""
    document, _ = _run_preflight(
        monkeypatch,
        exposures=[_managed("XBTUSD")],
        coverage_complete=True,
        incidents={"incidents": {"SYSTEM_HEALTH:" + scope: _incident_row(scope)}},
    )
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert "INCIDENT_OPEN" in document["evidence_shadow"]["blocking_reason_codes"]
    assert "INCIDENT_UNKNOWN" not in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["evidence_shadow"]["candidate_recoverable_incidents"] == []
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_unknown_incident_fails_closed(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: an unrecognized incident scope blocks with INCIDENT_UNKNOWN."""
    document, _ = _run_preflight(
        monkeypatch,
        exposures=[],
        coverage_complete=True,
        incidents={"incidents": {"SYSTEM_HEALTH:SOMETHING:ELSE": _incident_row("SOMETHING:ELSE")}},
    )
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert "INCIDENT_UNKNOWN" in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_malformed_incident_fails_closed(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: an incident record with no scope is malformed evidence and blocks; it is never treated as 'no incident'."""
    document, _ = _run_preflight(
        monkeypatch,
        exposures=[],
        coverage_complete=True,
        incidents={"incidents": {"SYSTEM_HEALTH:?:": {"incident_key": "SYSTEM_HEALTH:?:"}}},
    )
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert "EVIDENCE_MALFORMED" in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_unreadable_incident_store_is_never_empty(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: an unreadable durable incident store blocks EVIDENCE_SHADOW and is never interpreted as an empty incident set."""
    import app.jobs.report_protection_health as reporter
    from app.services.registry_io import RegistryIOError

    def _unreadable(path):
        raise RegistryIOError("incident store unreadable")

    monkeypatch.setattr(reporter, "read_json_without_quarantine", _unreadable)
    monkeypatch.setattr(
        reporter,
        "_read_only_resolver",
        lambda: types.SimpleNamespace(
            resolve=lambda: types.SimpleNamespace(
                exposures=(), coverage_complete=True, reason="", degraded_scopes=frozenset()
            )
        ),
    )
    document = preflight.evaluate_preflight(
        candidate_sha=SHA, release_profile="EVIDENCE_SHADOW"
    )
    assert document["evidence_shadow"]["state"] == STATE_BLOCKED
    assert BLOCK_INCIDENT_UNREADABLE in document["evidence_shadow"]["blocking_reason_codes"]
    assert document["incidents"]["health"] is None
    assert document["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_strict_incident_health_is_not_forgiven(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: candidate recoverability never makes strict incident health true while the durable incident remains unresolved."""
    pricing = "KRAKEN:HELD_ASSET_PRICING"
    document, _ = _run_preflight(
        monkeypatch,
        exposures=[],
        coverage_complete=True,
        degraded_scopes=frozenset(),
        incidents={"incidents": {"SYSTEM_HEALTH:" + pricing: _incident_row(pricing)}},
    )
    # Shadow forgives for readiness purposes...
    assert document["evidence_shadow"]["state"] == STATE_READY
    assert document["evidence_shadow"]["candidate_recoverable_incidents"] == [
        f"SYSTEM_HEALTH:{pricing}"
    ]
    # ...but strict F11 incident health is still false and its reason is present.
    assert document["incidents"]["health"] is False
    assert document["strict_f11"]["healthy"] is False
    assert "PROTECTION_INCIDENT_OPEN" in document["protection"]["reason_codes"]
    assert document["markers"]["incident_health"] == "false"


@pytest.mark.acceptance
def test_ac_026_preflight_does_not_mutate_the_incident_store(monkeypatch, tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: a preflight decision leaves the durable incident bytes unchanged and never calls a recovery/write path."""
    import app.jobs.report_protection_health as reporter
    from app.services import system_incidents

    state_file = tmp_path / "system_incidents.json"
    original = (
        '{"schema_version": 2, "incidents": {"SYSTEM_HEALTH:KRAKEN:HELD_ASSET_PRICING": '
        '{"scope": "KRAKEN:HELD_ASSET_PRICING", "state": "OPEN", "incident_key": '
        '"SYSTEM_HEALTH:KRAKEN:HELD_ASSET_PRICING"}}, "archive": {}}'
    )
    state_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(system_incidents, "STATE_FILE", state_file)
    monkeypatch.setattr(
        system_incidents,
        "observe_recovery",
        lambda **kwargs: pytest.fail("preflight must never recover an incident"),
    )
    monkeypatch.setattr(
        reporter,
        "_read_only_resolver",
        lambda: types.SimpleNamespace(
            resolve=lambda: types.SimpleNamespace(
                exposures=(), coverage_complete=True, reason="", degraded_scopes=frozenset()
            )
        ),
    )
    document = preflight.evaluate_preflight(
        candidate_sha=SHA, release_profile="EVIDENCE_SHADOW"
    )
    assert document["verdict"]["ready"] is True
    assert state_file.read_text(encoding="utf-8") == original


@pytest.mark.acceptance
def test_ac_026_no_lifecycle_or_trade_write_path_is_invoked(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the preflight calls exactly one non-mutating resolver and performs no lifecycle/trade write."""
    import app.jobs.report_protection_health as reporter
    import app.services.active_trade_registry as registry

    called = {"resolver": 0}

    original_resolver = reporter._read_only_resolver

    def _counting_resolver():
        called["resolver"] += 1
        return original_resolver()

    monkeypatch.setattr(reporter, "_read_only_resolver", _counting_resolver)
    monkeypatch.setattr(
        reporter, "read_json_without_quarantine", lambda path: {"incidents": {}}
    )
    for name in ("close_trade", "update_trade_remaining_quantity", "mark_order_filled"):
        if hasattr(registry, name):
            monkeypatch.setattr(
                registry, name, lambda *a, **k: pytest.fail(f"{name} must not be called")
            )
    preflight.evaluate_preflight(candidate_sha=SHA, release_profile="EVIDENCE_SHADOW")
    assert called["resolver"] == 1


@pytest.mark.acceptance
def test_ac_026_single_resolver_and_single_incident_read_per_decision(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: one exposure resolution and one durable-incident read feed both strict F11 and shadow readiness."""
    _, calls = _run_preflight(
        monkeypatch, exposures=[_unmanaged("XBTUSD")], coverage_complete=True
    )
    assert calls["resolver"] == 1
    assert calls["incident_reads"] == 1


@pytest.mark.acceptance
def test_ac_026_same_observation_drives_strict_and_shadow(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: strict F11 and shadow readiness consume the SAME captured exposure observation, so an unmanaged holding is simultaneously strict-non-healthy and shadow-advisory."""
    document, calls = _run_preflight(
        monkeypatch, exposures=[_unmanaged("ADAUSD")], coverage_complete=True
    )
    assert calls["resolver"] == 1
    assert document["protection"]["unmanaged_exposures"] == ["ADAUSD"]
    assert document["evidence_shadow"]["unmanaged_exposures"] == ["ADAUSD"]
    assert document["strict_f11"]["healthy"] is False
    assert document["evidence_shadow"]["ready"] is True


@pytest.mark.acceptance
def test_ac_026_candidate_sha_and_profile_are_still_validated(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: an invalid candidate SHA or a non-qualified profile refuses even when shadow readiness is READY."""
    ready_doc, _ = _run_preflight(
        monkeypatch, exposures=[_unmanaged("XBTUSD")], coverage_complete=True
    )
    assert ready_doc["verdict"]["ready"] is True

    bad_sha, _ = _run_preflight(
        monkeypatch,
        exposures=[_unmanaged("XBTUSD")],
        coverage_complete=True,
        candidate_sha="not-a-sha",
    )
    assert bad_sha["verdict"]["ready"] is False

    bad_profile, _ = _run_preflight(
        monkeypatch,
        exposures=[_unmanaged("XBTUSD")],
        coverage_complete=True,
        release_profile="SAFE_BASELINE",
    )
    assert bad_profile["verdict"]["ready"] is False


@pytest.mark.acceptance
def test_ac_026_refusal_exit_contract_is_unchanged(monkeypatch, capsys):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the preflight CLI still exits 76 when blocked and 0 only when READY."""
    from app.jobs.report_protection_health import ProtectionObservation

    ready = ProtectionObservation(
        report=_report(),
        incidents_healthy=True,
        shadow=_shadow(),
    )
    blocked = ProtectionObservation(
        report=_report(
            state="UNAVAILABLE",
            admissions_suspended=True,
            coverage_complete=False,
            reason_codes=["EXPOSURE_COVERAGE_INCOMPLETE"],
        ),
        incidents_healthy=True,
        shadow=_shadow(coverage_complete=False),
    )

    monkeypatch.setattr(preflight, "build_observation", lambda: ready)
    assert preflight.main(["--candidate-sha", SHA, "--release-profile", "EVIDENCE_SHADOW"]) == 0
    capsys.readouterr()

    monkeypatch.setattr(preflight, "build_observation", lambda: blocked)
    assert preflight.main(["--candidate-sha", SHA, "--release-profile", "EVIDENCE_SHADOW"]) == 76


@pytest.mark.acceptance
def test_ac_026_legacy_report_and_markers_still_mean_strict_protection(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the legacy report/marker surface is preserved and still means strict protection health, with the AC-026 projection added alongside."""
    import app.jobs.report_protection_health as reporter

    monkeypatch.setattr(
        reporter,
        "_read_only_resolver",
        lambda: types.SimpleNamespace(
            resolve=lambda: types.SimpleNamespace(
                exposures=(_unmanaged("ADAUSD"),),
                coverage_complete=True,
                reason="held balance without lifecycle context",
                degraded_scopes=frozenset(),
            )
        ),
    )
    monkeypatch.setattr(
        reporter, "read_json_without_quarantine", lambda path: {"incidents": {}}
    )
    report, incidents_healthy = reporter.build_report_with_incidents()
    assert incidents_healthy is True
    # Existing strict F11 fields keep their meaning.
    assert report["state"] == STATE_UNSAFE
    assert report["admissions_suspended"] is True
    assert REASON_UNMANAGED_EXPOSURE in report["reason_codes"]
    # The AC-026 projection is added alongside, never in place of, strict fields.
    assert report["evidence_shadow"]["state"] == STATE_READY
    assert report["evidence_shadow"]["ready"] is True


@pytest.mark.acceptance
def test_ac_026_ac024_marker_names_are_not_repurposed():
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the legacy JSON marker keys are unchanged in meaning, and the AC-026 markers are additional."""
    document = _document(_report())
    for legacy in (
        "unmanaged_exposures",
        "uncertain_exposures",
        "silent_holdings",
        "incident_health",
    ):
        assert legacy in document["markers"]
    for added in (
        "strict_f11_state",
        "strict_f11_reason_codes",
        "evidence_shadow_readiness",
        "evidence_shadow_blocking_reason_codes",
        "evidence_shadow_advisory_reason_codes",
        "evidence_shadow_unmanaged_exposures",
        "evidence_shadow_candidate_recoverable_incidents",
        "evidence_shadow_blocking_incidents",
    ):
        assert added in document["markers"]
    assert document["markers"]["strict_f11_state"] == "HEALTHY"
    assert document["markers"]["strict_f11_reason_codes"] == "PROTECTION_PROVEN"


@pytest.mark.acceptance
def test_ac_026_coverage_degraded_scopes_are_same_cycle_structured(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: same-cycle degradation evidence is derived from this observation's structured coverage facts (never the durable incident store or free text), and complete coverage provably implies an empty degraded set."""
    import app.services.kraken_exposure_resolver as ker
    from app.services.system_incidents import (
        SystemIncidentScope,
        coverage_degraded_scopes,
    )

    pricing = SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value
    position = SystemIncidentScope.KRAKEN_POSITION_VERIFICATION.value

    assert coverage_degraded_scopes(
        pricing_unavailable=False, position_verification_unavailable=False
    ) == frozenset()
    assert coverage_degraded_scopes(
        pricing_unavailable=True, position_verification_unavailable=False
    ) == frozenset({pricing})
    assert coverage_degraded_scopes(
        pricing_unavailable=False, position_verification_unavailable=True
    ) == frozenset({position})
    assert coverage_degraded_scopes(
        pricing_unavailable=True, position_verification_unavailable=True
    ) == frozenset({pricing, position})

    class _Private:
        enabled = True

        def assert_read_only(self):
            return types.SimpleNamespace(name="ro")

        def get_open_positions(self):
            return {}

    class _PrivateSol(_Private):
        def get_balance(self):
            return {"SOL": 1.0}

    class _PrivateSolDoge(_Private):
        def get_balance(self):
            return {"SOL": 1.0, "DOGE": 2.0}

    monkeypatch.setattr(ker, "_pair_catalog", lambda client: {"SOL": "SOLUSD"})
    monkeypatch.setattr(ker, "_minimum_unmanaged_notional_usd", lambda: 25.0)

    # DOGE has no catalog pair -> unpriced -> HELD_ASSET_PRICING is proven degraded.
    monkeypatch.setattr(
        ker,
        "_ticker_notionals",
        lambda client, *, quantities, pairs_by_asset: {
            asset: (100.0 if asset == "SOL" else None) for asset in quantities
        },
    )
    degraded = ker.KrakenExposureResolver(
        private_client=_PrivateSolDoge(), public_client=object(), trade_loader=lambda: []
    ).resolve()
    assert degraded.coverage_complete is False
    assert degraded.degraded_scopes == frozenset({pricing})

    # Every balance is priced -> complete coverage -> provably empty degraded set.
    monkeypatch.setattr(
        ker,
        "_ticker_notionals",
        lambda client, *, quantities, pairs_by_asset: {
            asset: 100.0 for asset in quantities
        },
    )
    complete = ker.KrakenExposureResolver(
        private_client=_PrivateSol(), public_client=object(), trade_loader=lambda: []
    ).resolve()
    assert complete.coverage_complete is True
    assert complete.degraded_scopes == frozenset()


@pytest.mark.acceptance
def test_ac_026_marker_formatters_are_bounded_and_sanitized():
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the AC-026 receipt markers are deterministic, bounded, sorted, NONE when empty, and drop anything that is not a bounded token/identity/code."""
    assert format_marker_codes(None) == "NONE"
    assert format_marker_codes([]) == "NONE"
    assert format_marker_codes(["B_CODE", "A_CODE", "B_CODE"]) == "A_CODE,B_CODE"
    assert format_marker_codes(["lower", "HAS SPACE", "OK_CODE"]) == "OK_CODE"
    assert format_marker_incidents(None) == "NONE"
    assert format_marker_incidents(
        ["SYSTEM_HEALTH:KRAKEN:POSITION_VERIFICATION", "SYSTEM_HEALTH:KRAKEN:HELD_ASSET_PRICING"]
    ) == "SYSTEM_HEALTH:KRAKEN:HELD_ASSET_PRICING,SYSTEM_HEALTH:KRAKEN:POSITION_VERIFICATION"
    assert format_marker_incidents(["free form incident reason text"]) == "NONE"
    encoded = format_marker_codes([f"CODE_{i}" for i in range(50)])
    assert encoded.count(",") <= 19
    assert len(encoded) <= 400
    assert " " not in encoded


# ---------------------------------------------------------------------------
# AC-026 Phase 4: the deploy controller's receipt parser consumes the explicit
# readiness verdict. These run the REAL embedded parser (the same Python the
# controller feeds the preflight receipt to) without requiring bash, so the
# fail-closed readiness contract is exercised on every platform.
# ---------------------------------------------------------------------------

_RECEIPT_FIELDS = (
    "status",
    "state",
    "coverage",
    "suspended",
    "codes",
    "reason",
    "unmanaged",
    "uncertain",
    "silent",
    "incident_health",
    "shadow_readiness",
    "shadow_blocking",
    "shadow_advisory",
    "shadow_unmanaged",
    "shadow_candidate",
    "shadow_blocking_incidents",
    "strict_f11_state",
)


def _receipt_parser_source() -> str:
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", DEPLOY, re.S)
    matching = [block for block in blocks if "def prefer(" in block]
    assert len(matching) == 1, "expected exactly one receipt-parser heredoc"
    return matching[0]


def _run_receipt_parser(tmp_path, document, *, rc=0, candidate_sha=SHA):
    parser = tmp_path / "receipt_parser.py"
    parser.write_text(_receipt_parser_source(), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    if isinstance(document, str):
        receipt.write_text(document, encoding="utf-8")
    else:
        receipt.write_text(json.dumps(document), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(parser), str(receipt), candidate_sha, str(rc)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert len(lines) == 17, lines
    return dict(zip(_RECEIPT_FIELDS, lines))


def _receipt_document(
    *,
    readiness="READY",
    strict_state="HEALTHY",
    strict_codes=(),
    coverage=True,
    unmanaged=(),
    shadow_ready=None,
    verdict_ready=None,
    include_shadow=True,
    shadow_blocking=(),
    shadow_advisory=(),
    shadow_unmanaged=(),
    shadow_candidate=(),
    shadow_blocking_incidents=(),
    incident_health=True,
    release_profile="EVIDENCE_SHADOW",
):
    if shadow_ready is None:
        shadow_ready = readiness == "READY"
    if verdict_ready is None:
        verdict_ready = shadow_ready
    codes = list(strict_codes) or ["PROTECTION_PROVEN"]
    markers = {
        "strict_f11_state": strict_state,
        "evidence_shadow_readiness": readiness,
        "evidence_shadow_blocking_reason_codes": ",".join(shadow_blocking) or "NONE",
        "evidence_shadow_advisory_reason_codes": ",".join(shadow_advisory) or "NONE",
        "evidence_shadow_unmanaged_exposures": ",".join(shadow_unmanaged) or "NONE",
        "evidence_shadow_candidate_recoverable_incidents": ",".join(shadow_candidate) or "NONE",
        "evidence_shadow_blocking_incidents": ",".join(shadow_blocking_incidents) or "NONE",
        "unmanaged_exposures": ",".join(unmanaged) or "NONE",
        "uncertain_exposures": "NONE",
        "silent_holdings": "NONE",
        "incident_health": "true" if incident_health else "false",
    }
    document = {
        "schema_version": 1,
        "read_only": True,
        "candidate_sha": SHA,
        "release_profile": release_profile,
        "protection": {
            "state": strict_state,
            "admissions_suspended": strict_state != "HEALTHY",
            "coverage_complete": coverage,
            "reason_codes": codes,
            "unmanaged_exposures": list(unmanaged),
            "uncertain_exposures": [],
            "silent_holdings": [],
        },
        "strict_f11": {
            "state": strict_state,
            "healthy": strict_state == "HEALTHY",
            "admissions_suspended": strict_state != "HEALTHY",
            "coverage_complete": coverage,
            "reason_codes": codes,
        },
        "incidents": {"health": incident_health},
        "markers": markers,
        "verdict": {"ready": verdict_ready, "reason": "test"},
    }
    if include_shadow:
        document["evidence_shadow"] = {
            "state": readiness,
            "ready": shadow_ready,
            "blocking_reason_codes": list(shadow_blocking),
            "advisory_reason_codes": list(shadow_advisory),
            "unmanaged_exposures": list(shadow_unmanaged),
            "candidate_recoverable_incidents": list(shadow_candidate),
            "blocking_incidents": list(shadow_blocking_incidents),
            "coverage_complete": coverage,
        }
    return document


@pytest.mark.acceptance
def test_ac_026_controller_accepts_explicit_readiness_with_advisory_strict_f11(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the controller PASSES an explicit EVIDENCE_SHADOW READY verdict even though strict F11 stays non-HEALTHY for an advisory unmanaged holding, and preserves the strict F11 markers."""
    document = _receipt_document(
        readiness="READY",
        strict_state="UNSAFE",
        strict_codes=("UNMANAGED_EXPOSURE_REQUIRES_REVIEW",),
        unmanaged=("XBTUSD",),
        shadow_unmanaged=("XBTUSD",),
        shadow_advisory=("UNMANAGED_EXPOSURE",),
    )
    fields = _run_receipt_parser(tmp_path, document)
    assert fields["status"] == "PASS"
    # Strict F11 remains visible and is not turned into HEALTHY.
    assert fields["strict_f11_state"] == "UNSAFE"
    assert fields["state"] == "UNSAFE"
    assert fields["codes"] == "UNMANAGED_EXPOSURE_REQUIRES_REVIEW"
    assert fields["unmanaged"] == "XBTUSD"
    assert fields["incident_health"] == "true"
    # The explicit AC-026 verdict drives the decision.
    assert fields["shadow_readiness"] == "READY"
    assert fields["shadow_unmanaged"] == "XBTUSD"


@pytest.mark.acceptance
def test_ac_026_controller_rejects_blocked_readiness(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: a BLOCKED readiness verdict refuses even when strict F11 is HEALTHY."""
    document = _receipt_document(
        readiness="BLOCKED",
        strict_state="HEALTHY",
        shadow_blocking=("EXPOSURE_COVERAGE_INCOMPLETE",),
        coverage=False,
        shadow_ready=False,
    )
    fields = _run_receipt_parser(tmp_path, document)
    assert fields["status"] == "FAIL"
    assert fields["shadow_readiness"] == "BLOCKED"


@pytest.mark.acceptance
def test_ac_026_controller_rejects_missing_readiness(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: a receipt with no readiness object refuses; absence of a failure marker is never readiness proof."""
    document = _receipt_document(include_shadow=False, strict_state="HEALTHY")
    fields = _run_receipt_parser(tmp_path, document)
    assert fields["status"] == "FAIL"
    assert fields["shadow_readiness"] == "UNPROVEN"


@pytest.mark.acceptance
def test_ac_026_controller_rejects_unknown_readiness(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: an unknown readiness state refuses."""
    document = _receipt_document(readiness="PROBABLY_FINE", strict_state="HEALTHY", shadow_ready=True)
    fields = _run_receipt_parser(tmp_path, document)
    assert fields["status"] == "FAIL"
    assert fields["shadow_readiness"] == "UNPROVEN"


@pytest.mark.acceptance
@pytest.mark.parametrize(
    "overrides",
    [
        # verdict.ready true but the readiness object says not ready
        {"readiness": "BLOCKED", "shadow_ready": False, "verdict_ready": True, "strict_state": "HEALTHY"},
        # readiness object says ready but its state is not READY
        {"readiness": "BLOCKED", "shadow_ready": True, "verdict_ready": True, "strict_state": "HEALTHY"},
        # readiness says READY but the verdict refuses
        {"readiness": "READY", "shadow_ready": True, "verdict_ready": False, "strict_state": "HEALTHY"},
    ],
)
def test_ac_026_controller_rejects_contradictory_state(tmp_path, overrides):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: contradictory receipt state fails closed."""
    fields = _run_receipt_parser(tmp_path, _receipt_document(**overrides))
    assert fields["status"] == "FAIL"


@pytest.mark.acceptance
def test_ac_026_controller_rejects_malformed_receipt(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: a malformed receipt refuses and yields bounded defaults."""
    fields = _run_receipt_parser(tmp_path, "{ not valid json")
    assert fields["status"] == "FAIL"
    assert fields["shadow_readiness"] == "UNPROVEN"
    assert fields["strict_f11_state"] == "UNAVAILABLE"


@pytest.mark.acceptance
def test_ac_026_controller_ac026_markers_are_bounded(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the AC-026 receipt markers drop non-token values and stay bounded in count and length."""
    hostile = "free form incident reason text"
    long_ids = [f"SYSTEM_HEALTH:KRAKEN:SCOPE_{i}" for i in range(40)]
    document = _receipt_document(
        readiness="READY",
        strict_state="UNSAFE",
        unmanaged=("NOT A SYMBOL", "XBTUSD"),
        shadow_unmanaged=("NOT A SYMBOL", "XBTUSD"),
        shadow_candidate=(hostile, *long_ids),
        shadow_blocking=("bad code", "PRICING_GAP"),
    )
    fields = _run_receipt_parser(tmp_path, document)
    assert fields["status"] == "PASS"
    # Non-token values are dropped; kept values are bounded.
    assert fields["unmanaged"] == "XBTUSD"
    assert fields["shadow_unmanaged"] == "XBTUSD"
    assert fields["shadow_blocking"] == "PRICING_GAP"
    assert hostile not in fields["shadow_candidate"]
    assert fields["shadow_candidate"].count(",") <= 19
    assert len(fields["shadow_candidate"]) <= 400
    assert " " not in fields["shadow_candidate"]


@pytest.mark.acceptance
def test_ac_026_controller_readiness_does_not_require_strict_f11():
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the controller's readiness computation no longer requires strict F11 HEALTHY; it requires an explicit READY verdict."""
    source = _receipt_parser_source()
    assert 'protection.get("state") == "HEALTHY"' not in source
    assert "shadow.get(\"ready\") is True" in source
    assert 'shadow_readiness == "READY"' in source
    assert 'doc.get("release_profile") == "EVIDENCE_SHADOW"' in source


@pytest.mark.acceptance
def test_ac_026_controller_markers_are_wired_through_the_receipt():
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the AC-026 preflight markers are emitted by the controller and consumed by the workflow."""
    for name in (
        "STRICT_F11_STATE",
        "EVIDENCE_SHADOW_READINESS",
        "EVIDENCE_SHADOW_BLOCKING_REASON_CODES",
        "EVIDENCE_SHADOW_ADVISORY_REASON_CODES",
        "EVIDENCE_SHADOW_UNMANAGED_EXPOSURES",
        "EVIDENCE_SHADOW_CANDIDATE_RECOVERABLE_INCIDENTS",
        "EVIDENCE_SHADOW_BLOCKING_INCIDENTS",
    ):
        assert f"OPIP_PROTECTION_PREFLIGHT_{name}=" in DEPLOY
        assert f"OPIP_PROTECTION_PREFLIGHT_{name}=" in _function_body()
    for output in (
        "protection_preflight_strict_f11_state=",
        "protection_preflight_evidence_shadow_readiness=",
        "protection_preflight_evidence_shadow_blocking_reason_codes=",
        "protection_preflight_evidence_shadow_advisory_reason_codes=",
        "protection_preflight_evidence_shadow_unmanaged_exposures=",
        "protection_preflight_evidence_shadow_candidate_recoverable_incidents=",
        "protection_preflight_evidence_shadow_blocking_incidents=",
    ):
        assert output in WORKFLOW


@pytest.mark.acceptance
def test_ac_026_workflow_gates_runtime_on_explicit_readiness():
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the workflow PROVEN verdict requires the explicit runtime readiness marker, not strict F11 HEALTHY."""
    assert '[[ "$RUNTIME_SHADOW_READINESS" == "READY" ]]' in WORKFLOW
    assert '[[ "$RUNTIME_PROTECTION" == "HEALTHY" ]]' not in WORKFLOW
    assert "OPIP_RELEASE_SHADOW_READINESS" in WORKFLOW


@requires_bash
@pytest.mark.acceptance
def test_ac_024_workflow_distinguishes_preflight_block_from_rollback(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-024: the receipt reports a pre-mutation protection block with the exact blocker symbols and does not call it a rollback; a genuine rollback stays a rollback."""
    blocked = "\n".join(
        [
            "OPIP_PROTECTION_PREFLIGHT=FAIL",
            "OPIP_PROTECTION_PREFLIGHT_STATE=UNAVAILABLE",
            "OPIP_PROTECTION_PREFLIGHT_COVERAGE_COMPLETE=false",
            "OPIP_PROTECTION_PREFLIGHT_ADMISSIONS_SUSPENDED=true",
            "OPIP_PROTECTION_PREFLIGHT_REASON_CODES=EXPOSURE_COVERAGE_INCOMPLETE,PROTECTION_INCIDENT_OPEN",
            "OPIP_PROTECTION_PREFLIGHT_RESOLUTION_REASON=USD/stable-quote pricing unavailable for held assets: ADA.S",
            "OPIP_PROTECTION_PREFLIGHT_UNMANAGED_EXPOSURES=ADA.S,SEI.B",
            "OPIP_PROTECTION_PREFLIGHT_UNCERTAIN_EXPOSURES=ETH2.S",
            "OPIP_PROTECTION_PREFLIGHT_SILENT_HOLDINGS=NONE",
            "OPIP_PROTECTION_PREFLIGHT_INCIDENT_HEALTH=false",
            "OPIP_CORE_DEPLOY_STATUS=NOT_STARTED",
            "OPIP_PRODUCTION_MUTATION=NOT_STARTED",
            "OPIP_LEARNING_EXPORT_STATUS=NOT_STARTED",
            "PROTECTION PREFLIGHT BLOCKED",
            "production_mutation_started=false",
            "rollback_required=false",
        ]
    )
    fields = _classify(tmp_path, blocked, 77)
    assert fields["RESULT"] == "PROTECTION PREFLIGHT BLOCKED"
    assert fields["ROLLBACK"] == "NOT REQUIRED"
    assert fields["GATE"] == "FAIL"
    assert fields["CORE_STATUS"] == "NOT_STARTED"
    assert fields["LEARNING_EXPORT_STATUS"] == "NOT_STARTED"
    assert fields["SAFE_BASELINE_ROLLBACK"] == "NOT_REQUIRED"
    assert fields["PREFLIGHT_UNMANAGED"] == "ADA.S,SEI.B"
    assert fields["PREFLIGHT_UNCERTAIN"] == "ETH2.S"
    assert fields["PREFLIGHT_SILENT"] == "NONE"
    assert fields["PREFLIGHT_INCIDENT"] == "false"

    rolled = "\n".join(
        [
            "OPIP_CORE_DEPLOY_STATUS=FAILED",
            "rollback health and paper checks passed",
            "OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS",
        ]
    )
    rolled_fields = _classify(tmp_path, rolled, 1, legacy_allowed=0)
    assert rolled_fields["RESULT"] == "ROLLED BACK"
    assert rolled_fields["ROLLBACK"] == "YES"
    assert rolled_fields["SAFE_BASELINE_ROLLBACK"] == "SUCCESS"


@requires_bash
@pytest.mark.acceptance
def test_ac_024_successful_release_classification_is_unchanged(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-024: a qualified EVIDENCE_SHADOW success is still SUCCESS when the preflight passed and no rollback is claimed."""
    from tests.test_opip_deployment_transaction_boundary_v1 import CORE_OK_LOG

    log = "\n".join(
        [
            "OPIP_PROTECTION_PREFLIGHT=PASS",
            "OPIP_PROTECTION_PREFLIGHT_STATE=HEALTHY",
            "OPIP_PROTECTION_PREFLIGHT_UNMANAGED_EXPOSURES=NONE",
            "OPIP_PROTECTION_PREFLIGHT_INCIDENT_HEALTH=true",
            CORE_OK_LOG,
            "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
            "OPIP_LEARNING_READINESS=READY",
            "O'Pip deployment succeeded",
        ]
    )
    fields = _classify(tmp_path, log, 0, legacy_allowed=0)
    assert fields["RESULT"] == "SUCCESS"
    assert fields["ROLLBACK"] == "NO"
    assert fields["GATE"] == "PASS"
    assert fields["PREFLIGHT_STATUS"] == "PASS"
    assert RELEASE_SHA


# ---------------------------------------------------------------------------
# Shell-path regressions for the preflight exit-code capture.
#
# The Python builder tests above cannot catch a bash-level defect inside
# run_protection_preflight itself, so these execute REAL extracted bash.
# ---------------------------------------------------------------------------


def _refusal_block() -> str:
    start = DEPLOY.index("if ! run_protection_preflight; then")
    end = DEPLOY.index("\nfi\n", start) + len("\nfi\n")
    return DEPLOY[start:end]


def _docker_rc_capture_block() -> str:
    """The real rc-capture block of run_protection_preflight, verbatim."""
    body = _function_body()
    start = body.index("  rc=1\n")
    end = body.index("\n  fi\n", start) + len("\n  fi\n")
    return body[start:end]


def _python_for_bash() -> str:
    return shutil.which("python3") or sys.executable


@pytest.mark.acceptance
def test_ac_024_docker_exit_code_capture_is_explicit(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-024: the preflight captures the container exit code explicitly on success and failure, so a zero exit yields rc=0 instead of the stale rc=1 that would misclassify a HEALTHY preflight as FAIL."""
    block = _docker_rc_capture_block()
    code = _without_comments(block)
    assert "if docker run" in block
    assert "|| rc=$?" not in code
    assert "rc=$?" in code
    assert "rc=0" in code

    if _bash() is None:
        pytest.skip("bash is not available in this environment")

    # Directories come from Python so the script below contains NO external
    # command and therefore needs no fork at all.
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app-root").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app-root" / ".env").write_text("", encoding="utf-8")

    script = "\n".join(
        [
            "set -Eeuo pipefail",
            f"root={tmp_path.as_posix()!r}",
            'candidate_app="$root/app"',
            'APP_ROOT="$root/app-root"',
            "image=sha256:fake",
            f"TARGET_SHA={SHA!r}",
            'receipt="$root/receipt.json"',
            "PAYLOAD='PROTECTION_PROVEN'",
            "DOCKER_RC=0",
            'docker() { printf "%s" "$PAYLOAD" ; return "$DOCKER_RC" ; }',
            block,
            # read/printf are builtins, so this stays fork-free: the capture
            # block must run even where bash cannot fork.
            'FIRST=""',
            'IFS= read -r FIRST < "$receipt" || true',
            'printf "RC=%s FIRST=%s\\n" "$rc" "$FIRST"',
            "",
        ]
    )
    path = tmp_path / "capture.sh"
    import subprocess

    for docker_rc in (0, 7):
        path.write_text(
            script.replace("DOCKER_RC=0", f"DOCKER_RC={docker_rc}"), encoding="utf-8"
        )
        proc = subprocess.run(
            [_bash(), str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        if _is_fork_failure(proc):
            pytest.skip("bash cannot fork reliably in this environment")
        assert proc.returncode == 0, proc.stderr
        assert f"RC={docker_rc}" in proc.stdout, proc.stdout + proc.stderr
        assert "FIRST=PROTECTION_PROVEN" in proc.stdout, proc.stdout


def _preflight_harness(tmp: Path, *, docker_rc: int, payload: str | None):
    """Run the real function with a stub docker that writes ``payload``.

    ``payload=None`` simulates a container that exits without writing a receipt.
    Every other external effect is stubbed and recorded, so this measures only the
    preflight's own exit-code handling and the real refusal control flow.
    """
    state = tmp / "state"
    app_root = tmp / "app"
    (app_root / "data").mkdir(parents=True, exist_ok=True)
    (app_root / ".env").write_text("", encoding="utf-8")
    fixture = tmp / "receipt-fixture.json"
    fixture.write_text(payload if payload is not None else "", encoding="utf-8")
    log = tmp / "calls.log"
    candidate_app = state / "protection-candidate" / "OHM-Trade-Agent-v1" / "app"

    script = "\n".join(
        [
            "set -Eeuo pipefail",
            f"TARGET_SHA={SHA!r}",
            f"STATE_DIR={state.as_posix()!r}",
            f"APP_ROOT={app_root.as_posix()!r}",
            "REPO_OWNER=owner",
            f"PYTHON_BIN={_python_for_bash()!r}",
            f"LOG={log.as_posix()!r}",
            f"FAKE_RECEIPT_JSON={fixture.as_posix()!r}",
            f"FAKE_CANDIDATE_APP={candidate_app.as_posix()!r}",
            f"FAKE_DOCKER_RC={int(docker_rc)}",
            "GIT=(git)",
            ': > "$LOG"',
            'git() { printf "git %s\\n" "$*" >>"$LOG"; '
            'case "$*" in *"worktree add"*) mkdir -p "$FAKE_CANDIDATE_APP" ;; esac; '
            "return 0; }",
            # chown is host-specific inside a sandbox; production uses the real
            # one. The invariant under test is the exit-code capture.
            "chown() { return 0; }",
            'cleanup_snapshot() { printf "cleanup_snapshot\\n" >>"$LOG"; }',
            "docker() {",
            '  printf "docker %s\\n" "$*" >>"$LOG"',
            '  case "$*" in',
            '    inspect*) printf "%s\\n" "sha256:fake" ; return 0 ;;',
            '    run*) cat "$FAKE_RECEIPT_JSON" ; return "$FAKE_DOCKER_RC" ;;',
            "  esac",
            "  return 0",
            "}",
            "run_protection_preflight() {" + _function_body(),
            # Return code of the real function, captured without errexit.
            "set +e",
            "run_protection_preflight",
            "FUNC_RET=$?",
            "set -e",
            'printf "FUNC_RET=%s\\n" "$FUNC_RET"',
            # The REAL pre-mutation refusal block. On FAIL it must exit 77 and
            # never reach the marker below.
            _refusal_block(),
            'echo "MUTATION_REACHED"',
            "",
        ]
    )
    return _run_bash_script(script, str(tmp), name="preflight.sh", timeout=120)


def _healthy_payload() -> str:
    import json

    document = _document(_report())
    assert document["verdict"]["ready"] is True
    return json.dumps(document)


def _blocked_payload() -> str:
    import json

    document = _document(
        _report(
            state="UNAVAILABLE",
            admissions_suspended=True,
            coverage_complete=False,
            reason_codes=["EXPOSURE_COVERAGE_INCOMPLETE"],
            resolution_reason="pricing unavailable for held assets: ADA.Z",
            unmanaged_exposures=["ADA.Z"],
        )
    )
    assert document["verdict"]["ready"] is False
    return json.dumps(document)


@pytest.mark.acceptance
def test_ac_024_shell_path_preflight_uses_the_docker_exit_code(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-024: the real run_protection_preflight captures the container exit code on success and failure, so a HEALTHY preflight returns 0 and emits PASS while a nonzero container exit still refuses before mutation."""
    if _bash() is None:
        pytest.skip("bash is not available in this environment")

    cases = [
        ("healthy_rc0", 0, _healthy_payload(), 0, "PASS"),
        ("healthy_doc_but_rc_nonzero", 3, _healthy_payload(), 77, "FAIL"),
        ("blocked_doc_rc76", 76, _blocked_payload(), 77, "FAIL"),
        ("empty_receipt_rc0", 0, None, 77, "FAIL"),
    ]
    for name, docker_rc, payload, expected_rc, expected_status in cases:
        case_dir = tmp_path / name
        case_dir.mkdir(parents=True, exist_ok=True)
        proc = _preflight_harness(case_dir, docker_rc=docker_rc, payload=payload)
        if _is_fork_failure(proc):
            pytest.skip("bash cannot fork reliably in this environment")
        combined = proc.stdout + proc.stderr
        assert f"OPIP_PROTECTION_PREFLIGHT={expected_status}" in proc.stdout, combined
        assert f"FUNC_RET={expected_rc}" in proc.stdout, combined
        assert proc.returncode == expected_rc, combined
        if expected_status == "PASS":
            # A genuine HEALTHY preflight must not be misclassified, and the
            # release continues past the boundary.
            assert "PROTECTION PREFLIGHT BLOCKED" not in combined
            assert "MUTATION_REACHED" in proc.stdout
        else:
            assert "OPIP_PROTECTION_PREFLIGHT_ABORT=REFUSED_BEFORE_MUTATION" in proc.stderr
            assert "production_mutation_started=false" in proc.stdout
            assert "OPIP_PRODUCTION_MUTATION=NOT_STARTED" in proc.stdout
            assert "OPIP_SAFE_BASELINE_UNCHANGED=true" in proc.stdout
            assert "MUTATION_REACHED" not in proc.stdout
            assert "cleanup_snapshot" in (case_dir / "calls.log").read_text(
                encoding="utf-8"
            )
