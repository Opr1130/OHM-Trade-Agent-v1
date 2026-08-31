from datetime import datetime, timedelta, timezone

import pytest

from app.opip.ml.temporal import TemporalIntegrityError
from app.scanner.models import MarketSnapshot
from app.services import entry_watch_queue, trade_quality_assessor
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.trade_feature_snapshot import build_trade_feature_snapshot
from app.services.trade_quality_assessor import (
    assess_entry,
    assess_trade_quality,
)


def snapshot(
    *,
    trend="bullish",
    technical_score=90,
    volume_ratio=1.8,
    liquidity=2_000_000.0,
    price=100.0,
    ema20=99.0,
    atr=2.0,
):
    row = MarketSnapshot(
        symbol="SOLUSD",
        last_price=price,
        ema20=ema20,
        ema50=95.0,
        ema200=90.0,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=atr,
        atr_pct=2.0,
        volume_ratio=volume_ratio,
        technical_score=technical_score,
        trend=trend,
        momentum_6h_pct=3.0,
        momentum_24h_pct=8.0,
        momentum_72h_pct=12.0,
        combined_24h_liquidity_usd=liquidity,
        cross_pair_confirmation_status="CONFIRMED",
        underlying_asset="SOL",
        primary_pair="SOLUSD",
        primary_quote_currency="USD",
        movement_data_status="AVAILABLE",
    )
    row.market_data_validation = type(
        "MarketData",
        (),
        {"status": "PASS"},
    )()
    row.execution_validation = type(
        "Execution",
        (),
        {
            "status": "VALID",
            "estimated_visible_round_trip_market_drag_pct": 0.2,
        },
    )()
    row.independent_market_reference = type(
        "Reference",
        (),
        {
            "status": "CONFIRMED",
            "price_divergence_pct": 0.1,
        },
    )()
    row.news_context = type("News", (), {"status": "AVAILABLE"})()
    row.scheduled_catalyst_context = type(
        "Catalyst",
        (),
        {"status": "UNRESOLVED"},
    )()
    return row


def plan(*, valid_now=True):
    return EntryExitPlan(
        symbol="SOLUSD",
        valid_now=valid_now,
        entry_style="pullback_or_retest" if valid_now else "wait_for_pullback",
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="test",
        direction="LONG",
    )


def feature_snapshot(market=None):
    return build_trade_feature_snapshot(
        market or snapshot(),
        decision_at=datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc),
        episode_id="W9EP:1",
        candidate_id="W9C:1",
        regime="NEUTRAL",
    )


def test_feature_snapshot_is_immutable_and_point_in_time():
    sealed = feature_snapshot()
    mapping = sealed.ml_feature_mapping()
    assert sealed.snapshot_id.startswith("MLSNAP:")
    assert "technical_score_input" not in mapping
    assert mapping["rsi"] == 55.0
    assert mapping["macd_histogram"] == 0.5
    assert mapping["execution_availability"] == "VALID"
    assert mapping["catalyst_availability"] == "UNRESOLVED"
    assert sealed.max_visible_at_utc == sealed.decision_at_utc


def test_feature_snapshot_rejects_naive_decision_time():
    with pytest.raises(ValueError):
        build_trade_feature_snapshot(
            snapshot(),
            decision_at=datetime(2026, 8, 30, 20, 0),
            episode_id="W9EP:2",
            candidate_id="W9C:2",
            regime="NEUTRAL",
        )


def test_strong_continuation_and_valid_entry_are_actionable():
    assessment = assess_trade_quality(feature_snapshot(), plan())
    assert assessment.continuation.decision == "PASS"
    assert assessment.entry.decision == "PASS"
    assert assessment.actionable is True


def test_strong_continuation_but_invalid_now_waits():
    assessment = assess_trade_quality(feature_snapshot(), plan(valid_now=False))
    assert assessment.continuation.decision == "PASS"
    assert assessment.entry.decision == "WAIT"
    assert assessment.actionable is False


def test_severe_extension_vetoes_entry():
    stretched = snapshot(price=110.0, ema20=99.0, atr=2.0)
    assessment = assess_trade_quality(feature_snapshot(stretched), plan())
    assert assessment.continuation.decision == "FAIL"
    assert "SEVERE_EXTENSION" in assessment.continuation.vetoes
    assert assessment.actionable is False


def test_low_liquidity_vetoes_continuation():
    thin = snapshot(liquidity=50_000.0)
    assessment = assess_trade_quality(feature_snapshot(thin), plan())
    assert assessment.continuation.decision == "FAIL"
    assert "LIQUIDITY_BELOW_CONFIGURED_MINIMUM" in assessment.continuation.vetoes


def test_missing_liquidity_cannot_become_actionable():
    unverified = snapshot(liquidity=None)
    assessment = assess_trade_quality(feature_snapshot(unverified), plan())
    assert assessment.continuation.decision == "FAIL"
    assert "LIQUIDITY_UNAVAILABLE" in assessment.continuation.vetoes
    assert assessment.actionable is False


def test_runtime_liquidity_floor_is_honored():
    market = snapshot(liquidity=150_000.0)
    assessment = assess_trade_quality(
        feature_snapshot(market),
        plan(),
        min_liquidity_usd=200_000.0,
    )
    assert assessment.continuation.decision == "FAIL"
    assert "LIQUIDITY_BELOW_CONFIGURED_MINIMUM" in assessment.continuation.vetoes


def test_default_liquidity_floor_uses_resolved_settings(monkeypatch):
    monkeypatch.setattr(
        trade_quality_assessor,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {"signal_quality_min_liquidity_usd": 200_000.0},
        )(),
    )
    market = snapshot(liquidity=150_000.0)

    assessment = assess_trade_quality(feature_snapshot(market), plan())

    assert assessment.continuation.decision == "FAIL"
    assert "LIQUIDITY_BELOW_CONFIGURED_MINIMUM" in assessment.continuation.vetoes


def test_execution_unavailable_cannot_be_actionable():
    market = snapshot()
    market.execution_validation = None
    assessment = assess_trade_quality(feature_snapshot(market), plan())
    assert assessment.continuation.decision == "FAIL"
    assert "EXECUTION_UNAVAILABLE" in assessment.continuation.vetoes
    assert assessment.actionable is False


def test_execution_drag_must_be_available():
    market = snapshot()
    market.execution_validation = type(
        "Execution",
        (),
        {
            "status": "VALID",
            "estimated_visible_round_trip_market_drag_pct": None,
        },
    )()
    assessment = assess_trade_quality(feature_snapshot(market), plan())
    assert assessment.continuation.decision == "FAIL"
    assert "EXECUTION_DRAG_UNAVAILABLE" in assessment.continuation.vetoes


def test_market_data_unavailable_cannot_be_actionable():
    market = snapshot()
    market.market_data_validation = None
    assessment = assess_trade_quality(feature_snapshot(market), plan())
    assert assessment.continuation.decision == "FAIL"
    assert "MARKET_DATA_UNAVAILABLE_OR_INVALID" in assessment.continuation.vetoes
    assert assessment.actionable is False


def test_market_data_reject_cannot_be_actionable():
    market = snapshot()
    market.market_data_validation = type(
        "MarketData",
        (),
        {"status": "REJECT"},
    )()
    assessment = assess_trade_quality(feature_snapshot(market), plan())
    assert assessment.continuation.decision == "FAIL"
    assert "MARKET_DATA_UNAVAILABLE_OR_INVALID" in assessment.continuation.vetoes


def test_entry_direction_must_match_feature_snapshot():
    assessment = assess_trade_quality(
        feature_snapshot(),
        EntryExitPlan(
            symbol="SOLUSD",
            valid_now=True,
            entry_style="test",
            entry_low=99.0,
            entry_high=100.0,
            chase_limit=98.0,
            stop_price=105.0,
            target_1=95.0,
            target_2=90.0,
            reward_to_risk_1=2.0,
            reward_to_risk_2=3.0,
            risk_level="low",
            reason="opposite",
            direction="SHORT",
        ),
    )
    assert assessment.entry.decision == "VETO"
    assert "DIRECTION_MISMATCH" in assessment.entry.reasons


def test_long_entry_geometry_must_order_stop_and_targets():
    bad = plan()
    bad.stop_price = 101.0
    assessment = assess_trade_quality(feature_snapshot(), bad)
    assert assessment.entry.decision == "VETO"
    assert "INVALID_DIRECTIONAL_GEOMETRY" in assessment.entry.reasons


def test_entry_assessment_requires_same_snapshot():
    first = feature_snapshot()
    second = build_trade_feature_snapshot(
        snapshot(),
        decision_at=datetime(2026, 8, 30, 20, 1, tzinfo=timezone.utc),
        episode_id="W9EP:3",
        candidate_id="W9C:3",
        regime="NEUTRAL",
    )
    continuation = assess_trade_quality(first, plan()).continuation
    with pytest.raises(ValueError, match="same snapshot"):
        assess_entry(second, plan(), continuation)


def test_entry_watch_is_bounded_due_and_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(
        entry_watch_queue,
        "ENTRY_WATCH_FILE",
        tmp_path / "entry_watch.json",
    )
    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)

    entry_watch_queue.enqueue_entry_watch(
        symbol="SOLUSD",
        direction="LONG",
        candidate_id="C1",
        continuation_score=80,
        now=now,
        ttl_seconds=300,
        recheck_seconds=60,
    )
    assert entry_watch_queue.due_entry_watch(now=now) == []

    due = entry_watch_queue.due_entry_watch(
        now=now + timedelta(seconds=61)
    )
    assert len(due) == 1
    assert due[0]["candidate_id"] == "C1"

    assert entry_watch_queue.defer_entry_watch(
        "SOLUSD",
        "LONG",
        now=now + timedelta(seconds=61),
        recheck_seconds=60,
    )
    assert entry_watch_queue.due_entry_watch(
        now=now + timedelta(seconds=62)
    ) == []

    assert entry_watch_queue.due_entry_watch(
        now=now + timedelta(seconds=301)
    ) == []