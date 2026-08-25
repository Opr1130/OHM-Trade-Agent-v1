from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.p1_intelligence_contracts import (
    CatalystContext,
    MarketDataBar,
    MarketDataSlice,
    build_live_scan_snapshot,
)


NOW = datetime(2026, 8, 24, 21, 30, tzinfo=timezone.utc)


def candidate(symbol="TESTUSD", **overrides):
    fields = dict(
        symbol=symbol,
        universe_size=200,
        stage="BREAKOUT_CANDIDATE",
        pattern="REACCELERATION",
        opportunity_score=78,
        explosion_potential_score=74,
        tradeability_score=72,
        pattern_strength_score=80,
        volume_acceleration_score=70,
        relative_strength_score=88,
        persistence_scans=3,
        exhaustion_penalty=10,
        exhaustion_band="LOW",
        relative_strength_percentile=95.0,
        liquidity_24h_usd_approx=2_000_000.0,
        suppressed=False,
        reasons=("test",),
        components={"near_high": 75.0, "bad": float("nan")},
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_live_snapshot_is_deterministic_point_in_time_and_non_authoritative():
    first = build_live_scan_snapshot(
        candidate(),
        decision_at=NOW,
        candidate_rank=1,
        reference_prices={"TESTUSD": 10.5},
    )
    second = build_live_scan_snapshot(
        candidate(),
        decision_at=NOW,
        candidate_rank=1,
        reference_prices={"TESTUSD": 10.5},
    )

    assert first.snapshot_id == second.snapshot_id
    assert first.decision_at_utc == NOW.isoformat()
    assert first.reference_price == 10.5
    assert first.components["bad"] is None
    assert first.affects_ranking is False
    assert first.affects_telegram is False
    assert first.affects_pending_setup is False
    assert first.trade_authority_changed is False
    assert first.production_execution_gate_changed is False


def test_live_snapshot_rejects_naive_decision_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        build_live_scan_snapshot(
            candidate(),
            decision_at=datetime(2026, 8, 24, 21, 30),
            candidate_rank=1,
        )


def test_market_data_slice_rejects_future_bar():
    end_at = datetime(2026, 8, 24, 12, 15, tzinfo=timezone.utc)
    future = MarketDataBar(
        opened_at_utc=datetime(2026, 8, 24, 12, 15, tzinfo=timezone.utc),
        closed_at_utc=datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc),
        open=10,
        high=11,
        low=9,
        close=10.5,
    )
    with pytest.raises(ValueError, match="closed after requested end"):
        MarketDataSlice(
            exchange="KRAKEN",
            canonical_symbol="TESTUSD",
            interval_minutes=15,
            requested_end_at_utc=end_at,
            fetched_at_utc=datetime(2026, 8, 24, 12, 40, tzinfo=timezone.utc),
            bars=(future,),
        )


def test_catalyst_contract_is_context_only_with_zero_trade_weight():
    catalyst = CatalystContext(
        catalyst_id="c1",
        symbol="BTCUSD",
        source="SOURCE",
        publication_at_utc=NOW,
        observed_at_utc=NOW,
        category="REGULATORY",
        headline="test",
    )
    assert catalyst.context_only is True
    assert catalyst.numerical_trade_weight == 0.0
    assert catalyst.affects_ranking is False
    assert catalyst.affects_telegram is False
    assert catalyst.trade_authority_changed is False
