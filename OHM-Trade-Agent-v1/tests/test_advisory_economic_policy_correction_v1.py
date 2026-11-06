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
import math
import pathlib
from types import SimpleNamespace

import pytest

from app.core.strategy_policy import parse_strategy_policy
from app.jobs import scan_opportunities
from app.opip.decision import store as opip_store
from app.services import chief_analyst
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.market_scanner import ScanResult
from app.scanner.models import MarketSnapshot
from app.scanner.reference_market_validation import ReferenceMarketValidation
from app.services import trade_action_gate
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


def _install_scan(monkeypatch, tmp_path, snapshots, *, chief_top=None):
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
    # I/O boundary 2: the live position registry.
    monkeypatch.setattr(scan_opportunities, "get_active_trades", lambda: [])
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
    assert "ACTION GATE PASS rank=1 LONG AKTUSD" in output
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

    # 5. Rollback with no previous version is refused rather than guessed.
    with pytest.raises(ValueError, match="NO_SAFE_ROLLBACK"):
        rollback_active_policy(
            expected_digest=rolled_back["digest"], directory=tmp_path
        )

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

