"""AC-024: read-only protection preflight refuses a release before mutation.

The preflight reuses the existing protection report. These tests prove the
ready/blocked contract, the deploy-script boundary, and the workflow receipt.
They do not call Kraken or mutate a registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.jobs.preflight_protection_health import (
    build_preflight_document,
    diagnostic_classes,
)
from app.jobs.report_protection_health import build_report as _build_report
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
SHA = "9da2fc4a70340b5a6cf158e539ef55ffa25971f8"
WHEN = "2026-10-08T02:30:00Z"


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


def _success_path() -> str:
    marker = "# AC-024 candidate checkout precedes protection preflight."
    start = DEPLOY.index(marker)
    trap = DEPLOY.index("\ntrap rollback ERR\n", start)
    return DEPLOY[start:trap]


@pytest.mark.acceptance
def test_ac_024_healthy_preflight_is_ready():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: only a HEALTHY complete non-suspended protection decision is ready to cross the mutable release boundary."""
    document = _document(_report())
    assert document["read_only"] is True
    assert document["verdict"]["ready"] is True
    assert document["protection"]["reason_codes"] == ["PROTECTION_PROVEN"]
    assert document["incidents"]["open_incident_count"] is None
    assert document["incidents"]["open_incidents"] is None


@pytest.mark.acceptance
@pytest.mark.parametrize(
    ("report", "incidents", "code"),
    [
        (
            {
                "state": "UNAVAILABLE",
                "admissions_suspended": True,
                "coverage_complete": False,
                "reason_codes": ["EXPOSURE_COVERAGE_INCOMPLETE", "UNAVAILABLE"],
                "resolution_reason": "USD/stable-quote pricing unavailable for held assets: ADA.Z",
            },
            True,
            "EXPOSURE_COVERAGE_INCOMPLETE",
        ),
        (
            {
                "state": "UNSAFE",
                "admissions_suspended": True,
                "coverage_complete": True,
                "reason_codes": ["UNMANAGED_EXPOSURE_REQUIRES_REVIEW"],
                "unmanaged_exposures": ["ADAUSD"],
            },
            True,
            "UNMANAGED_EXPOSURE_REQUIRES_REVIEW",
        ),
        (
            {
                "state": "UNAVAILABLE",
                "admissions_suspended": True,
                "coverage_complete": True,
                "reason_codes": ["PROTECTION_INCIDENT_OPEN"],
            },
            False,
            "PROTECTION_INCIDENT_OPEN",
        ),
        (
            {
                "state": "UNAVAILABLE",
                "admissions_suspended": True,
                "coverage_complete": False,
                "reason_codes": ["UNAVAILABLE"],
            },
            None,
            "UNAVAILABLE",
        ),
        (
            {
                "state": "UNSAFE",
                "admissions_suspended": True,
                "coverage_complete": True,
                "reason_codes": ["SILENT_HOLDING_UNPROTECTED_EXPOSURE"],
                "silent_holdings": ["BTCUSD"],
            },
            True,
            "SILENT_HOLDING_UNPROTECTED_EXPOSURE",
        ),
    ],
)
def test_ac_024_non_healthy_protection_is_not_ready(report, incidents, code):
    """ATDD-RELEASE-PIPELINE-v1/AC-024: incomplete coverage, unmanaged exposure, an open incident, UNAVAILABLE and UNSAFE all refuse before mutation."""
    document = _document(_report(**report))
    assert document["verdict"]["ready"] is False
    assert code in document["protection"]["reason_codes"]
    assert document["protection"]["admissions_suspended"] is True


@pytest.mark.acceptance
def test_ac_024_preflight_uses_the_existing_read_only_report():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: candidate preflight calls the existing non-mutating protection report and does not recover, quarantine, or trade."""
    assert "build_report()" in PREFLIGHT
    assert "protection_incidents_healthy()" in PREFLIGHT
    assert "read_active_trades_without_mutation" in REPORT
    assert "minimum_unmanaged_notional_usd=0.0" in REPORT
    assert "minimum_unmanaged_notional_usd" not in PREFLIGHT
    for forbidden in (
        "observe_recovery",
        "quarantine",
        "AddOrder",
        "add_order",
        "cancel_order",
        "create_order",
    ):
        assert forbidden not in PREFLIGHT
    assert _build_report.__module__ == "app.jobs.report_protection_health"


@pytest.mark.acceptance
def test_ac_024_candidate_preflight_is_before_the_first_mutation():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: candidate checkout and the read-only preflight finish before paper stop, image build, scheduler reconcile, writer activation, or last-good-sha."""
    section = _success_path()
    assert section.index("checkout -f main") < section.index("run_protection_preflight")
    assert "stop_paper_stack" not in section
    assert "docker compose" not in section
    assert "LAST_GOOD_FILE" not in section
    assert "reconcile-scheduler" not in section
    after = DEPLOY[DEPLOY.index("\ntrap rollback ERR\n") :]
    assert after.index("stop_paper_stack") < after.index(
        'docker compose build --build-arg "OPIP_RELEASE_SHA=$TARGET_SHA"'
    )
    assert after.index(
        "docker compose up -d --remove-orphans opip-canonical-writer"
    ) < after.index('bash "$SCHEDULER_RECONCILE"')
    function = DEPLOY.split("run_protection_preflight() {", 1)[1].split(
        "\n# AC-024 candidate checkout", 1
    )[0]
    assert '-v "$APP_ROOT/app:/app/app:ro"' in function
    assert '-v "$APP_ROOT/data:/app/data:ro"' in function
    assert "python -m app.jobs.preflight_protection_health" in function
    assert "--read-only" in function
    assert "docker compose" not in function
    assert "-p " not in function
    assert "stop_paper_stack" not in function


@pytest.mark.acceptance
def test_ac_024_preflight_block_does_not_claim_rollback():
    """ATDD-RELEASE-PIPELINE-v1/AC-024: a preflight refusal reports mutation not started and does not invoke rollback."""
    function = DEPLOY.split("run_protection_preflight() {", 1)[1].split(
        "\n# AC-024 candidate checkout", 1
    )[0]
    assert "PROTECTION PREFLIGHT BLOCKED" in function
    assert "production_mutation_started=false" in function
    assert "rollback_required=false" in function
    assert "OPIP_PRODUCTION_MUTATION=NOT_STARTED" in function
    assert "OPIP_CORE_DEPLOY_STATUS=NOT_STARTED" in function
    assert "rollback()" not in function
    boundary = DEPLOY.index("if ! run_protection_preflight; then")
    trap = DEPLOY.index("\ntrap rollback ERR\n")
    assert boundary < trap


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
    """ATDD-RELEASE-PIPELINE-v1/AC-024: an unpriced held asset remains a coverage failure and stays visible to the owner."""
    document = _document(
        _report(
            state="UNAVAILABLE",
            admissions_suspended=True,
            coverage_complete=False,
            reason_codes=["EXPOSURE_COVERAGE_INCOMPLETE", "UNMANAGED_EXPOSURE_REQUIRES_REVIEW"],
            resolution_reason="USD/stable-quote pricing unavailable for held assets: ADA.Z",
            unmanaged_exposures=["ADA.Z"],
        )
    )
    assert document["verdict"]["ready"] is False
    assert "ADA.Z" in document["protection"]["unmanaged_exposures"]
    assert "PRICING_OR_COVERAGE" in diagnostic_classes(document["protection"])
    assert "UNMANAGED_EXPOSURE" in diagnostic_classes(document["protection"])


@requires_bash
@pytest.mark.acceptance
def test_ac_024_workflow_distinguishes_preflight_block_from_rollback(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-024: the receipt reports a pre-mutation protection block without calling it a rollback, and a genuine rollback stays a rollback."""
    blocked = "\n".join(
        [
            "OPIP_PROTECTION_PREFLIGHT=FAIL",
            "OPIP_PROTECTION_PREFLIGHT_STATE=UNAVAILABLE",
            "OPIP_PROTECTION_PREFLIGHT_COVERAGE_COMPLETE=false",
            "OPIP_PROTECTION_PREFLIGHT_ADMISSIONS_SUSPENDED=true",
            "OPIP_PROTECTION_PREFLIGHT_REASON_CODES=EXPOSURE_COVERAGE_INCOMPLETE,PROTECTION_INCIDENT_OPEN",
            "OPIP_PROTECTION_PREFLIGHT_RESOLUTION_REASON=USD/stable-quote pricing unavailable for held assets: ADA.S",
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
