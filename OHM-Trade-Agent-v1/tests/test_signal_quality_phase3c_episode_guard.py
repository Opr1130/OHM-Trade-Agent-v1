from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.services.signal_quality_phase3c import (
    Phase3CRow,
    build_phase3c_report,
    deduplicate_first_per_episode,
    join_point_in_time_evidence,
)


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _row(index: int) -> Phase3CRow:
    return Phase3CRow(
        episode_id=f"E{index}",
        snapshot_id=f"S{index}",
        symbol=f"C{index}USD",
        decision_at_utc=BASE + timedelta(hours=index),
        candidate_rank=1,
        reference_price=10.0,
        liquidity_24h_usd_approx=2_000_000.0,
        stage="BREAKOUT_CANDIDATE",
        opportunity_score=75,
        tradeability_score=70,
        explosion_potential_score=72,
        persistence_scans=3,
        exhaustion_penalty=10,
        suppressed=False,
        return_4h_pct=1.0,
        window_complete=True,
    )


def test_unassigned_snapshot_is_not_treated_as_independent_episode():
    snapshot = {
        "snapshot_id": "S1",
        "decision_at_utc": BASE.isoformat(),
        "symbol": "BTCUSD",
        "candidate_rank": 1,
        "reference_price": 100.0,
        "liquidity_24h_usd_approx": 5_000_000,
        "stage": "BREAKOUT_CANDIDATE",
        "opportunity_score": 80,
        "tradeability_score": 75,
        "explosion_potential_score": 78,
        "persistence_scans": 3,
        "exhaustion_penalty": 5,
        "suppressed": False,
    }
    joined = join_point_in_time_evidence([snapshot])
    assert joined[0].episode_id.startswith("UNASSIGNED:")
    assert deduplicate_first_per_episode(joined) == []
    report = build_phase3c_report(joined, bootstrap_resamples=10)
    assert report["episodes"] == 0
    assert report["data_quality"]["unassigned_snapshot_rows"] == 1
    assert report["promotion_gate"]["gate0_ready"] is False


def test_gate0_requires_observed_primary_outcomes_in_holdout():
    rows = [_row(i) for i in range(10)]
    # With a 60/20/20 split, the final two episodes are the holdout. Remove
    # their primary 4h outcomes: episode count alone must not open Gate 0.
    rows[-2] = replace(rows[-2], return_4h_pct=None)
    rows[-1] = replace(rows[-1], return_4h_pct=None)
    report = build_phase3c_report(
        rows,
        min_bucket_episodes=2,
        min_holdout_episodes=2,
        bootstrap_resamples=10,
    )
    assert report["split"]["test_episodes"] == 2
    assert report["data_quality"]["test_primary_outcome_episodes"] == 0
    assert report["promotion_gate"]["gate0_ready"] is False
