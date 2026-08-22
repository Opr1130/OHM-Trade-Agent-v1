from datetime import datetime, timedelta, timezone

import pytest

from app.indicators.technical import (
    bollinger_bandwidth,
    bollinger_bandwidth_series,
    percentile_rank,
)
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.models import MarketSnapshot
from app.services.entry_exit_advisor import EntryExitPlan
from app.services import price_movement_learning as learning
from app.services.price_movement_notifier import format_price_movement_message
from app.services.price_movement_radar import (
    CONFIRMED,
    READY,
    WATCH,
    attach_actionable_plan,
    evaluate_price_movement,
)


def snapshot(**changes) -> MarketSnapshot:
    values = dict(
        symbol="BTCUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=95.0,
        ema200=90.0,
        rsi=58.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.2,
        technical_score=88,
        trend="bullish",
        trade_direction="LONG",
        movement_timeframe="15M",
        movement_data_status="AVAILABLE",
        bollinger_bandwidth_pct=0.8,
        bollinger_bandwidth_percentile=5.0,
        atr_percentile=10.0,
        movement_volume_ratio=1.2,
        confirmed_close=100.0,
        confirmed_price_change_1h_pct=0.2,
        prior_24h_range_high=101.0,
        prior_24h_range_low=95.0,
    )
    values.update(changes)
    return MarketSnapshot(**values)


def derivatives_evidence() -> dict:
    return {
        "bundle": {
            "derivatives": [
                {
                    "status": "AVAILABLE",
                    "funding_rate_pct": 0.04,
                    "open_interest_change_15m_pct": 1.7,
                    "open_interest_change_1h_pct": 3.2,
                    "open_interest_change_24h_pct": 8.0,
                    "open_interest_acceleration_zscore": 2.4,
                    "liquidations_1h_usd": 4_000_000,
                    "liquidations_24h_usd": 8_000_000,
                    "long_short_ratio": 1.6,
                }
            ]
        }
    }


def execution() -> ExecutionValidation:
    return ExecutionValidation(
        status="VALID",
        book_coverage_status="COMPLETE",
        warnings=[],
        bid_depth_050_usd=1_000_000,
        ask_depth_050_usd=100_000,
    )


def test_bollinger_bandwidth_and_symbol_relative_percentile():
    assert bollinger_bandwidth([100.0] * 20) == pytest.approx(0.0)
    expanding = [100.0 + ((index % 5) - 2) * index / 20 for index in range(60)]
    series = bollinger_bandwidth_series(expanding)
    assert len(series) == 41
    assert percentile_rank([1.0, 2.0, 3.0], 1.0) == pytest.approx(100 / 3)


def test_compression_and_volume_create_non_actionable_watch():
    signal = evaluate_price_movement(snapshot(), now=datetime(2026, 8, 17, tzinfo=timezone.utc))
    assert signal is not None
    assert signal.stage == WATCH
    assert signal.direction == "UNCONFIRMED"
    assert signal.as_dict()["actionable"] is False
    assert signal.expected_window_primary == "4-12h"


def test_derivatives_and_book_evidence_promote_ready_without_entry():
    signal = evaluate_price_movement(
        snapshot(movement_volume_ratio=1.7, execution_validation=execution()),
        market_intelligence=derivatives_evidence(),
        now=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    assert signal is not None
    assert signal.stage == READY
    assert signal.direction == "UNCONFIRMED"
    assert signal.readiness_score >= 70
    assert signal.components.leverage == 25
    assert signal.expected_window_primary == "1-4h"


def test_tradingview_confirmation_allows_existing_plan_to_be_attached():
    candidate = snapshot(movement_volume_ratio=1.7, execution_validation=execution())
    candidate._tradingview_evidence = {
        "source_classification": "TRADINGVIEW_CONFIRMED_NATIVE"
    }
    signal = evaluate_price_movement(
        candidate,
        market_intelligence=derivatives_evidence(),
        now=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    assert signal is not None
    assert signal.stage == CONFIRMED
    assert signal.direction == "LONG"

    plan = EntryExitPlan(
        symbol="BTCUSD",
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=96.0,
        target_1=106.0,
        target_2=109.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="confirmed",
        direction="LONG",
    )
    actionable = attach_actionable_plan(
        signal.as_dict(),
        plan=plan,
        action="ENTER_NOW",
        now=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    assert actionable is not None
    assert actionable["actionable"] is True
    assert actionable["entry"]["zone_low"] == 99.0
    assert actionable["exits"]["target_2"] == 109.0
    assert actionable["setup_expires_at"].startswith("2026-08-17T04:00:00")


def test_watch_notification_never_contains_an_entry_instruction():
    signal = evaluate_price_movement(snapshot())
    assert signal is not None
    message = format_price_movement_message(signal.as_dict())
    assert "MONITOR ONLY" in message
    assert "no entry is authorized" in message
    assert "Entry Zone" not in message


def test_watch_notification_falls_back_when_move_estimate_is_zero():
    signal = evaluate_price_movement(snapshot())
    assert signal is not None
    payload = signal.as_dict()
    payload["readiness_score"] = 44
    payload["expected_move_low_pct"] = 0
    payload["expected_move_high_pct"] = 0
    message = format_price_movement_message(payload)
    assert "Potential: +5% to +15%" in message
    assert "Potential: +0% to +0%" not in message


@pytest.mark.parametrize(
    ("low", "high"),
    [
        (None, None),
        (-5, -1),
        (10, 5),
        ("bad", "data"),
    ],
)
def test_watch_notification_falls_back_for_invalid_move_ranges(low, high):
    signal = evaluate_price_movement(snapshot())
    assert signal is not None
    payload = signal.as_dict()
    payload["readiness_score"] = 44
    payload["expected_move_low_pct"] = low
    payload["expected_move_high_pct"] = high
    message = format_price_movement_message(payload)
    assert "Potential: +5% to +15%" in message
    assert "Potential: +0% to +0%" not in message


def test_watch_notification_preserves_valid_move_range():
    signal = evaluate_price_movement(snapshot())
    assert signal is not None
    payload = signal.as_dict()
    payload["expected_move_low_pct"] = 3.5
    payload["expected_move_high_pct"] = 8.5
    message = format_price_movement_message(payload)
    assert "Potential: +4% to +8%" in message


def test_movement_learning_records_absolute_and_atr_outcomes(monkeypatch, tmp_path):
    monkeypatch.setattr(learning, "MOVEMENT_FILE", tmp_path / "movement.json")
    monkeypatch.setattr(learning, "LOCK_FILE", tmp_path / ".movement.lock")
    started = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    signal = evaluate_price_movement(snapshot(), now=started)
    assert signal is not None
    learning.record_price_movement(signal.as_dict())

    class Candle:
        def __init__(self, timestamp, close):
            self.timestamp = timestamp
            self.close = close

    class Client:
        def get_ohlc(self, pair, interval, since=None):
            assert interval == 15
            return [
                Candle((started + timedelta(minutes=45)).timestamp(), 104.0),
                # This candle opens exactly at the 1h horizon and closes later;
                # it must never be used as the 1h outcome.
                Candle((started + timedelta(hours=1)).timestamp(), 150.0),
            ]

        def get_tickers(self, pairs):
            return {"BTCUSD": {"last": 150.0}}

        def get_ticker(self, pair):
            return {"last": 150.0}

    result = learning.observe_due_price_movements(
        client=Client(),
        now=started + timedelta(hours=1, minutes=1),
    )
    assert result["observations_added"] == 1
    row = learning.get_price_movement_records()[0]
    assert row["observations"]["1h"]["price"] == pytest.approx(104.0)
    assert row["observations"]["1h"]["absolute_move_pct"] == pytest.approx(4.0)
    assert row["observations"]["1h"]["move_atr"] == pytest.approx(2.0)
    assert row["observations"]["1h"]["directional_move_pct"] is None
