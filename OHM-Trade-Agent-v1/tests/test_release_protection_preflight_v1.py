"""AC-024: read-only protection preflight refuses a release before mutation.

The preflight reuses the existing protection report, materializes the candidate
SHA into a temporary tree so the live checkout is never changed before PASS, and
surfaces the exact blockers through bounded receipt markers. These tests do not
call Kraken and do not mutate a registry.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from app.jobs import preflight_protection_health as preflight
from app.jobs.preflight_protection_health import (
    build_preflight_document,
    format_incident_health,
    format_marker_symbols,
)
from app.jobs.report_protection_health import build_report as _build_report
from app.services.protection_health import evaluate_protection_health
from tests.test_opip_deployment_transaction_boundary_v1 import (
    RELEASE_SHA,
    _classify,
    requires_bash,
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


def _document(report, incidents=True):
    return build_preflight_document(
        report,
        incidents_healthy=incidents,
        candidate_sha=SHA,
        release_profile="EVIDENCE_SHADOW",
        checked_at_utc=WHEN,
    )


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
    """ATDD-RELEASE-PIPELINE-v1/AC-024: only a HEALTHY complete non-suspended protection decision is ready to cross the mutable release boundary."""
    document = _document(_report())
    assert document["read_only"] is True
    assert document["verdict"]["ready"] is True
    assert document["protection"]["reason_codes"] == ["PROTECTION_PROVEN"]
    assert document["incidents"]["open_incident_count"] is None
    assert document["incidents"]["open_incidents"] is None
    assert document["markers"]["incident_health"] == "true"


@pytest.mark.acceptance
@pytest.mark.parametrize(
    ("report", "incidents", "code", "expected_health"),
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
            "EXPOSURE_COVERAGE_INCOMPLETE",
            "true",
        ),
        (
            _report(
                state="UNSAFE",
                admissions_suspended=True,
                reason_codes=["UNMANAGED_EXPOSURE_REQUIRES_REVIEW"],
                unmanaged_exposures=["ADAUSD"],
            ),
            True,
            "UNMANAGED_EXPOSURE_REQUIRES_REVIEW",
            "true",
        ),
        (
            _report(
                state="UNAVAILABLE",
                admissions_suspended=True,
                reason_codes=["PROTECTION_INCIDENT_OPEN"],
            ),
            False,
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
            "SILENT_HOLDING_UNPROTECTED_EXPOSURE",
            "true",
        ),
    ],
)
def test_ac_024_non_healthy_protection_is_not_ready(
    report, incidents, code, expected_health
):
    """ATDD-RELEASE-PIPELINE-v1/AC-024: incomplete coverage, unmanaged exposure, an open incident, UNAVAILABLE and UNSAFE all refuse before mutation, and the incident verdict is carried through."""
    document = _document(report, incidents)
    assert document["verdict"]["ready"] is False
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
    import app.jobs.report_protection_health as reporter

    calls = {"count": 0}

    class FakeResolution:
        exposures: tuple = ()
        coverage_complete = True
        reason = ""

    def fake_incidents():
        calls["count"] += 1
        return False

    monkeypatch.setattr(reporter, "protection_incidents_healthy", fake_incidents)
    monkeypatch.setattr(
        reporter, "_read_only_resolver", lambda: types.SimpleNamespace(resolve=FakeResolution)
    )
    document = preflight.evaluate_preflight(
        candidate_sha=SHA, release_profile="EVIDENCE_SHADOW"
    )
    assert calls["count"] == 1
    assert document["incidents"]["health"] is False
    assert "PROTECTION_INCIDENT_OPEN" in document["protection"]["reason_codes"]
    assert document["markers"]["incident_health"] == "false"


@pytest.mark.acceptance
def test_ac_024_preflight_uses_the_existing_read_only_report():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: candidate preflight calls the shared non-mutating protection report, observes incidents once, and never recovers, repairs, or trades."""
    assert "build_report_with_incidents()" in PREFLIGHT
    assert "protection_incidents_healthy" not in PREFLIGHT
    assert "build_report_with_incidents" in REPORT
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
