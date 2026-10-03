from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import release_runtime_verifier
from app.services.release_runtime_verifier import _new_evidence_is_valid

pytestmark = pytest.mark.acceptance


def _snapshot(cutoff: datetime):
    return SimpleNamespace(
        snapshot_id=f"snapshot-{cutoff.isoformat()}",
        instrument_version_id="instrument-v1",
        evaluation_cutoff=cutoff,
        evaluation_grid_seconds=60,
    )


def _evidence(cutoff: datetime, source_cutoff: datetime | None = None):
    return SimpleNamespace(
        instrument_version_id="instrument-v1",
        evaluation_time=cutoff,
        source_cutoff=source_cutoff or cutoff,
    )


def test_runtime_evidence_requires_consecutive_fresh_snapshots_and_matching_fev():
    """ATDD-RELEASE-PIPELINE-v1/AC-009: fresh sequential snapshots need matching prospective F5 evidence."""
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
    """ATDD-RELEASE-PIPELINE-v1/AC-009: gaps and late source timestamps fail closed."""
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


def test_runtime_evidence_rejects_stale_snapshots_and_unmatched_fev():
    """ATDD-RELEASE-PIPELINE-v1/AC-009: stale or unmatched evidence fails closed."""
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
    """ATDD-RELEASE-PIPELINE-v1/AC-009: prospective evidence cannot be future-dated."""
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


def test_safe_baseline_is_not_a_deploy_candidate(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-008: SAFE_BASELINE is rollback-only."""
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
