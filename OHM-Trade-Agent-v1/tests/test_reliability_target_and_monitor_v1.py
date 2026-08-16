"""Regression coverage for the reliability pass on Target 1 calibration and
trade-monitor alert sensitivity.

Context: live signals were observed reaching meaningful unrealized profit and
reversing before ever tagging Target 1. Two contributing, independently
verifiable causes were identified and fixed here:

1. Target 1 was fixed at a flat 2R distance regardless of what a symbol
   actually does, and the attainability gate tolerated targets requiring a
   move up to 1.10x the symbol's own historical p90 excursion (an uncommon
   event by definition). entry_exit_advisor.build_entry_exit_plan now caps
   (never raises) Target 1 to the symbol's own rolling p75 excursion, floored
   so it never collapses to a fee-noise distance, and
   target_attainability.MATERIAL_P90_EXCESS_RATIO no longer grants a 10%
   allowance past p90.
2. trade_monitor.monitor_trade escalated to a "WARNING" Telegram alert on any
   single fast 1H oscillator flip (EMA20 loss, MACD cross, RSI swing), which
   fires routinely during ordinary pullbacks inside an intact trend while the
   trade is still profitable. It now requires at least two independent
   signals to corroborate before escalating; a single flip is still surfaced
   in the reasons list for visibility but does not drive the action.
"""

from types import SimpleNamespace

import pytest

from app.scanner.models import MarketSnapshot
from app.services import trade_monitor
from app.services.active_trade_registry import ActiveTrade
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.target_attainability import (
    MATERIAL_P90_EXCESS_RATIO,
    evaluate_target_attainability,
)


def snapshot(**overrides) -> MarketSnapshot:
    values = dict(
        symbol="SOL/USD", last_price=100.0, ema20=99.0, ema50=95.0, ema200=90.0,
        rsi=58.0, macd_line=1.0, macd_signal=0.5, macd_histogram=0.5,
        atr=2.0, atr_pct=2.0, volume_ratio=1.5, technical_score=90, trend="bullish",
        recent_24h_high=110.0, recent_24h_low=94.0,
        recent_72h_high=116.0, recent_72h_low=88.0,
        momentum_6h_pct=1.0, momentum_24h_pct=3.0, momentum_72h_pct=7.0,
        rolling_24h_range_median_pct=8.0, rolling_24h_range_p75_pct=10.0, rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0, rolling_72h_range_p75_pct=16.0, rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=8.0, rolling_24h_upside_p75_pct=10.0, rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0, rolling_72h_upside_p75_pct=16.0, rolling_72h_upside_p90_pct=20.0,
        rolling_24h_downside_median_pct=8.0, rolling_24h_downside_p75_pct=10.0, rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=12.0, rolling_72h_downside_p75_pct=16.0, rolling_72h_downside_p90_pct=20.0,
    )
    values.update(overrides)
    return MarketSnapshot(**values)


# ---------------------------------------------------------------------------
# entry_exit_advisor: Target 1 realism cap
# ---------------------------------------------------------------------------


def test_target_1_stays_mechanical_when_symbol_supports_it():
    """A symbol with a normal upside distribution (p75 well past 2R) keeps
    the existing flat 2R Target 1 unchanged."""
    plan = build_entry_exit_plan(snapshot(), "low")
    entry = (plan.entry_low + plan.entry_high) / 2
    risk_per_unit = entry - plan.stop_price
    assert plan.target_1 == pytest.approx(entry + risk_per_unit * 2.0)


def test_target_1_is_capped_for_a_historically_quiet_symbol():
    """A symbol whose p75 upside implies well under 2R gets a nearer, more
    attainable Target 1 instead of a flat 2R distance it rarely reaches."""
    quiet = snapshot(
        rolling_24h_upside_median_pct=2.0,
        rolling_24h_upside_p75_pct=2.5,
        rolling_24h_upside_p90_pct=3.0,
    )
    plan = build_entry_exit_plan(quiet, "low")
    entry = (plan.entry_low + plan.entry_high) / 2
    risk_per_unit = entry - plan.stop_price
    mechanical_target_1 = entry + risk_per_unit * 2.0
    assert plan.target_1 < mechanical_target_1
    # Never collapses below the 1.2R floor.
    assert plan.target_1 >= entry + risk_per_unit * 1.2 - 1e-9
    assert plan.reward_to_risk_1 == pytest.approx(1.2, abs=0.01)


def test_target_1_cap_never_raises_the_mechanical_target():
    """An unusually strong upside distribution must not push Target 1 beyond
    the existing deterministic 2R ceiling."""
    strong = snapshot(
        rolling_24h_upside_median_pct=30.0,
        rolling_24h_upside_p75_pct=40.0,
        rolling_24h_upside_p90_pct=50.0,
    )
    plan = build_entry_exit_plan(strong, "low")
    entry = (plan.entry_low + plan.entry_high) / 2
    risk_per_unit = entry - plan.stop_price
    assert plan.target_1 == pytest.approx(entry + risk_per_unit * 2.0)


def test_target_1_cap_applies_symmetrically_to_shorts():
    quiet_short = snapshot(
        trade_direction="SHORT",
        trend="bearish",
        ema20=101.0,
        rolling_24h_downside_median_pct=2.0,
        rolling_24h_downside_p75_pct=2.5,
        rolling_24h_downside_p90_pct=3.0,
    )
    plan = build_entry_exit_plan(quiet_short, "low", direction="SHORT")
    entry = (plan.entry_low + plan.entry_high) / 2
    risk_per_unit = plan.stop_price - entry
    mechanical_target_1 = entry - risk_per_unit * 2.0
    assert plan.target_1 > mechanical_target_1  # nearer to entry for a short
    assert plan.target_1 <= entry - risk_per_unit * 1.2 + 1e-9


def test_geometry_bias_regression_unaffected_by_target_1_cap():
    """Existing invariant: two snapshots that differ only in momentum/volume
    evidence (not in price/ATR/percentile geometry) must still produce an
    identical mechanical plan."""
    supportive = snapshot(recent_24h_high=120.0, recent_72h_high=130.0)
    hostile = snapshot(
        recent_24h_high=104.0, recent_72h_high=106.0,
        momentum_6h_pct=-1.0, momentum_24h_pct=-2.0, momentum_72h_pct=-3.0,
        volume_ratio=0.6, realized_range_24h_pct=2.0,
    )
    supportive_plan = build_entry_exit_plan(supportive, "low")
    hostile_plan = build_entry_exit_plan(hostile, "low")
    assert supportive_plan.target_1 == hostile_plan.target_1
    assert supportive_plan.target_2 == hostile_plan.target_2


# ---------------------------------------------------------------------------
# target_attainability: no grace allowance past the historical p90 move
# ---------------------------------------------------------------------------


def test_material_p90_excess_ratio_no_longer_grants_grace():
    assert MATERIAL_P90_EXCESS_RATIO == pytest.approx(1.00)


def test_generated_historically_normal_target_still_passes():
    """A symbol with a supportive upside history must still qualify after
    tightening the p90 grace allowance."""
    market = snapshot(recent_24h_high=120.0, recent_72h_high=130.0)
    result = evaluate_target_attainability(build_entry_exit_plan(market, "medium"), market)
    assert result.qualified is True
    assert result.attainability_score >= 90


def test_target_beyond_p90_is_rejected_without_grace():
    market = snapshot(
        rolling_24h_upside_median_pct=2.0, rolling_24h_upside_p75_pct=2.5, rolling_24h_upside_p90_pct=3.0,
        rolling_72h_upside_median_pct=3.0, rolling_72h_upside_p75_pct=4.0, rolling_72h_upside_p90_pct=5.0,
    )
    plan = build_entry_exit_plan(market, "low")
    result = evaluate_target_attainability(plan, market)
    assert result.qualified is False
    assert any("historical p90" in reason for reason in result.rejection_reasons)


# ---------------------------------------------------------------------------
# trade_monitor: WARNING requires corroboration, not a single signal
# ---------------------------------------------------------------------------


class _FakeCandle(SimpleNamespace):
    pass


class _FakeKrakenClient:
    def __init__(self, price: float):
        self._price = price

    def get_ohlc(self, symbol, interval=60):
        history = [_FakeCandle(close=100.0, volume=10.0) for _ in range(249)]
        history.append(_FakeCandle(close=self._price, volume=10.0))
        return history


def _run_monitor(monkeypatch, *, price, ema20, rsi_value, macd_line, macd_signal, vol_ratio, direction="LONG", **trade_kwargs):
    monkeypatch.setattr(trade_monitor, "KrakenClient", lambda: _FakeKrakenClient(price))
    monkeypatch.setattr(trade_monitor, "ema", lambda closes, period: ema20)
    monkeypatch.setattr(trade_monitor, "rsi", lambda closes, period: rsi_value)
    monkeypatch.setattr(trade_monitor, "macd", lambda closes: (macd_line, macd_signal, macd_line - macd_signal))
    monkeypatch.setattr(trade_monitor, "volume_ratio", lambda volumes, period: vol_ratio)
    if direction == "SHORT":
        defaults = dict(stop_price=110.0, target_1=80.0, target_2=70.0)
    else:
        defaults = dict(stop_price=90.0, target_1=120.0, target_2=130.0)
    defaults.update(trade_kwargs)
    trade = ActiveTrade(
        symbol="BTCUSD", entry_price=100.0, risk_level="low",
        direction=direction, **defaults,
    )
    return trade_monitor.monitor_trade(trade)


def test_single_weak_signal_while_profitable_does_not_warn(monkeypatch):
    result = _run_monitor(
        monkeypatch, price=108.0, ema20=110.0, rsi_value=55.0,
        macd_line=1.0, macd_signal=0.5, vol_ratio=1.0,
    )
    assert result.action == "HOLD"
    assert any("single signal" in reason for reason in result.reasons)


def test_two_corroborating_signals_do_warn(monkeypatch):
    result = _run_monitor(
        monkeypatch, price=108.0, ema20=110.0, rsi_value=35.0,
        macd_line=1.0, macd_signal=0.5, vol_ratio=1.0,
    )
    assert result.action == "WARNING"
    assert "Price lost EMA20" in result.reasons
    assert "RSI momentum weakened" in result.reasons


def test_stop_breach_still_takes_priority_over_weak_signals(monkeypatch):
    result = _run_monitor(
        monkeypatch, price=89.0, ema20=110.0, rsi_value=35.0,
        macd_line=0.4, macd_signal=0.5, vol_ratio=1.0,
    )
    assert result.action == "EXIT_NOW"
    assert "Stop price breached" in result.reasons


def test_heavy_opposing_volume_still_forces_exit_now(monkeypatch):
    result = _run_monitor(
        monkeypatch, price=95.0, ema20=94.0, rsi_value=55.0,
        macd_line=1.0, macd_signal=0.5, vol_ratio=2.0,
    )
    assert result.action == "EXIT_NOW"
    assert "Heavy selling volume detected" in result.reasons


def test_short_side_single_weak_signal_does_not_warn(monkeypatch):
    result = _run_monitor(
        monkeypatch, price=92.0, ema20=90.0, rsi_value=55.0,
        macd_line=0.4, macd_signal=0.5, vol_ratio=1.0,
        direction="SHORT",
    )
    assert result.action == "HOLD"


def test_short_side_two_signals_do_warn(monkeypatch):
    result = _run_monitor(
        monkeypatch, price=92.0, ema20=90.0, rsi_value=65.0,
        macd_line=0.4, macd_signal=0.5, vol_ratio=1.0,
        direction="SHORT",
    )
    assert result.action == "WARNING"
