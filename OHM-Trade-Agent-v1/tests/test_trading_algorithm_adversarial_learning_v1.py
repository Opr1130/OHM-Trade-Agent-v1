from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.exchanges.kraken import Candle
from app.services import price_movement_learning as movement_learning
from app.services.explosion_learning import _window_metrics as explosion_window_metrics
from app.services.learning_governance import evaluate_learning_promotion
from app.services.movement_discovery_outcomes import _window_metrics as discovery_window_metrics


BASE = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _candle(minutes: int, *, close: float, high: float | None = None, low: float | None = None) -> Candle:
    return Candle(
        timestamp=int((BASE + timedelta(minutes=minutes)).timestamp()),
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        vwap=close,
        volume=100.0,
        trade_count=10,
    )


def test_movement_discovery_horizon_excludes_candle_not_fully_completed_by_end():
    candles = [
        _candle(0, close=100.0),
        _candle(15, close=101.0),
        _candle(30, close=102.0),
        _candle(45, close=103.0, high=104.0, low=99.0),
        _candle(60, close=150.0, high=160.0, low=80.0),
    ]
    result = discovery_window_metrics(
        candles,
        start=BASE,
        end=BASE + timedelta(hours=1),
        reference_price=100.0,
        direction="LONG",
    )
    assert result is not None
    assert result["sample_candles"] == 4
    assert result["close_price"] == 103.0
    assert result["mfe_pct"] == pytest.approx(4.0)
    assert result["mae_pct"] == pytest.approx(-1.0)


def test_explosion_horizon_excludes_candle_not_fully_completed_by_end():
    candles = [
        _candle(0, close=100.0),
        _candle(15, close=101.0),
        _candle(30, close=102.0),
        _candle(45, close=103.0, high=104.0, low=99.0),
        _candle(60, close=150.0, high=160.0, low=80.0),
    ]
    result = explosion_window_metrics(
        candles,
        start=BASE,
        end=BASE + timedelta(hours=1),
        reference_price=100.0,
    )
    assert result is not None
    assert result["sample_candles"] == 4
    assert result["close_price"] == 103.0
    assert result["mfe_pct"] == pytest.approx(4.0)
    assert result["mae_pct"] == pytest.approx(-1.0)


@pytest.mark.parametrize(
    "field",
    ["expectancy", "profit_factor", "max_drawdown_pct"],
)
def test_learning_promotion_rejects_non_finite_performance_statistics(field):
    values = dict(
        samples=100,
        expectancy=1.0,
        profit_factor=1.5,
        max_drawdown_pct=4.0,
    )
    values[field] = math.nan
    result = evaluate_learning_promotion(**values)
    assert result.eligible_for_review is False
    assert result.auto_apply_allowed is False
    assert result.state == "REJECT"
    assert "finite" in result.reason.lower() or "invalid" in result.reason.lower()


def test_price_movement_delayed_scheduler_uses_exact_horizon_not_current_ticker(monkeypatch, tmp_path):
    monkeypatch.setattr(movement_learning, "MOVEMENT_FILE", tmp_path / "movement.json")
    monkeypatch.setattr(movement_learning, "LOCK_FILE", tmp_path / ".movement.lock")

    signal = {
        "symbol": "SOLUSD",
        "stage": "WATCH",
        "direction": "LONG",
        "source": "OHM_PRICE_MOVEMENT_RADAR",
        "observed_at": BASE.isoformat(),
        "reference_price": 100.0,
        "reference_atr": 2.0,
        "expected_move_low_atr": 1.0,
    }
    movement_learning.record_price_movement(signal)

    class Client:
        def get_tickers(self, symbols):
            return {"SOLUSD": {"last": 150.0}}

        def get_ticker(self, symbol):
            return {"last": 150.0}

        def get_ohlc(self, symbol, interval, since=None):
            return [
                _candle(45, close=104.0),
                _candle(60, close=105.0),
                _candle(75, close=150.0),
            ]

    result = movement_learning.observe_due_price_movements(
        client=Client(),
        now=BASE + timedelta(hours=1, minutes=30),
    )
    assert result["observations_added"] == 1
    row = movement_learning.get_price_movement_records()[0]
    observation = row["observations"]["1h"]
    assert observation["price"] == 105.0
    assert observation["directional_move_pct"] == pytest.approx(5.0)
    assert observation["observed_at"] == (BASE + timedelta(hours=1)).isoformat()
