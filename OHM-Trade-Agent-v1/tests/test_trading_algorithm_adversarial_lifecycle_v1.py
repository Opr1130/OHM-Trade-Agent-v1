from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from app.exchanges.kraken import Candle
from app.services import pending_setup_monitor, trade_monitor
from app.services.active_trade_registry import ActiveTrade
from app.services.entry_timing import evaluate_entry_timing
from app.services.pending_setup_monitor import monitor_pending_setup
from app.services.pending_setup_registry import PendingSetup
from app.services.trade_monitor import monitor_trade


def _trade(direction: str = "LONG") -> ActiveTrade:
    short = direction == "SHORT"
    return ActiveTrade(
        symbol="SOLUSD",
        entry_price=100.0,
        stop_price=105.0 if short else 95.0,
        target_1=95.0 if short else 105.0,
        target_2=90.0 if short else 110.0,
        risk_level="medium",
        trade_id="TEST-TRADE",
        direction=direction,
        capital=1000.0,
    )


def _setup(direction: str = "LONG") -> PendingSetup:
    short = direction == "SHORT"
    return PendingSetup(
        symbol="SOLUSD",
        entry_low=99.0 if not short else 100.0,
        entry_high=100.0 if not short else 101.0,
        chase_limit=101.0 if not short else 99.0,
        stop_price=95.0 if not short else 105.0,
        target_1=105.0 if not short else 95.0,
        target_2=110.0 if not short else 90.0,
        risk_level="medium",
        confidence=80,
        trade_id="TEST-SETUP",
        direction=direction,
    )


def _candles(last_close: float) -> list[Candle]:
    base = int(datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc).timestamp())
    rows = []
    for index in range(30):
        close = 100.0 + index * 0.05
        if index == 29:
            close = last_close
        rows.append(
            Candle(
                timestamp=base + index * 3600,
                open=100.0,
                high=101.0,
                low=99.0,
                close=close,
                vwap=100.0,
                volume=100.0,
                trade_count=10,
            )
        )
    return rows


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_entry_timing_rejects_non_finite_prices(value):
    with pytest.raises(ValueError, match="finite"):
        evaluate_entry_timing(
            direction="LONG",
            current_price=value,
            entry_low=99.0,
            entry_high=100.0,
            chase_limit=101.0,
        )


def test_pending_setup_monitor_rejects_non_finite_ticker(monkeypatch):
    class Client:
        def get_ticker(self, symbol):
            return {"last": math.nan}

    monkeypatch.setattr(pending_setup_monitor, "KrakenClient", Client)
    with pytest.raises(ValueError, match="finite"):
        monitor_pending_setup(_setup())


@pytest.mark.parametrize(
    "direction, current",
    [("LONG", 95.0), ("SHORT", 105.0)],
)
def test_pending_setup_invalidates_at_stop_boundary(monkeypatch, direction, current):
    class Client:
        def get_ticker(self, symbol):
            return {"last": current}

    monkeypatch.setattr(pending_setup_monitor, "KrakenClient", Client)
    result = monitor_pending_setup(_setup(direction))
    assert result.status == "INVALIDATED"


def test_active_trade_monitor_rejects_non_finite_market_price(monkeypatch):
    class Client:
        def get_ohlc(self, symbol, interval):
            return _candles(math.nan)

    monkeypatch.setattr(trade_monitor, "KrakenClient", Client)
    with pytest.raises(ValueError, match="finite"):
        monitor_trade(_trade())


def test_active_trade_monitor_does_not_hold_when_price_is_non_finite(monkeypatch):
    class Client:
        def get_ohlc(self, symbol, interval):
            return _candles(math.inf)

    monkeypatch.setattr(trade_monitor, "KrakenClient", Client)
    with pytest.raises(ValueError, match="finite"):
        monitor_trade(_trade())
