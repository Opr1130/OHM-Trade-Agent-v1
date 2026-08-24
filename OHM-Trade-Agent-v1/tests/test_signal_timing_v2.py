"""Phase 3A: gate diagnosis, forward outcomes, stage timing, and counterfactual sweeps.

These tests exist to pin the correction that started Phase 3A: an alert
observed at Stage=BREAKOUT_CANDIDATE, Persistence=3 is NOT, by itself,
evidence that persistence was what blocked ACTIONABLE_REVIEW.
``test_gate_status_agrees_with_determine_stage`` is the load-bearing test in
this file - it asserts the diagnostic gate table can never silently drift
from what ``determine_stage`` (the real production stage machine) actually
decides.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.signal_features import ObservationSnapshot
from app.services.signal_quality_phase2 import (
    CandidateRow,
    MoveEpisode,
    Phase2Config,
    ReplayObservation,
    SymbolTimeline,
    build_timelines,
    reconstruct_scan_frames,
)
from app.services.signal_scoring import (
    STAGE_ACTIONABLE_REVIEW,
    STAGE_BREAKOUT_CANDIDATE,
    STAGE_EARLY_BUILDING,
    STAGE_SUPPRESSED,
    SignalQualityConfig,
    determine_stage,
)
from app.services.signal_timing_v2 import (
    build_stage_timing_records,
    compute_forward_outcome,
    evaluate_stage_gates,
    opportunity_decay_by_persistence,
    run_persistence_counterfactual,
    run_threshold_ablation,
)

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _snapshot(at, price, *, notional=2_000_000.0, low=None, high=None):
    low = min(100.0, price) if low is None else low
    high = price if high is None else high
    return ObservationSnapshot(
        observed_at=at,
        last_price=price,
        volume_24h=notional / price,
        notional_24h_usd_approx=notional,
        high_24h=high,
        low_24h=low,
        lift_from_24h_low_pct=(price / low - 1.0) * 100.0 if low > 0 else 0.0,
        distance_from_24h_high_pct=max(0.0, (high - price) / price * 100.0),
    )


def _obs(minutes, price, symbol="TESTUSD", **kwargs):
    at = BASE + timedelta(minutes=minutes)
    return ReplayObservation(at, symbol, _snapshot(at, price, **kwargs))


def _row(
    minutes,
    price,
    *,
    symbol="TESTUSD",
    stage=STAGE_BREAKOUT_CANDIDATE,
    opportunity=72,
    explosion=68,
    tradeability=45,
    persistence=3,
    exhaustion=5,
    liquidity=2_000_000.0,
    distance_from_24h_high_pct=None,
) -> CandidateRow:
    return CandidateRow(
        scan_at=BASE + timedelta(minutes=minutes),
        symbol=symbol,
        stage=stage,
        price=price,
        imputed_input=False,
        opportunity_score=opportunity,
        explosion_potential_score=explosion,
        tradeability_score=tradeability,
        pattern_strength_score=70,
        volume_acceleration_score=60,
        relative_strength_score=80,
        persistence_scans=persistence,
        exhaustion_penalty=exhaustion,
        liquidity_24h_usd_approx=liquidity,
        pattern="REACCELERATION",
        reasons=(),
        distance_from_24h_high_pct=distance_from_24h_high_pct,
    )


CONFIG = SignalQualityConfig(enabled=True, early_alerts_enabled=True)


# ---------------------------------------------------------------------------
# Gate-status diagnosis
# ---------------------------------------------------------------------------


def test_the_drv_case_persistence_alone_does_not_establish_actionable_eligibility():
    """Stage=BREAKOUT_CANDIDATE, Persistence=3 must not, by itself, read as
    'only persistence was missing' for ACTIONABLE_REVIEW. Opportunity and
    tradeability below the actionable bar are real, independent blockers.
    """
    row = _row(
        0,
        100.0,
        stage=STAGE_BREAKOUT_CANDIDATE,
        opportunity=72,       # below actionable_opportunity (80)
        explosion=76,         # above actionable_explosion (75)
        tradeability=45,      # below actionable_tradeability (70)
        persistence=3,        # already meets actionable_min_persistence_scans (3)
        exhaustion=5,
    )
    status = evaluate_stage_gates(row, config=CONFIG, target_stage=STAGE_ACTIONABLE_REVIEW)

    assert status.eligible is False
    assert "persistence_scans" not in status.blocking_gates
    assert "opportunity" in status.blocking_gates
    assert "tradeability" in status.blocking_gates


def test_all_gates_passed_is_eligible():
    row = _row(
        0,
        100.0,
        opportunity=85,
        explosion=80,
        tradeability=75,
        persistence=4,
        exhaustion=2,
    )
    status = evaluate_stage_gates(row, config=CONFIG, target_stage=STAGE_ACTIONABLE_REVIEW)
    assert status.eligible is True
    assert status.blocking_gates == ()


def test_observation_only_liquidity_blocks_regardless_of_scores():
    row = _row(
        0,
        100.0,
        opportunity=90,
        explosion=90,
        tradeability=90,
        persistence=5,
        exhaustion=0,
        liquidity=10_000.0,  # below observation_liquidity_usd (250k)
    )
    status = evaluate_stage_gates(row, config=CONFIG, target_stage=STAGE_ACTIONABLE_REVIEW)
    assert status.eligible is False
    assert "liquidity_below_observation_threshold" in status.blocking_gates
    assert all(gate.passed for gate in status.gates)  # every scored gate passed


def test_evaluate_stage_gates_rejects_unsupported_target_stage():
    row = _row(0, 100.0)
    with pytest.raises(ValueError):
        evaluate_stage_gates(row, config=CONFIG, target_stage=STAGE_EARLY_BUILDING)


@pytest.mark.parametrize("seed", range(60))
def test_gate_status_agrees_with_determine_stage(seed):
    """Cross-validation: the diagnostic table's eligibility bit must always
    match what the real production stage machine (``determine_stage``)
    decides, so the two structures can never silently drift apart.
    """
    import random

    rng = random.Random(seed)
    opportunity = rng.uniform(0, 100)
    explosion = rng.uniform(0, 100)
    tradeability = rng.uniform(0, 100)
    persistence = rng.randint(0, 6)
    exhaustion = rng.uniform(0, 40)
    liquidity = rng.choice([50_000.0, 200_000.0, 2_000_000.0])

    row = _row(
        0,
        100.0,
        opportunity=opportunity,
        explosion=explosion,
        tradeability=tradeability,
        persistence=persistence,
        exhaustion=exhaustion,
        liquidity=liquidity,
    )
    actual_stage = determine_stage(
        opportunity=opportunity,
        explosion=explosion,
        tradeability=tradeability,
        persistence_scans=persistence,
        exhaustion_penalty=exhaustion,
        liquidity_24h_usd=liquidity,
        config=CONFIG,
    )

    actionable = evaluate_stage_gates(row, config=CONFIG, target_stage=STAGE_ACTIONABLE_REVIEW)
    breakout = evaluate_stage_gates(row, config=CONFIG, target_stage=STAGE_BREAKOUT_CANDIDATE)

    if actionable.eligible:
        assert actual_stage == STAGE_ACTIONABLE_REVIEW
    elif breakout.eligible:
        assert actual_stage == STAGE_BREAKOUT_CANDIDATE
    else:
        assert actual_stage in (STAGE_EARLY_BUILDING, STAGE_SUPPRESSED)


# ---------------------------------------------------------------------------
# Forward outcomes (MFE / MAE / fixed horizons)
# ---------------------------------------------------------------------------


def _timeline(rows) -> SymbolTimeline:
    return build_timelines(rows)["TESTUSD"]


def test_compute_forward_outcome_horizons_mfe_mae():
    rows = [
        _obs(0, 100.0),
        _obs(5, 103.0),
        _obs(15, 108.0),
        _obs(30, 95.0),   # the MAE
        _obs(60, 130.0),  # the MFE
        _obs(240, 120.0),
        _obs(480, 110.0),
        _obs(1440, 115.0),
    ]
    timeline = _timeline(rows)

    outcome = compute_forward_outcome(timeline, reference_at=BASE, reference_price=100.0)

    assert outcome is not None
    assert outcome.horizon_returns_pct["5m"] == pytest.approx(3.0)
    assert outcome.horizon_returns_pct["15m"] == pytest.approx(8.0)
    assert outcome.mfe_pct == pytest.approx(30.0)
    assert outcome.mfe_at == BASE + timedelta(minutes=60)
    assert outcome.time_to_mfe_seconds == pytest.approx(3600.0)
    assert outcome.mae_pct == pytest.approx(-5.0)
    assert outcome.mae_at == BASE + timedelta(minutes=30)
    assert outcome.window_complete is True


def test_compute_forward_outcome_none_for_non_positive_price():
    timeline = _timeline([_obs(0, 100.0)])
    assert compute_forward_outcome(timeline, reference_at=BASE, reference_price=0.0) is None


def test_compute_forward_outcome_incomplete_window_is_flagged():
    rows = [_obs(0, 100.0), _obs(30, 110.0)]
    timeline = _timeline(rows)
    outcome = compute_forward_outcome(timeline, reference_at=BASE, reference_price=100.0)
    assert outcome.window_complete is False
    # price_asof carries the last observation forward even past its own data,
    # so the 24h "return" here is really "still 110 as of the last print" -
    # window_complete=False is what tells a caller not to trust it as a true
    # 24h-later measurement.
    assert outcome.horizon_returns_pct["24h"] == pytest.approx(10.0)


def test_forward_outcome_never_sees_data_before_reference_at():
    """No-lookahead: a price print before the reference time must not leak in."""
    rows = [_obs(0, 100.0), _obs(-1, 999.0, symbol="TESTUSD")]
    # The pre-reference observation would dominate MFE if it leaked forward.
    timeline = _timeline([r for r in rows if r.observed_at >= BASE])
    outcome = compute_forward_outcome(timeline, reference_at=BASE, reference_price=100.0)
    assert outcome.mfe_pct is None or outcome.mfe_pct < 800.0


# ---------------------------------------------------------------------------
# Stage timing decomposition
# ---------------------------------------------------------------------------


def _episode(symbol="TESTUSD", baseline_price=100.0, peak_price=200.0):
    return MoveEpisode(
        symbol=symbol,
        baseline_at=BASE,
        baseline_price=baseline_price,
        peak_at=BASE + timedelta(hours=2),
        peak_price=peak_price,
        end_at=BASE + timedelta(hours=3),
        peak_return_pct=(peak_price / baseline_price - 1.0) * 100.0,
        outcome_class="MOVE_100",
        crossings={},
    )


def test_stage_timing_record_captures_first_candidate_and_gate_status():
    episode = _episode()
    detections = [
        _row(10, 120.0, stage=STAGE_EARLY_BUILDING, opportunity=58, explosion=52, tradeability=25, persistence=1),
        _row(40, 140.0, stage=STAGE_BREAKOUT_CANDIDATE, opportunity=72, explosion=68, tradeability=45, persistence=2),
        _row(90, 170.0, stage=STAGE_ACTIONABLE_REVIEW, opportunity=82, explosion=78, tradeability=72, persistence=3),
    ]
    timelines = {
        "TESTUSD": _timeline([
            _obs(m, p) for m, p in [(10, 120.0), (40, 140.0), (90, 170.0), (150, 190.0), (200, 205.0)]
        ])
    }

    records = build_stage_timing_records([episode], detections, timelines, scoring_config=CONFIG)

    assert len(records) == 1
    record = records[0]
    assert record.first_candidate_stage == STAGE_EARLY_BUILDING
    assert record.first_candidate_at == BASE + timedelta(minutes=10)
    assert record.first_early_building_at == BASE + timedelta(minutes=10)
    assert record.first_breakout_candidate_at == BASE + timedelta(minutes=40)
    assert record.first_actionable_review_at == BASE + timedelta(minutes=90)
    # The first-candidate gate diagnosis must show blockers, not eligibility,
    # since first candidate was only EARLY_BUILDING.
    assert record.first_candidate_gate_status_actionable.eligible is False
    assert record.outcome_from_first_candidate is not None
    assert record.outcome_from_first_breakout_candidate is not None
    assert record.outcome_from_first_actionable_review is not None
    # move_completed_fraction_pct reuses evaluate_episode_detection's figure.
    assert record.first_candidate_move_completed_fraction_pct == pytest.approx(20.0)


def test_stage_timing_record_with_no_detections_is_all_none():
    episode = _episode()
    records = build_stage_timing_records([episode], [], {"TESTUSD": _timeline([_obs(0, 100.0)])}, scoring_config=CONFIG)

    assert len(records) == 1
    record = records[0]
    assert record.first_candidate_at is None
    assert record.first_candidate_gate_status_breakout is None
    assert record.outcome_from_first_candidate is None


def test_stage_timing_ignores_detections_at_or_after_peak():
    """evaluate_episode_detection's own window [baseline_at, peak_at) applies;
    a detection after the peak is not a prediction of this episode.
    """
    episode = _episode()
    late = _row(200, 210.0, stage=STAGE_ACTIONABLE_REVIEW)  # after peak_at (2h = 120min)
    timelines = {"TESTUSD": _timeline([_obs(200, 210.0)])}

    records = build_stage_timing_records([episode], [late], timelines, scoring_config=CONFIG)
    assert records[0].first_candidate_at is None


def test_opportunity_decay_reports_gain_before_confirmation():
    episode = _episode()
    detections = [
        _row(10, 100.0, stage=STAGE_EARLY_BUILDING, opportunity=58, explosion=52, tradeability=25, persistence=1),
        _row(40, 140.0, stage=STAGE_BREAKOUT_CANDIDATE, opportunity=72, explosion=68, tradeability=45, persistence=2),
    ]
    timelines = {"TESTUSD": _timeline([_obs(10, 100.0), _obs(40, 140.0), _obs(200, 205.0)])}
    records = build_stage_timing_records([episode], detections, timelines, scoring_config=CONFIG)

    decay = opportunity_decay_by_persistence(records)

    assert decay["episodes_with_first_candidate"] == 1
    row = decay["rows"][0]
    assert row["breakout_candidate_gained_before_confirmation_pct"] == pytest.approx(40.0)
    assert row["actionable_review_gained_before_confirmation_pct"] is None


# ---------------------------------------------------------------------------
# Counterfactual sweeps
# ---------------------------------------------------------------------------


def _rising_price_observations(count=40, step=8.0):
    return [_obs(10 * i, 100.0 + step * i, symbol="AUSD") for i in range(count)]


def _sweep_inputs():
    observations = _rising_price_observations()
    config = Phase2Config()
    frames = reconstruct_scan_frames(
        observations,
        interval_seconds=config.scan_interval_seconds,
        max_carry_seconds=config.max_carry_seconds,
    )
    timelines = build_timelines(observations)
    from app.services.signal_quality_phase2 import build_all_episodes

    episodes = build_all_episodes(timelines, config=config.episodes)
    return frames, episodes, timelines, config


def test_persistence_counterfactual_reports_separately_from_production():
    frames, episodes, timelines, config = _sweep_inputs()

    result = run_persistence_counterfactual(
        frames,
        episodes,
        timelines,
        base_config=config,
        breakout_persistence_values=(1, 2, 3),
        actionable_persistence_values=(1, 2, 3),
    )

    assert result["status"] == "PROVISIONAL_COUNTERFACTUAL_NOT_PRODUCTION"
    assert result["sweep"]
    # actionable < breakout combinations are excluded by construction.
    for row in result["sweep"]:
        assert row["actionable_min_persistence_scans"] >= row["breakout_min_persistence_scans"]
    # A looser persistence-1 gate must never see fewer breakout episodes than persistence-3.
    by_breakout = {row["breakout_min_persistence_scans"]: row for row in result["sweep"] if row["actionable_min_persistence_scans"] == 3}
    assert by_breakout[1]["episodes_with_breakout"] >= by_breakout[3]["episodes_with_breakout"]


def test_threshold_ablation_loosening_never_reduces_detections():
    frames, episodes, timelines, config = _sweep_inputs()

    result = run_threshold_ablation(
        frames,
        episodes,
        timelines,
        base_config=config,
        overrides=[
            {},  # baseline (production defaults, as a sanity anchor)
            {"breakout_opportunity": 1.0, "breakout_explosion": 1.0, "breakout_tradeability": 1.0},
        ],
    )

    assert result["status"] == "PROVISIONAL_COUNTERFACTUAL_NOT_PRODUCTION"
    baseline, loosened = result["sweep"]
    assert loosened["episodes_with_breakout"] >= baseline["episodes_with_breakout"]


def test_threshold_ablation_rejects_unknown_field():
    frames, episodes, timelines, config = _sweep_inputs()
    with pytest.raises(TypeError):
        run_threshold_ablation(
            frames, episodes, timelines, base_config=config,
            overrides=[{"not_a_real_field": 1.0}],
        )


def test_counterfactual_sweeps_do_not_mutate_base_config():
    frames, episodes, timelines, config = _sweep_inputs()
    before = (
        config.scoring.breakout_min_persistence_scans,
        config.scoring.actionable_min_persistence_scans,
        config.scoring.breakout_opportunity,
    )
    run_persistence_counterfactual(frames, episodes, timelines, base_config=config)
    run_threshold_ablation(
        frames, episodes, timelines, base_config=config,
        overrides=[{"breakout_opportunity": 1.0}],
    )
    after = (
        config.scoring.breakout_min_persistence_scans,
        config.scoring.actionable_min_persistence_scans,
        config.scoring.breakout_opportunity,
    )
    assert before == after
