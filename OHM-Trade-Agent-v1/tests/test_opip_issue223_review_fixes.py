"""Issue #223 review-fix integration tests.

These exercise the integrated production paths (scan_early_movers,
full_market_observation, card rendering, timing ledger), not helper-only
builders.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.jobs import scan_movers
from app.opip.early import flags
from app.opip.early.cohort_selector import EarlyCandidateFeatures, select_early_candidates
from app.opip.early.observation_context import build_observation_context
from app.opip.early.operator_semantics import (
    assessment_from_signal,
    build_operator_assessment,
    operator_headline,
    operator_why_now,
)
from app.opip.early.taxonomy import (
    EvidenceGrade,
    EvidenceStance,
    MarketPhase,
    OperatorDisposition,
    ValidationClass,
    coerce_evidence_grade,
    coerce_market_phase,
    coerce_operator_disposition,
    is_early_phase,
)
from app.opip.early.timing_ledger import (
    early_episode_id,
    ledger_from_dict,
    resolve_episode_ledger,
    should_reset_episode,
)
from app.opip.early.validation_parity import (
    CHECK_FINITE_FEATURES,
    CHECK_NATIVE_FLOW,
    CHECK_SOCIAL,
    MANDATORY_CHECKS,
    ValidationCheck,
    ValidationResult,
    corroborating_family_count,
    evaluate_early_watch_validations,
    report_from_snapshot,
)
from app.scanner.market_data_validation import MarketDataValidation
from app.services import full_market_observation as fmo
from app.services import movement_discovery_v2 as discovery
from app.services.full_market_observation import MarketObservation

DECISION_AT = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)


def _mover(
    *,
    base: str = "IGN",
    pair: str | None = None,
    lift: float = 3.0,
    distance: float = 1.5,
    notional: float = 800_000.0,
    score: float = 40.0,
    bid: float = 1.0,
    ask: float = 1.001,
) -> discovery.CoarseMover:
    primary = pair or f"{base}USD"
    return discovery.CoarseMover(
        base_asset=base,
        primary_pair=primary,
        kraken_public_symbol=f"{base}/USD",
        last_price=1.0,
        volume_24h=100_000.0,
        notional_24h_usd_approx=notional,
        high_24h=1.02,
        low_24h=0.95,
        lift_from_24h_low_pct=lift,
        distance_from_24h_high_pct=distance,
        coarse_score=score,
        universe_count=200,
        ticker_bid=bid,
        ticker_ask=ask,
    )


def _qualifying_snapshot(
    *,
    symbol: str = "IGNUSD",
    one_hour: float = 2.5,
    six_hour: float = 4.5,
    day: float = 6.0,
    volume: float = 2.8,
    near_high: float = 1.0,
    bandwidth: float = 25.0,
    atr: float = 30.0,
    ticker_last: float = 1.0,
) -> SimpleNamespace:
    validation = MarketDataValidation(
        status="PASS",
        qualified=True,
        warnings=[],
        rejection_reasons=[],
        candle_count=720,
        latest_candle_timestamp=int(DECISION_AT.timestamp()),
        latest_candle_age_seconds=60.0,
        duplicate_timestamp_count=0,
        gap_count=0,
        largest_gap_seconds=0.0,
        invalid_ohlc_count=0,
        non_finite_value_count=0,
        ticker_last=ticker_last,
        latest_ohlc_close=ticker_last,
        ticker_vs_ohlc_difference_pct=0.1,
        suspicious_spike_detected=False,
    )
    return SimpleNamespace(
        symbol=symbol,
        confirmed_price_change_1h_pct=one_hour,
        momentum_6h_pct=six_hour,
        momentum_24h_pct=day,
        movement_volume_ratio=volume,
        volume_ratio=volume,
        distance_to_24h_high_pct=near_high,
        trend="bullish",
        last_price=ticker_last,
        movement_timeframe="1H",
        bollinger_bandwidth_percentile=bandwidth,
        atr_percentile=atr,
        market_data_validation=validation,
        execution_validation=SimpleNamespace(
            status="VALID",
            spread_pct=0.1,
            book_coverage_status="COMPLETE",
        ),
        independent_market_reference=None,
        native_flow_evidence=SimpleNamespace(available=True, bias="BULLISH", strength=6),
        native_flow_metrics=SimpleNamespace(available=True, bias="BULLISH"),
        cross_pair_confirmation_status="CONFIRMED",
        cross_pair_price_status="NORMAL",
    )


def test_selector_promoted_false_keeps_legacy_top_n_identical():
    ranked = [
        _mover(base=f"E{i}", lift=20.0 - i * 0.1, distance=0.2, score=100.0 - i)
        for i in range(50)
    ]
    ranked.append(_mover(base="IGN1", lift=2.5, distance=2.0, score=5.0))
    ranked.sort(key=lambda item: (-item.coarse_score, item.base_asset))
    legacy = ranked[:40]

    history = {
        "IGN1USD": [
            {
                "observed_at": (DECISION_AT - timedelta(minutes=20)).isoformat(),
                "last_price": 1.0,
                "volume_24h": 1_000.0,
                "lift_from_24h_low_pct": 1.0,
                "distance_from_24h_high_pct": 3.0,
            },
            {
                "observed_at": (DECISION_AT - timedelta(minutes=10)).isoformat(),
                "last_price": 1.01,
                "volume_24h": 1_500.0,
                "lift_from_24h_low_pct": 1.5,
                "distance_from_24h_high_pct": 2.5,
            },
            {
                "observed_at": DECISION_AT.isoformat(),
                "last_price": 1.05,
                "volume_24h": 2_500.0,
                "lift_from_24h_low_pct": 2.5,
                "distance_from_24h_high_pct": 2.0,
            },
        ]
    }

    assert flags.early_selector_promoted({}) is False
    assert [m.primary_pair for m in legacy] == [m.primary_pair for m in ranked[:40]]

    promoted = discovery.apply_promoted_selector(
        ranked, max_candidates=40, history=history
    )
    assert len(promoted) == 40
    assert any(m.base_asset == "IGN1" for m in promoted)
    assert all(m.base_asset != "IGN1" for m in legacy)


def test_selector_promoted_true_changes_selection_without_widening_budget(monkeypatch):
    ranked = [_mover(base=f"E{i}", lift=18.0, distance=0.3, score=90.0 - i) for i in range(45)]
    ranked.append(_mover(base="IGN1", lift=2.4, distance=1.5, score=1.0))
    history = {
        "IGN1USD": [
            {
                "observed_at": (DECISION_AT - timedelta(minutes=20)).isoformat(),
                "last_price": 1.0,
                "volume_24h": 1_000.0,
                "lift_from_24h_low_pct": 1.0,
                "distance_from_24h_high_pct": 3.0,
            },
            {
                "observed_at": DECISION_AT.isoformat(),
                "last_price": 1.04,
                "volume_24h": 2_000.0,
                "lift_from_24h_low_pct": 2.4,
                "distance_from_24h_high_pct": 1.5,
            },
        ]
    }

    def fake_discover(*args, **kwargs):
        on_ranked = kwargs.get("on_ranked")
        if on_ranked is not None:
            on_ranked(list(ranked))
        return ranked[:40]

    monkeypatch.setattr(discovery, "discover_coarse_movers", fake_discover)
    monkeypatch.setattr(
        discovery,
        "analyze_symbol",
        lambda *a, **k: ("skip", None, "test"),
    )

    coarse_dark, _ = discovery.scan_early_movers(
        max_candidates=40,
        selector_promoted=False,
        validation_parity_enabled=False,
        observation_history=history,
    )
    coarse_lit, _ = discovery.scan_early_movers(
        max_candidates=40,
        selector_promoted=True,
        validation_parity_enabled=False,
        observation_history=history,
    )

    assert len(coarse_dark) == 40
    assert len(coarse_lit) == 40
    assert [m.primary_pair for m in coarse_dark] == [m.primary_pair for m in ranked[:40]]
    assert any(m.base_asset == "IGN1" for m in coarse_lit)
    assert all(m.base_asset != "IGN1" for m in coarse_dark)


def test_evaluate_promotion_never_auto_enables_selector_flag():
    from app.opip.early.promotion import evaluate_promotion

    evaluation = evaluate_promotion(baseline={}, candidate={})
    assert evaluation.as_dict()["operator_promotion_requires_human_flag"] is True
    assert flags.early_selector_promoted({}) is False


def test_observation_context_exposes_real_prior_and_persistence_counts():
    history = {
        "RAYUSD": [
            {
                "observed_at": (DECISION_AT - timedelta(minutes=30)).isoformat(),
                "last_price": 1.0,
                "volume_24h": 1_000.0,
                "notional_24h_usd_approx": 100_000.0,
                "high_24h": 1.1,
                "low_24h": 0.9,
                "lift_from_24h_low_pct": 2.0,
                "distance_from_24h_high_pct": 3.0,
            },
            {
                "observed_at": (DECISION_AT - timedelta(minutes=20)).isoformat(),
                "last_price": 1.02,
                "volume_24h": 1_200.0,
                "notional_24h_usd_approx": 120_000.0,
                "high_24h": 1.1,
                "low_24h": 0.9,
                "lift_from_24h_low_pct": 3.0,
                "distance_from_24h_high_pct": 2.0,
            },
            {
                "observed_at": (DECISION_AT - timedelta(minutes=10)).isoformat(),
                "last_price": 1.05,
                "volume_24h": 1_500.0,
                "notional_24h_usd_approx": 150_000.0,
                "high_24h": 1.1,
                "low_24h": 0.9,
                "lift_from_24h_low_pct": 4.0,
                "distance_from_24h_high_pct": 1.0,
            },
        ]
    }
    context = build_observation_context(history=history)
    assert context.as_dict()["synthesised"] is False
    assert context.prior_observation_counts["RAY"] == 2
    assert context.prior_observation_counts["RAYUSD"] == 2


def test_scan_early_movers_can_qualify_when_every_mandatory_and_corroboration_passes(
    monkeypatch,
):
    mover = _mover(base="QAL", lift=3.0, distance=1.0, notional=1_500_000.0, score=50.0)
    snapshot = _qualifying_snapshot(symbol="QALUSD")

    def fake_discover(*args, **kwargs):
        on_ranked = kwargs.get("on_ranked")
        if on_ranked is not None:
            on_ranked([mover])
        return [mover]

    monkeypatch.setattr(discovery, "discover_coarse_movers", fake_discover)
    monkeypatch.setattr(
        discovery,
        "analyze_symbol",
        lambda *a, **k: ("ok", snapshot, None),
    )
    monkeypatch.setattr(discovery, "_enrich_bounded_candidate_evidence", lambda *a, **k: None)

    coarse, signals = discovery.scan_early_movers(
        max_candidates=5,
        validation_parity_enabled=True,
        selector_promoted=False,
        prior_observation_counts={"QAL": 4},
        persistence_scans={"QAL": 3},
        observation_history={},
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.evidence_grade == EvidenceGrade.QUALIFIED.value
    assert signal.qualification_blocking_failures == ()
    assert signal.alert_eligible is True


def test_history_advances_when_signal_quality_off_and_early_history_on(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(flags.HISTORY_CAPTURE_FLAG, "true")
    observations = [
        MarketObservation(
            version=fmo.VERSION,
            base_asset="AAA",
            symbol="AAAUSD",
            kraken_public_symbol="AAA/USD",
            last_price=1.0,
            volume_24h=100.0,
            notional_24h_usd_approx=100_000.0,
            high_24h=1.1,
            low_24h=0.9,
            lift_from_24h_low_pct=2.0,
            distance_from_24h_high_pct=1.0,
        )
    ]
    monkeypatch.setattr(fmo, "collect_full_market_observations", lambda client=None: observations)

    settings = SimpleNamespace(
        signal_quality_v1_enabled=False,
        signal_quality_history_scans=8,
        signal_quality_stale_history_retention_seconds=3600.0,
    )
    result = fmo.process_full_market_observations(
        observation_file=tmp_path / "obs.jsonl",
        state_file=tmp_path / "state.json",
        settings=settings,
    )
    state = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert "history_by_symbol" in state
    assert "AAAUSD" in state
    assert result.signal_quality_enabled is False
    assert result.signal_quality_candidates == ()


def test_history_does_not_advance_when_both_capture_flags_are_dark(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(flags.HISTORY_CAPTURE_FLAG, raising=False)
    observations = [
        MarketObservation(
            version=fmo.VERSION,
            base_asset="BBB",
            symbol="BBBUSD",
            kraken_public_symbol="BBB/USD",
            last_price=1.0,
            volume_24h=100.0,
            notional_24h_usd_approx=100_000.0,
            high_24h=1.1,
            low_24h=0.9,
            lift_from_24h_low_pct=2.0,
            distance_from_24h_high_pct=1.0,
        )
    ]
    monkeypatch.setattr(fmo, "collect_full_market_observations", lambda client=None: observations)
    settings = SimpleNamespace(
        signal_quality_v1_enabled=False,
        signal_quality_history_scans=8,
        signal_quality_stale_history_retention_seconds=3600.0,
    )
    fmo.process_full_market_observations(
        observation_file=tmp_path / "obs.jsonl",
        state_file=tmp_path / "state.json",
        settings=settings,
    )
    state = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert "history_by_symbol" not in state


def test_timeframe_shadow_uses_real_percentiles_from_signal():
    from app.opip.early.shadow_observer import observe_timeframe_shadow
    from app.opip.early.timeframe_policy import TIMEFRAME_FINE, TIMEFRAME_HOURLY

    compressed = SimpleNamespace(
        symbol="CMPUSD",
        market_phase=MarketPhase.COILED.value,
        bollinger_bandwidth_percentile=20.0,
        atr_percentile=25.0,
        detection_timeframe="15M",
    )
    ignition = SimpleNamespace(
        symbol="IGNUSD",
        market_phase=MarketPhase.IGNITION.value,
        bollinger_bandwidth_percentile=85.0,
        atr_percentile=90.0,
        detection_timeframe="1H",
    )
    rows = observe_timeframe_shadow(
        signals=[compressed, ignition],
        scan_id="s1",
        decision_at=DECISION_AT,
    )
    by_symbol = {row["symbol"]: row for row in rows}

    assert by_symbol["CMPUSD"]["production"]["timeframe"] == TIMEFRAME_FINE
    assert by_symbol["CMPUSD"]["inversion_detected"] is False
    assert by_symbol["IGNUSD"]["production"]["timeframe"] == TIMEFRAME_HOURLY
    assert by_symbol["IGNUSD"]["candidate"]["timeframe"] == TIMEFRAME_FINE
    assert by_symbol["IGNUSD"]["inversion_detected"] is True


def test_early_mover_signal_retains_decision_time_percentiles():
    signal = discovery.evaluate_early_mover(
        _qualifying_snapshot(bandwidth=22.0, atr=33.0),
        _mover(),
        validation_parity_enabled=False,
        prior_observation_count=3,
        persistence_scans=2,
        native_flow_available=True,
        cross_venue_available=True,
        depth_slippage_available=True,
    )
    assert not isinstance(signal, discovery.DeepEvaluationRejection)
    assert signal.bollinger_bandwidth_percentile == pytest.approx(22.0)
    assert signal.atr_percentile == pytest.approx(33.0)


def test_below_threshold_scan_persists_achieved_score_and_margin(monkeypatch):
    mover = _mover(base="WEAK", lift=2.5, score=20.0)
    # Fire some score components (1h + near_high) but stay below the 45 floor.
    weak = _qualifying_snapshot(
        symbol="WEAKUSD",
        one_hour=0.8,
        six_hour=0.3,
        day=0.4,
        volume=1.0,
        near_high=1.5,
    )
    weak.trend = "neutral"
    captured: list[dict] = []

    def fake_discover(*args, **kwargs):
        on_ranked = kwargs.get("on_ranked")
        if on_ranked is not None:
            on_ranked([mover])
        return [mover]

    monkeypatch.setattr(discovery, "discover_coarse_movers", fake_discover)
    monkeypatch.setattr(
        discovery, "analyze_symbol", lambda *a, **k: ("ok", weak, None)
    )
    monkeypatch.setattr(discovery, "_enrich_bounded_candidate_evidence", lambda *a, **k: None)

    coarse, signals = discovery.scan_early_movers(
        max_candidates=5,
        on_evaluated=captured.append,
        validation_parity_enabled=False,
        selector_promoted=False,
    )

    assert signals == []
    below = [row for row in captured if row.get("outcome") == "BELOW_THRESHOLD"]
    assert below
    evidence = below[0]["metadata"]["score_evidence"]
    assert evidence["achieved_score"] is not None
    assert evidence["required_score"] == discovery.MIN_DEEP_DISCOVERY_SCORE
    assert evidence["score_margin"] == pytest.approx(
        evidence["achieved_score"] - evidence["required_score"]
    )
    assert evidence["score_margin"] < 0
    assert evidence["blocking_reason"] == "deep_discovery_score_below_minimum"
    assert evidence["components"]


@pytest.mark.parametrize("phase", list(MarketPhase))
def test_early_watch_headline_only_for_early_phases(phase: MarketPhase):
    assessment = build_operator_assessment(
        symbol="XUSD",
        phase=phase,
        grade=EvidenceGrade.QUALIFIED,
        disposition=OperatorDisposition.DEEP_REVIEW,
    )
    signal = SimpleNamespace(
        symbol="XUSD",
        stage="READY",
        reference_price=1.0,
        detection_timeframe="1H",
        momentum_1h_pct=2.0,
        momentum_6h_pct=3.0,
        momentum_state="ACCELERATING",
        continuation_confidence=70,
        continuation_confidence_is_probability=False,
        entry_quality=70,
        entry_recommendation="BREAKOUT_ENTRY_POSSIBLE",
        relative_volume=2.0,
        distance_to_24h_high_pct=1.0,
        liquidity_24h_usd_approx=1_000_000.0,
        extended_move=False,
        reasons=("momentum",),
        market_phase=phase.value,
        evidence_grade=EvidenceGrade.QUALIFIED.value,
        operator_disposition=OperatorDisposition.DEEP_REVIEW.value,
        actionability_reasons=(),
    )
    card = scan_movers._compact_card(signal)
    if is_early_phase(phase):
        assert assessment.claims_early_discovery is True
        assert "EARLY WATCH" in card
    else:
        assert assessment.claims_early_discovery is False
        assert "EARLY WATCH" not in card
        assert "MARKET WATCH" in card


def test_confirmed_expansion_never_renders_early_watch():
    signal = SimpleNamespace(
        symbol="XUSD",
        stage="READY",
        reference_price=1.0,
        detection_timeframe="1H",
        momentum_1h_pct=3.0,
        momentum_6h_pct=5.0,
        momentum_state="ACCELERATING",
        continuation_confidence=80,
        continuation_confidence_is_probability=False,
        entry_quality=70,
        entry_recommendation="BREAKOUT_ENTRY_POSSIBLE",
        relative_volume=3.0,
        distance_to_24h_high_pct=1.0,
        liquidity_24h_usd_approx=1_000_000.0,
        extended_move=False,
        reasons=("momentum",),
        market_phase=MarketPhase.CONFIRMED_EXPANSION.value,
        evidence_grade=EvidenceGrade.QUALIFIED.value,
        operator_disposition=OperatorDisposition.DEEP_REVIEW.value,
        actionability_reasons=(),
    )
    card = scan_movers._compact_card(signal)
    assert "EARLY WATCH" not in card
    assert "MARKET WATCH" in card


def test_two_episodes_for_same_symbol_cannot_share_episode_id():
    from app.opip.early.timing_ledger import (
        MILESTONE_EXHAUSTION,
        MILESTONE_FIRST_OBSERVED,
        record_milestone,
    )

    first = resolve_episode_ledger(
        symbol="RAYUSD",
        decision_at=DECISION_AT,
        phase=MarketPhase.IGNITION,
    )
    exhausted = record_milestone(
        first,
        milestone=MILESTONE_FIRST_OBSERVED,
        observed_at=DECISION_AT,
        anchor_price=1.0,
    )
    exhausted = record_milestone(
        exhausted,
        milestone=MILESTONE_EXHAUSTION,
        observed_at=DECISION_AT + timedelta(hours=1),
        anchor_price=1.5,
    )
    later = DECISION_AT + timedelta(hours=50)
    second = resolve_episode_ledger(
        symbol="RAYUSD",
        decision_at=later,
        phase=MarketPhase.IGNITION,
        existing=exhausted,
    )
    assert first.episode_id != second.episode_id
    assert first.episode_id != "RAYUSD"
    assert second.episode_id != "RAYUSD"
    assert should_reset_episode(exhausted, decision_at=later, phase=MarketPhase.IGNITION)
    assert early_episode_id(symbol="RAYUSD", episode_started_at=DECISION_AT).startswith(
        "EP:RAYUSD:"
    )


def test_rolling_volume_change_is_not_labelled_relative_volume_change():
    from app.opip.early.shadow_observer import derive_delta_features

    features = derive_delta_features(
        [
            {
                "observed_at": "2026-03-04T11:00:00+00:00",
                "volume_24h": 100.0,
                "last_price": 1.0,
                "lift_from_24h_low_pct": 1.0,
                "distance_from_24h_high_pct": 2.0,
            },
            {
                "observed_at": "2026-03-04T12:00:00+00:00",
                "volume_24h": 150.0,
                "last_price": 1.1,
                "lift_from_24h_low_pct": 2.0,
                "distance_from_24h_high_pct": 1.0,
            },
        ]
    )
    assert features["relative_volume_change"] is None
    assert features["trade_count_acceleration"] is None
    assert features["rolling_24h_volume_change"] == pytest.approx(0.5)


def test_ignition_cohort_accepts_rolling_volume_proxy_without_fake_rvol():
    row = EarlyCandidateFeatures(
        identifier="IGNUSD",
        base_asset="IGN",
        lift_from_24h_low_pct=2.5,
        distance_from_24h_high_pct=1.5,
        notional_usd=200_000.0,
        rolling_24h_volume_change=0.5,
        relative_volume_change=None,
        trade_count_acceleration=None,
        momentum_acceleration=None,
    )
    selection = select_early_candidates([row], total_candidates=8)
    assert "IGNUSD" in selection.cohort_members("IGNITION")


def test_validation_layers_actually_executed_for_qualified_candidate():
    report = evaluate_early_watch_validations(
        market_data_validation=SimpleNamespace(
            qualified=True, status="PASS", rejection_reasons=[], candle_count=720
        ),
        symbol_identity_resolved=True,
        completed_candle_count=720,
        liquidity_24h_usd=1_500_000.0,
        ticker_last=1.0,
        latest_ohlc_close=1.0,
        ticker_bid=0.999,
        ticker_ask=1.001,
        finite_features=True,
        persistence_scans=3,
        prior_observation_count=4,
        duplicate_state_detected=False,
        native_flow_available=True,
        native_flow_bias="BULLISH",
        cross_market_status="CONFIRMED",
        execution_validation=SimpleNamespace(
            status="VALID", book_coverage_status="COMPLETE"
        ),
        volatility_regime=40.0,
    )
    for name in MANDATORY_CHECKS:
        assert report.result_for(name) is ValidationResult.PASS
    assert report.evidence_grade is EvidenceGrade.QUALIFIED
    assert report.check("native_flow_evidence").counts_toward_qualification is True
    assert report.check("cross_market_confirmation").counts_toward_qualification is True
    assert report.check("depth_and_slippage_estimate").counts_toward_qualification is False
    assert report.check("prior_observation_available").counts_toward_qualification is False
    assert report.check("relative_strength_percentile").counts_toward_qualification is False


def _mandatory_pass_kwargs(**overrides):
    kwargs = {
        "market_data_validation": SimpleNamespace(
            qualified=True, status="PASS", rejection_reasons=[], candle_count=720
        ),
        "symbol_identity_resolved": True,
        "completed_candle_count": 720,
        "liquidity_24h_usd": 1_500_000.0,
        "ticker_last": 1.0,
        "latest_ohlc_close": 1.0,
        "ticker_bid": 0.999,
        "ticker_ask": 1.001,
        "finite_features": True,
        "persistence_scans": 3,
        "prior_observation_count": 4,
        "duplicate_state_detected": False,
    }
    kwargs.update(overrides)
    return kwargs


def test_bearish_native_flow_cannot_help_qualify():
    report = evaluate_early_watch_validations(
        **_mandatory_pass_kwargs(
            native_flow_available=True,
            native_flow_bias="BEARISH",
            cross_market_status="CONFIRMED",
            execution_validation=SimpleNamespace(
                status="VALID", book_coverage_status="COMPLETE"
            ),
        )
    )
    flow = report.check("native_flow_evidence")
    assert flow.contradicts_signal is True
    assert flow.counts_toward_qualification is False
    assert report.evidence_grade is not EvidenceGrade.QUALIFIED


def test_material_cross_market_divergence_cannot_help_qualify():
    report = evaluate_early_watch_validations(
        **_mandatory_pass_kwargs(
            native_flow_available=True,
            native_flow_bias="BULLISH",
            cross_market_status="MATERIAL_DIVERGENCE",
            execution_validation=SimpleNamespace(
                status="VALID", book_coverage_status="COMPLETE"
            ),
        )
    )
    market = report.check("cross_market_confirmation")
    assert market.contradicts_signal is True
    assert market.counts_toward_qualification is False
    assert market.source == "CROSS_MARKET"
    assert report.evidence_grade is not EvidenceGrade.QUALIFIED


def test_invalid_or_insufficient_execution_cannot_help_qualify():
    invalid = evaluate_early_watch_validations(
        **_mandatory_pass_kwargs(
            native_flow_available=True,
            native_flow_bias="BULLISH",
            cross_market_status="CONFIRMED",
            execution_validation=SimpleNamespace(
                status="INVALID", book_coverage_status="UNAVAILABLE"
            ),
        )
    )
    assert invalid.check("depth_and_slippage_estimate").counts_toward_qualification is False
    assert invalid.check("depth_and_slippage_estimate").contradicts_signal is True

    insufficient = evaluate_early_watch_validations(
        **_mandatory_pass_kwargs(
            native_flow_available=True,
            native_flow_bias="BULLISH",
            cross_market_status="CONFIRMED",
            execution_validation=SimpleNamespace(
                status="VALID", book_coverage_status="INSUFFICIENT"
            ),
        )
    )
    assert insufficient.check("depth_and_slippage_estimate").counts_toward_qualification is False
    assert insufficient.check("depth_and_slippage_estimate").contradicts_signal is True


def test_low_relative_strength_and_negative_rank_velocity_cannot_help_qualify():
    report = evaluate_early_watch_validations(
        **_mandatory_pass_kwargs(
            relative_strength_percentile=5.0,
            rank_velocity=-3.0,
            volatility_regime=40.0,
        )
    )
    assert report.evidence_grade is not EvidenceGrade.QUALIFIED
    assert report.check("relative_strength_percentile").counts_toward_qualification is False
    assert report.check("rank_velocity").counts_toward_qualification is False
    assert report.check("volatility_regime").counts_toward_qualification is False


def test_prior_observation_and_atr_are_not_independent_confirmation():
    report = evaluate_early_watch_validations(
        **_mandatory_pass_kwargs(
            prior_observation_count=8,
            persistence_scans=4,
            signal_quality_history_continuous=True,
            volatility_regime=88.0,
        )
    )
    assert report.check("prior_observation_available").counts_toward_qualification is False
    assert report.check("signal_quality_history_continuity").counts_toward_qualification is False
    assert report.check("volatility_regime").counts_toward_qualification is False
    assert report.evidence_grade is not EvidenceGrade.QUALIFIED


def test_availability_without_verdict_is_not_qualified():
    report = evaluate_early_watch_validations(
        **_mandatory_pass_kwargs(
            native_flow_available=True,
            cross_venue_available=True,
            depth_slippage_available=True,
        )
    )
    assert report.check("native_flow_evidence").counts_toward_qualification is False
    assert report.check("cross_venue_reference").counts_toward_qualification is False
    assert report.check("depth_and_slippage_estimate").counts_toward_qualification is False
    assert report.evidence_grade is not EvidenceGrade.QUALIFIED


def _phase_signal(phase: MarketPhase, *, disposition=OperatorDisposition.DEEP_REVIEW):
    return SimpleNamespace(
        symbol="RAYUSD",
        stage="READY",
        reference_price=1.0,
        detection_timeframe="1H",
        momentum_1h_pct=3.0,
        momentum_6h_pct=5.0,
        momentum_24h_pct=8.0,
        momentum_state="ACCELERATING",
        continuation_confidence=80,
        continuation_confidence_is_probability=False,
        entry_quality=70,
        entry_recommendation="BREAKOUT_ENTRY_POSSIBLE",
        relative_volume=3.0,
        distance_to_24h_high_pct=1.0,
        liquidity_24h_usd_approx=1_000_000.0,
        extended_move=phase in {MarketPhase.LATE_EXTENSION, MarketPhase.EXHAUSTION_RISK},
        reasons=("momentum",),
        warnings=(),
        market_phase=phase.value,
        evidence_grade=EvidenceGrade.QUALIFIED.value,
        operator_disposition=(
            OperatorDisposition.DO_NOT_CHASE.value
            if phase in {MarketPhase.LATE_EXTENSION, MarketPhase.EXHAUSTION_RISK}
            else disposition.value
        ),
        actionability_reasons=(),
    )


@pytest.mark.parametrize("phase", list(MarketPhase))
def test_send_formatter_never_leaks_ready_or_false_early(phase: MarketPhase):
    from app.opip.early.operator_semantics import misleading_early_language
    from app.services.movement_discovery_v2 import format_early_mover_message

    signal = _phase_signal(phase)
    message = format_early_mover_message(signal)
    assessment = build_operator_assessment(
        symbol=signal.symbol,
        phase=phase,
        grade=EvidenceGrade.QUALIFIED,
        disposition=signal.operator_disposition,
    )
    assert " — READY" not in message
    assert signal.stage not in message
    assert misleading_early_language(message, assessment) == ()
    if is_early_phase(phase) and assessment.disposition not in {
        OperatorDisposition.DO_NOT_CHASE,
        OperatorDisposition.NO_ACTION,
    }:
        assert "EARLY WATCH" in message
    else:
        assert "EARLY WATCH" not in message
        assert "MARKET WATCH" in message
        assert "Early movement conditions detected" not in message


def test_late_extension_telegram_path_has_neither_early_nor_ready():
    from app.services.movement_discovery_v2 import format_early_mover_message

    message = format_early_mover_message(_phase_signal(MarketPhase.LATE_EXTENSION))
    assert "EARLY WATCH" not in message
    assert " — READY" not in message
    assert "Market: LATE_EXTENSION" in message
    assert "Disposition: DO NOT CHASE" in message


def test_promoted_selector_has_one_authoritative_outcome_per_instrument(monkeypatch):
    from app.opip.early.replay import (
        authoritative_outcomes_by_instrument,
        verify_forensic_replay_matches_production,
    )

    ranked = [_mover(base=f"E{i}", lift=18.0, distance=0.3, score=90.0 - i) for i in range(45)]
    ranked.append(_mover(base="IGN1", lift=2.4, distance=1.5, score=1.0))
    history = {
        "IGN1USD": [
            {
                "observed_at": (DECISION_AT - timedelta(minutes=20)).isoformat(),
                "last_price": 1.0,
                "volume_24h": 1_000.0,
                "lift_from_24h_low_pct": 1.0,
                "distance_from_24h_high_pct": 3.0,
            },
            {
                "observed_at": DECISION_AT.isoformat(),
                "last_price": 1.04,
                "volume_24h": 2_000.0,
                "lift_from_24h_low_pct": 2.4,
                "distance_from_24h_high_pct": 1.5,
            },
        ]
    }
    qualifying = _qualifying_snapshot(symbol="IGN1USD")
    captured: list[dict] = []

    def fake_discover(*args, **kwargs):
        on_ranked = kwargs.get("on_ranked")
        if on_ranked is not None:
            on_ranked(list(ranked))
        return ranked[:40]

    def fake_analyze(symbol, *args, **kwargs):
        if str(symbol).startswith("IGN1"):
            return "ok", qualifying, None
        return "skip", None, "not ignition"

    monkeypatch.setattr(discovery, "discover_coarse_movers", fake_discover)
    monkeypatch.setattr(discovery, "analyze_symbol", fake_analyze)
    monkeypatch.setattr(discovery, "_enrich_bounded_candidate_evidence", lambda *a, **k: None)

    discovery.scan_early_movers(
        max_candidates=40,
        on_coarse_evaluated=captured.append,
        on_evaluated=captured.append,
        selector_promoted=True,
        validation_parity_enabled=True,
        prior_observation_counts={"IGN1": 4},
        persistence_scans={"IGN1": 3},
        observation_history=history,
        scan_id="promoted-scan",
    )

    ignition_rows = [row for row in captured if str(row.get("raw_identifier", "")).startswith("IGN1")]
    outcomes = [row["outcome"] for row in ignition_rows]
    assert "COARSE_RANK_LIMIT" not in outcomes
    assert outcomes == ["ADVANCED"]
    assert ignition_rows[0]["metadata"]["authoritative"] is True
    assert ignition_rows[0]["metadata"]["selector_comparison"]["legacy_selector"]["selected"] is False
    assert ignition_rows[0]["metadata"]["selector_comparison"]["promoted_selector"]["selected"] is True

    rejected = [
        row
        for row in captured
        if row.get("outcome") == "COARSE_RANK_LIMIT"
        and row.get("metadata", {}).get("selector_comparison", {}).get("legacy_selector", {}).get("selected")
    ]
    assert rejected
    assert all(row["metadata"]["authoritative"] is True for row in rejected)
    assert all(
        row["metadata"].get("stage0_evidence_schema_version") == 1
        for row in rejected
    )
    assert all(row["metadata"].get("coarse_rank") for row in rejected)
    assert all(
        row["metadata"]["selector_comparison"]["promoted_selector"]["selected"] is False
        for row in rejected
    )

    production = {}
    replay_input = []
    for row in captured:
        instrument = f"KRAKEN:{row['raw_identifier']}"
        replay_input.append(
            {
                "venue_instrument_id": instrument,
                "outcome": row["outcome"],
                "scan_id": "promoted-scan",
                "observed_at": DECISION_AT.isoformat(),
                "metadata": row.get("metadata"),
            }
        )
        if bool((row.get("metadata") or {}).get("authoritative")):
            assert instrument not in production
            production[instrument] = row["outcome"]

    mapped = authoritative_outcomes_by_instrument(replay_input)
    assert mapped["KRAKEN:IGN1USD"] == "ADVANCED"
    assert mapped == production
    verification = verify_forensic_replay_matches_production(
        replayed=mapped,
        production=production,
    )
    assert verification["exact_match"] is True


def test_authoritative_outcomes_prefer_latest_same_authority_observation():
    from app.opip.early.replay import authoritative_outcomes_by_instrument

    earlier = (DECISION_AT - timedelta(minutes=10)).isoformat()
    later = DECISION_AT.isoformat()
    rows = [
        {
            "venue_instrument_id": "KRAKEN:FOOUSD",
            "outcome": "BELOW_THRESHOLD",
            "observed_at": earlier,
            "metadata": {"authoritative": True},
        },
        {
            "venue_instrument_id": "KRAKEN:FOOUSD",
            "outcome": "ADVANCED",
            "observed_at": later,
            "metadata": {"authoritative": True},
        },
    ]

    assert authoritative_outcomes_by_instrument(rows)["KRAKEN:FOOUSD"] == "ADVANCED"
    assert authoritative_outcomes_by_instrument(list(reversed(rows)))["KRAKEN:FOOUSD"] == "ADVANCED"


def test_unknown_symbol_identity_status_is_none_and_fails_closed():
    coarse = _mover()
    snapshot = SimpleNamespace(
        independent_market_reference=SimpleNamespace(status="UNAVAILABLE"),
        symbol="IGNUSD",
    )

    assert discovery._resolve_symbol_identity(coarse, snapshot, explicit=None) is None

    report = evaluate_early_watch_validations(symbol_identity_resolved=None)
    identity = report.check("canonical_symbol_identity")
    assert identity is not None
    assert identity.result is ValidationResult.NOT_EVALUATED
    assert report.evidence_grade is not EvidenceGrade.QUALIFIED


def test_confirmed_and_warn_reference_identity_are_accepted():
    coarse = _mover()
    for status in ("CONFIRMED", "WARN"):
        snapshot = SimpleNamespace(
            independent_market_reference=SimpleNamespace(
                status=status,
                mapping_status="UNIQUE",
                coingecko_id="ignition-token",
                coingecko_name="Ignition",
            ),
            symbol="IGNUSD",
        )
        assert discovery._resolve_symbol_identity(coarse, snapshot, explicit=None) is True


def test_confirmed_status_with_ambiguous_mapping_is_rejected():
    coarse = _mover()
    snapshot = SimpleNamespace(
        independent_market_reference=SimpleNamespace(
            status="CONFIRMED",
            mapping_status="AMBIGUOUS",
            coingecko_id="ignition-token",
            coingecko_name="Ignition",
        ),
        symbol="IGNUSD",
    )
    assert discovery._resolve_symbol_identity(coarse, snapshot, explicit=None) is False


def test_confirmed_status_without_identity_fields_fails_closed():
    coarse = _mover()
    snapshot = SimpleNamespace(
        independent_market_reference=SimpleNamespace(
            status="CONFIRMED",
            mapping_status="UNIQUE",
            coingecko_id=None,
            coingecko_name=None,
        ),
        symbol="IGNUSD",
    )
    assert discovery._resolve_symbol_identity(coarse, snapshot, explicit=None) is None
    missing_mapping = SimpleNamespace(
        independent_market_reference=SimpleNamespace(status="CONFIRMED"),
        symbol="IGNUSD",
    )
    assert discovery._resolve_symbol_identity(coarse, missing_mapping, explicit=None) is None
    id_only = SimpleNamespace(
        independent_market_reference=SimpleNamespace(
            status="CONFIRMED",
            mapping_status="UNIQUE",
            coingecko_id="ignition-token",
            coingecko_name=None,
        ),
        symbol="IGNUSD",
    )
    name_only = SimpleNamespace(
        independent_market_reference=SimpleNamespace(
            status="CONFIRMED",
            mapping_status="UNIQUE",
            coingecko_id=None,
            coingecko_name="Ignition",
        ),
        symbol="IGNUSD",
    )
    assert discovery._resolve_symbol_identity(coarse, id_only, explicit=None) is None
    assert discovery._resolve_symbol_identity(coarse, name_only, explicit=None) is None


def test_evaluate_reference_market_confirmed_identity_is_accepted():
    from app.scanner.models import MarketSnapshot
    from app.scanner.reference_market_validation import evaluate_reference_market

    coarse = _mover()
    snapshot = MarketSnapshot(
        symbol="IGNUSD",
        last_price=1.0,
        ema20=1.0,
        ema50=1.0,
        ema200=1.0,
        rsi=50,
        macd_line=0.0,
        macd_signal=0.0,
        macd_histogram=0.0,
        atr=0.1,
        atr_pct=1.0,
        volume_ratio=1.0,
        technical_score=50,
        trend="bullish",
        ticker_last=1.0,
        primary_pair="IGNUSD",
        underlying_asset="IGN",
        primary_quote_currency="USD",
    )
    reference = evaluate_reference_market(
        snapshot,
        [
            {
                "id": "ignition-token",
                "symbol": "ign",
                "name": "Ignition",
                "current_price": 1.0,
                "last_updated": DECISION_AT.isoformat(),
            }
        ],
        usdt_usd_rate=None,
        api_mode="DEMO",
        now=DECISION_AT,
    )
    snapshot.independent_market_reference = reference
    assert reference.status == "CONFIRMED"
    assert reference.mapping_status == "UNIQUE"
    assert discovery._resolve_symbol_identity(coarse, snapshot, explicit=None) is True


def test_stale_reference_identity_is_unknown_and_fails_closed():
    coarse = _mover()
    snapshot = SimpleNamespace(
        independent_market_reference=SimpleNamespace(status="STALE"),
        symbol="IGNUSD",
    )
    assert discovery._resolve_symbol_identity(coarse, snapshot, explicit=None) is None
    report = evaluate_early_watch_validations(symbol_identity_resolved=None)
    assert report.check("canonical_symbol_identity").result is ValidationResult.NOT_EVALUATED
    assert report.evidence_grade is not EvidenceGrade.QUALIFIED


def test_material_divergence_reference_identity_is_rejected():
    coarse = _mover()
    snapshot = SimpleNamespace(
        independent_market_reference=SimpleNamespace(status="MATERIAL_DIVERGENCE"),
        symbol="IGNUSD",
    )
    assert discovery._resolve_symbol_identity(coarse, snapshot, explicit=None) is False


def test_native_flow_plus_depth_does_not_satisfy_two_family_rule():
    """Native flow and depth share the Kraken PreTrade book on the bounded path."""
    report = evaluate_early_watch_validations(
        **_mandatory_pass_kwargs(
            native_flow_available=True,
            native_flow_bias="BULLISH",
            execution_validation=SimpleNamespace(
                status="VALID", book_coverage_status="COMPLETE"
            ),
        )
    )
    assert report.check("native_flow_evidence").counts_toward_qualification is True
    assert report.check("depth_and_slippage_estimate").result is ValidationResult.PASS
    assert report.check("depth_and_slippage_estimate").counts_toward_qualification is False
    assert corroborating_family_count(report.checks) == 1
    assert report.evidence_grade is not EvidenceGrade.QUALIFIED


def test_observation_context_does_not_reload_when_history_is_supplied(monkeypatch):
    calls = {"n": 0}

    def fake_load(*_args, **_kwargs):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(
        "app.opip.early.observation_context.load_observation_history",
        fake_load,
    )
    build_observation_context(history={"RAYUSD": []})
    assert calls["n"] == 0
    build_observation_context()
    assert calls["n"] == 1


def test_ledger_retention_keeps_edit_only_lifecycle_rows(tmp_path):
    from app.opip.early import shadow_observer

    path = tmp_path / "early_timing_ledger_state.json"
    edited_at = DECISION_AT.isoformat()
    state = {
        "EP:FOOUSD:1": {
            "symbol": "FOOUSD",
            "episode_id": "EP:FOOUSD:1",
            "milestones": {},
            "card_edited_at": edited_at,
            "card_edit_count": 1,
        },
        "_symbol_episode_index": {"FOOUSD": "EP:FOOUSD:1"},
    }

    assert shadow_observer._latest_milestone_moment(state["EP:FOOUSD:1"]) is not None
    shadow_observer._save_ledger_state(state, path=path, now=DECISION_AT)
    saved = shadow_observer._load_ledger_state(path)
    assert "EP:FOOUSD:1" in saved
    assert not list(tmp_path.glob("*.tmp"))


def test_card_delivery_capture_fails_soft(monkeypatch, tmp_path):
    from app.opip.early import shadow_observer

    monkeypatch.setattr(
        shadow_observer,
        "_record_card_delivery_outcomes",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    rows = shadow_observer.record_card_delivery_outcomes(
        {"FOOUSD": ("CREATED", True)},
        state_path=tmp_path / "ledger.json",
        environ={"OPIP_EARLY_TIMING_LEDGER_ENABLED": "true"},
        decision_at=DECISION_AT,
    )
    assert rows == []


def test_scan_early_movers_shadow_failure_does_not_drop_signals(monkeypatch):
    ranked = [_mover(base="IGN1", lift=3.0, distance=1.5, score=90.0)]

    def fake_discover(*args, **kwargs):
        on_ranked = kwargs.get("on_ranked")
        if on_ranked is not None:
            on_ranked(list(ranked))
        return list(ranked)

    monkeypatch.setattr(discovery, "discover_coarse_movers", fake_discover)
    monkeypatch.setattr(
        discovery,
        "analyze_symbol",
        lambda *a, **k: ("ok", _qualifying_snapshot(symbol="IGN1USD"), None),
    )
    monkeypatch.setattr(discovery, "_enrich_bounded_candidate_evidence", lambda *a, **k: None)
    monkeypatch.setattr(
        discovery,
        "observe_scan_shadow",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("shadow boom")),
    )

    coarse, signals = discovery.scan_early_movers(
        max_candidates=1,
        selector_promoted=False,
        validation_parity_enabled=False,
        prior_observation_counts={"IGN1": 4},
        persistence_scans={"IGN1": 3},
    )
    assert coarse
    assert signals


def test_v22_shadow_handles_deep_evaluation_rejection(monkeypatch):
    from app.services import movement_discovery_v2_2_shadow as v22
    from app.services.movement_discovery_v2 import DeepEvaluationRejection

    rejection = DeepEvaluationRejection(
        achieved_score=10.0,
        required_score=40.0,
        discovery_score_raw=10.0,
        components={},
    )
    candidate = SimpleNamespace(
        cohort="CURRENT_MOMENTUM",
        mover=_mover(base="LOW", pair="LOWUSD"),
        challenger_score=1.0,
        lift_today_pct=1.0,
    )
    monkeypatch.setattr(v22, "discover_v22_shadow_candidates", lambda *a, **k: [candidate])
    monkeypatch.setattr(v22, "analyze_symbol", lambda *a, **k: ("ok", _qualifying_snapshot(symbol="LOWUSD"), None))
    monkeypatch.setattr(v22, "evaluate_early_mover", lambda *a, **k: rejection)

    _candidates, results, stats = v22.scan_v22_shadow(total_candidates=1)
    assert stats["analyzed"] == 1
    assert results[0].v21_signal_present is False
    assert results[0].v21_stage is None


def _supportive_native_flow() -> ValidationCheck:
    return ValidationCheck(
        CHECK_NATIVE_FLOW,
        ValidationResult.PASS,
        ValidationClass.SOFT,
        stance=EvidenceStance.SUPPORTIVE,
        supports_long_continuation=True,
        counts_toward_qualification=True,
    )


def test_duplicate_family_checks_count_once_toward_qualified():
    assert corroborating_family_count(
        [_supportive_native_flow(), _supportive_native_flow()]
    ) == 1


def test_non_family_supportive_flag_does_not_count_toward_qualified():
    advisory = ValidationCheck(
        CHECK_SOCIAL,
        ValidationResult.PASS,
        ValidationClass.ADVISORY,
        stance=EvidenceStance.SUPPORTIVE,
        supports_long_continuation=True,
        counts_toward_qualification=True,
    )
    assert corroborating_family_count([advisory]) == 0
    assert corroborating_family_count([advisory, _supportive_native_flow()]) == 1


def test_coarse_unavailable_rows_carry_stage0_schema_envelope():
    from app.opip.early.stage0_evidence import (
        STAGE0_EVIDENCE_SCHEMA_VERSION,
        build_coarse_status_metadata,
    )

    metadata = build_coarse_status_metadata(universe_count=9)
    assert metadata["stage0_evidence_schema_version"] == STAGE0_EVIDENCE_SCHEMA_VERSION
    assert metadata["universe_count"] == 9
    assert metadata["measurement_only"] is True
    assert metadata["trade_authority_changed"] is False


def test_timing_ledger_failure_does_not_drop_selector_shadow_rows(monkeypatch):
    from app.opip.early import shadow_observer

    monkeypatch.setattr(
        shadow_observer,
        "observe_timing_milestones",
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("lock")),
    )
    rows = shadow_observer.observe_scan_shadow(
        all_movers=[_mover()],
        production_selection=[_mover()],
        signals=[],
        universe_count=1,
        scan_id="OPIPS:early",
        decision_at=DECISION_AT,
        history={},
        environ={
            "OPIP_EARLY_SELECTOR_SHADOW_ENABLED": "true",
            "OPIP_EARLY_TIMING_LEDGER_ENABLED": "true",
        },
    )
    assert rows
    assert rows[0]["record_type"] == shadow_observer.RECORD_SELECTOR_COMPARISON


def test_report_from_snapshot_cannot_qualify_by_hardcoding_finite_features():
    snapshot = SimpleNamespace(
        market_data_validation=SimpleNamespace(
            qualified=True,
            status="PASS",
            rejection_reasons=[],
            candle_count=720,
            ticker_last=100.0,
            latest_ohlc_close=100.2,
            ticker_vs_ohlc_difference_pct=0.2,
        ),
        ticker_bid=99.95,
        ticker_ask=100.05,
        combined_24h_liquidity_usd=1_500_000.0,
        native_flow_evidence=SimpleNamespace(available=True, bias="BULLISH"),
        cross_pair_confirmation_status="CONFIRMED",
        execution_validation=SimpleNamespace(status="VALID", book_coverage_status="COMPLETE"),
        atr_percentile=55.0,
    )
    report = report_from_snapshot(
        snapshot,
        persistence_scans=3,
        prior_observation_count=5,
        symbol_identity_resolved=True,
        overrides={"duplicate_state_detected": False},
    )

    assert report.result_for(CHECK_FINITE_FEATURES) is ValidationResult.NOT_EVALUATED
    assert report.evidence_grade is not EvidenceGrade.QUALIFIED


def test_report_from_snapshot_rejects_non_finite_measured_decision_features():
    snapshot = SimpleNamespace(
        last_price=float("nan"),
        confirmed_price_change_1h_pct=1.0,
    )
    report = report_from_snapshot(snapshot)

    assert report.result_for(CHECK_FINITE_FEATURES) is ValidationResult.FAIL
    assert report.evidence_grade is EvidenceGrade.REJECTED


def test_stage0_taxonomy_tokens_round_trip_without_enum_class_prefix():
    from app.opip.early.stage0_evidence import build_advanced_metadata

    metadata = build_advanced_metadata(
        universe_count=12,
        market_phase=MarketPhase.IGNITION,
        evidence_grade=EvidenceGrade.QUALIFIED,
        operator_disposition=OperatorDisposition.DEEP_REVIEW,
    )

    assert metadata["market_phase"] == "IGNITION"
    assert metadata["evidence_grade"] == "QUALIFIED"
    assert metadata["operator_disposition"] == "DEEP_REVIEW"
    assert "MarketPhase." not in str(metadata["market_phase"])
    assert coerce_market_phase(metadata["market_phase"]) is MarketPhase.IGNITION
    assert coerce_evidence_grade(metadata["evidence_grade"]) is EvidenceGrade.QUALIFIED
    assert (
        coerce_operator_disposition(metadata["operator_disposition"])
        is OperatorDisposition.DEEP_REVIEW
    )

    as_strings = build_advanced_metadata(
        universe_count=12,
        market_phase="LATE_EXTENSION",
        evidence_grade="CORROBORATED",
        operator_disposition="DO_NOT_CHASE",
    )
    assert coerce_market_phase(as_strings["market_phase"]) is MarketPhase.LATE_EXTENSION
    assert coerce_evidence_grade(as_strings["evidence_grade"]) is EvidenceGrade.CORROBORATED
    assert (
        coerce_operator_disposition(as_strings["operator_disposition"])
        is OperatorDisposition.DO_NOT_CHASE
    )


def test_early_do_not_chase_empty_reasons_does_not_claim_early_movement():
    signal = SimpleNamespace(
        symbol="IGNUSD",
        stage="READY",
        reasons=(),
        warnings=(),
        market_phase=MarketPhase.IGNITION.value,
        evidence_grade=EvidenceGrade.QUALIFIED.value,
        operator_disposition=OperatorDisposition.DO_NOT_CHASE.value,
        actionability_reasons=("move is already extended",),
        reference_price=1.0,
        detection_timeframe="1H",
        momentum_1h_pct=2.0,
        momentum_6h_pct=3.0,
        momentum_24h_pct=4.0,
        momentum_state="ACCELERATING",
        continuation_confidence=80,
        continuation_confidence_is_probability=False,
        entry_quality=70,
        entry_recommendation="WAIT_FOR_PULLBACK",
        relative_volume=2.0,
        distance_to_24h_high_pct=1.0,
        liquidity_24h_usd_approx=1_000_000.0,
        extended_move=False,
    )
    assessment = assessment_from_signal(signal)

    assert assessment.claims_early_discovery is True
    assert assessment.may_use_early_wording is False
    assert operator_headline(assessment) == "🔎 MARKET WATCH"
    assert operator_why_now(signal, assessment) == "Market movement conditions detected"
    assert "Early movement conditions detected" not in operator_why_now(signal, assessment)
    card = scan_movers._compact_card(signal)
    assert "EARLY WATCH" not in card
    assert "Early movement conditions detected" not in card


def _observation_row(*, minutes_ago: int, price: float, volume: float) -> dict[str, float | str]:
    return {
        "observed_at": (DECISION_AT - timedelta(minutes=minutes_ago)).isoformat(),
        "last_price": price,
        "volume_24h": volume,
        "notional_24h_usd_approx": volume * price,
        "high_24h": price * 1.1,
        "low_24h": price * 0.9,
        "lift_from_24h_low_pct": 2.0,
        "distance_from_24h_high_pct": 3.0,
    }


def test_ethbtc_history_does_not_inflate_eth_base_asset_persistence():
    eth_usd = [
        _observation_row(minutes_ago=30, price=2_000.0, volume=1_000.0),
        _observation_row(minutes_ago=20, price=2_010.0, volume=1_200.0),
        _observation_row(minutes_ago=10, price=2_020.0, volume=1_500.0),
    ]
    eth_btc = [
        _observation_row(minutes_ago=90 - (i * 10), price=0.05 + i * 0.001, volume=500.0 + i)
        for i in range(10)
    ]
    context = build_observation_context(history={"ETHUSD": eth_usd, "ETHBTC": eth_btc})

    assert context.prior_observation_counts["ETH"] == 2
    assert context.prior_observation_counts["ETHUSD"] == 2
    assert context.prior_observation_counts["ETHBTC"] == 9
    assert context.prior_observation_counts["ETH"] < context.prior_observation_counts["ETHBTC"]
    assert context.persistence_scans.get("ETH", 0) <= context.persistence_scans.get("ETHUSD", 0)


def test_forensic_replay_tolerates_malformed_nested_venue_instrument():
    from app.opip.early.replay import forensic_rows

    rows = forensic_rows(
        [
            {"outcome": "ADVANCED", "venue_instrument": "not-a-mapping"},
            {"outcome": "ADVANCED", "venue_instrument": ["KRAKEN:BAD"]},
            {
                "outcome": "SELECTED",
                "venue_instrument_id": "KRAKEN:FOOUSD",
                "venue_instrument": "still-not-a-mapping",
            },
            {
                "outcome": "SELECTED",
                "venue_instrument": {"venue_instrument_id": "KRAKEN:BARUSD"},
            },
        ]
    )

    assert [row.venue_instrument_id for row in rows] == [
        "",
        "",
        "KRAKEN:FOOUSD",
        "KRAKEN:BARUSD",
    ]


def test_ledger_from_dict_tolerates_malformed_telemetry_fields():
    ledger = ledger_from_dict(
        {
            "symbol": "FOOUSD",
            "episode_id": "ep-1",
            "schema_version": "not-an-int",
            "milestones": "not-a-mapping",
            "anchor_prices": {
                "first_observed_at": "bad-price",
                "first_qualified_at": 1.25,
            },
            "card_created_at": "not-a-timestamp",
            "card_edited_at": "2026-03-04T12:00:00+00:00",
            "card_edit_count": "x",
            "delivered_notification_count": object(),
        }
    )

    assert ledger.symbol == "FOOUSD"
    assert ledger.episode_id == "ep-1"
    assert ledger.schema_version == 1
    assert ledger.milestones == {}
    assert ledger.anchor_prices == {"first_qualified_at": 1.25}
    assert ledger.card_created_at is None
    assert ledger.card_edited_at is not None
    assert ledger.card_edit_count == 0
    assert ledger.delivered_notification_count == 0


