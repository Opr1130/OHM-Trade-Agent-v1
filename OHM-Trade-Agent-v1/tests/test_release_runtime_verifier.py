from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys

import pytest

from app.services import release_runtime_verifier
from app.services.release_runtime_verifier import _new_evidence_is_valid

pytestmark = pytest.mark.acceptance


def _snapshot(cutoff: datetime, *, grid_seconds: int = 60):
    return SimpleNamespace(
        snapshot_id=f"snapshot-{cutoff.isoformat()}",
        instrument_version_id="instrument-v1",
        evaluation_cutoff=cutoff,
        evaluation_grid_seconds=grid_seconds,
    )


def _evidence(cutoff: datetime, source_cutoff: datetime | None = None):
    return SimpleNamespace(
        instrument_version_id="instrument-v1",
        evaluation_time=cutoff,
        source_cutoff=source_cutoff or cutoff,
    )


def test_runtime_evidence_requires_consecutive_fresh_snapshots_and_matching_fev():
    """ATDD-RELEASE-PIPELINE-v1/AC-010: fresh sequential snapshots need matching prospective F5 evidence."""
    ready_after = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    first_cutoff = datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)
    second_cutoff = first_cutoff + timedelta(minutes=1)
    now = second_cutoff + timedelta(seconds=30)

    passed, report = _new_evidence_is_valid(
        [_snapshot(first_cutoff), _snapshot(second_cutoff)],
        [_evidence(second_cutoff, second_cutoff - timedelta(seconds=10))],
        ready_after=ready_after,
        now=now,
    )

    assert passed is True
    assert report["consecutive_60s_snapshots"] is True
    assert report["feasibility_matches_fresh_snapshot"] is True


def test_runtime_evidence_rejects_backfill_gaps_and_late_source_cutoffs():
    """ATDD-RELEASE-PIPELINE-v1/AC-010: gaps and late source timestamps fail closed."""
    ready_after = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    first_cutoff = datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)
    second_cutoff = first_cutoff + timedelta(minutes=2)
    now = second_cutoff + timedelta(seconds=30)

    passed, report = _new_evidence_is_valid(
        [_snapshot(first_cutoff), _snapshot(second_cutoff)],
        [_evidence(second_cutoff, second_cutoff + timedelta(seconds=1))],
        ready_after=ready_after,
        now=now,
    )

    assert passed is False
    assert report["consecutive_60s_snapshots"] is False
    assert report["feasibility_matches_fresh_snapshot"] is False


@pytest.mark.parametrize(
    "older_grid,newer_grid,gap_seconds",
    [
        (30, 30, 30),
        (60, 30, 60),
        (30, 60, 60),
    ],
)
def test_runtime_evidence_rejects_non_60_second_snapshot_grid(
    older_grid: int,
    newer_grid: int,
    gap_seconds: int,
) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-010: runtime evidence requires a true 60-second grid."""
    ready_after = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    first_cutoff = datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)
    second_cutoff = first_cutoff + timedelta(seconds=gap_seconds)
    now = second_cutoff + timedelta(seconds=30)

    passed, report = _new_evidence_is_valid(
        [
            _snapshot(first_cutoff, grid_seconds=older_grid),
            _snapshot(second_cutoff, grid_seconds=newer_grid),
        ],
        [_evidence(second_cutoff)],
        ready_after=ready_after,
        now=now,
    )

    assert passed is False
    assert report["consecutive_60s_snapshots"] is False


def test_runtime_evidence_rejects_stale_snapshots_and_unmatched_fev():
    """ATDD-RELEASE-PIPELINE-v1/AC-010: stale or unmatched evidence fails closed."""
    ready_after = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    first_cutoff = datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)
    second_cutoff = first_cutoff + timedelta(minutes=1)
    now = second_cutoff + timedelta(minutes=4)

    passed, report = _new_evidence_is_valid(
        [_snapshot(first_cutoff), _snapshot(second_cutoff)],
        [_evidence(first_cutoff)],
        ready_after=ready_after,
        now=now,
    )

    assert passed is False
    assert report["fresh_instrument_count"] == 0


def test_runtime_evidence_rejects_future_timestamps():
    """ATDD-RELEASE-PIPELINE-v1/AC-010: prospective evidence cannot be future-dated."""
    ready_after = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    first_cutoff = datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)
    second_cutoff = first_cutoff + timedelta(minutes=1)
    now = second_cutoff - timedelta(seconds=1)

    passed, report = _new_evidence_is_valid(
        [_snapshot(first_cutoff), _snapshot(second_cutoff)],
        [_evidence(second_cutoff)],
        ready_after=ready_after,
        now=now,
    )

    assert passed is False
    assert report["fresh_instrument_count"] == 1
    assert report["consecutive_60s_snapshots"] is False


def test_runtime_posture_requires_profile_notional(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-010: runtime posture must include the fixed profile notional."""
    from app.services.release_profiles import resolve_release_profile

    expected = resolve_release_profile("EVIDENCE_SHADOW")["allowed_modes"]
    for key, value in expected.items():
        if key != "OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD":
            monkeypatch.setenv(key, value)
    monkeypatch.delenv("OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD", raising=False)

    with pytest.raises(ValueError, match="runtime modes do not match"):
        release_runtime_verifier._verify_live_posture("EVIDENCE_SHADOW")

    monkeypatch.setenv("OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD", "5000.0")
    with pytest.raises(ValueError, match="runtime modes do not match"):
        release_runtime_verifier._verify_live_posture("EVIDENCE_SHADOW")


def test_safe_baseline_is_not_a_deploy_candidate(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-009: SAFE_BASELINE is rollback-only."""
    monkeypatch.setenv("OPIP_RELEASE_PROFILE", "SAFE_BASELINE")
    with pytest.raises(ValueError, match="SAFE_BASELINE is rollback-only"):
        release_runtime_verifier.verify_release_runtime(
            expected_sha="a" * 40,
            baseline={},
            ready_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_runtime_verification_rejects_blocked_target_paper(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-001: TARGET_PAPER remains blocked."""
    monkeypatch.setenv("OPIP_RELEASE_PROFILE", "TARGET_PAPER")

    with pytest.raises(ValueError, match="not active"):
        release_runtime_verifier.verify_release_runtime(
            expected_sha="a" * 40,
            baseline={},
            ready_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_posture_failure_receipt_is_actionable(capsys):
    """ATDD-RELEASE-PIPELINE-v1/AC-022: a protection posture failure reports the sanitized resolver reason with proven evidence."""
    error = release_runtime_verifier.ReleaseRuntimePostureError(
        "read-only protection health is not HEALTHY",
        reason_codes=("UNAVAILABLE", "EXPOSURE_COVERAGE_INCOMPLETE"),
        evidence={"feature_snapshot_count": 4, "consecutive_60s_snapshots": True},
        protection_reason="active trade registry unavailable:\nread failed\x00",
    )

    release_runtime_verifier._emit_failure_diagnostics(error)

    out = capsys.readouterr().out
    assert "OPIP_RELEASE_RUNTIME_FAILURE_STAGE=LIVE_POSTURE" in out
    assert "OPIP_RELEASE_RUNTIME_FAILURE_REASON=read-only protection health is not HEALTHY" in out
    assert "OPIP_RELEASE_RUNTIME_FAILURE_CODES=UNAVAILABLE,EXPOSURE_COVERAGE_INCOMPLETE" in out
    assert (
        "OPIP_RELEASE_RUNTIME_PROTECTION_REASON="
        "active trade registry unavailable: read failed"
    ) in out
    assert "OPIP_RELEASE_FEATURE_SNAPSHOT_COUNT=4" in out
    assert "OBSERVED_EVIDENCE=NONE" not in out


def test_non_protection_posture_failure_does_not_emit_protection_reason(capsys):
    """ATDD-RELEASE-PIPELINE-v1/AC-022: non-protection posture failures never fabricate a protection-resolution reason."""
    error = release_runtime_verifier.ReleaseRuntimePostureError(
        "runtime modes do not match the allowlisted profile"
    )

    release_runtime_verifier._emit_failure_diagnostics(error)

    out = capsys.readouterr().out
    assert "OPIP_RELEASE_RUNTIME_FAILURE_STAGE=LIVE_POSTURE" in out
    assert "OPIP_RELEASE_RUNTIME_PROTECTION_REASON=" not in out


# ---------------------------------------------------------------------------
# AC-026 Phase 4: runtime verification uses the canonical AC-026 readiness
# composition, so an advisory strict-F11 blocker never causes a false
# post-mutation rollback while every genuine current blocker still fails.
# ---------------------------------------------------------------------------


def _unmanaged_exposure(symbol="XBTUSD"):
    return SimpleNamespace(status="VERIFIED_UNMANAGED", symbol=symbol, trade=None)


def _set_modes(monkeypatch, profile="EVIDENCE_SHADOW"):
    from app.services.release_profiles import resolve_release_profile

    for key, value in resolve_release_profile(profile)["allowed_modes"].items():
        monkeypatch.setenv(key, value)


def _stub_spine_and_settings(monkeypatch, *, mode, reason):
    import app.services.target_spine_cycle as spine

    monkeypatch.setattr(
        spine,
        "run_target_spine_cycle",
        lambda settings: SimpleNamespace(
            mode=mode, inert=True, handoffs_built=0, errors=0, reason=reason
        ),
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace())


def _stub_observation(monkeypatch, *, strict_state, strict_codes, shadow):
    import app.jobs.report_protection_health as reporter

    observation = SimpleNamespace(
        report={
            "state": strict_state,
            "admissions_suspended": strict_state != "HEALTHY",
            "coverage_complete": True,
            "reason_codes": list(strict_codes),
        },
        incidents_healthy=True,
        shadow=shadow,
    )
    monkeypatch.setattr(reporter, "build_observation", lambda: observation)


def _incident(scope):
    return {"scope": scope, "incident_key": f"SYSTEM_HEALTH:{scope}"}


def test_ac_026_runtime_verifier_accepts_advisory_strict_f11(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: a legitimate shadow deployment whose strict F11 stays non-HEALTHY only for an advisory unmanaged holding PASSES runtime verification; strict F11 stays visible but is not the gate."""
    import app.services.target_spine_cycle as spine
    from app.services.evidence_shadow_readiness import evaluate_evidence_shadow_readiness

    _set_modes(monkeypatch)
    _stub_spine_and_settings(monkeypatch, mode="shadow", reason=spine.REASON_NO_SNAPSHOT_SOURCE)
    shadow = evaluate_evidence_shadow_readiness([_unmanaged_exposure()], coverage_complete=True)
    assert shadow.ready is True
    _stub_observation(
        monkeypatch,
        strict_state="UNSAFE",
        strict_codes=("UNMANAGED_EXPOSURE_REQUIRES_REVIEW",),
        shadow=shadow,
    )
    posture = release_runtime_verifier._verify_live_posture("EVIDENCE_SHADOW")
    assert posture["shadow_readiness"] == "READY"
    assert posture["protection"] == "UNSAFE"
    assert posture["protection_ready"] is False
    assert posture["target_authority"] == "ABSENT"


def test_ac_026_runtime_verifier_accepts_candidate_recoverable_incidents(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: durable coverage incidents whose canonical current predicate is proven keep strict F11 non-HEALTHY but do not block EVIDENCE_SHADOW runtime verification."""
    import app.services.target_spine_cycle as spine
    from app.services.evidence_shadow_readiness import evaluate_evidence_shadow_readiness
    from app.services.system_incidents import SystemIncidentScope

    _set_modes(monkeypatch)
    _stub_spine_and_settings(monkeypatch, mode="shadow", reason=spine.REASON_NO_SNAPSHOT_SOURCE)
    pricing = SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value
    position = SystemIncidentScope.KRAKEN_POSITION_VERIFICATION.value
    shadow = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=True,
        open_incidents=[_incident(pricing), _incident(position)],
        current_degraded_scopes=frozenset(),
    )
    assert shadow.ready is True
    _stub_observation(
        monkeypatch,
        strict_state="UNAVAILABLE",
        strict_codes=("PROTECTION_INCIDENT_OPEN",),
        shadow=shadow,
    )
    posture = release_runtime_verifier._verify_live_posture("EVIDENCE_SHADOW")
    assert posture["shadow_readiness"] == "READY"
    assert posture["protection"] == "UNAVAILABLE"


@pytest.mark.parametrize(
    "build",
    [
        # current pricing degradation (coverage incomplete + relevant incident)
        lambda: (
            [],
            False,
            [_incident("KRAKEN:HELD_ASSET_PRICING")],
            frozenset({"KRAKEN:HELD_ASSET_PRICING"}),
        ),
        # current position/account verification degradation
        lambda: (
            [],
            True,
            [_incident("KRAKEN:POSITION_VERIFICATION")],
            frozenset({"KRAKEN:POSITION_VERIFICATION"}),
        ),
        # auth failure
        lambda: ([], True, [_incident("KRAKEN:READ_ONLY_AUTH")], frozenset()),
        # connectivity
        lambda: ([], True, [_incident("KRAKEN:PUBLIC_CONNECTIVITY")], frozenset()),
        # rate limit
        lambda: ([], True, [_incident("KRAKEN:RATE_LIMIT")], frozenset()),
        # unknown incident
        lambda: ([], True, [_incident("SOMETHING:ELSE")], frozenset()),
        # malformed incident
        lambda: ([], True, [{"incident_key": "SYSTEM_HEALTH:?:"}], frozenset()),
    ],
)
def test_ac_026_runtime_verifier_blocks_real_current_blockers(monkeypatch, build):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: every genuine current blocker fails runtime verification after mutation."""
    import app.services.target_spine_cycle as spine
    from app.services.evidence_shadow_readiness import evaluate_evidence_shadow_readiness

    _set_modes(monkeypatch)
    _stub_spine_and_settings(monkeypatch, mode="shadow", reason=spine.REASON_NO_SNAPSHOT_SOURCE)
    exposures, coverage, incidents, degraded = build()
    shadow = evaluate_evidence_shadow_readiness(
        exposures,
        coverage_complete=coverage,
        open_incidents=incidents,
        current_degraded_scopes=degraded,
    )
    assert shadow.ready is False
    _stub_observation(
        monkeypatch,
        strict_state="HEALTHY",
        strict_codes=("PROTECTION_PROVEN",),
        shadow=shadow,
    )
    with pytest.raises(release_runtime_verifier.ReleaseRuntimePostureError):
        release_runtime_verifier._verify_live_posture("EVIDENCE_SHADOW")


def test_ac_026_runtime_verifier_blocks_unreadable_incident_evidence(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: unreadable incident evidence fails runtime verification and is never treated as no incident."""
    import app.services.target_spine_cycle as spine
    from app.services.evidence_shadow_readiness import evaluate_evidence_shadow_readiness
    from app.services.evidence_shadow_readiness import BLOCK_INCIDENT_UNREADABLE

    _set_modes(monkeypatch)
    _stub_spine_and_settings(monkeypatch, mode="shadow", reason=spine.REASON_NO_SNAPSHOT_SOURCE)
    shadow = evaluate_evidence_shadow_readiness(
        [], coverage_complete=True, open_incidents=None
    )
    assert shadow.ready is False
    _stub_observation(
        monkeypatch,
        strict_state="HEALTHY",
        strict_codes=("PROTECTION_PROVEN",),
        shadow=shadow,
    )
    with pytest.raises(release_runtime_verifier.ReleaseRuntimePostureError) as excinfo:
        release_runtime_verifier._verify_live_posture("EVIDENCE_SHADOW")
    assert BLOCK_INCIDENT_UNREADABLE in excinfo.value.reason_codes


def test_ac_026_runtime_verifier_keeps_strict_gate_for_non_shadow_profiles(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: non-EVIDENCE_SHADOW profiles keep strict F11 as the gate, so shadow readiness never loosens another profile."""
    import app.services.target_spine_cycle as spine
    from app.services.evidence_shadow_readiness import evaluate_evidence_shadow_readiness

    _set_modes(monkeypatch, "SAFE_BASELINE")
    _stub_spine_and_settings(monkeypatch, mode="off", reason=spine.REASON_MODE_OFF)
    shadow = evaluate_evidence_shadow_readiness([_unmanaged_exposure()], coverage_complete=True)
    assert shadow.ready is True
    _stub_observation(
        monkeypatch,
        strict_state="UNSAFE",
        strict_codes=("UNMANAGED_EXPOSURE_REQUIRES_REVIEW",),
        shadow=shadow,
    )
    with pytest.raises(release_runtime_verifier.ReleaseRuntimePostureError, match="not HEALTHY"):
        release_runtime_verifier._verify_live_posture("SAFE_BASELINE")


def test_ac_026_runtime_verifier_pass_receipt_emits_readiness_and_strict_state(monkeypatch, capsys):
    """ATDD-RELEASE-PIPELINE-v1/AC-026: the PASS receipt emits the explicit readiness verdict and the preserved strict F11 state, and the workflow gates on the former."""
    result = {
        "status": "PASS",
        "sha": "a" * 40,
        "verified_at": "2026-01-01T00:00:00Z",
        "evidence": {"evidence_capture": "PASS"},
        "profile": "EVIDENCE_SHADOW",
        "protection": "UNSAFE",
        "protection_ready": False,
        "shadow_readiness": "READY",
        "shadow_blocking_reason_codes": [],
        "target_authority": "ABSENT",
    }
    monkeypatch.setattr(
        release_runtime_verifier, "verify_release_runtime", lambda **kwargs: result
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verifier",
            "--expected-sha",
            "a" * 40,
            "--baseline-json",
            "{}",
            "--ready-after",
            "2026-01-01T00:00:00Z",
        ],
    )
    release_runtime_verifier.main()
    out = capsys.readouterr().out
    assert "OPIP_RELEASE_SHADOW_READINESS=READY" in out
    assert "OPIP_RELEASE_PROTECTION=UNSAFE" in out
    assert "OPIP_RELEASE_PROTECTION_READY=false" in out
    assert "OPIP_RELEASE_TARGET_AUTHORITY=ABSENT" in out
