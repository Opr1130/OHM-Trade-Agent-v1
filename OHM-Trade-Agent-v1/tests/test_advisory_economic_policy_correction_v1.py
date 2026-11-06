"""Advisory economic-policy correction: acceptance and regression contracts.

Owner-approved scope: ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1.

The three optional advisory filters - minimum estimated net profit, minimum
Target 2 movement and minimum reward/risk - are OFF by default, bounded when
enabled, and validated without ever falling back to the historical $75 / 4% /
2.5:1 floors. Disabling a filter never removes the independent requirement that
modelled net profit after costs be strictly positive, and never bypasses a
liquidity, capital, exposure, F11 or execution protection.
"""

import json
import inspect
import math
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.core.strategy_policy import parse_strategy_policy
from app.jobs import scan_opportunities
from app.opip.decision import store as opip_store
from app.opip.decision.models import GateStatus
from app.services import chief_analyst
from app.services import economic_quality_gate
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.market_scanner import ScanResult
from app.scanner.models import MarketSnapshot
from app.scanner.reference_market_validation import ReferenceMarketValidation
from app.services import active_trade_registry
from app.services import trade_action_gate, tradevisor_evaluation
from app.services.tradevisor_ingress import TradevisorSignal
from app.services.capital_allocation import CapitalAllocation
from app.services.economic_quality_gate import (
    ADVISORY_POLICY_ID,
    ADVISORY_POLICY_VERSION,
    EXECUTION_GATE_POLICY_ID,
    MIN_NET_PROFIT,
    MIN_REWARD_TO_RISK,
    MIN_TARGET_2_MOVE_PCT,
    PRODUCTION_MAX_CAPITAL_FRACTION,
    advisory_economic_policy,
    advisory_policy_from_profile,
    describe_advisory_economic_policy,
    evaluate_economic_quality,
    net_profit_is_positive,
    quantize_usd_cents,
)
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.portfolio_risk import PortfolioRiskDecision
from app.services.trade_decision_intelligence import TradeDecisionIntelligence

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]


def plan(
    *,
    target_2: float = 106.0,
    target_1: float = 103.0,
    reward_to_risk_2: float = 3.0,
    stop: float = 98.0,
    direction: str = "LONG",
) -> EntryExitPlan:
    """Build one deterministic LONG plan around a $100 entry reference."""
    return EntryExitPlan(
        symbol="TESTUSD",
        valid_now=True,
        entry_style="ready",
        entry_low=100.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=stop,
        target_1=target_1,
        target_2=target_2,
        reward_to_risk_1=1.5,
        reward_to_risk_2=reward_to_risk_2,
        risk_level="low",
        reason="acceptance fixture",
        direction=direction,
    )


def plan_for_net(net: float, *, equity: float = 5_000.0) -> tuple[EntryExitPlan, dict]:
    """Return a plan whose after-cost Target 2 profit is exactly ``net`` dollars.

    Notional is equity * 20% at 1x; a 10% Target 2 move grosses 10% of notional,
    and the round-trip cost percentage is solved so gross minus costs equals the
    requested net. The move (10%) and reward/risk (3:1) therefore never bind, so
    only the profit rule under test can reject the candidate.
    """
    notional = equity * PRODUCTION_MAX_CAPITAL_FRACTION
    gross = notional * 0.10
    cost_pct = (gross - net) / notional * 100.0
    return plan(target_2=110.0, target_1=105.0), {
        "estimated_round_trip_cost_pct": cost_pct,
        "advisory_policy": {"estimated_round_trip_cost_pct": cost_pct},
    }


def advisory(**filters) -> dict:
    return {"advisory_policy": filters} if filters else {}


def gate(p: EntryExitPlan, equity: float = 500.0, **policy):
    return evaluate_economic_quality(
        p,
        available_capital=equity,
        max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
        **advisory_economic_policy(policy or None),
    )


# --------------------------------------------------------------- AC-001 ---


@pytest.mark.acceptance
def test_ac_001_default_off_admits_positive_after_cost_candidate():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-001: default OFF."""
    policy = advisory_economic_policy()
    assert float(policy["min_net_profit"]) == 0.0
    assert float(policy["min_target_2_move_pct"]) == 0.0
    assert float(policy["min_reward_to_risk"]) == 0.0
    assert policy["policy_id"] == ADVISORY_POLICY_ID

    result = gate(plan())
    assert result.qualified is True
    # No historical floor is named in either direction: the candidate is below
    # $75 of profit and would have failed the execution-compatible bar.
    assert 0 < result.target_2_net_profit < MIN_NET_PROFIT
    assert result.policy_id == ADVISORY_POLICY_ID
    assert result.min_net_profit_applied == 0.0
    assert result.rejection_reason is None


@pytest.mark.acceptance
def test_ac_001_missing_optional_configuration_is_off():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-001: omitted is OFF."""
    assert advisory_policy_from_profile(None) is None
    for overrides in (None, {}, {"min_net_profit": 0}, {"min_reward_to_risk": 0.0}):
        described = describe_advisory_economic_policy(overrides)
        assert described["regime"] == "ADVISORY"
        assert described["policy_version"] == ADVISORY_POLICY_VERSION
        assert described["requires_positive_net_profit"] is True
        assert described["filters"]["min_net_profit"]["value"] >= 0.0
    # An explicit zero on one knob does not enable another.
    partial = describe_advisory_economic_policy({"min_net_profit": 0})
    assert partial["filters"]["min_net_profit"] == {"enabled": False, "value": 0.0}
    assert partial["filters"]["min_target_2_move_pct"] == {
        "enabled": False,
        "value": 0.0,
    }


# --------------------------------------------------------------- AC-002 ---

_AC_002_NET_VALUES = (
    -0.01, 0.0, 0.01, 10.0, 25.70, 28.06, 36.86, 74.99, 75.0, 75.01, 100.0,
)


@pytest.mark.acceptance
def test_ac_002_net_profit_boundaries_have_no_step_at_seventy_five():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-002: strict positivity, no $75 step."""
    observed = {}
    for net in _AC_002_NET_VALUES:
        fixture_plan, kwargs = plan_for_net(net)
        result = evaluate_economic_quality(
            fixture_plan,
            available_capital=5_000.0,
            max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
            **advisory_economic_policy(kwargs["advisory_policy"]),
        )
        observed[net] = result
        # The modelled projection really is the requested value ...
        assert result.target_2_net_profit == pytest.approx(net, abs=0.005)
        # ... and eligibility is exactly "strictly positive after costs".
        assert result.qualified is (net > 0), (net, result.rejection_reason)

    # No discontinuity at the retired $75 bar.
    assert observed[74.99].qualified is True
    assert observed[75.0].qualified is True
    assert observed[75.01].qualified is True
    assert observed[-0.01].qualified is False
    assert observed[0.0].qualified is False
    # A zero-return opportunity is named truthfully, never as a $75 shortfall.
    assert "$75" not in (observed[0.0].rejection_reason or "")
    assert "below minimum $0.00 after estimated costs" in (
        observed[0.0].rejection_reason or ""
    )


@pytest.mark.acceptance
def test_ac_002_zero_and_negative_after_cost_outcomes_are_not_recommendable():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-002: no fabricated profit."""
    # A cost assumption larger than the gross move models a true loss.
    loss = gate(plan(), estimated_round_trip_cost_pct=7.0)
    assert loss.qualified is False
    assert loss.target_2_net_profit < 0

    # Cent-level rounding cannot manufacture a pass out of a sub-cent result.
    assert net_profit_is_positive(0.0) is False
    assert net_profit_is_positive(0.004) is False
    assert net_profit_is_positive(-0.004) is False
    assert net_profit_is_positive(0.01) is True
    assert quantize_usd_cents(0.004) == 0.0
    assert quantize_usd_cents(24.995) == 25.0
    assert quantize_usd_cents(-0.004) == 0.0

    # Unavailable economic input is rejected, never assumed profitable. The
    # policy layer refuses the configuration outright ...
    with pytest.raises(ValueError):
        advisory_economic_policy({"min_net_profit": math.nan})
    with pytest.raises(ValueError):
        advisory_economic_policy({"min_target_2_move_pct": math.inf})
    # ... and a non-finite value handed straight to the gate yields an
    # unqualified result rather than an assumed profit.
    assert (
        evaluate_economic_quality(
            plan(),
            available_capital=500.0,
            max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
            min_net_profit=math.nan,
        ).qualified
        is False
    )


# --------------------------------------------------------------- AC-003 ---


@pytest.mark.acceptance
def test_ac_003_configured_dollar_floor_boundaries():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-003: an enabled floor is exact."""
    verdicts = {}
    for net in (24.99, 25.0, 25.01):
        fixture_plan, kwargs = plan_for_net(net)
        result = evaluate_economic_quality(
            fixture_plan,
            available_capital=5_000.0,
            max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
            **advisory_economic_policy(
                {**kwargs["advisory_policy"], "min_net_profit": 25.0}
            ),
        )
        verdicts[net] = result

    assert verdicts[24.99].qualified is False
    assert "below minimum $25.00" in (verdicts[24.99].rejection_reason or "")
    assert verdicts[25.0].qualified is True
    assert verdicts[25.01].qualified is True
    # The floor is reported as applied, not inferred.
    assert verdicts[24.99].min_net_profit_applied == 25.0


# --------------------------------------------------------------- AC-004 ---


@pytest.mark.acceptance
def test_ac_004_target_move_filter_off_and_configured_boundaries():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-004: Target 2 movement."""
    def move_plan(move_pct: float) -> EntryExitPlan:
        return plan(target_1=102.0, target_2=100.0 + move_pct, reward_to_risk_2=3.0)

    # OFF: a sub-4% deterministic projection is not rejected for its size.
    off = gate(move_plan(2.5))
    assert off.qualified is True
    assert off.target_2_move_pct == pytest.approx(2.5, abs=0.01)
    assert off.min_target_2_move_pct_applied == 0.0

    configured = {}
    for move_pct in (3.99, 4.0, 4.01):
        configured[move_pct] = gate(
            move_plan(move_pct), min_target_2_move_pct=4.0
        )
    assert configured[3.99].qualified is False
    assert "below minimum 4.00%" in (configured[3.99].rejection_reason or "")
    assert configured[4.0].qualified is True
    assert configured[4.01].qualified is True


# --------------------------------------------------------------- AC-005 ---


@pytest.mark.acceptance
def test_ac_005_reward_risk_filter_off_and_configured_boundaries():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-005: reward/risk."""
    # OFF: a structurally valid 2.0:1 plan is not rejected for the old 2.5 bar.
    off = gate(plan(reward_to_risk_2=2.0))
    assert off.qualified is True
    assert off.min_reward_to_risk_applied == 0.0

    configured = {}
    for ratio in (2.49, 2.50, 2.51):
        configured[ratio] = gate(plan(reward_to_risk_2=ratio), min_reward_to_risk=2.5)
    assert configured[2.49].qualified is False
    assert "below minimum 2.50:1" in (configured[2.49].rejection_reason or "")
    assert configured[2.50].qualified is True
    assert configured[2.51].qualified is True

    # A disabled ratio filter never authorises invalid geometry.
    broken = plan(target_2=99.0, target_1=98.5, stop=98.0)
    assert gate(broken).qualified is False
    assert "geometry" in (gate(broken).rejection_reason or "").lower()


# --------------------------------------------------------------- AC-006 ---

_INVALID_ADVISORY_VALUES = (
    None,
    "",
    "  ",
    "abc",
    True,
    False,
    -1,
    -0.01,
    math.nan,
    math.inf,
    -math.inf,
    [1],
    {"value": 1},
)


@pytest.mark.acceptance
def test_ac_006_invalid_advisory_configuration_is_refused():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-006: fail closed."""
    for value in _INVALID_ADVISORY_VALUES:
        with pytest.raises(ValueError):
            advisory_economic_policy({"min_net_profit": value})

    # An unknown key is refused rather than ignored: a typo cannot silently
    # disable or silently backdate an owner-configured filter.
    with pytest.raises(ValueError, match="Unsupported advisory economic key"):
        advisory_economic_policy({"min_net_profit_usd": 25})
    with pytest.raises(ValueError, match="Unsupported advisory economic key"):
        advisory_economic_policy({"min_profit": 25})

    # A zero and a small positive value are the only valid "off" and "on" forms.
    assert float(
        advisory_economic_policy({"min_net_profit": 0})["min_net_profit"]
    ) == 0.0
    assert float(
        advisory_economic_policy({"min_net_profit": 0.01})["min_net_profit"]
    ) == 0.01
    # Precision boundary: the smallest finite positive float is still a value.
    assert float(
        advisory_economic_policy({"min_net_profit": 1e-9})["min_net_profit"]
    ) == 1e-9

    # The profile-to-policy translation refuses an unknown profile field too.
    with pytest.raises(ValueError, match="Unsupported advisory profile field"):
        advisory_policy_from_profile({"min_net_profit_usd": 0, "unknown": 1})


@pytest.mark.acceptance
def test_ac_006_profile_validation_rejects_unknown_keys_and_versions():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-006: versioned schema."""
    source = json.loads((APP_ROOT / "app/core/strategy_policy.json").read_text())
    # The reference profile really does carry all-OFF advisory floors.
    assert source["advisory_economics"]["min_net_profit_usd"] == 0
    assert source["advisory_economics"]["min_target_2_move_pct"] == 0
    assert source["advisory_economics"]["min_reward_to_risk"] == 0
    assert advisory_policy_from_profile(source["advisory_economics"]) == {
        "min_net_profit": 0,
        "min_target_2_move_pct": 0,
        "min_reward_to_risk": 0,
        "estimated_round_trip_cost_pct": 0.6,
    }

    # An incompatible policy version is refused, never coerced.
    incompatible = json.loads(json.dumps(source))
    incompatible["schema_version"] = 2
    with pytest.raises(ValueError, match="INVALID_STRATEGY_POLICY_SCHEMA"):
        parse_strategy_policy(json.dumps(incompatible))

    # A missing advisory key is refused, not defaulted to a historical floor.
    incomplete = json.loads(json.dumps(source))
    del incomplete["advisory_economics"]["min_net_profit_usd"]
    with pytest.raises(ValueError, match="INVALID_ECONOMIC_POLICY"):
        parse_strategy_policy(json.dumps(incomplete))

    # An extreme value beyond the versioned bound is refused.
    extreme = json.loads(json.dumps(source))
    extreme["advisory_economics"]["min_net_profit_usd"] = 10_001
    with pytest.raises(ValueError, match="ECONOMIC_PROFILE_BOUND_EXCEEDED"):
        parse_strategy_policy(json.dumps(extreme))

    # Negative and null are outside the schema as well.
    for bad in (-1, None):
        invalid = json.loads(json.dumps(source))
        invalid["advisory_economics"]["min_net_profit_usd"] = bad
        with pytest.raises(ValueError):
            parse_strategy_policy(json.dumps(invalid))



# --------------------------------------------------------------- AC-007 ---


def _intelligence(capital: float = 400.0) -> TradeDecisionIntelligence:
    return TradeDecisionIntelligence(
        calibration_status="INSUFFICIENT_DATA",
        calibration_multiplier=1.0,
        projected_net_edge_pct=8.0,
        quality_score=90.0,
        allocation=CapitalAllocation(
            recommended_capital=capital,
            risk_dollars=20.0,
            position_pct=20.0,
            reason="base",
        ),
        portfolio_risk=PortfolioRiskDecision(
            allowed=True,
            reason="portfolio risk limits satisfied",
            open_positions=0,
            gross_exposure=0.0,
            proposed_exposure=capital,
            proposed_total_exposure=capital,
        ),
    )


def _install_action_gate(monkeypatch, capital: float = 400.0) -> None:
    monkeypatch.setattr(
        trade_action_gate,
        "evaluate_trade_decision",
        lambda **kwargs: _intelligence(capital),
    )
    monkeypatch.setattr(
        trade_action_gate,
        "evaluate_portfolio_risk",
        lambda **kwargs: PortfolioRiskDecision(
            allowed=True,
            reason="portfolio risk limits satisfied",
            open_positions=0,
            gross_exposure=0.0,
            proposed_exposure=kwargs["proposed_capital"],
            proposed_total_exposure=kwargs["proposed_capital"],
        ),
    )


def _aktusd_candidate(observed_net: float, *, ceiling: float = 100.0) -> dict:
    """Deterministic fixture for the November 6 capacity-adjusted net profit.

    ``economic_validation_net_t2`` is the projection at the $400 validation
    envelope; a $100 liquidity ceiling scales it by 0.25, reproducing the
    recorded capacity-adjusted values exactly.
    """
    scale = ceiling / 400.0
    return {
        "economic_qualified": True,
        "direction": "LONG",
        "liquidity_capacity_ceiling_usd": ceiling,
        "economic_validation_capital": 400.0,
        "economic_validation_net_t2": observed_net / scale,
    }


_AKTUSD_OBSERVED_CAPACITY_NET = (25.70, 28.06, 36.86, 18.89)


@pytest.mark.acceptance
def test_ac_007_november_aktusd_capacity_rejections_are_not_caused_by_the_old_floor(
    monkeypatch,
):
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-007: incident regression."""
    _install_action_gate(monkeypatch)
    for observed in _AKTUSD_OBSERVED_CAPACITY_NET:
        candidate = _aktusd_candidate(observed)
        decision = trade_action_gate.apply_action_gate(
            candidate=candidate,
            plan=plan(),
            account_capital=2_000.0,
            active_trades=[],
        )
        # The capacity adjustment is preserved exactly as recorded ...
        assert candidate["capacity_adjusted_validation_net_t2"] == observed
        assert candidate["liquidity_capacity_capped"] is True
        assert candidate["recommended_capital"] == 100.0
        # ... and the advisory recommendation is not vetoed by the old $75 bar.
        assert decision.allowed is True, decision.reason
        assert "75" not in decision.reason
        assert decision.rejection_class is None
        assert candidate["action_gate_allowed"] is True
        assert "action_gate_rejection_class" not in candidate
        # The applied advisory policy is recorded with the decision.
        applied = candidate["advisory_economic_policy"]
        assert applied["policy_id"] == ADVISORY_POLICY_ID
        assert applied["filters"]["min_net_profit"] == {
            "enabled": False,
            "value": 0.0,
        }

    # A configured floor still applies, and is reported as an economic stop -
    # never as missing capital.
    candidate = _aktusd_candidate(25.70)
    decision = trade_action_gate.apply_action_gate(
        candidate=candidate,
        plan=plan(),
        account_capital=2_000.0,
        active_trades=[],
        advisory_policy={"min_net_profit": 30.0},
    )
    assert decision.allowed is False
    assert decision.rejection_class == trade_action_gate.REJECTION_ECONOMIC_FLOOR
    assert decision.reason_code == "ECONOMIC_GATE_FAILED"
    assert "NO_CAPITAL" not in (decision.reason_code or "")
    assert "below the configured advisory minimum USD 30.00" in decision.reason


@pytest.mark.acceptance
def test_ac_007_negative_capacity_adjustment_still_blocks(monkeypatch):
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-007: unprofitable stays blocked."""
    _install_action_gate(monkeypatch)
    for observed in (-0.01, 0.0):
        candidate = _aktusd_candidate(observed)
        decision = trade_action_gate.apply_action_gate(
            candidate=candidate,
            plan=plan(),
            account_capital=2_000.0,
            active_trades=[],
        )
        assert decision.allowed is False, observed
        assert decision.rejection_class == trade_action_gate.REJECTION_PROFITABILITY
        assert "is not positive after estimated costs" in decision.reason

    # Capacity capping that destroys the executable notional still fails, and is
    # classified as a capacity stop rather than an economic one.
    candidate = _aktusd_candidate(25.70, ceiling=50.0)
    decision = trade_action_gate.apply_action_gate(
        candidate=candidate,
        plan=plan(),
        account_capital=2_000.0,
        active_trades=[],
    )
    assert decision.allowed is False
    assert decision.rejection_class == (
        trade_action_gate.REJECTION_MIN_EXECUTABLE_NOTIONAL
    )
    assert "minimum executable notional" in decision.reason


# --------------------------------------------------------------- AC-008 ---
#
# End-to-end advisory journey over a real production scan. Only I/O boundaries
# are stubbed (market fetch, model transport, position registry, Telegram send,
# lineage/paper writers). Every deterministic gate - the Chief pre-AI viability
# prefilter, the recommendation gate, target attainability, economic quality,
# the capital/portfolio action gate and entry/exit planning - runs for real, so
# this proves the policy rather than a mock of it.


def _journey_snapshot(symbol: str, shape: str = "viable", price: float = 100.0):
    result = MarketSnapshot(
        symbol=symbol, last_price=price, ema20=price * 0.99, ema50=price * 0.95,
        ema200=price * 0.90, rsi=55.0, macd_line=1.0, macd_signal=0.5,
        macd_histogram=0.5, atr=price * 0.02, atr_pct=2.0, volume_ratio=1.5,
        technical_score=90, trend="bullish",
        recent_24h_high=price * 1.20, recent_24h_low=price * 0.94,
        recent_72h_high=price * 1.30, recent_72h_low=price * 0.88,
        momentum_6h_pct=1.0, momentum_24h_pct=3.0, momentum_72h_pct=7.0,
        distance_to_24h_high_pct=16.0, distance_to_72h_high_pct=23.0,
        realized_range_24h_pct=16.0, realized_range_72h_pct=28.0,
        average_hourly_range_24h_pct=1.0, average_hourly_range_72h_pct=1.1,
        rolling_24h_range_median_pct=8.0, rolling_24h_range_p75_pct=10.0,
        rolling_24h_range_p90_pct=12.0, rolling_72h_range_median_pct=12.0,
        rolling_72h_range_p75_pct=16.0, rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=6.0, rolling_24h_upside_p75_pct=8.0,
        rolling_24h_upside_p90_pct=10.0, rolling_72h_upside_median_pct=10.0,
        rolling_72h_upside_p75_pct=14.0, rolling_72h_upside_p90_pct=18.0,
        underlying_asset=symbol.removesuffix("USD"), primary_pair=symbol,
        primary_quote_currency="USD", combined_24h_liquidity_usd=5_000_000.0,
        primary_24h_liquidity_usd=5_000_000.0,
        cross_pair_confirmation_status="CONFIRMED", ticker_last=price,
    )
    if shape == "no_economic_room":
        # Real volatility collapse: the targets cannot clear modelled costs.
        result.atr = price * 0.0005
        result.atr_pct = 0.05
    result.execution_validation = ExecutionValidation(
        status="VALID", book_coverage_status="COMPLETE", warnings=[],
        validation_notional_usd=2_000.0, spread_bps=6.0,
        estimated_visible_round_trip_market_drag_pct=0.25,
        recent_trade_status="FRESH",
    )
    result.independent_market_reference = ReferenceMarketValidation(
        status="CONFIRMED", available=True, mapping_status="UNIQUE",
        api_mode="KEYLESS", coingecko_id="asset", coingecko_name="Asset",
        matched_candidate_count=1, reference_price_usd=price,
        kraken_normalized_price_usd=price, price_divergence_pct=0.0,
    )
    return result


class _FakeChief:
    def __init__(self, top_candidates):
        self._payload = {
            "market_view": "neutral",
            "recommended_action": "alert" if top_candidates else "no_trade",
            "top_candidates": top_candidates,
            "summary": "journey summary",
        }
        self.responses = SimpleNamespace(create=self._create)

    def __call__(self, *args, **kwargs):
        return self

    def _create(self, **kwargs):
        return SimpleNamespace(output_text=json.dumps(self._payload), usage=None)


def _install_scan(monkeypatch, tmp_path, snapshots, *, chief_top=None, extra_settings=None):
    """Stub only I/O boundaries; keep every deterministic gate real."""
    settings = SimpleNamespace(
        openai_model="model", openai_api_key="key", account_equity=10_000.0,
        coingecko_api_key=None, cryptopanic_auth_token=None,
        cryptopanic_api_plan="developer", coinmarketcal_api_key=None,
        telegram_bot_token=None, telegram_chat_id=None, telegram_enabled=False,
        price_movement_mode="off", max_margin_leverage=3.0,
        paper_trade_capital_per_trade=1_000.0, paper_trade_max_hold_hours=24,
        paper_trade_pending_ttl_hours=24, paper_trade_starting_equity=10_000.0,
        paper_trade_max_positions=3, tradingview_v2_enabled=False,
    )
    for key, value in (extra_settings or {}).items():
        setattr(settings, key, value)
    sent: list = []
    monkeypatch.setattr(scan_opportunities, "get_settings", lambda: settings)
    monkeypatch.setattr(
        scan_opportunities, "scan_market",
        lambda limit: ScanResult(
            snapshots, len(snapshots), len(snapshots), 0, 0, [], []
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "evaluate_market_regime",
        lambda items: SimpleNamespace(
            sample_size=len(items), regime="NEUTRAL", breadth_score=50.0,
            pct_above_ema20=50.0, pct_above_ema50=50.0, pct_above_ema200=50.0,
            pct_positive_momentum_24h=50.0, pct_positive_momentum_72h=50.0,
            pct_bullish_trend=50.0,
        ),
    )
    monkeypatch.setattr(scan_opportunities, "select_candidates", lambda items: items)
    monkeypatch.setattr(
        scan_opportunities, "validate_short_margin_eligibility",
        lambda candidates, **kwargs: SimpleNamespace(
            requested=0, eligible=0, ineligible=0, unavailable=0
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "confirm_secondary_markets",
        lambda *args: SimpleNamespace(requested=0, analyzed=0, failed=0),
    )
    monkeypatch.setattr(
        scan_opportunities, "deep_validate_candidates",
        lambda candidates, *args: candidates,
    )
    monkeypatch.setattr(
        scan_opportunities, "validate_finalist_references",
        lambda candidates, *args, **kwargs: SimpleNamespace(
            requested=len(candidates), available=len(candidates), unavailable=0,
            ambiguous=0, api_mode="KEYLESS",
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "load_coingecko_global_context",
        lambda **kwargs: SimpleNamespace(
            status="UNAVAILABLE", market_cap_change_24h_pct=None,
            btc_market_cap_percentage=None,
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "validate_finalist_news",
        lambda candidates, **kwargs: SimpleNamespace(
            requested=len(candidates), available=0, unavailable=len(candidates),
            unresolved=0,
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "validate_scheduled_catalysts",
        lambda candidates, **kwargs: SimpleNamespace(
            requested=len(candidates), available=0, unresolved=len(candidates),
            unavailable=0,
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "enrich_finalist_market_intelligence",
        lambda candidates, regime: SimpleNamespace(
            candidates=candidates, evidence=[], assessments={},
            chief_market_regime_context=None,
        ),
    )
    # I/O boundary 1: Telegram delivery. The candidate that reaches this call is
    # exactly the candidate the notification policy admitted.
    monkeypatch.setattr(
        scan_opportunities, "send_trade_plan",
        lambda **kwargs: sent.append(kwargs) or True,
    )
    # I/O boundary 2: the on-disk active-trade registry. The action gate reads it
    # through ``paper_v2_portfolio_source.projected_positions``, which imports
    # ``active_trade_registry.get_active_trades`` at call time, so that module
    # attribute is the boundary that actually runs - not the scanner's local
    # alias. Both bindings are stubbed so the portfolio input is deterministic on
    # any platform (the real registry lives under an absolute /app path that a CI
    # runner cannot create).
    monkeypatch.setattr(scan_opportunities, "get_active_trades", lambda: [])
    monkeypatch.setattr(
        active_trade_registry, "get_active_trades", lambda: []
    )
    monkeypatch.setattr(
        scan_opportunities, "_prepare_qualified_lineage",
        lambda ranked, **kwargs: (len(list(ranked)), 0),
    )
    monkeypatch.setattr(
        scan_opportunities, "_publish_freqtrade_paper_opportunities",
        lambda ranked, **kwargs: (0, 0),
    )
    monkeypatch.setattr(
        scan_opportunities, "_maybe_enroll_paper_opportunities",
        lambda ranked, **kwargs: (0, 0),
    )
    monkeypatch.setattr(
        scan_opportunities, "_capture_native_scan_cohort", lambda scan, **kwargs: 0
    )
    monkeypatch.setattr(scan_opportunities, "_paper_trade_enabled_safe", lambda: False)


    # Real Chief review; only its transport and its writers are isolated.
    monkeypatch.setattr(chief_analyst, "OpenAI", _FakeChief(chief_top or []))
    monkeypatch.setattr(
        chief_analyst, "capture_prefilter_rejection", lambda *a, **k: True
    )
    monkeypatch.setattr(
        chief_analyst, "capture_chief_review_decisions",
        lambda *a, **k: {"captured": 0, "qualified_alerts_deferred": 0,
                         "not_selected": 0, "unmatched": 0},
    )
    monkeypatch.setattr(chief_analyst, "append_usage_record", lambda **kwargs: {})
    monkeypatch.setattr(chief_analyst, "budget_block_reason", lambda: None)
    monkeypatch.setattr(chief_analyst, "get_cached_review", lambda fingerprint: None)
    monkeypatch.setattr(chief_analyst, "store_cached_review", lambda *a, **k: None)

    monkeypatch.setattr(
        opip_store, "FUNNEL_EVENTS_FILE", tmp_path / "funnel_events.jsonl"
    )
    monkeypatch.setattr(
        opip_store, "SCAN_SUMMARIES_FILE", tmp_path / "scan_summaries.jsonl"
    )
    monkeypatch.setattr(opip_store, "DEAD_LETTER_FILE", tmp_path / "dead.jsonl")
    monkeypatch.setenv("OPIP_FUNNEL_TELEMETRY_ENABLED", "true")
    return sent


def _journey_summary(tmp_path) -> dict:
    rows = opip_store.read_jsonl(tmp_path / "scan_summaries.jsonl")
    assert rows, "the scan must persist exactly one summary"
    return rows[-1]


def _chief_alert(symbol: str) -> list:
    return [
        {
            "symbol": symbol, "direction": "LONG", "rank": 1,
            "confidence": 95, "risk_level": "low",
            "decision": "alert", "reason": "clean continuation",
        }
    ]


@pytest.mark.acceptance
def test_ac_008_advisory_journey_admits_positive_opportunity(
    monkeypatch, tmp_path, capsys
):
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-008: full advisory journey."""
    sent = _install_scan(
        monkeypatch, tmp_path, [_journey_snapshot("AKTUSD")],
        chief_top=_chief_alert("AKTUSD"),
    )
    scan_opportunities.main()

    output = capsys.readouterr().out
    summary = _journey_summary(tmp_path)
    # The candidate survived selection, the pre-AI prefilter, the Chief review,
    # target quality, economics, ranking and the capital/portfolio gate.
    assert "ECONOMIC PASS LONG AKTUSD" in output
    if "ACTION GATE PASS rank=1 LONG AKTUSD" not in output:
        pytest.fail(
            "the capital/portfolio gate did not pass the qualified candidate: "
            f"{[line for line in output.splitlines() if 'ACTION GATE' in line]}"
        )
    assert "ACTION GATE REJECT" not in output
    assert summary["funnel"]["entered"] == 1
    assert summary["funnel"]["qualified"] == 1
    assert summary["funnel"]["rejected_by_policy"] == 0
    # It reached the notification boundary with an authorized advisory state.
    assert len(sent) == 1
    candidate = sent[0]["candidate"]
    assert candidate["symbol"] == "AKTUSD"
    assert candidate["action_gate_allowed"] is True
    assert "action_gate_rejection_class" not in candidate
    assert candidate["advisory_economic_policy"]["policy_id"] == ADVISORY_POLICY_ID
    # The shadow engine reproduced the production verdict under the same policy.
    comparison = summary.get("shadow_comparison") or {}
    if comparison:
        assert comparison.get("divergences", 0) == 0, comparison


@pytest.mark.acceptance
def test_ac_008_seeded_exposure_still_blocks_the_advisory(monkeypatch, tmp_path, capsys):
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-008: portfolio protection stays live."""
    sent = _install_scan(
        monkeypatch, tmp_path, [_journey_snapshot("AKTUSD")],
        chief_top=_chief_alert("AKTUSD"),
    )
    # Seed a real open exposure through the same registry boundary the action
    # gate reads. A duplicate symbol must still be refused: stubbing the I/O
    # boundary must never stub out portfolio protection.
    monkeypatch.setattr(
        active_trade_registry,
        "get_active_trades",
        lambda: [
            SimpleNamespace(
                symbol="AKTUSD", status="active", direction="LONG",
                capital=500.0, margin_leverage=1.0,
            )
        ],
    )
    scan_opportunities.main()

    output = capsys.readouterr().out
    assert "ECONOMIC PASS LONG AKTUSD" in output
    assert "ACTION GATE PASS" not in output
    assert "ACTION GATE REJECT rank=1 LONG AKTUSD" in output
    # Nothing reached the notification boundary, and the stop is classified.
    assert sent == []


@pytest.mark.acceptance
def test_ac_008_advisory_journey_still_blocks_invalid_opportunity(
    monkeypatch, tmp_path, capsys
):
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-008: invalid stays blocked."""
    sent = _install_scan(
        monkeypatch, tmp_path, [_journey_snapshot("RAYUSD", "no_economic_room")],
        chief_top=_chief_alert("RAYUSD"),
    )
    scan_opportunities.main()

    output = capsys.readouterr().out
    summary = _journey_summary(tmp_path)
    assert "ACTION GATE PASS" not in output
    assert summary["funnel"]["entered"] == 1
    assert summary["funnel"]["qualified"] == 0
    assert summary["funnel"]["rejected_by_policy"] == 1
    assert summary["funnel"]["operationally_unresolved"] == 0
    # A genuine economic/target failure is a POLICY rejection, never an
    # operational failure and never a qualified advisory.
    assert summary["terminal"]["reason_classes"]["POLICY"] == 1
    assert summary["terminal"]["reason_classes"]["OPERATIONAL"] == 0
    assert summary["terminal"]["dominant_terminal_gate"] in {
        "DETERMINISTIC_QUALITY",
        "TARGET_QUALITY",
        "ECONOMIC_QUALITY",
    }
    # Nothing reached the notification boundary.
    assert sent == []



# --------------------------------------------------------------- AC-009 ---


@pytest.mark.acceptance
def test_ac_009_tradevisor_final_allocation_economics_use_advisory_policy():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-009: Tradevisor parity."""
    from app.services import tradevisor_evaluation

    applied = tradevisor_evaluation.ADVISORY_ECONOMIC_POLICY
    assert applied["policy_id"] == ADVISORY_POLICY_ID
    assert float(applied["min_net_profit"]) == 0.0
    assert float(applied["min_target_2_move_pct"]) == 0.0
    assert float(applied["min_reward_to_risk"]) == 0.0
    evidence = tradevisor_evaluation.ADVISORY_ECONOMIC_POLICY_EVIDENCE
    assert evidence["regime"] == "ADVISORY"
    assert evidence["requires_positive_net_profit"] is True

    # The module's own two-step basis: qualify at the validation envelope, then
    # report at the F7-permitted allocation. Both calls apply the advisory
    # policy, so a positive projection below $75 survives both, and the final
    # figure is the smaller, deployable one.
    equity, allocation = 5_000.0, 500.0
    path = plan()
    envelope = evaluate_economic_quality(
        path,
        equity,
        max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
        **applied,
    )
    final = evaluate_economic_quality(
        path,
        equity,
        max_capital_fraction=allocation / equity,
        max_account_risk_at_stop_pct=None,
        **applied,
    )
    assert envelope.qualified is True
    assert final.qualified is True
    assert envelope.min_net_profit_applied == 0.0
    assert final.min_net_profit_applied == 0.0
    assert 0 < final.target_2_net_profit < envelope.target_2_net_profit
    assert final.target_2_net_profit < MIN_NET_PROFIT
    # The executed defaults would have refused this same plan, so the two
    # regimes are provably distinct and the difference is labelled.
    assert (
        evaluate_economic_quality(
            path,
            equity,
            max_capital_fraction=allocation / equity,
            max_account_risk_at_stop_pct=None,
        ).qualified
        is False
    )

    source = pathlib.Path(
        "app/services/tradevisor_evaluation.py"
    ).read_text(encoding="utf-8")
    assert "ADVISORY_ECONOMIC_POLICY" in source
    assert "ADVISORY_VALIDATION_ENVELOPE" in source
    # Tradevisor still cannot become actionable: the advisory blocker stands.
    assert "ADVISORY_EVIDENCE_INCOMPLETE" in source
    assert "F6_TRUSTED_FORECAST" in source


# --------------------------------------------------------------- AC-010 ---


@pytest.mark.acceptance
def test_ac_010_advisory_pass_does_not_grant_execution_or_change_authority():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-010: execution independence."""
    from app.core.strategy_policy import parse_strategy_policy

    # A qualifying advisory projection carries no execution authority.
    result = gate(plan())
    assert result.qualified is True
    assert not hasattr(result, "execute")
    assert not hasattr(result, "authorized")

    # The versioned profile still refuses to carry trading permission.
    source = json.loads((APP_ROOT / "app/core/strategy_policy.json").read_text())
    assert source["protection"] == {
        "allow_funded_orders": False,
        "allow_auto_trading_activation": False,
    }
    granted = json.loads(json.dumps(source))
    granted["protection"]["allow_funded_orders"] = True
    with pytest.raises(ValueError, match="TRADING_PERMISSION_CONFIG_FORBIDDEN"):
        parse_strategy_policy(json.dumps(granted))

    # The action gate remains upstream of Telegram and downstream of F11/capital:
    # a passing advisory is `allowed` and nothing more.
    gate_source = pathlib.Path(
        "app/services/trade_action_gate.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in ("kraken", "ccxt", "place_order", "create_order", "cancel_order"):
        assert forbidden not in gate_source

    # The owner editor cannot reach execution, F11 or model authority.
    editor = (APP_ROOT / "app/api/strategy_admin.html").read_text(encoding="utf-8")
    assert "allow_funded_orders" not in editor
    assert "allow_auto_trading_activation" not in editor


@pytest.mark.acceptance
def test_ac_010_notification_boundary_still_requires_an_authorized_gate():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-010: no notification bypass."""
    from app.services import chief_alert_notifier

    source = chief_alert_notifier.__file__
    text = pathlib.Path(source).read_text(encoding="utf-8")
    # The notifier still requires an authorized action gate before delivery, and
    # an unevaluated gate is still refused. The fix removed the obsolete dollar
    # floor upstream; it did not remove the boundary.
    assert 'candidate.get("action_gate_allowed") is True' in text
    assert "ACTION_GATE_NOT_EVALUATED" in text
    assert "PORTFOLIO_OR_ALLOCATION_GUARDRAIL" in text



# --------------------------------------------------------------- AC-011 ---


@pytest.mark.acceptance
def test_ac_011_configuration_lifecycle_is_versioned_and_not_activated(tmp_path):
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-011: draft lifecycle."""
    pytest.importorskip(
        "fcntl", reason="draft storage uses POSIX advisory locks; exercised in CI"
    )
    from app.services.strategy_admin_drafts import (
        StrategyDraftConflict,
        activate_policy_draft,
        read_active_policy_status,
        read_policy_draft,
        rollback_active_policy,
        save_policy_draft,
    )

    reference = read_policy_draft(tmp_path)
    assert reference["status"] == "REFERENCE_ONLY"
    assert reference["active_runtime_changed"] is False
    assert reference["document"]["advisory_economics"]["min_net_profit_usd"] == 0

    # 1. A validated draft saves with an explicit OFF representation and does not
    #    touch runtime.
    proposed = reference["document"]
    proposed["advisory_economics"]["min_net_profit_usd"] = 0
    proposed["advisory_economics"]["min_target_2_move_pct"] = 4
    proposed["advisory_economics"]["min_reward_to_risk"] = 0
    saved = save_policy_draft(
        proposed, expected_digest=reference["digest"], directory=tmp_path
    )
    assert saved["status"] == "DRAFT_SAVED"
    assert saved["active_runtime_changed"] is False
    assert read_active_policy_status(tmp_path)["status"] == "BUNDLED_DEFAULT"

    # 2. A stale expected digest fails closed (concurrent editor).
    with pytest.raises(StrategyDraftConflict):
        save_policy_draft(
            proposed, expected_digest=reference["digest"], directory=tmp_path
        )

    # 3. Failed validation leaves the saved draft untouched.
    rejected = read_policy_draft(tmp_path)["document"]
    rejected["advisory_economics"]["min_net_profit_usd"] = 10_001
    with pytest.raises(ValueError):
        save_policy_draft(
            rejected, expected_digest=saved["digest"], directory=tmp_path
        )
    assert (
        read_policy_draft(tmp_path)["document"]["advisory_economics"][
            "min_net_profit_usd"
        ]
        == 0
    )

    # 4. Activation is explicit, separately authorized and restores the previous
    #    version on rollback. No funded-trading authority is ever conveyed.
    activated = activate_policy_draft(
        expected_digest=saved["digest"], directory=tmp_path
    )
    assert activated["status"] == "ACTIVATED_ADVISORY_PROFILE"
    assert activated["funded_trading_activated"] is False
    active = read_active_policy_status(tmp_path)
    assert active["status"] == "ACTIVE_PROFILE"
    assert active["funded_trading_activated"] is False
    assert active["document"]["advisory_economics"]["min_target_2_move_pct"] == 4

    rolled_back = rollback_active_policy(
        expected_digest=activated["digest"], directory=tmp_path
    )
    assert rolled_back["status"] == "ROLLED_BACK_ADVISORY_PROFILE"
    assert rolled_back["funded_trading_activated"] is False
    # Restart-safe persistence: the state is on disk, not in memory.
    assert read_active_policy_status(tmp_path)["digest"] == rolled_back["digest"]

    # 5. The guard refuses a rollback that has no safe target instead of
    #    guessing a version to restore. ``previous.json`` is deliberately not
    #    consumed by a rollback, so the guard must be exercised where the
    #    rollback state is genuinely absent (a fresh directory), not by
    #    repeating a rollback that already succeeded.
    empty = tmp_path / "fresh-admin-root"
    with pytest.raises(ValueError, match="NO_SAFE_ROLLBACK"):
        rollback_active_policy(
            expected_digest=rolled_back["digest"], directory=empty
        )
    assert not (empty / "active.json").exists()

    # 6. Repeating a rollback re-applies the same single previous version and is
    #    idempotent: it cannot invent a different policy and still never grants
    #    funded-trading authority.
    repeated = rollback_active_policy(
        expected_digest=rolled_back["digest"], directory=tmp_path
    )
    assert repeated["status"] == "ROLLED_BACK_ADVISORY_PROFILE"
    assert repeated["funded_trading_activated"] is False
    assert read_active_policy_status(tmp_path)["digest"] == rolled_back["digest"]

    # The bundled production reference policy was never modified by any of this.
    assert (
        json.loads((APP_ROOT / "app/core/strategy_policy.json").read_text())[
            "advisory_economics"
        ]["min_target_2_move_pct"]
        == 0
    )



# --------------------------------------------------------------- AC-012 ---


@pytest.mark.acceptance
def test_ac_012_recorded_threshold_and_policy_match_the_applied_rule():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-012: evidence integrity."""
    import dataclasses

    from app.opip.decision.gates import economic_quality_gate_from_result

    rejected = gate(plan(target_1=101.0, target_2=102.0), min_net_profit=25.0)
    assert rejected.qualified is False
    # The policy identity and the applied floor are recorded, and the reason
    # names exactly the same number.
    assert rejected.policy_id == ADVISORY_POLICY_ID
    assert rejected.min_net_profit_applied == 25.0
    assert "below minimum $25.00" in (rejected.rejection_reason or "")

    # An invalid-geometry rejection is attributed to the same policy, never to
    # the execution defaults it was not evaluated under.
    malformed = gate(plan(target_1=103.0, target_2=102.0), min_net_profit=25.0)
    assert malformed.qualified is False
    assert malformed.policy_id == ADVISORY_POLICY_ID
    assert malformed.min_net_profit_applied == 25.0
    assert malformed.min_reward_to_risk_applied == 0.0

    evidence = economic_quality_gate_from_result(rejected)
    assert evidence.threshold == 25.0
    assert evidence.metadata["policy_id"] == ADVISORY_POLICY_ID
    assert evidence.metadata["min_net_profit_applied"] == 25.0
    assert "below minimum $25.00" in evidence.reason

    # The advisory default never claims the execution bar.
    accepted = gate(plan())
    assert accepted.min_net_profit_applied == 0.0
    assert accepted.min_target_2_move_pct_applied == 0.0
    assert accepted.min_reward_to_risk_applied == 0.0
    execution_default = evaluate_economic_quality(
        plan(),
        available_capital=500.0,
        max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
    )
    assert execution_default.policy_id == EXECUTION_GATE_POLICY_ID
    assert execution_default.min_net_profit_applied == MIN_NET_PROFIT
    assert MIN_NET_PROFIT == 75.0
    assert MIN_TARGET_2_MOVE_PCT == 4.0
    assert MIN_REWARD_TO_RISK == 2.5

    # Historical evidence stays interpretable: the identical inputs reproduce
    # the identical verdict and attribution under the same policy version, so a
    # stored rejection can always be re-derived rather than rewritten.
    first = gate(plan(target_1=101.0, target_2=102.0), min_net_profit=25.0)
    second = gate(plan(target_1=101.0, target_2=102.0), min_net_profit=25.0)
    assert dataclasses.asdict(first) == dataclasses.asdict(second)
    assert first.policy_id == second.policy_id == ADVISORY_POLICY_ID
    assert ADVISORY_POLICY_VERSION == 2


@pytest.mark.acceptance
def test_ac_012_source_wide_old_floor_call_site_audit():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-012: call-site audit."""
    # The only remaining references to the named execution constants are their
    # own definition, the read-only threshold mirror, and the legacy-evidence
    # fallback in the Chief nearest-miss telemetry. No advisory call site may
    # reference them.
    execution_compat = {
        "services/economic_quality_gate.py",
        "services/chief_analyst.py",
        "opip/decision/thresholds.py",
    }
    names = ("MIN_NET_PROFIT", "MIN_TARGET_2_MOVE_PCT", "MIN_REWARD_TO_RISK")
    offenders = sorted(
        path.relative_to(APP_ROOT / "app").as_posix()
        for path in (APP_ROOT / "app").rglob("*.py")
        if any(name in path.read_text(encoding="utf-8") for name in names)
        and path.relative_to(APP_ROOT / "app").as_posix() not in execution_compat
    )
    assert offenders == []

    # The four advisory call sites resolve the policy instead.
    for rel in (
        "services/trade_action_gate.py",
        "services/tradevisor_evaluation.py",
        "opip/decision/gates.py",
        "jobs/scan_opportunities.py",
    ):
        text = (APP_ROOT / "app" / rel).read_text(encoding="utf-8")
        assert "advisory_economic_policy" in text, rel
        assert "MIN_NET_PROFIT" not in text, rel



# --------------------------------------------------------------- AC-013 ---
#
# Configured-threshold propagation. AC-001..AC-012 prove that the *default OFF*
# state is safe; these tests prove that an owner-ENABLED threshold is enforced
# consistently by every advisory path, from the versioned profile through the
# orchestration layer to the pure evaluator, with no path recomputing a default.

_TV_NOW = datetime(2026, 10, 23, 13, 0, 5, tzinfo=timezone.utc)
#: The Tradevisor fixture below models a 9.05% Target 2 move at 3.0:1 reward/risk.
_TV_MOVE_PCT = 9.05
_TV_REWARD_TO_RISK = 3.0


def _tv_snapshot():
    return MarketSnapshot(
        symbol="BTCUSD", last_price=100.0, ema20=99.0, ema50=95.0, ema200=90.0,
        rsi=58.0, macd_line=1.0, macd_signal=0.5, macd_histogram=0.5, atr=2.0,
        atr_pct=2.0, volume_ratio=1.5, technical_score=90, trend="bullish",
        recent_24h_high=112.0, recent_24h_low=94.0, recent_72h_high=120.0,
        recent_72h_low=88.0, momentum_6h_pct=1.0, momentum_24h_pct=3.0,
        momentum_72h_pct=7.0, rolling_24h_range_median_pct=8.0,
        rolling_24h_range_p75_pct=10.0, rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0, rolling_72h_range_p75_pct=16.0,
        rolling_72h_range_p90_pct=20.0, rolling_24h_upside_median_pct=8.0,
        rolling_24h_upside_p75_pct=10.0, rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0, rolling_72h_upside_p75_pct=16.0,
        rolling_72h_upside_p90_pct=20.0, rolling_24h_downside_median_pct=8.0,
        rolling_24h_downside_p75_pct=10.0, rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=12.0, rolling_72h_downside_p75_pct=16.0,
        rolling_72h_downside_p90_pct=20.0, ticker_last=100.0,
        execution_validation=ExecutionValidation(
            status="VALID", book_coverage_status="COMPLETE", warnings=[],
            best_bid=99.95, best_ask=100.05, mid_price=100.0, spread_pct=0.1,
            spread_bps=5.0, validation_notional_usd=250.0, buy_vwap=100.02,
            sell_vwap=99.98, buy_fully_covered=True, sell_fully_covered=True,
            estimated_visible_round_trip_market_drag_pct=0.10,
            recent_trade_status="FRESH", latest_trade_price=100.0,
            latest_trade_age_seconds=10.0, recent_trade_count=20,
        ),
    )


def _tv_signal() -> TradevisorSignal:
    return TradevisorSignal(
        dedup_id="advisory-policy-correction", signal="BUY", instrument="BTC/USD",
        market="BTCUSD", source_exchange="COINBASE", source_symbol="BTCUSD",
        timeframe_minutes=60,
        bar_opened_at=_TV_NOW - timedelta(minutes=65),
        bar_closed_at=_TV_NOW - timedelta(minutes=5),
        triggered_at=_TV_NOW - timedelta(seconds=5),
        received_at=_TV_NOW, signal_price=100.0,
        alert_config_version="tradevisor-1h-buy",
    )


def _tv_evaluate(*, equity: float, advisory_policy: dict | None = None):
    """Run the real Tradevisor evaluation with a deterministic market fixture.

    Only the market fetch is a fixture; every gate is the production owner.
    """
    return tradevisor_evaluation.evaluate_signal(
        _tv_signal(),
        now=_TV_NOW,
        settings=Settings(_env_file=None, webhook_secret="x" * 12),
        snapshot_provider=lambda _market: ("ok", _tv_snapshot(), None),
        account_equity=equity,
        active_trades=[],
        advisory_policy=advisory_policy,
    )



@pytest.mark.acceptance
def test_ac_013_configured_minimum_profit_is_enforced_by_tradevisor():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-013: $25 floor, Tradevisor."""
    # OFF: a $9.00 after-cost projection at the deployable allocation qualifies.
    off = _tv_evaluate(equity=1_000.0)
    assert off.rejection_code == "ADVISORY_EVIDENCE_INCOMPLETE"
    assert off.actionable is False
    assert off.economics.qualified is True
    assert off.economics.target_2_net_profit == pytest.approx(9.0, abs=0.05)

    # Configured $25: the same opportunity is refused by the owner's floor, and
    # the refusal names that exact number rather than a historical default.
    configured = _tv_evaluate(equity=1_000.0, advisory_policy={"min_net_profit": 25})
    assert configured.rejection_code == "ECONOMIC_REJECT"
    assert configured.actionable is False
    assert "below minimum $25.00" in configured.reason
    assert "75" not in configured.reason
    applied = configured.evidence["advisory_economic_policy"]
    assert applied["filters"]["min_net_profit"] == {"enabled": True, "value": 25.0}
    assert applied["policy_version"] == ADVISORY_POLICY_VERSION

    # The same floor does not over-reach: at $90.05 of profit it still passes.
    roomy = _tv_evaluate(equity=10_000.0, advisory_policy={"min_net_profit": 25})
    assert roomy.rejection_code == "ADVISORY_EVIDENCE_INCOMPLETE"
    assert roomy.economics.target_2_net_profit == pytest.approx(90.05, abs=0.05)
    assert roomy.economics.min_net_profit_applied == 25.0


@pytest.mark.acceptance
def test_ac_013_configured_target_move_is_enforced_by_tradevisor():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-013: 4% move, Tradevisor."""
    # The fixture projects a 9.05% deterministic Target 2 move.
    configured_4 = _tv_evaluate(
        equity=10_000.0, advisory_policy={"min_target_2_move_pct": 4}
    )
    assert configured_4.rejection_code == "ADVISORY_EVIDENCE_INCOMPLETE"
    assert configured_4.economics.target_2_move_pct == pytest.approx(
        _TV_MOVE_PCT, abs=0.01
    )
    assert configured_4.economics.min_target_2_move_pct_applied == 4.0
    assert configured_4.evidence["advisory_economic_policy"]["filters"][
        "min_target_2_move_pct"
    ] == {"enabled": True, "value": 4.0}

    # A configured movement filter above the projection binds and is enforced.
    configured_12 = _tv_evaluate(
        equity=10_000.0, advisory_policy={"min_target_2_move_pct": 12}
    )
    assert configured_12.rejection_code == "ECONOMIC_REJECT"
    assert "below minimum 12.00%" in configured_12.reason


@pytest.mark.acceptance
def test_ac_013_configured_reward_risk_is_enforced_by_tradevisor():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-013: 2.5:1, Tradevisor."""
    # The fixture plan is 3.0:1, so a configured 2.5:1 must admit it.
    configured_25 = _tv_evaluate(
        equity=10_000.0, advisory_policy={"min_reward_to_risk": 2.5}
    )
    assert configured_25.plan.reward_to_risk_2 == pytest.approx(_TV_REWARD_TO_RISK)
    assert configured_25.rejection_code == "ADVISORY_EVIDENCE_INCOMPLETE"
    assert configured_25.economics.min_reward_to_risk_applied == 2.5
    assert configured_25.evidence["advisory_economic_policy"]["filters"][
        "min_reward_to_risk"
    ] == {"enabled": True, "value": 2.5}

    # A configured ratio above the plan binds and is enforced.
    configured_4 = _tv_evaluate(
        equity=10_000.0, advisory_policy={"min_reward_to_risk": 4}
    )
    assert configured_4.rejection_code == "ECONOMIC_REJECT"
    assert "below minimum 4.00:1" in configured_4.reason



@pytest.mark.acceptance
def test_ac_013_configured_thresholds_reach_both_tradevisor_economic_bases(monkeypatch):
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-013: one policy, two bases."""
    configured = {
        "min_net_profit": 25,
        "min_target_2_move_pct": 4,
        "min_reward_to_risk": 2.5,
    }
    captured: list[dict] = []
    real = economic_quality_gate.evaluate_economic_quality

    def _recording(*args, **kwargs):
        captured.append(dict(kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(tradevisor_evaluation, "evaluate_economic_quality", _recording)
    decision = _tv_evaluate(equity=10_000.0, advisory_policy=configured)

    # Both the validation-envelope qualification and the F7 final-allocation
    # re-evaluation receive the identical configured values: no regime mixing.
    assert len(captured) >= 2, captured
    for kwargs in captured:
        assert kwargs["min_net_profit"] == 25.0
        assert kwargs["min_target_2_move_pct"] == 4.0
        assert kwargs["min_reward_to_risk"] == 2.5
        assert kwargs["policy_id"] == ADVISORY_POLICY_ID
    fractions = {round(kwargs["max_capital_fraction"], 6) for kwargs in captured}
    assert PRODUCTION_MAX_CAPITAL_FRACTION in fractions
    assert len(fractions) >= 2, "the final allocation must use its own basis"

    # The pure evaluator stayed pure: the policy arrived as an argument.
    assert "advisory_policy" in inspect.signature(
        tradevisor_evaluation.evaluate_signal
    ).parameters
    source = pathlib.Path("app/services/tradevisor_evaluation.py").read_text(
        encoding="utf-8"
    )
    assert "load_active_strategy_policy" not in source
    assert "open(" not in source
    assert decision.economics.min_net_profit_applied == 25.0


@pytest.mark.acceptance
def test_ac_013_configured_thresholds_are_enforced_by_the_shadow_engine():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-013: shadow engine honours the owner."""
    from app.opip.decision.gates import evaluate_economic_quality_gate

    # Each plan clears every OTHER optional filter, so exactly one configured
    # threshold can bind.
    candidates = {
        "min_net_profit": (
            plan(target_1=101.0, target_2=102.0, reward_to_risk_2=3.0),
            25,
            "below minimum $25.00",
        ),
        "min_target_2_move_pct": (
            plan(target_1=101.5, target_2=103.99, reward_to_risk_2=3.0),
            4,
            "below minimum 4.00%",
        ),
        "min_reward_to_risk": (plan(reward_to_risk_2=2.0), 2.5, "below minimum 2.50:1"),
    }
    snapshot = SimpleNamespace(trade_direction="LONG")

    for knob, (candidate_plan, value, expect_fragment) in candidates.items():
        policy = {knob: value}
        off = evaluate_economic_quality_gate(
            candidate_plan, snapshot, account_equity=500
        )
        assert off.status is GateStatus.PASS, (knob, off.reason)

        configured = evaluate_economic_quality_gate(
            candidate_plan, snapshot, account_equity=500, advisory_policy=policy
        )
        assert configured.status is GateStatus.FAIL, knob
        assert expect_fragment in configured.reason, (knob, configured.reason)
        assert configured.metadata["policy_id"] == ADVISORY_POLICY_ID
        assert configured.metadata["policy_regime"] == "ADVISORY"
        assert configured.metadata["advisory_overrides_visible"] is True
        assert configured.metadata[f"{knob}_applied"] == float(value)
        # The gate's ``threshold`` field always measures the net-profit rule it
        # reports; the binding configured floor for this knob is named in
        # metadata and in the reason, so both stay truthful for every knob.
        assert configured.threshold == float(
            configured.metadata["min_net_profit_applied"]
        )


@pytest.mark.acceptance
def test_ac_013_shadow_policy_reaches_the_engine_from_orchestration(monkeypatch):
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-013: orchestration seam."""
    import app.opip.decision.observer as observer_module
    from app.opip.decision.observer import build_scan_observer

    configured = {"min_net_profit": 25, "min_target_2_move_pct": 4}
    observer = build_scan_observer(
        snapshots=[],
        decision_at=_TV_NOW,
        account_equity=1_000.0,
        advisory_policy=configured,
    )
    assert observer.advisory_policy == configured

    captured: dict = {}

    class _FakeEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def evaluate(self, evidence):  # pragma: no cover - no candidates here
            raise AssertionError("no candidate should be evaluated")

    monkeypatch.setattr(observer_module, "OPipDecisionEngine", _FakeEngine)
    observer.run_shadow_engine()
    assert captured["advisory_policy"] == configured
    # The engine performs no I/O of its own: the policy is an argument.
    engine_source = pathlib.Path("app/opip/decision/engine.py").read_text(
        encoding="utf-8"
    )
    assert "load_active_strategy_policy" not in engine_source
    assert "open(" not in engine_source
    gates_source = pathlib.Path("app/opip/decision/gates.py").read_text(
        encoding="utf-8"
    )
    assert "load_active_strategy_policy" not in gates_source



@pytest.mark.acceptance
def test_ac_013_configured_threshold_governs_the_native_scan(monkeypatch, tmp_path, capsys):
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-013: configured end-to-end."""
    import app.core.strategy_policy as strategy_policy_module

    def _profile(**economics):
        values = {
            "min_net_profit_usd": 0,
            "min_target_2_move_pct": 0,
            "min_reward_to_risk": 0,
            "estimated_round_trip_cost_pct": 0.6,
        }
        values.update(economics)
        return SimpleNamespace(advisory_economics=values, digest="d" * 64)

    # The same candidate, through the real scan, under three policies.
    for economics, expect_pass, fragment in (
        ({}, True, None),
        ({"min_target_2_move_pct": 25}, False, "below minimum 25.00%"),
        ({"min_net_profit_usd": 10_000}, False, "below minimum $10000.00"),
    ):
        monkeypatch.setattr(
            strategy_policy_module,
            "load_active_strategy_policy",
            lambda _e=economics: _profile(**_e),
        )
        sent = _install_scan(
            monkeypatch, tmp_path, [_journey_snapshot("AKTUSD")],
            chief_top=_chief_alert("AKTUSD"),
            extra_settings={"opip_advisory_profile_enabled": True},
        )
        # Capture the live pre-AI screen's own evidence instead of its stub.
        prefiltered: list[dict] = []
        monkeypatch.setattr(
            chief_analyst,
            "capture_prefilter_rejection",
            lambda candidate, *, quality_by_risk_level, market_regime_context: (
                prefiltered.append(dict(quality_by_risk_level)) or True
            ),
        )
        scan_opportunities.main()
        output = capsys.readouterr().out
        summary = _journey_summary(tmp_path)

        if expect_pass:
            assert "ECONOMIC PASS LONG AKTUSD" in output
            assert "ACTION GATE PASS rank=1 LONG AKTUSD" in output
            assert summary["funnel"]["qualified"] == 1
            assert len(sent) == 1
        else:
            # The owner's enabled threshold stops the recommendation inside the
            # journey, before any notification, and the recorded rejection names
            # the configured value rather than a historical default.
            assert "ACTION GATE PASS" not in output
            assert summary["funnel"]["qualified"] == 0
            assert summary["funnel"]["rejected_by_policy"] == 1
            assert sent == []
            assert prefiltered, "the pre-AI screen must have recorded a verdict"
            reasons = " ".join(
                str(level.get("economic_rejection") or "")
                for levels in prefiltered
                for level in levels.values()
            )
            assert fragment in reasons, reasons

        # Reset the persisted telemetry between scenarios.
        for name in ("funnel_events.jsonl", "scan_summaries.jsonl"):
            path = tmp_path / name
            if path.exists():
                path.unlink()


@pytest.mark.acceptance
def test_ac_013_admin_ui_serializes_empty_optional_field_as_zero():
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-013: empty means OFF."""
    editor = (APP_ROOT / "app/api/strategy_admin.html").read_text(encoding="utf-8")

    # All three optional filters go through the OFF-aware serializer ...
    for field in ("profit", "move", "rr"):
        assert f'optionalNumber("{field}")' in editor, field
        assert f'id="{field}State"' in editor, field
    # ... which maps an empty field to the approved disabled value 0.
    assert 'input.value.trim()===""' in editor
    assert "return 0;" in editor
    # The cost assumption is NOT optional: it still uses the strict parser.
    assert 'number("cost")' in editor
    # Advisory eligibility is distinguished from execution authority in the UI.
    assert "advisory eligibility only" in editor
    assert "0 means OFF" in editor
    # The editor still cannot reach execution or protection settings.
    assert "allow_funded_orders" not in editor


@pytest.mark.acceptance
def test_ac_013_malformed_stored_configuration_fails_closed(monkeypatch):
    """ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1/AC-013: never guess a policy."""
    from app.core import strategy_policy as strategy_policy_module
    from app.services import tradevisor_pipeline

    # A configured profile is translated and handed to the evaluator.
    monkeypatch.setattr(
        strategy_policy_module,
        "load_active_strategy_policy",
        lambda: SimpleNamespace(
            advisory_economics={
                "min_net_profit_usd": 25,
                "min_target_2_move_pct": 4,
                "min_reward_to_risk": 2.5,
                "estimated_round_trip_cost_pct": 0.6,
            }
        ),
    )
    assert tradevisor_pipeline._advisory_policy() == {
        "min_net_profit": 25,
        "min_target_2_move_pct": 4,
        "min_reward_to_risk": 2.5,
        "estimated_round_trip_cost_pct": 0.6,
    }

    # A malformed active profile must never be replaced by the all-OFF default:
    # that would silently disable a filter the owner enabled.
    def _malformed():
        raise ValueError("INVALID_ECONOMIC_BOUND")

    monkeypatch.setattr(
        strategy_policy_module, "load_active_strategy_policy", _malformed
    )
    with pytest.raises(tradevisor_pipeline.AdvisoryPolicyUnavailable):
        tradevisor_pipeline._advisory_policy()
    # The composition root refuses the evaluation, so nothing is delivered.
    with pytest.raises(tradevisor_pipeline.AdvisoryPolicyUnavailable):
        tradevisor_pipeline.evaluate_and_notify(
            _tv_signal(),
            settings=Settings(_env_file=None, webhook_secret="x" * 12),
            snapshot_provider=lambda _market: ("ok", _tv_snapshot(), None),
            account_equity=10_000.0,
            active_trades=[],
        )

