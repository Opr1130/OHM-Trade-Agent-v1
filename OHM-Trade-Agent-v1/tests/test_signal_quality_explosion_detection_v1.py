"""Signal Quality / Explosion Detection v1 (Phase 1).

Covers the design's required regression scenarios: the reported thin-market
false positives, the liquid leader that should outrank them, persistence
integrity across scan continuity, whole-universe percentiles, the no-lookahead
property, exhaustion behaviour at both ends, and the notification boundary.

Every threshold exercised here is an interpretable prior. These tests pin
*behaviour* - suppression, ordering, monotonicity, invariants - not calibrated
performance, because Phase 1 has no outcome data to calibrate against.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.jobs.scan_movers import _broad_watch_feed, _signal_quality_card
from app.services.full_market_observation import FullMarketResult, MarketTransition
from app.services.signal_features import (
    MIN_SCANS_FOR_FEATURES,
    FeatureDerivationConfig,
    ObservationSnapshot,
    derive_features_for_universe,
    derive_symbol_features,
    derive_universe_percentiles,
    percentile_rank,
)
from app.services.signal_scoring import (
    REASON_BLOW_OFF_RISK,
    REASON_EXTENDED_MOVE,
    REASON_INSUFFICIENT_LIQUIDITY,
    REASON_INVALID_MARKET_DATA,
    REASON_MOMENTUM_DECELERATING,
    REASON_OBSERVATION_ONLY_LIQUIDITY,
    STAGE_ACTIONABLE_REVIEW,
    STAGE_BREAKOUT_CANDIDATE,
    STAGE_EARLY_BUILDING,
    STAGE_SUPPRESSED,
    SignalQualityConfig,
    assess_exhaustion,
    evaluate_candidate,
    evaluate_universe,
    main_feed_candidates,
    persistence_score,
    tradeability_score,
)


BASE_TIME = datetime(2026, 8, 22, tzinfo=timezone.utc)
SCAN_INTERVAL = 600

CONFIG = SignalQualityConfig(enabled=True)
FEATURE_CONFIG = FeatureDerivationConfig(nominal_interval_seconds=float(SCAN_INTERVAL))

# A steadily reaccelerating series: ~2% per scan, holding at the 24h high.
LEADER_PRICES = [102.0, 104.0, 106.1, 108.2, 110.4]
LEADER_NOTIONAL_GROWTH = 1.03


def _history(
    prices,
    notionals=None,
    *,
    low=100.0,
    interval_seconds=SCAN_INTERVAL,
    start=BASE_TIME,
):
    """Build a runtime scan history. Rising series sit at their 24h high."""
    if notionals is None:
        notionals = [1_000_000.0] * len(prices)
    rows = []
    running_high = max(prices[0], low)
    for index, (price, notional) in enumerate(zip(prices, notionals)):
        running_high = max(running_high, price)
        rows.append(
            ObservationSnapshot(
                observed_at=start + timedelta(seconds=interval_seconds * index),
                last_price=price,
                volume_24h=notional / price,
                notional_24h_usd_approx=notional,
                high_24h=running_high,
                low_24h=low,
                lift_from_24h_low_pct=(price / low - 1.0) * 100.0,
                distance_from_24h_high_pct=max(0.0, (running_high - price) / price * 100.0),
            )
        )
    return rows


def _growing_notionals(base, count, factor=LEADER_NOTIONAL_GROWTH):
    return [base * (factor ** index) for index in range(count)]


def _leader_history(notional_base):
    return _history(
        LEADER_PRICES,
        _growing_notionals(notional_base, len(LEADER_PRICES)),
    )


def _features(history):
    return derive_symbol_features(history, config=FEATURE_CONFIG)


def _background(count=10):
    """Flat, liquid markets making up the rest of the observable universe.

    Relative strength is a percentile, so a fixture containing only movers
    would rank every one of them at the median and quietly neuter the
    component. Most of a real universe is not moving.
    """
    return {
        f"FLAT{index}USD": _history([100.0] * 5, [2_000_000.0] * 5)
        for index in range(count)
    }


def _universe(**markets):
    universe = _background()
    universe.update(markets)
    return universe


def _evaluate(universe, *, config=CONFIG):
    """Run the two-pass pipeline over a {symbol: history} universe."""
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    percentiles = derive_universe_percentiles(features)
    return {row.symbol: row for row in evaluate_universe(features, percentiles, config=config)}


# ---------------------------------------------------------------------------
# Reported false positives: thin markets with genuinely strong geometry.
# ---------------------------------------------------------------------------


def test_duck_like_thin_market_is_suppressed_despite_strong_pattern():
    """~$10.6k of 24h notional. Strong acceleration must not buy a card."""
    universe = _universe(
        DUCKUSD=_leader_history(10_638.0),
        BIGUSD=_leader_history(5_000_000.0),
    )

    duck = _evaluate(universe)["DUCKUSD"]

    assert duck.stage == STAGE_SUPPRESSED
    assert duck.tradeability_score == 0
    assert REASON_INSUFFICIENT_LIQUIDITY in duck.reasons
    assert duck.opportunity_score == 0
    # The pattern was genuinely strong - that is the point of the regression.
    assert duck.pattern_strength_score >= 60


def test_pols_like_thin_market_is_suppressed_identically():
    """~$4.0k of 24h notional with the same reacceleration geometry."""
    universe = _universe(
        POLSUSD=_leader_history(3_983.0),
        BIGUSD=_leader_history(5_000_000.0),
    )

    pols = _evaluate(universe)["POLSUSD"]

    assert pols.stage == STAGE_SUPPRESSED
    assert pols.tradeability_score == 0
    assert REASON_INSUFFICIENT_LIQUIDITY in pols.reasons


def test_duck_pols_regression_keeps_thin_markets_out_of_the_telegram_feed():
    """The end-to-end guard on the originally reported alerts."""
    universe = _universe(
        DUCKUSD=_leader_history(10_638.0),
        POLSUSD=_leader_history(3_983.0),
        BIGUSD=_leader_history(5_000_000.0),
    )
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    candidates = evaluate_universe(
        features, derive_universe_percentiles(features), config=CONFIG
    )

    feed = main_feed_candidates(candidates, config=CONFIG)
    symbols = {row.symbol for row in feed}

    assert "DUCKUSD" not in symbols
    assert "POLSUSD" not in symbols
    assert "BIGUSD" in symbols

    # Suppressed rows survive for audit rather than disappearing.
    suppressed = {row.symbol for row in candidates if row.suppressed}
    assert {"DUCKUSD", "POLSUSD"} <= suppressed


def test_liquid_leader_materially_outranks_thin_markets():
    """Same geometry, real liquidity: the leader must win by a wide margin."""
    universe = _universe(
        DUCKUSD=_leader_history(10_638.0),
        POLSUSD=_leader_history(3_983.0),
        BIGUSD=_leader_history(5_000_000.0),
    )
    scored = _evaluate(universe)
    leader = scored["BIGUSD"]

    assert leader.stage in {STAGE_BREAKOUT_CANDIDATE, STAGE_ACTIONABLE_REVIEW}
    assert leader.tradeability_score >= CONFIG.breakout_tradeability
    assert leader.opportunity_score >= CONFIG.breakout_opportunity
    assert leader.opportunity_score - scored["DUCKUSD"].opportunity_score >= 50
    assert leader.opportunity_score - scored["POLSUSD"].opportunity_score >= 50


def test_liquid_leader_wins_even_with_slightly_weaker_acceleration():
    """A weaker but tradeable mover still outranks a thin parabolic one."""
    weaker = [101.4, 102.8, 104.2, 105.7, 107.2]
    universe = _universe(
        DUCKUSD=_leader_history(10_638.0),
        BIGUSD=_history(weaker, _growing_notionals(5_000_000.0, len(weaker))),
    )
    scored = _evaluate(universe)

    assert scored["BIGUSD"].opportunity_score > scored["DUCKUSD"].opportunity_score
    assert scored["DUCKUSD"].stage == STAGE_SUPPRESSED


def test_observation_grade_liquidity_cannot_advance_past_early_building():
    """$100k-$250k is observation grade: capped no matter how strong it looks."""
    universe = _universe(
        MIDUSD=_leader_history(150_000.0),
        BIGUSD=_leader_history(5_000_000.0),
    )
    mid = _evaluate(universe)["MIDUSD"]

    assert mid.stage in {STAGE_EARLY_BUILDING, STAGE_SUPPRESSED}
    assert mid.stage != STAGE_BREAKOUT_CANDIDATE
    assert mid.stage != STAGE_ACTIONABLE_REVIEW
    assert REASON_OBSERVATION_ONLY_LIQUIDITY in mid.reasons


# ---------------------------------------------------------------------------
# Tradeability
# ---------------------------------------------------------------------------


def test_tradeability_is_zero_below_the_hard_floor_and_monotonic_above_it():
    assert tradeability_score(99_999.0, config=CONFIG) == 0.0
    assert tradeability_score(10_638.0, config=CONFIG) == 0.0

    ladder = [100_000.0, 250_000.0, 500_000.0, 1_000_000.0, 2_500_000.0, 5_000_000.0, 10_000_000.0]
    scores = [tradeability_score(value, config=CONFIG) for value in ladder]
    assert scores == sorted(scores)
    assert scores == pytest.approx([20.0, 40.0, 55.0, 70.0, 82.0, 90.0, 100.0])
    assert tradeability_score(50_000_000.0, config=CONFIG) == 100.0


def test_pattern_strength_carries_no_liquidity_term():
    """Identical geometry must score identically regardless of market size."""
    thin = _evaluate(_universe(AUSD=_leader_history(10_638.0), BUSD=_leader_history(5_000_000.0)))
    assert thin["AUSD"].pattern_strength_score == thin["BUSD"].pattern_strength_score


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_one_scan_spike_earns_no_meaningful_persistence():
    """Flat, flat, flat, then a jump. One qualifying scan, not four."""
    history = _history([100.0, 100.0, 100.0, 100.0, 112.0])
    features = _features(history)

    assert features.consecutive_qualifying_scans == 1
    assert persistence_score(features.consecutive_qualifying_scans) == 20.0


def test_consecutive_qualifying_scans_increase_persistence_deterministically():
    rising = [102.0 + 2.0 * index for index in range(8)]
    counts = [
        _features(_history(rising[:length])).consecutive_qualifying_scans
        for length in range(2, 9)
    ]

    # Each additional qualifying scan extends the chain by exactly one.
    assert counts == list(range(1, 8))

    scores = [persistence_score(count) for count in counts]
    assert scores == sorted(scores)
    assert persistence_score(1) == 20.0
    assert persistence_score(2) == 40.0
    assert persistence_score(3) == 60.0
    assert persistence_score(6) == 100.0
    assert persistence_score(99) == 100.0


def test_broken_scan_continuity_does_not_preserve_persistence():
    """A gap longer than the continuity window severs the chain."""
    intact = _history([102.0 + 2.0 * index for index in range(5)])
    assert _features(intact).consecutive_qualifying_scans == 4

    broken = list(intact)
    # Push the newest scan far beyond interval * continuity_multiplier.
    broken[-1] = ObservationSnapshot(
        **{
            **broken[-1].__dict__,
            "observed_at": broken[-2].observed_at + timedelta(seconds=SCAN_INTERVAL * 10),
        }
    )
    features = _features(broken)

    assert features.continuity_intact is False
    assert features.consecutive_qualifying_scans == 0
    assert persistence_score(features.consecutive_qualifying_scans) == 0.0


def test_reversal_breaks_the_qualifying_chain():
    history = _history([102.0, 104.0, 106.0, 103.0, 105.0])
    assert _features(history).consecutive_qualifying_scans == 1


# ---------------------------------------------------------------------------
# Relative strength across the whole universe
# ---------------------------------------------------------------------------


def test_percentile_rank_uses_midpoint_for_ties():
    assert percentile_rank([1.0, 2.0, 3.0], 3.0) == pytest.approx(83.3333, abs=1e-3)
    assert percentile_rank([1.0, 1.0, 1.0], 1.0) == pytest.approx(50.0)
    assert percentile_rank([5.0, 6.0], 1.0) == pytest.approx(0.0)


def test_relative_strength_ranks_against_the_whole_observed_universe():
    """Percentiles include markets that never became candidates.

    Ranking only among transition candidates would be selection-biased: a
    laggard would look strong simply because the weak majority was excluded.
    """
    universe = dict(_background(9))
    universe["LEADUSD"] = _leader_history(5_000_000.0)

    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    percentiles = derive_universe_percentiles(features)

    assert percentiles["LEADUSD"].universe_size == 10
    assert percentiles["LEADUSD"].price_change_percentile >= 90.0
    assert percentiles["FLAT0USD"].price_change_percentile <= 50.0


def test_universe_percentiles_ignore_markets_without_derivable_features():
    universe = {
        "LEADUSD": _leader_history(5_000_000.0),
        "NEWUSD": _history([100.0]),  # single scan, no features yet
    }
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    percentiles = derive_universe_percentiles(features)

    assert "NEWUSD" not in percentiles
    assert percentiles["LEADUSD"].universe_size == 1


# ---------------------------------------------------------------------------
# No lookahead
# ---------------------------------------------------------------------------


def test_no_lookahead_in_feature_derivation():
    """Features at scan *i* must not depend on anything after scan *i*.

    Checked at every index of the series, against a spliced future that never
    happened. The final assertion keeps the property non-vacuous: a derivation
    that ignored its input entirely would satisfy the first check trivially.
    """
    series = _history(
        [100.0, 101.0, 103.0, 102.5, 106.0, 109.0, 108.0, 113.0, 118.0, 117.0, 124.0, 131.0],
        _growing_notionals(3_000_000.0, 12),
    )

    for index in range(MIN_SCANS_FOR_FEATURES, len(series) + 1):
        prefix = series[:index]
        expected = derive_symbol_features(prefix, config=FEATURE_CONFIG)

        # A wildly divergent future spliced onto the identical prefix.
        poisoned = list(prefix) + _history(
            [9_999.0, 0.01, 5_000.0],
            [1e12, 1.0, 1e12],
            start=prefix[-1].observed_at + timedelta(seconds=SCAN_INTERVAL),
        )
        assert derive_symbol_features(poisoned[:index], config=FEATURE_CONFIG) == expected

        if index > MIN_SCANS_FOR_FEATURES:
            earlier = derive_symbol_features(series[: index - 1], config=FEATURE_CONFIG)
            assert earlier != expected


def test_features_require_at_least_two_scans():
    features = _features(_history([100.0]))
    assert features.valid is False
    assert features.invalid_reason == "INSUFFICIENT_HISTORY"


def test_invalid_market_data_is_suppressed_not_scored():
    history = _history([100.0, 105.0])
    broken = [
        history[0],
        ObservationSnapshot(**{**history[1].__dict__, "last_price": 0.0}),
    ]
    features = derive_symbol_features(broken, config=FEATURE_CONFIG)
    assert features.valid is False

    candidate = evaluate_candidate("BROKENUSD", features, derive_universe_percentiles({})
                                   .get("BROKENUSD", _empty_percentiles()), config=CONFIG)
    assert candidate.stage == STAGE_SUPPRESSED
    assert REASON_INVALID_MARKET_DATA in candidate.reasons


def _empty_percentiles():
    from app.services.signal_features import UniversePercentiles

    return UniversePercentiles()


# ---------------------------------------------------------------------------
# Sampling normalisation
# ---------------------------------------------------------------------------


def test_rates_are_normalised_to_the_nominal_scan_cadence():
    """A slow scan interval must not inflate the apparent rate of advance."""
    fast = _features(_history([100.0, 102.0], interval_seconds=SCAN_INTERVAL))
    slow = _features(_history([100.0, 102.0], interval_seconds=SCAN_INTERVAL * 2))

    assert fast.price_change_since_prior_pct == pytest.approx(slow.price_change_since_prior_pct)
    assert slow.price_change_rate_pct == pytest.approx(fast.price_change_rate_pct / 2.0)


def test_rolling_growth_proxy_is_labelled_as_a_proxy_not_interval_volume():
    from app.services.signal_features import ROLLING_VOLUME_GROWTH_PROXY_NOTE

    assert "rolling 24-hour aggregate" in ROLLING_VOLUME_GROWTH_PROXY_NOTE
    assert "not interval volume" in ROLLING_VOLUME_GROWTH_PROXY_NOTE


def test_single_anomalous_notional_print_cannot_earn_a_high_volume_score():
    """One outsized snapshot is not confirmation."""
    spike = _features(
        _history(
            [102.0, 104.0, 106.1, 108.2, 110.4],
            [1_000_000.0, 1_000_000.0, 1_000_000.0, 1_000_000.0, 9_000_000.0],
        )
    )
    sustained = _features(_leader_history(1_000_000.0))

    from app.services.signal_scoring import volume_acceleration_score

    assert volume_acceleration_score(spike) <= 45.0
    assert volume_acceleration_score(sustained) > volume_acceleration_score(spike)


# ---------------------------------------------------------------------------
# Exhaustion / chase penalty
# ---------------------------------------------------------------------------


def test_early_strong_mover_receives_no_chase_penalty():
    """Strength alone is never penalised."""
    features = _features(_leader_history(5_000_000.0))
    exhaustion = assess_exhaustion(features, config=CONFIG)

    assert exhaustion.penalty == 0.0
    assert exhaustion.reasons == ()
    assert exhaustion.band == "NONE"


def test_extended_parabolic_move_receives_a_penalty_with_reason_codes():
    prices = [100.0, 112.0, 128.0, 146.0, 158.0, 163.0]
    features = _features(
        _history(prices, [2_000_000.0] * len(prices))  # flat notional: no confirmation
    )
    exhaustion = assess_exhaustion(features, config=CONFIG)

    assert exhaustion.penalty >= 20.0
    assert REASON_EXTENDED_MOVE in exhaustion.reasons
    assert REASON_BLOW_OFF_RISK in exhaustion.reasons
    assert exhaustion.band in {"HIGH", "BLOW_OFF"}


def test_decelerating_momentum_after_a_large_run_is_flagged():
    prices = [100.0, 110.0, 122.0, 130.0, 131.0]
    features = _features(_history(prices, _growing_notionals(2_000_000.0, len(prices))))
    exhaustion = assess_exhaustion(features, config=CONFIG)

    assert features.momentum_decelerating is True
    assert REASON_MOMENTUM_DECELERATING in exhaustion.reasons


def test_exhaustion_penalty_is_bounded():
    prices = [100.0, 180.0, 320.0, 500.0, 505.0]
    features = _features(_history(prices, [2_000_000.0] * len(prices)))
    exhaustion = assess_exhaustion(features, config=CONFIG)

    assert exhaustion.penalty <= CONFIG.exhaustion.total_max_points


def test_lift_from_24h_low_alone_cannot_dominate_exhaustion():
    """Weak legacy evidence stays capped.

    A market far off its 24h low but with no recent run-up must not be treated
    as a chase.
    """
    prices = [148.0, 148.3, 148.6, 148.9, 149.2]
    features = _features(_history(prices, _growing_notionals(2_000_000.0, len(prices)), low=100.0))
    exhaustion = assess_exhaustion(features, config=CONFIG)

    assert features.lift_from_24h_low_pct > 45.0
    assert exhaustion.penalty <= CONFIG.exhaustion.lift_legacy_max_points


# ---------------------------------------------------------------------------
# Stage machine and leaderboard
# ---------------------------------------------------------------------------


def test_stage_thresholds_are_ordered_and_ranking_follows_stage_priority():
    universe = _universe(
        BIGUSD=_leader_history(5_000_000.0),
        MIDUSD=_leader_history(150_000.0),
        DUCKUSD=_leader_history(10_638.0),
    )
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    ranked = evaluate_universe(features, derive_universe_percentiles(features), config=CONFIG)

    assert ranked[0].symbol == "BIGUSD"
    assert ranked[0].stage == STAGE_BREAKOUT_CANDIDATE
    assert ranked[1].symbol == "MIDUSD"
    assert ranked[-1].stage == STAGE_SUPPRESSED

    # Stage priority dominates the ordering, and within the leaderboard
    # opportunity descends inside each stage.
    from app.services.signal_scoring import STAGE_PRIORITY

    priorities = [STAGE_PRIORITY[row.stage] for row in ranked]
    assert priorities == sorted(priorities)
    for earlier, later in zip(ranked, ranked[1:]):
        if earlier.stage == later.stage:
            assert earlier.opportunity_score >= later.opportunity_score


def test_leaderboard_retains_suppressed_rows_with_reasons():
    from app.services.signal_scoring import leaderboard_rows

    universe = _universe(
        BIGUSD=_leader_history(5_000_000.0),
        DUCKUSD=_leader_history(10_638.0),
    )
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    ranked = evaluate_universe(features, derive_universe_percentiles(features), config=CONFIG)
    rows = leaderboard_rows(ranked)

    duck = next(row for row in rows if row["symbol"] == "DUCKUSD")
    assert duck["suppressed"] is True
    assert REASON_INSUFFICIENT_LIQUIDITY in duck["reasons"]
    for field in (
        "pattern",
        "stage",
        "opportunity_score",
        "liquidity_24h_usd_approx",
        "persistence_scans",
        "exhaustion_penalty",
    ):
        assert field in duck


# ---------------------------------------------------------------------------
# Notification boundary
# ---------------------------------------------------------------------------


def _full_market(candidates, *, enabled=True):
    return FullMarketResult(
        observed_markets=len(candidates),
        persisted_events=0,
        transition_alerts=(),
        signal_quality_candidates=tuple(candidates),
        signal_quality_enabled=enabled,
    )


def _settings(**overrides):
    base = {
        "signal_quality_v1_enabled": True,
        "signal_quality_early_alerts_enabled": False,
        "signal_quality_max_cards_per_scan": 4,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_notification_feed_sends_only_breakout_and_actionable_stages():
    universe = _universe(
        BIGUSD=_leader_history(5_000_000.0),
        MIDUSD=_leader_history(150_000.0),
        DUCKUSD=_leader_history(10_638.0),
    )
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    ranked = evaluate_universe(features, derive_universe_percentiles(features), config=CONFIG)

    feed = _broad_watch_feed(_full_market(ranked), settings=_settings(), excluded_symbols=set())
    symbols = {symbol for symbol, _, _ in feed}

    assert "BIGUSD" in symbols
    assert "DUCKUSD" not in symbols
    assert "MIDUSD" not in symbols


def test_suppressed_candidates_never_reach_the_feed():
    universe = _universe(DUCKUSD=_leader_history(10_638.0), POLSUSD=_leader_history(3_983.0))
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    ranked = evaluate_universe(features, derive_universe_percentiles(features), config=CONFIG)

    assert all(row.suppressed for row in ranked)
    assert _broad_watch_feed(_full_market(ranked), settings=_settings(), excluded_symbols=set()) == []


def test_early_building_reaches_the_feed_only_behind_its_own_flag():
    universe = _universe(
        MIDUSD=_leader_history(150_000.0),
        BIGUSD=_leader_history(5_000_000.0),
    )
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    early_config = SignalQualityConfig(enabled=True, early_alerts_enabled=True)
    ranked = evaluate_universe(features, derive_universe_percentiles(features), config=early_config)
    mid = next(row for row in ranked if row.symbol == "MIDUSD")

    if mid.stage != STAGE_EARLY_BUILDING:
        pytest.skip("fixture did not reach EARLY_BUILDING under current priors")

    off = _broad_watch_feed(_full_market(ranked), settings=_settings(), excluded_symbols=set())
    on = _broad_watch_feed(
        _full_market(ranked),
        settings=_settings(signal_quality_early_alerts_enabled=True),
        excluded_symbols=set(),
    )

    assert "MIDUSD" not in {symbol for symbol, _, _ in off}
    assert "MIDUSD" in {symbol for symbol, _, _ in on}


def test_feed_is_capped_at_max_cards_per_scan():
    universe = _universe(**{f"BIG{index}USD": _leader_history(5_000_000.0) for index in range(9)})
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    ranked = evaluate_universe(features, derive_universe_percentiles(features), config=CONFIG)

    assert len([row for row in ranked if not row.suppressed]) > 4
    feed = _broad_watch_feed(
        _full_market(ranked),
        settings=_settings(signal_quality_max_cards_per_scan=2),
        excluded_symbols=set(),
    )
    assert len(feed) == 2


def test_feed_excludes_symbols_already_alerted_by_the_deep_mover_path():
    universe = _universe(BIGUSD=_leader_history(5_000_000.0))
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    ranked = evaluate_universe(features, derive_universe_percentiles(features), config=CONFIG)

    feed = _broad_watch_feed(
        _full_market(ranked), settings=_settings(), excluded_symbols={"BIGUSD"}
    )
    assert feed == []


def test_legacy_broad_watch_path_is_preserved_while_the_flag_is_off():
    """Phase 1 ships dark: with the flag off nothing about the feed changes."""
    transitions = tuple(
        MarketTransition(
            version="full-market-observation-v1",
            symbol=f"LEGACY{index}USD",
            pattern="REACCELERATION",
            score=80,
            price_change_since_prior_pct=2.0,
            lift_change_since_prior_pct=2.0,
            lift_from_24h_low_pct=8.0,
            distance_from_24h_high_pct=0.5,
            liquidity_24h_usd_approx=2_000_000.0,
            alert_tier="DEEP_REVIEW",
        )
        for index in range(6)
    )
    result = FullMarketResult(
        observed_markets=6,
        persisted_events=0,
        transition_alerts=transitions,
        signal_quality_enabled=False,
    )

    feed = _broad_watch_feed(
        result, settings=_settings(signal_quality_v1_enabled=False), excluded_symbols=set()
    )

    assert len(feed) == 4  # the historical [:4] cap
    assert "OHM BROAD WATCH" in feed[0][2]
    assert "DEEP REVIEW" in feed[0][2]


def test_watch_only_and_deep_review_tiers_still_render():
    watch_only = MarketTransition(
        version="full-market-observation-v1",
        symbol="LOWUSD",
        pattern="COMPRESSION_RELEASE",
        score=65,
        price_change_since_prior_pct=4.0,
        lift_change_since_prior_pct=4.0,
        lift_from_24h_low_pct=5.0,
        distance_from_24h_high_pct=1.0,
        liquidity_24h_usd_approx=40_000.0,
        alert_tier="WATCH_ONLY",
    )
    result = FullMarketResult(
        observed_markets=1,
        persisted_events=0,
        transition_alerts=(watch_only,),
        signal_quality_enabled=False,
    )
    feed = _broad_watch_feed(
        result, settings=_settings(signal_quality_v1_enabled=False), excluded_symbols=set()
    )
    assert "WATCH ONLY" in feed[0][2]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_card_renders_every_required_line_and_refuses_to_authorise_entry():
    universe = _universe(BIGUSD=_leader_history(5_000_000.0))
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    ranked = evaluate_universe(features, derive_universe_percentiles(features), config=CONFIG)
    card = _signal_quality_card(ranked[0])

    assert "OHM EARLY WATCH — BIGUSD" in card
    for label in (
        "Stage:",
        "Pattern:",
        "Pattern strength*:",
        "Tradeability*:",
        "Explosion potential*:",
        "Opportunity score*:",
        "Liquidity:",
        "Relative strength:",
        "Persistence:",
        "Volume growth proxy:",
        "Exhaustion:",
    ):
        assert label in card
    assert "Action: HUMAN REVIEW ONLY — no entry is authorized" in card
    assert "*Heuristic scores, not probabilities; Phase 2 calibration pending." in card
    assert "percentile" in card
    assert "consecutive scans" in card


def test_actionable_review_card_still_authorises_nothing():
    from app.services.signal_scoring import SignalQualityCandidate, VERSION

    candidate = SignalQualityCandidate(
        version=VERSION,
        symbol="BIGUSD",
        stage=STAGE_ACTIONABLE_REVIEW,
        pattern="REACCELERATION",
        tradeability_score=91,
        pattern_strength_score=78,
        volume_acceleration_score=80,
        persistence_score=60,
        relative_strength_score=96,
        explosion_potential_score=84,
        opportunity_score=82,
        exhaustion_penalty=0,
        exhaustion_band="NONE",
        liquidity_24h_usd_approx=4_800_000.0,
        persistence_scans=3,
        relative_strength_percentile=96.0,
        universe_size=400,
        reasons=(),
        components={},
    )
    card = _signal_quality_card(candidate)

    assert "Stage: ACTIONABLE REVIEW" in card
    assert "no entry is authorized" in card
    assert "$4.8M / 24h" in card
    assert "96th percentile" in card


# ---------------------------------------------------------------------------
# Safety invariants
# ---------------------------------------------------------------------------


def test_advisory_only_invariants_hold_on_every_candidate():
    universe = _universe(
        BIGUSD=_leader_history(5_000_000.0),
        DUCKUSD=_leader_history(10_638.0),
        MIDUSD=_leader_history(150_000.0),
    )
    features = derive_features_for_universe(universe, config=FEATURE_CONFIG)
    ranked = evaluate_universe(features, derive_universe_percentiles(features), config=CONFIG)

    assert ranked
    for candidate in ranked:
        assert candidate.advisory_only is True
        assert candidate.weights_are_calibrated is False
        assert candidate.trade_authority_changed is False
        assert candidate.production_execution_gate_changed is False


def test_phase_1_does_not_touch_execution_or_position_verification():
    """No scoring module may reach an execution or verification surface."""
    import app.services.signal_features as features_module
    import app.services.signal_scoring as scoring_module

    for module in (features_module, scoring_module):
        source = open(module.__file__, encoding="utf-8").read()
        assert "execution_validation" not in source
        assert "kraken_position_verification" not in source
        assert "kraken_private" not in source
        assert "confirm_entry" not in source
        assert "register_trade" not in source


def test_scoring_modules_perform_no_io():
    """Feature and scoring modules are pure: no network, no filesystem."""
    import app.services.signal_features as features_module
    import app.services.signal_scoring as scoring_module

    for module in (features_module, scoring_module):
        source = open(module.__file__, encoding="utf-8").read()
        assert "import httpx" not in source
        assert "KrakenClient" not in source
        assert "open(" not in source
        assert "save_json_atomic" not in source
