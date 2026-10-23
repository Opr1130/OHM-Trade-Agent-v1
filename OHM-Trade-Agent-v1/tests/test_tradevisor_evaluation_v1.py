"""ATDD: Tradevisor decision pipeline (native correlation, F5/F7/R4 reuse).

Covers ATDD-05 (mapping), ATDD-07 (native corroboration), ATDD-26..33, ATDD-51.

Every market input is synthetic. The gates exercised here are the real
production owners (R4 geometry kernel, target attainability, economic quality,
capital allocation, portfolio exposure); only their inputs are fixtures.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.models import MarketSnapshot
from app.services import tradevisor_evaluation as te
from app.services.tradevisor_ingress import TradevisorSignal

NOW = datetime(2026, 10, 23, 13, 0, 5, tzinfo=timezone.utc)


def settings(**over) -> Settings:
    values = dict(_env_file=None, webhook_secret="x" * 12)
    values.update(over)
    return Settings(**values)


def execution_evidence(**over) -> ExecutionValidation:
    values = dict(
        status="VALID",
        book_coverage_status="COMPLETE",
        warnings=[],
        best_bid=99.95,
        best_ask=100.05,
        mid_price=100.0,
        spread_pct=0.1,
        spread_bps=5.0,
        validation_notional_usd=250.0,
        buy_vwap=100.02,
        sell_vwap=99.98,
        buy_fully_covered=True,
        sell_fully_covered=True,
        estimated_visible_round_trip_market_drag_pct=0.10,
        recent_trade_status="FRESH",
        latest_trade_price=100.0,
        latest_trade_age_seconds=10.0,
        recent_trade_count=20,
    )
    values.update(over)
    return ExecutionValidation(**values)


def snapshot(**over) -> MarketSnapshot:
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
        volume_ratio=1.5,
        technical_score=90,
        trend="bullish",
        recent_24h_high=112.0,
        recent_24h_low=94.0,
        recent_72h_high=120.0,
        recent_72h_low=88.0,
        momentum_6h_pct=1.0,
        momentum_24h_pct=3.0,
        momentum_72h_pct=7.0,
        rolling_24h_range_median_pct=8.0,
        rolling_24h_range_p75_pct=10.0,
        rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0,
        rolling_72h_range_p75_pct=16.0,
        rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=8.0,
        rolling_24h_upside_p75_pct=10.0,
        rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0,
        rolling_72h_upside_p75_pct=16.0,
        rolling_72h_upside_p90_pct=20.0,
        rolling_24h_downside_median_pct=8.0,
        rolling_24h_downside_p75_pct=10.0,
        rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=12.0,
        rolling_72h_downside_p75_pct=16.0,
        rolling_72h_downside_p90_pct=20.0,
        ticker_last=100.0,
        execution_validation=execution_evidence(),
    )
    values.update(over)
    return MarketSnapshot(**values)


def signal(direction: str = "BUY", price: float = 100.0, symbol: str = "BTCUSD") -> TradevisorSignal:
    return TradevisorSignal(
        dedup_id=f"dedup-{direction}-{symbol}",
        signal=direction,
        instrument="BTC/USD",
        market=symbol,
        source_exchange="COINBASE",
        source_symbol=symbol,
        timeframe_minutes=60,
        bar_opened_at=NOW - timedelta(minutes=65),
        bar_closed_at=NOW - timedelta(minutes=5),
        triggered_at=NOW - timedelta(seconds=5),
        received_at=NOW,
        signal_price=price,
        alert_config_version="tradevisor-1h-buy",
    )


def provider(**over):
    resolved = snapshot(**over)
    return lambda _market: ("ok", resolved, None)


def evaluate(sig=None, **kwargs):
    options = dict(
        now=NOW,
        settings=settings(),
        snapshot_provider=provider(),
        account_equity=10_000.0,
        active_trades=[],
    )
    options.update(kwargs)
    return te.evaluate_signal(sig or signal(), **options)


# ---------------------------------------------------------------------------
# Qualified path (ATDD-06, 07) and determinism (ATDD-51)
# ---------------------------------------------------------------------------


def test_atdd06_qualified_buy_produces_an_actionable_plan():
    decision = evaluate()
    assert decision.decision == te.DECISION_ENTER_NOW
    assert decision.actionable is True
    plan = decision.plan
    assert plan is not None
    assert plan.stop_price < plan.entry_low <= plan.entry_high
    assert plan.entry_high < plan.target_1 < plan.target_2
    assert decision.allocation is not None and decision.allocation.recommended_capital > 0
    assert decision.economics is not None and decision.economics.qualified is True
    assert decision.expires_at == NOW + timedelta(minutes=90)


def test_atdd07_native_corroboration_is_recorded_not_invented():
    decision = evaluate()
    evidence = decision.evidence
    assert decision.market_regime == "BULLISH"
    assert evidence["regime_basis"] == "NATIVE_1H_EMA_STACK"
    # 4H context and a trusted forecast model are NOT available in this path and
    # are recorded as such rather than fabricated.
    assert evidence["regime_4h"] == "UNAVAILABLE"
    assert evidence["f6_forecast_status"] == "UNAVAILABLE_NO_TRUSTED_MODEL"
    assert evidence["native_admission"] == "NOT_PERFORMED"
    assert evidence["native_snapshot_status"] == "AVAILABLE"
    assert decision.attainability is not None
    assert decision.attainability.qualified is True


def test_atdd51_replay_of_the_same_frozen_inputs_is_identical():
    first = evaluate().as_record()
    second = evaluate().as_record()
    assert first == second


def test_swing_classification_uses_volatility_not_the_source_timeframe():
    swing = evaluate()
    assert swing.horizon == te.HORIZON_SWING

    tactical = evaluate(snapshot_provider=provider(atr=5.0, atr_pct=5.0))
    assert tactical.horizon == te.HORIZON_TACTICAL

    quiet = evaluate(snapshot_provider=provider(atr=0.8, atr_pct=0.8))
    assert quiet.horizon == te.HORIZON_POSITION


def test_limit_entry_decision_when_price_is_extended():
    # Price far above EMA20 => wait_for_pullback => SET LIMIT ENTRY, never a
    # market entry.
    decision = evaluate(
        signal(price=140.0),
        snapshot_provider=provider(last_price=140.0, ticker_last=140.0, ema20=99.0),
        account_equity=100_000.0,
    )
    assert decision.decision in {te.DECISION_SET_LIMIT_ENTRY, te.DECISION_WATCH}
    if decision.decision == te.DECISION_SET_LIMIT_ENTRY:
        assert decision.plan is not None
        assert decision.plan.entry_high < decision.current_price
        assert decision.plan.chase_limit > decision.plan.entry_high


# ---------------------------------------------------------------------------
# Refusals: venue, evidence, gates (ATDD-26..32)
# ---------------------------------------------------------------------------


def test_atdd26_cross_venue_divergence_is_refused():
    decision = evaluate(signal(price=103.0))
    assert decision.decision == te.DECISION_NO_TRADE
    assert decision.rejection_code == "CROSS_VENUE_DIVERGENCE"
    assert decision.actionable is False


def test_atdd27_missing_native_market_evidence_is_an_abstention():
    decision = evaluate(
        snapshot_provider=lambda _market: ("skip", None, "insufficient history")
    )
    assert decision.decision == te.DECISION_NO_TRADE
    assert decision.rejection_code == "MARKET_DATA_UNAVAILABLE"
    assert "insufficient history" in decision.reason


def test_atdd29_missing_execution_evidence_abstains():
    decision = evaluate(
        snapshot_provider=lambda _market: (
            "ok",
            snapshot(execution_validation=None),
            None,
        ),
        execution_provider=lambda *a, **k: None,
    )
    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "EXECUTION_EVIDENCE_UNAVAILABLE"


def test_atdd28_target_attainability_rejection_abstains():
    # Targets far above the historical p90 become unattainable.
    decision = evaluate(
        snapshot_provider=provider(
            rolling_24h_upside_p75_pct=0.4,
            rolling_24h_upside_p90_pct=0.6,
            rolling_72h_upside_p75_pct=0.5,
            rolling_72h_upside_p90_pct=0.8,
            recent_24h_high=100.5,
            recent_72h_high=101.0,
        )
    )
    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "TARGET_ATTAINABILITY_REJECT"


def test_atdd32_after_cost_economics_reject_a_thin_account():
    # A small account cannot clear the fixed net-profit floor after costs.
    decision = evaluate(account_equity=1_000.0)
    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "ECONOMIC_REJECT"


def test_atdd30_undersized_capital_is_rejected():
    decision = evaluate(account_equity=2_500.0)
    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code in {
        "CAPITAL_BELOW_EXECUTABLE_MINIMUM",
        "ECONOMIC_REJECT",
    }


def test_atdd30_exposure_capacity_rejects_a_full_book():
    trades = [
        SimpleNamespace(
            status="active", symbol=name, capital=1_000.0, margin_leverage=1.0, direction="LONG"
        )
        for name in ("ETHUSD", "SOLUSD", "ADAUSD")
    ]
    decision = evaluate(active_trades=trades)
    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "EXPOSURE_REJECT"
    assert decision.portfolio is not None


def test_exposure_not_evaluated_is_recorded_as_unknown():
    decision = evaluate(active_trades=None)
    assert decision.decision == te.DECISION_ENTER_NOW
    assert decision.evidence["exposure"] == {"status": "EXPOSURE_NOT_EVALUATED"}


def test_atdd31_invalid_geometry_is_rejected(monkeypatch):
    from app.services import tradevisor_evaluation as module

    broken = module.EntryExitPlan(
        symbol="BTCUSD",
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=100.0,
        entry_high=101.0,
        chase_limit=102.0,
        stop_price=99.0,
        target_1=100.5,  # inside the entry zone: not a valid LONG plan
        target_2=110.0,
        reward_to_risk_1=0.5,
        reward_to_risk_2=2.0,
        risk_level="low",
        reason="broken",
        direction="LONG",
    )
    monkeypatch.setattr(module, "build_entry_exit_plan", lambda *a, **k: broken)
    decision = evaluate()
    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "INVALID_GEOMETRY"


# ---------------------------------------------------------------------------
# SELL semantics (ATDD-33)
# ---------------------------------------------------------------------------


def test_atdd33_sell_is_evidence_only_never_a_short():
    decision = evaluate(signal("SELL"))
    assert decision.decision == te.DECISION_WATCH
    assert decision.actionable is False
    assert decision.plan is None
    assert decision.evidence["sell_semantics"]["short_order"] == "NOT_PERMITTED"
    assert (
        decision.evidence["sell_semantics"]["auto_close_funded_position"]
        == "NOT_PERFORMED"
    )


def test_atdd33_sell_is_corroborated_only_by_a_bearish_native_regime():
    bearish = evaluate(signal("SELL"), snapshot_provider=provider(trend="bearish"))
    assert bearish.rejection_code == "SELL_CORROBORATED_BEARISH"

    bullish = evaluate(signal("SELL"))
    assert bullish.rejection_code == "SELL_UNCONFIRMED_BY_NATIVE"


def test_sell_without_native_data_still_abstains_safely():
    decision = evaluate(signal("SELL"), snapshot_provider=lambda _market: None)
    assert decision.decision == te.DECISION_WATCH
    assert decision.rejection_code == "SELL_EVIDENCE_ONLY_NO_NATIVE_DATA"
    assert decision.evidence["sell_semantics"]["short_order"] == "NOT_PERMITTED"


# ---------------------------------------------------------------------------
# Decision records cannot be read as permissions (ATDD-35)
# ---------------------------------------------------------------------------


def test_decision_record_declares_no_authority():
    record = evaluate().as_record()
    assert record["trade_authority_changed"] is False
    assert record["execution_authority"] is False
    assert record["admission_authority"] is False
    assert record["measurement_only"] is True
    assert record["evidence"]["native_admission"] == "NOT_PERFORMED"


