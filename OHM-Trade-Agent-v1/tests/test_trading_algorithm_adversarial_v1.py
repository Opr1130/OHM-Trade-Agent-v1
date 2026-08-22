from __future__ import annotations

from datetime import datetime, timezone

from app.exchanges.kraken import BookLevel, Candle, PreTradeBook
from app.scanner import market_scanner
from app.scanner.execution_validation import INVALID, evaluate_execution
from app.scanner.models import MarketSnapshot
from app.services.entry_exit_advisor import EntryExitPlan, build_entry_exit_plan
from app.services.short_target_attainability import evaluate_short_target_attainability
from app.services.target_attainability import evaluate_target_attainability


def _candles(*, interval_seconds: int, count: int, forming_close: float) -> list[Candle]:
    end = int(datetime.now(timezone.utc).timestamp()) - 60
    start = end - (count - 1) * interval_seconds
    rows: list[Candle] = []
    for index in range(count - 1):
        close = 100.0 + index * 0.02
        rows.append(
            Candle(
                timestamp=start + index * interval_seconds,
                open=close - 0.01,
                high=close + 0.10,
                low=close - 0.10,
                close=close,
                vwap=close,
                volume=100.0 + (index % 7),
                trade_count=20,
            )
        )
    previous_close = rows[-1].close
    rows.append(
        Candle(
            timestamp=end,
            open=previous_close,
            high=max(previous_close, forming_close) + 0.10,
            low=min(previous_close, forming_close) - 0.10,
            close=forming_close,
            vwap=(previous_close + forming_close) / 2.0,
            volume=250.0,
            trade_count=30,
        )
    )
    return rows


def _target_snapshot(*, direction: str = "LONG", atr: float = 2.0) -> MarketSnapshot:
    short = direction == "SHORT"
    return MarketSnapshot(
        symbol="SOLUSD",
        last_price=100.0,
        ema20=101.0 if short else 99.0,
        ema50=105.0 if short else 95.0,
        ema200=110.0 if short else 90.0,
        rsi=42.0 if short else 58.0,
        macd_line=-1.0 if short else 1.0,
        macd_signal=-0.5 if short else 0.5,
        macd_histogram=-0.5 if short else 0.5,
        atr=atr,
        atr_pct=2.0 if atr else 0.0,
        volume_ratio=1.5,
        technical_score=90,
        trend="bearish" if short else "bullish",
        trade_direction=direction,
        recent_24h_high=110.0,
        recent_24h_low=90.0,
        recent_72h_high=120.0,
        recent_72h_low=80.0,
        momentum_6h_pct=-1.0 if short else 1.0,
        momentum_24h_pct=-3.0 if short else 3.0,
        momentum_72h_pct=-7.0 if short else 7.0,
        realized_range_24h_pct=10.0,
        rolling_24h_range_median_pct=10.0,
        rolling_24h_range_p75_pct=12.0,
        rolling_24h_range_p90_pct=15.0,
        rolling_72h_range_median_pct=18.0,
        rolling_72h_range_p75_pct=22.0,
        rolling_72h_range_p90_pct=28.0,
        rolling_24h_upside_median_pct=5.0,
        rolling_24h_upside_p75_pct=8.0,
        rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=10.0,
        rolling_72h_upside_p75_pct=15.0,
        rolling_72h_upside_p90_pct=20.0,
        rolling_24h_downside_median_pct=5.0,
        rolling_24h_downside_p75_pct=8.0,
        rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=10.0,
        rolling_72h_downside_p75_pct=15.0,
        rolling_72h_downside_p90_pct=20.0,
    )


def test_core_scanner_decision_features_do_not_repaint_from_forming_hour(monkeypatch):
    state = {"forming_close": 104.0}
    quarter_hour = _candles(interval_seconds=900, count=201, forming_close=104.0)

    class Client:
        def get_ohlc(self, symbol, interval):
            if interval == 15:
                return quarter_hour
            return _candles(
                interval_seconds=3600,
                count=201,
                forming_close=state["forming_close"],
            )

    monkeypatch.setattr(market_scanner, "KrakenClient", Client)

    status_a, first, reason_a = market_scanner.analyze_symbol("SOLUSD")
    assert status_a == "ok", reason_a
    assert first is not None

    state["forming_close"] = 160.0
    status_b, second, reason_b = market_scanner.analyze_symbol("SOLUSD")
    assert status_b == "ok", reason_b
    assert second is not None

    decision_fields = (
        "last_price",
        "ema20",
        "ema50",
        "ema200",
        "rsi",
        "macd_line",
        "macd_signal",
        "macd_histogram",
        "atr",
        "atr_pct",
        "volume_ratio",
        "technical_score",
        "trend",
        "momentum_6h_pct",
        "momentum_24h_pct",
        "momentum_72h_pct",
        "recent_24h_high",
        "recent_24h_low",
        "recent_72h_high",
        "recent_72h_low",
        "realized_range_24h_pct",
        "realized_range_72h_pct",
    )
    assert {field: getattr(first, field) for field in decision_fields} == {
        field: getattr(second, field) for field in decision_fields
    }


def test_long_target_gate_rejects_inverted_target_geometry():
    snapshot = _target_snapshot(direction="LONG")
    plan = EntryExitPlan(
        symbol="SOLUSD",
        valid_now=True,
        entry_style="test",
        entry_low=100.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=95.0,
        target_1=99.0,
        target_2=98.0,
        reward_to_risk_1=0.2,
        reward_to_risk_2=0.4,
        risk_level="low",
        reason="adversarial invalid geometry",
        direction="LONG",
    )
    result = evaluate_target_attainability(plan, snapshot)
    assert result.qualified is False
    assert any("geometry" in reason.lower() for reason in result.rejection_reasons)


def test_short_target_gate_rejects_inverted_target_geometry():
    snapshot = _target_snapshot(direction="SHORT")
    plan = EntryExitPlan(
        symbol="SOLUSD",
        valid_now=True,
        entry_style="test",
        entry_low=100.0,
        entry_high=100.0,
        chase_limit=99.0,
        stop_price=105.0,
        target_1=101.0,
        target_2=102.0,
        reward_to_risk_1=0.2,
        reward_to_risk_2=0.4,
        risk_level="low",
        reason="adversarial invalid geometry",
        direction="SHORT",
    )
    result = evaluate_short_target_attainability(plan, snapshot)
    assert result.qualified is False
    assert any("geometry" in reason.lower() for reason in result.rejection_reasons)


def test_zero_atr_entry_plan_is_never_valid_now():
    snapshot = _target_snapshot(direction="LONG", atr=0.0)
    snapshot.ema20 = snapshot.last_price
    plan = build_entry_exit_plan(snapshot, "low")
    assert plan.valid_now is False
    assert plan.stop_price != (plan.entry_low + plan.entry_high) / 2.0


def test_execution_validation_fails_closed_on_zero_quote_conversion_rate():
    book = PreTradeBook(
        "SOL/USD",
        bids=[BookLevel(99.9, 10.0)],
        asks=[BookLevel(100.1, 10.0)],
    )
    result = evaluate_execution(
        book=book,
        validation_notional_usd=500.0,
        ticker_last=100.0,
        quote_to_usd_rate=0.0,
        trades=[],
    )
    assert result.status == INVALID
