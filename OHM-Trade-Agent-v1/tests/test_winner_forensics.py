from app.services.daily_gainer_reconciliation import DailyGainerTrace
from app.services.winner_forensics import classify_winner_trace


def _trace(**overrides):
    data = dict(
        base_asset="TEST",
        symbol="TESTUSD",
        current_price=2.0,
        gain_today_pct=20.0,
        liquidity_24h_usd_approx=500_000.0,
        first_ohm_seen_at="2026-08-21T00:00:00+00:00",
        first_ohm_phase="DORMANT",
        first_ohm_phase_score=10,
        first_ohm_price=1.0,
        gain_when_first_seen_pct=0.5,
        remaining_upside_after_first_seen_pct=19.4,
        first_source_cohort="EARLY_ACCELERATION",
        first_momentum_1h_pct=0.2,
        first_momentum_acceleration=0.1,
        first_relative_volume=0.2,
        first_trade_count_acceleration=None,
        first_aggressor_imbalance=None,
        trace_status="TRACED_FROM_RECORDED_STATE",
    )
    data.update(overrides)
    return DailyGainerTrace(**data)


def test_seen_early_dormant_big_winner_is_underestimated():
    case = classify_winner_trace(_trace())
    assert case.classification == "SEEN_EARLY_UNDERESTIMATED"
    assert case.production_decision_changed is False


def test_never_seen_winner_is_coverage_miss():
    case = classify_winner_trace(_trace(
        first_ohm_seen_at=None,
        first_ohm_phase=None,
        first_ohm_phase_score=None,
        first_ohm_price=None,
        gain_when_first_seen_pct=None,
        remaining_upside_after_first_seen_pct=None,
        first_source_cohort=None,
        first_momentum_1h_pct=None,
        first_momentum_acceleration=None,
        first_relative_volume=None,
        trace_status="NOT_SEEN_BY_WAVE5",
    ))
    assert case.classification == "COVERAGE_MISS"


def test_early_ignition_with_large_remaining_upside_is_success():
    case = classify_winner_trace(_trace(
        first_ohm_phase="IGNITION",
        gain_when_first_seen_pct=-1.0,
        remaining_upside_after_first_seen_pct=21.0,
    ))
    assert case.classification == "EARLY_DETECTION_SUCCESS"


def test_materially_late_first_detection_is_late_detection():
    case = classify_winner_trace(_trace(
        first_ohm_phase="CONFIRMED_EXPANSION",
        gain_when_first_seen_pct=10.0,
        remaining_upside_after_first_seen_pct=8.0,
    ))
    assert case.classification == "LATE_DETECTION"


def test_early_late_extension_with_large_continuation_is_phase_misclassification():
    case = classify_winner_trace(_trace(
        first_ohm_phase="LATE_EXTENSION",
        gain_when_first_seen_pct=0.6,
        remaining_upside_after_first_seen_pct=22.0,
    ))
    assert case.classification == "PHASE_MISCLASSIFICATION"
