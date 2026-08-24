from datetime import datetime, timedelta, timezone

from app.services.signal_quality_phase3c import (
    Phase3CRow,
    bootstrap_mean_ci,
    build_phase3c_report,
    chronological_split,
    deduplicate_first_per_episode,
    join_point_in_time_evidence,
)


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def row(index, *, episode_id=None, rank=1, chase="LOW"):
    return Phase3CRow(
        episode_id=episode_id or f"E{index}",
        snapshot_id=f"S{index}",
        symbol=f"C{index}USD",
        decision_at_utc=BASE + timedelta(hours=index),
        candidate_rank=rank,
        reference_price=10.0,
        liquidity_24h_usd_approx=2_500_000.0,
        stage="BREAKOUT_CANDIDATE",
        opportunity_score=75,
        tradeability_score=70,
        explosion_potential_score=72,
        persistence_scans=3,
        exhaustion_penalty=10,
        suppressed=False,
        structure_status="AVAILABLE_COMPLETED_KRAKEN_SPOT_OHLC" if rank <= 8 else None,
        structure_bias="BULLISH" if rank <= 8 else None,
        retest_state="HELD" if rank <= 8 else None,
        chase_risk_score=25 if rank <= 8 else None,
        chase_risk_band=chase if rank <= 8 else None,
        return_15m_pct=0.2 + index * 0.01,
        return_30m_pct=0.3 + index * 0.01,
        return_60m_pct=0.4 + index * 0.01,
        return_4h_pct=0.5 + index * 0.01,
        mfe_24h_pct=1.5,
        mae_24h_pct=-0.5,
        window_complete=True,
        top8_structure_cohort=rank <= 8,
    )


def test_episode_dedup_keeps_first_detection_only():
    early = row(1, episode_id="SAME")
    later = Phase3CRow(
        **{
            **early.__dict__,
            "snapshot_id": "later",
            "decision_at_utc": early.decision_at_utc + timedelta(minutes=10),
        }
    )
    dedup = deduplicate_first_per_episode([later, early])
    assert len(dedup) == 1
    assert dedup[0].snapshot_id == early.snapshot_id


def test_chronological_split_is_episode_level_60_20_20():
    split = chronological_split([row(i) for i in range(10)])
    assert len(split.train) == 6
    assert len(split.validation) == 2
    assert len(split.test) == 2
    assert max(item.decision_at_utc for item in split.train) < min(
        item.decision_at_utc for item in split.validation
    )


def test_bootstrap_is_deterministic():
    first = bootstrap_mean_ci([1.0, 2.0, 3.0], resamples=100, seed=7)
    second = bootstrap_mean_ci([1.0, 2.0, 3.0], resamples=100, seed=7)
    assert first == second


def test_report_exposes_selection_bias_and_never_auto_promotes():
    rows = [row(i, rank=(i % 12) + 1) for i in range(20)]
    report = build_phase3c_report(
        rows,
        min_bucket_episodes=30,
        min_holdout_episodes=100,
        bootstrap_resamples=50,
    )
    assert report["status"] == "BUILDING_EVIDENCE"
    assert report["score_is_probability"] is False
    assert report["auto_promotion_allowed"] is False
    assert report["promotion_gate"]["validated_for_shadow"] == []
    assert "top-8" in report["data_quality"]["selection_bias_note"].lower()
    assert report["overall"]["status"] == "INSUFFICIENT_SAMPLE"


def test_gate0_can_become_review_ready_without_auto_promotion():
    rows = [row(i) for i in range(10)]
    report = build_phase3c_report(
        rows,
        min_bucket_episodes=2,
        min_holdout_episodes=2,
        bootstrap_resamples=20,
    )
    assert report["promotion_gate"]["gate0_ready"] is True
    assert report["auto_promotion_allowed"] is False
    assert report["promotion_gate"]["gate1_feature_validation_performed"] is False


def test_point_in_time_join_uses_exact_symbol_and_decision_timestamp():
    decision = BASE.isoformat()
    snapshot = {
        "snapshot_id": "S1",
        "decision_at_utc": decision,
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
    structure = {
        "symbol": "BTCUSD",
        "recorded_at": decision,
        "structure_status": "AVAILABLE_COMPLETED_KRAKEN_SPOT_OHLC",
        "structure_bias": "BULLISH",
        "retest_state": "HELD",
        "chase_risk_score": 20,
        "chase_risk_band": "LOW",
    }
    outcome = {
        "symbol": "BTCUSD",
        "reference_at": decision,
        "episode_id": "E1",
        "horizon_returns_pct": {"15m": 1.0, "30m": 2.0, "60m": 2.5, "4h": 4.0},
        "mfe_pct": 6.0,
        "max_adverse_excursion_pct": -1.0,
        "window_complete": True,
    }

    joined = join_point_in_time_evidence(
        [snapshot], phase3b_rows=[structure], outcomes=[outcome]
    )
    assert len(joined) == 1
    assert joined[0].episode_id == "E1"
    assert joined[0].return_4h_pct == 4.0
    assert joined[0].retest_state == "HELD"
    assert joined[0].top8_structure_cohort is True
