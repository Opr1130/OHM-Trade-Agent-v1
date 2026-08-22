from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from app.scanner.execution_validation import COMPLETE, FRESH, ExecutionValidation
from app.scanner.models import MarketSnapshot
from app.services.capital_allocation import recommend_capital
from app.services.capital_rotation_intelligence import (
    IncumbentOpportunity,
    evaluate_rotation,
)
from app.services.economic_quality_gate import EconomicGateResult, evaluate_economic_quality
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.profit_ranking import evaluate_profit_ranking
from app.services.target_attainability import TargetAttainabilityResult


def _plan(**overrides) -> EntryExitPlan:
    values = dict(
        symbol="SOLUSD",
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=100.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="adversarial",
        direction="LONG",
    )
    values.update(overrides)
    return EntryExitPlan(**values)


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="SOLUSD",
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
        execution_validation=ExecutionValidation(
            status="VALID",
            book_coverage_status=COMPLETE,
            warnings=[],
            estimated_visible_round_trip_market_drag_pct=0.2,
            recent_trade_status=FRESH,
        ),
    )


def _target_result(score: int = 90) -> TargetAttainabilityResult:
    return TargetAttainabilityResult(
        symbol="SOLUSD",
        qualified=True,
        target_1_move_pct=10.0,
        target_2_move_pct=15.0,
        target_1_atr_multiple=5.0,
        target_2_atr_multiple=7.5,
        clearance_to_24h_resistance_pct=2.0,
        clearance_to_72h_resistance_pct=3.0,
        momentum_context="supportive",
        volatility_context="normal",
        attainability_score=score,
        strengths=[],
        warnings=[],
        rejection_reasons=[],
    )


def _economic_result(target_2_move_pct: float = 10.0) -> EconomicGateResult:
    return EconomicGateResult(
        qualified=True,
        entry_reference=100.0,
        stop_pct=5.0,
        target_1_move_pct=5.0,
        target_2_move_pct=target_2_move_pct,
        recommended_capital=1000.0,
        target_1_gross_profit=50.0,
        target_2_gross_profit=100.0,
        estimated_costs=6.0,
        target_1_net_profit=44.0,
        target_2_net_profit=94.0,
    )


@pytest.mark.parametrize(
    "field",
    ["target_2", "reward_to_risk_2", "stop_price"],
)
def test_economic_gate_rejects_non_finite_plan_geometry(field):
    values = {field: math.nan}
    result = evaluate_economic_quality(
        _plan(**values),
        available_capital=10_000.0,
        min_net_profit=1.0,
    )
    assert result.qualified is False
    assert result.rejection_reason is not None
    assert "finite" in result.rejection_reason.lower() or "invalid" in result.rejection_reason.lower()


def test_explicit_production_economic_envelope_uses_twenty_percent_capital():
    result = evaluate_economic_quality(
        _plan(),
        available_capital=10_000.0,
        min_net_profit=1.0,
        max_capital_fraction=0.20,
    )
    assert result.recommended_capital == pytest.approx(2_000.0)
    assert result.position_notional == pytest.approx(2_000.0)
    assert result.target_2_gross_profit == pytest.approx(300.0)
    assert result.estimated_costs == pytest.approx(12.0)
    assert result.target_2_net_profit == pytest.approx(288.0)


def test_nonproduction_economic_gate_preserves_reusable_full_capital_default(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    result = evaluate_economic_quality(
        _plan(),
        available_capital=10_000.0,
        min_net_profit=1.0,
    )
    assert result.recommended_capital == pytest.approx(10_000.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"available_capital": math.nan},
        {"stop_distance_pct": math.inf},
        {"confidence_score": math.nan},
        {"net_edge_pct": math.inf},
        {"leverage": math.nan},
    ],
)
def test_capital_allocation_rejects_non_finite_inputs(kwargs):
    values = dict(
        available_capital=10_000.0,
        stop_distance_pct=5.0,
        confidence_score=80.0,
        quality_score=80.0,
        net_edge_pct=1.0,
        leverage=1.0,
    )
    values.update(kwargs)
    with pytest.raises(ValueError, match="finite"):
        recommend_capital(**values)


def test_profit_ranking_does_not_award_nan_economic_move_full_credit():
    result = evaluate_profit_ranking(
        _snapshot(),
        _target_result(),
        _economic_result(target_2_move_pct=math.nan),
    )
    assert math.isfinite(result.total_score)
    assert result.economic_opportunity_score == 0.0
    assert any("non-finite" in warning.lower() for warning in result.warnings)


def test_profit_ranking_does_not_award_nan_execution_drag_full_credit():
    snapshot = _snapshot()
    snapshot.execution_validation = ExecutionValidation(
        status="VALID",
        book_coverage_status=COMPLETE,
        warnings=[],
        estimated_visible_round_trip_market_drag_pct=math.nan,
        recent_trade_status=FRESH,
    )
    result = evaluate_profit_ranking(snapshot, _target_result(), _economic_result())
    assert math.isfinite(result.total_score)
    assert result.execution_quality_score < 20.0
    assert any("non-finite" in warning.lower() for warning in result.warnings)


def test_capital_rotation_non_finite_candidate_inputs_cannot_pass_exceptional_gate():
    active = [SimpleNamespace(capital=9_800.0)]
    incumbents = [
        IncumbentOpportunity(
            symbol="BTCUSD",
            capital=9_800.0,
            remaining_upside_pct=5.0,
            downside_to_stop_pct=2.0,
        )
    ]
    result = evaluate_rotation(
        candidate_symbol="SOLUSD",
        candidate_confidence_score=100.0,
        candidate_remaining_upside_pct=math.inf,
        candidate_downside_pct=2.0,
        account_capital=10_000.0,
        active_trades=active,
        incumbents=incumbents,
    )
    assert result.exceptional_gate_passed is False
    assert result.recommendation != "REVIEW_ROTATION"
    assert result.candidate_quality_proxy is None or math.isfinite(result.candidate_quality_proxy)
