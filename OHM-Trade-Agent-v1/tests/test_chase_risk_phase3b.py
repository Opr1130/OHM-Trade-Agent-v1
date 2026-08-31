from app.services.chase_risk import ChaseRiskInput, assess_chase_risk


def test_extension_monotonically_increases_score():
    low = assess_chase_risk(ChaseRiskInput(current_price=102, breakout_level=100))
    mid = assess_chase_risk(ChaseRiskInput(current_price=108, breakout_level=100))
    high = assess_chase_risk(ChaseRiskInput(current_price=120, breakout_level=100))
    assert low.score <= mid.score <= high.score


def test_near_high_risk_requires_supplied_point_in_time_data():
    base = assess_chase_risk(ChaseRiskInput(current_price=100))
    near = assess_chase_risk(ChaseRiskInput(current_price=100, recent_high=100.5))
    assert near.score > base.score


def test_held_retest_reduces_chase_risk():
    no_retest = assess_chase_risk(ChaseRiskInput(current_price=110, breakout_level=100, retest_state="NOT_SEEN"))
    held = assess_chase_risk(ChaseRiskInput(current_price=110, breakout_level=100, retest_state="HELD"))
    assert held.score < no_retest.score
    assert held.retest_available is True


def test_failed_retest_increases_risk():
    neutral = assess_chase_risk(ChaseRiskInput(current_price=104, breakout_level=100))
    failed = assess_chase_risk(ChaseRiskInput(current_price=104, breakout_level=100, retest_state="FAILED"))
    assert failed.score > neutral.score


def test_missing_data_degrades_to_neutral_not_certainty():
    result = assess_chase_risk(ChaseRiskInput(current_price=100))
    assert result.score == 0
    assert result.band == "LOW"
    assert result.late_entry is False
    assert result.advisory_only is True


def test_invalid_current_price_is_neutral():
    result = assess_chase_risk(ChaseRiskInput(current_price=0, breakout_level=100, recent_high=101))
    assert result.score == 0
    assert result.extension_pct_from_breakout is None


def test_large_completed_move_and_exhaustion_can_mark_late_entry():
    result = assess_chase_risk(
        ChaseRiskInput(
            current_price=125,
            breakout_level=100,
            recent_high=126,
            lift_from_24h_low_pct=45,
            move_completed_fraction_pct=85,
            persistence_scans=5,
            exhaustion_penalty=25,
            retest_state="NOT_SEEN",
        )
    )
    assert result.score >= 60
    assert result.late_entry is True
    assert result.band in {"HIGH", "EXTREME"}


def test_deterministic_repeatability():
    data = ChaseRiskInput(current_price=112, breakout_level=100, recent_high=113, move_completed_fraction_pct=70)
    assert assess_chase_risk(data) == assess_chase_risk(data)


def test_output_never_contains_execution_authority():
    result = assess_chase_risk(ChaseRiskInput(current_price=100))
    assert result.advisory_only is True
    assert not hasattr(result, "order")
    assert not hasattr(result, "short")
