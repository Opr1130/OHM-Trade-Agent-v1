from types import SimpleNamespace

from app.backtesting.historical_data import load_kraken_candles
from app.models.signal import TradingSignal
from app.services.drawdown_guard import DrawdownPolicy
from app.services.execution_analytics import execution_quality_summary
from app.services.historical_analytics import historical_summary
from app.services.risk import build_risk_plan


def _signal() -> TradingSignal:
    return TradingSignal(
        symbol="BTCUSD",
        asset_class="crypto",
        side="long",
        price=100.0,
        stop_price=95.0,
        target_price=110.0,
        rsi=55.0,
        volume_ratio=1.5,
        ema_fast=101.0,
        ema_slow=99.0,
        market_regime="bullish",
    )


def test_risk_plan_reduces_size_inside_soft_drawdown():
    normal = build_risk_plan(_signal(), equity=10_000, risk_pct=1.0, peak_equity=10_000)
    reduced = build_risk_plan(_signal(), equity=9_400, risk_pct=1.0, peak_equity=10_000)
    assert normal.allowed is True
    assert reduced.allowed is True
    assert reduced.risk_dollars == 47.0
    assert reduced.position_size == 9.4
    assert reduced.risk_dollars < normal.risk_dollars


def test_risk_plan_halts_at_hard_drawdown():
    plan = build_risk_plan(
        _signal(),
        equity=9_100,
        risk_pct=1.0,
        peak_equity=10_000,
        drawdown_policy=DrawdownPolicy(soft_limit_pct=5, hard_limit_pct=8),
    )
    assert plan.allowed is False
    assert plan.position_size == 0
    assert "Portfolio risk halted" in plan.rejection_reason


def test_historical_summary_segments_setup_and_regime():
    rows = [
        {
            "entered_trade": True,
            "terminal_status": "CLOSED",
            "direction": "LONG",
            "market_regime": "bullish",
            "setup_type": "breakout",
            "net_pnl": value,
        }
        for value in (10.0, 8.0, -2.0, 6.0, 3.0)
    ]
    summary = historical_summary(rows)
    setup = summary["by_setup"]["BREAKOUT"]
    combo = summary["by_regime_setup"]["BULLISH|BREAKOUT"]
    assert setup["count"] == 5
    assert setup["expectancy"] == 5.0
    assert setup["sufficient_samples"] is True
    assert combo["net_pnl"] == 25.0


def test_execution_quality_reports_slippage_latency_and_fees():
    records = [
        {
            "symbol": "BTCUSD",
            "direction": "LONG",
            "fill_vs_limit_pct": slippage,
            "order_to_first_fill_seconds": latency,
            "idea_to_first_fill_seconds": latency + 2,
            "entry_fee": 1.0,
            "exit_fee": 1.5,
        }
        for slippage, latency in ((0.10, 3), (-0.05, 5), (0.20, 4), (0.0, 2), (0.05, 6))
    ]
    summary = execution_quality_summary(records)
    overall = summary["overall"]
    assert overall["slippage_samples"] == 5
    assert overall["avg_fill_vs_limit_pct"] == 0.06
    assert overall["adverse_fill_rate_pct"] == 60.0
    assert overall["avg_order_to_first_fill_seconds"] == 4.0
    assert overall["total_fees"] == 12.5
    assert overall["sufficient_samples"] is True


def test_kraken_history_adapter_drops_forming_candle_and_sorts():
    rows = [
        SimpleNamespace(timestamp=200, open=2, high=3, low=1, close=2.5, volume=20),
        SimpleNamespace(timestamp=100, open=1, high=2, low=0.5, close=1.5, volume=10),
        SimpleNamespace(timestamp=300, open=3, high=4, low=2, close=3.5, volume=30),
    ]

    class FakeClient:
        def get_ohlc(self, pair, interval=60, since=None):
            assert pair == "BTCUSD"
            assert interval == 60
            return rows

    candles = load_kraken_candles("BTCUSD", client=FakeClient())
    assert len(candles) == 2
    assert [int(c.timestamp.timestamp()) for c in candles] == [100, 200]
    assert candles[0].close == 1.5
