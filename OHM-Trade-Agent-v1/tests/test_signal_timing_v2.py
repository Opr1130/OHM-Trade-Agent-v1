"""Phase 3A: gate diagnosis, forward outcomes, stage timing, and counterfactual sweeps.

These tests exist to pin the correction that started Phase 3A: an alert
observed at Stage=BREAKOUT_CANDIDATE, Persistence=3 is NOT, by itself,
evidence that persistence was what blocked ACTIONABLE_REVIEW.
``test_gate_status_agrees_with_determine_stage`` (randomized) plus the
``test_*_gate_boundary_*`` and ``test_*_boundary_matches_determine_stage``
tests (deterministic, exactly-at/epsilon-below/epsilon-above every gate)
together provide randomized and boundary regression coverage against
``determine_stage`` (the real production stage machine) - evidence, not a
proof of exact equivalence across the full continuous input space.
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
    """Randomized regression coverage against the real ``determine_stage``
    implementation: for 60 pseudo-random score/persistence/exhaustion/
    liquidity combinations, the diagnostic table's eligibility bit agrees
    with what the real production stage machine decides. This samples the
    input space; it does not prove exact equivalence across it. The
    deterministic ``test_*_gate_boundary_*`` and
    ``test_*_boundary_matches_determine_stage`` tests below specifically
    target the edges randomized sampling is least likely to hit.
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


EPS = 1e-6

_ACTIONABLE_GATE_BOUNDARIES = [
    ("opportunity", "opportunity", CONFIG.actionable_opportunity, ">="),
    ("explosion_potential", "explosion", CONFIG.actionable_explosion, ">="),
    ("tradeability", "tradeability", CONFIG.actionable_tradeability, ">="),
    ("persistence_scans", "persistence", CONFIG.actionable_min_persistence_scans, ">="),
    ("exhaustion_penalty", "exhaustion", CONFIG.actionable_max_exhaustion, "<"),
]

_BREAKOUT_GATE_BOUNDARIES = [
    ("opportunity", "opportunity", CONFIG.breakout_opportunity, ">="),
    ("explosion_potential", "explosion", CONFIG.breakout_explosion, ">="),
    ("tradeability", "tradeability", CONFIG.breakout_tradeability, ">="),
    ("persistence_scans", "persistence", CONFIG.breakout_min_persistence_scans, ">="),
    ("exhaustion_penalty", "exhaustion", CONFIG.breakout_max_exhaustion, "<"),
]

_COMFORTABLY_PASSING = dict(
    opportunity=95, explosion=95, tradeability=95, persistence=10, exhaustion=0,
)


def _gate_at(status, name):
    return next(gate for gate in status.gates if gate.name == name)


@pytest.mark.parametrize("gate_name, kwarg, threshold, comparison", _ACTIONABLE_GATE_BOUNDARIES)
def test_actionable_gate_boundary_is_correct(gate_name, kwarg, threshold, comparison):
    """Deterministic exactly-at / epsilon-below / epsilon-above coverage for
    every ACTIONABLE_REVIEW gate - randomized sampling can miss an exact
    boundary; this targets it directly. Persistence is integer-stepped since
    epsilon has no meaning for a scan count.
    """
    step = 1 if kwarg == "persistence" else EPS

    def _status(value):
        kwargs = dict(_COMFORTABLY_PASSING)
        kwargs[kwarg] = value
        row = _row(0, 100.0, liquidity=2_000_000.0, **kwargs)
        return evaluate_stage_gates(row, config=CONFIG, target_stage=STAGE_ACTIONABLE_REVIEW)

    at = _gate_at(_status(threshold), gate_name)
    below = _gate_at(_status(threshold - step), gate_name)
    above = _gate_at(_status(threshold + step), gate_name)

    if comparison == ">=":
        assert at.passed is True
        assert below.passed is False
        assert above.passed is True
    else:  # strict "<" (exhaustion_penalty)
        assert at.passed is False  # exactly at the cap does NOT satisfy strict <
        assert below.passed is True
        assert above.passed is False


@pytest.mark.parametrize("gate_name, kwarg, threshold, comparison", _BREAKOUT_GATE_BOUNDARIES)
def test_breakout_gate_boundary_is_correct(gate_name, kwarg, threshold, comparison):
    step = 1 if kwarg == "persistence" else EPS

    def _status(value):
        kwargs = dict(_COMFORTABLY_PASSING)
        kwargs[kwarg] = value
        row = _row(0, 100.0, liquidity=2_000_000.0, **kwargs)
        return evaluate_stage_gates(row, config=CONFIG, target_stage=STAGE_BREAKOUT_CANDIDATE)

    at = _gate_at(_status(threshold), gate_name)
    below = _gate_at(_status(threshold - step), gate_name)
    above = _gate_at(_status(threshold + step), gate_name)

    if comparison == ">=":
        assert at.passed is True
        assert below.passed is False
        assert above.passed is True
    else:
        assert at.passed is False
        assert below.passed is True
        assert above.passed is False


def test_exhaustion_strict_less_than_boundary_matches_determine_stage():
    """The exhaustion gate is a strict ``<``, not ``<=`` - exactly at the cap
    must FAIL against both the diagnostic table and the real production
    function, not just one of them.
    """
    kwargs = dict(_COMFORTABLY_PASSING, liquidity=2_000_000.0)

    for exhaustion, expect_actionable in (
        (CONFIG.actionable_max_exhaustion - EPS, True),
        (CONFIG.actionable_max_exhaustion, False),
        (CONFIG.actionable_max_exhaustion + EPS, False),
    ):
        row = _row(0, 100.0, exhaustion=exhaustion, **{k: v for k, v in kwargs.items() if k != "exhaustion"})
        status = evaluate_stage_gates(row, config=CONFIG, target_stage=STAGE_ACTIONABLE_REVIEW)
        actual_stage = determine_stage(
            opportunity=kwargs["opportunity"],
            explosion=kwargs["explosion"],
            tradeability=kwargs["tradeability"],
            persistence_scans=kwargs["persistence"],
            exhaustion_penalty=exhaustion,
            liquidity_24h_usd=kwargs["liquidity"],
            config=CONFIG,
        )
        assert status.eligible is expect_actionable
        assert (actual_stage == STAGE_ACTIONABLE_REVIEW) is expect_actionable


def test_liquidity_observation_boundary_matches_determine_stage():
    """observation_only is ``liquidity < observation_liquidity_usd`` - exactly
    at the threshold must NOT be observation-only, one cent below must be.
    """
    kwargs = dict(_COMFORTABLY_PASSING)
    threshold = CONFIG.observation_liquidity_usd

    for liquidity, expect_actionable in (
        (threshold, True),
        (threshold - EPS, False),
        (threshold + EPS, True),
    ):
        row = _row(0, 100.0, liquidity=liquidity, **kwargs)
        status = evaluate_stage_gates(row, config=CONFIG, target_stage=STAGE_ACTIONABLE_REVIEW)
        actual_stage = determine_stage(
            opportunity=kwargs["opportunity"],
            explosion=kwargs["explosion"],
            tradeability=kwargs["tradeability"],
            persistence_scans=kwargs["persistence"],
            exhaustion_penalty=kwargs["exhaustion"],
            liquidity_24h_usd=liquidity,
            config=CONFIG,
        )
        assert status.eligible is expect_actionable
        assert (actual_stage == STAGE_ACTIONABLE_REVIEW) is expect_actionable


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


def test_five_minute_horizon_is_unobserved_on_a_ten_minute_scan_grid():
    """The reviewer's core concern: production scans every 10 minutes
    (DEFAULT_SCAN_INTERVAL_SECONDS). On data sampled no finer than that grid,
    a 5-minute-ahead query has no observation at all - it must read as
    unobserved, never as a silently-carried-forward 0% return.
    """
    rows = [_obs(0, 100.0), _obs(10, 101.0), _obs(20, 102.0)]
    timeline = _timeline(rows)

    outcome = compute_forward_outcome(timeline, reference_at=BASE, reference_price=100.0)

    assert outcome.horizon_observed["5m"] is False
    assert outcome.horizon_returns_pct["5m"] is None


def test_coarser_horizon_is_observed_on_the_same_ten_minute_grid():
    """15m and up ordinarily see at least one real scan inside the window,
    even at exactly production cadence.
    """
    rows = [_obs(0, 100.0), _obs(10, 101.0), _obs(20, 102.0)]
    timeline = _timeline(rows)

    outcome = compute_forward_outcome(timeline, reference_at=BASE, reference_price=100.0)

    assert outcome.horizon_observed["15m"] is True
    assert outcome.horizon_returns_pct["15m"] == pytest.approx(1.0)


def test_horizon_observed_has_an_entry_for_every_horizon():
    rows = [_obs(0, 100.0), _obs(10, 101.0)]
    timeline = _timeline(rows)
    outcome = compute_forward_outcome(timeline, reference_at=BASE, reference_price=100.0)

    assert set(outcome.horizon_observed) == set(outcome.horizon_returns_pct)
    for label, observed in outcome.horizon_observed.items():
        if not observed:
            assert outcome.horizon_returns_pct[label] is None


def test_max_adverse_excursion_is_capped_at_zero_when_every_price_rose():
    """A run that only ever went up has a positive signed minimum-future
    return (mae_pct) - the conventional MAE reading must still be 0, not
    positive, so a reader cannot mistake "nothing adverse happened" for an
    adverse move.
    """
    rows = [_obs(0, 100.0), _obs(10, 105.0), _obs(20, 110.0)]
    timeline = _timeline(rows)

    outcome = compute_forward_outcome(timeline, reference_at=BASE, reference_price=100.0)

    assert outcome.mae_pct == pytest.approx(5.0)  # signed: positive here
    assert outcome.max_adverse_excursion_pct == pytest.approx(0.0)


def test_max_adverse_excursion_equals_raw_mae_when_negative():
    rows = [_obs(0, 100.0), _obs(10, 90.0), _obs(20, 95.0)]
    timeline = _timeline(rows)

    outcome = compute_forward_outcome(timeline, reference_at=BASE, reference_price=100.0)

    assert outcome.mae_pct == pytest.approx(-10.0)
    assert outcome.max_adverse_excursion_pct == pytest.approx(-10.0)


def test_max_adverse_excursion_is_none_when_mae_is_none():
    timeline = _timeline([_obs(0, 100.0)])  # nothing forward at all
    outcome = compute_forward_outcome(timeline, reference_at=BASE, reference_price=100.0)

    assert outcome.mae_pct is None
    assert outcome.max_adverse_excursion_pct is None


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
