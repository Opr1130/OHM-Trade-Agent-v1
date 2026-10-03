from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
