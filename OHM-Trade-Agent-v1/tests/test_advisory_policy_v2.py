"""Advisory-only economic and AI move-scenario contract tests."""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from app.jobs.scan_opportunities import _economic_quality
from app.services.chief_analyst import _normalize_chief_potential
from app.services.economic_quality_gate import (
    MIN_NET_PROFIT, PRODUCTION_MAX_CAPITAL_FRACTION, evaluate_economic_quality,
)
from app.core.strategy_policy import load_strategy_policy, parse_strategy_policy
from app.services.chief_alert_notifier import format_trade_plan
from app.services.entry_exit_advisor import EntryExitPlan


def plan():
    return EntryExitPlan(
        symbol="TESTUSD",valid_now=True,entry_style="ready",entry_low=100,
        entry_high=100,chase_limit=101,stop_price=98,target_1=103,target_2=106,
        reward_to_risk_1=1.5,reward_to_risk_2=3,risk_level="low",
        reason="test",direction="LONG",
    )


@pytest.mark.acceptance
def test_fixed_profit_floor_is_disabled_in_shared_default():
    """Phase 2A: the shared economic default has no fixed dollar floor."""
    execution_default = evaluate_economic_quality(
        plan(), available_capital=500,
        max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
    )
    advisory_default = _economic_quality(
        plan(), SimpleNamespace(trade_direction="LONG"), 500,
    )
    advisory_configured = _economic_quality(
        plan(), SimpleNamespace(trade_direction="LONG"), 500,
        advisory_policy={
            "min_net_profit": 0,
            "min_target_2_move_pct": 4,
            "min_reward_to_risk": 2.5,
            "estimated_round_trip_cost_pct": 0.6,
        },
    )
    assert MIN_NET_PROFIT == 0.0
    assert execution_default.qualified is True
    assert advisory_default.qualified is True
    assert advisory_configured.qualified is True
    assert 0 < advisory_default.target_2_net_profit < 75


@pytest.mark.acceptance
def test_no_dollar_cutoff_in_shared_default():
    """Phase 2A: no arbitrary dollar floor in the shared economic default."""
    advice = _economic_quality(
        plan(), SimpleNamespace(trade_direction="LONG"), 500,
    )
    assert advice.qualified is True
    assert 0 < advice.target_2_net_profit < 75
    assert MIN_NET_PROFIT == 0.0


@pytest.mark.acceptance
def test_negative_after_costs_is_not_qualified_and_explicit_stricter_policy_is_preserved():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-002: fees and opt-in policy still matter."""
    shortfall = _economic_quality(
        plan(), SimpleNamespace(trade_direction="LONG"), 500,
        advisory_policy={"estimated_round_trip_cost_pct": 7.0},
    )
    stricter = _economic_quality(
        plan(), SimpleNamespace(trade_direction="LONG"), 500,
        advisory_policy={"min_net_profit": 75},
    )
    assert shortfall.qualified is False
    assert shortfall.target_2_net_profit < 0
    assert stricter.qualified is False
    assert "below minimum $75.00" in (stricter.rejection_reason or "")



@pytest.mark.acceptance
def test_optional_move_and_reward_risk_filters_default_off_but_can_be_enabled():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-003: observation is not a fixed-threshold veto."""
    smaller_move = replace(
        plan(), target_1=101, target_2=102,
        reward_to_risk_1=0.5, reward_to_risk_2=1.0,
    )
    default = _economic_quality(
        smaller_move, SimpleNamespace(trade_direction="LONG"), 500,
    )
    stricter = _economic_quality(
        smaller_move, SimpleNamespace(trade_direction="LONG"), 500,
        advisory_policy={"min_target_2_move_pct": 4, "min_reward_to_risk": 2.5},
    )
    assert default.qualified is True
    assert 0 < default.target_2_net_profit < 75
    assert stricter.qualified is False


@pytest.mark.acceptance
def test_versioned_reference_policy_disables_only_advisory_filters():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-004: optional filters stay owner-configurable."""
    policy = load_strategy_policy()
    assert policy.advisory_economics["min_net_profit_usd"] == 0
    assert policy.advisory_economics["min_target_2_move_pct"] == 0
    assert policy.advisory_economics["min_reward_to_risk"] == 0
    assert policy.advisory_economics["estimated_round_trip_cost_pct"] > 0
    assert policy.scoring["v2_threshold"] == 75
    assert policy.scoring["production"] == "v1"
    # Preserve the ability to explicitly select a bounded advisory filter.
    import json
    from pathlib import Path
    source = json.loads((Path(__file__).parents[1] / "app/core/strategy_policy.json").read_text())
    source["advisory_economics"]["min_reward_to_risk"] = 2.5
    source["advisory_economics"]["min_target_2_move_pct"] = 4
    strict = parse_strategy_policy(json.dumps(source))
    assert strict.advisory_economics["min_reward_to_risk"] == 2.5
    assert strict.advisory_economics["min_target_2_move_pct"] == 4


@pytest.mark.acceptance
def test_private_editor_supports_disabled_advisory_reward_risk_filter():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-005: zero is editable, protected fields stay protected."""
    from pathlib import Path
    editor = (Path(__file__).parents[1] / "app/api/strategy_admin.html").read_text()
    assert 'id="rr" type="number" min="0"' in editor
    assert "Protected: model authority, position limits" in editor
    assert "strategy_admin" not in editor.lower() or "funded execution" in editor.lower()


@pytest.mark.acceptance
def test_invalid_ai_return_potential_cannot_become_trade_claim():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-007: AI potential is bounded evidence."""
    review = {"top_candidates": [
        {"potential_move_pct": 150, "potential_timeframe_hours": 8, "potential_rationale": "unsupported"},
        {"potential_move_pct": float("nan"), "potential_timeframe_hours": 24},
        {"potential_move_pct": 6.2, "potential_timeframe_hours": 48,
         "potential_rationale": "conditional momentum"},
    ]}
    _normalize_chief_potential(review)
    entries = review["top_candidates"]
    assert entries[0]["potential_move_pct"] is None
    assert entries[1]["potential_move_pct"] is None
    assert entries[2]["potential_move_pct"] == pytest.approx(6.2)
    assert entries[2]["potential_timeframe_hours"] == 48


@pytest.mark.acceptance
def test_conditional_ai_move_is_visible_but_not_claimed_as_guaranteed_return():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-007: advisory communicates uncertainty."""
    candidate = {
        "direction": "LONG", "symbol": "TESTUSD", "decision": "alert",
        "confidence": 90, "potential_move_pct": 5.5,
        "potential_timeframe_hours": 48,
        "potential_rationale": "4h trend, support retest and volatility",
    }
    text = format_trade_plan(candidate, plan(), "Regime neutral")
    assert "Conditional AI upside: 5.50% over ~48h" in text
    assert "not a trade target or guarantee" in text
    assert "AI scenario basis: 4h trend" in text


# --- AC-006: economic projection correctness ---------------------------------

# With this plan the advisory net P&L is exactly equity * 0.0108 at the
# production 20% capital envelope: 6% Target 2 move minus 0.6% round-trip costs on
# a notional of 20% of equity. The regression matrix below uses that identity to
# pin a specific projected net profit.
_NET_PER_EQUITY = 0.20 * (6.0 - 0.6) / 100.0


def _equity_for_net(target: float) -> float:
    return target / _NET_PER_EQUITY


@pytest.mark.acceptance
def test_ac_006_projection_is_computable_and_never_fabricated():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-006: no fabricated profitable trade."""
    snapshot = SimpleNamespace(trade_direction="LONG")
    # A usable projection scales with capital and never invents a profit.
    nets = []
    for equity in (100.0, 500.0, 1000.0, 10_000.0):
        advice = _economic_quality(plan(), snapshot, equity)
        assert advice.qualified is True, equity
        assert advice.target_2_net_profit > 0, equity
        assert advice.target_2_net_profit == pytest.approx(
            equity * _NET_PER_EQUITY, rel=1e-9
        )
        nets.append(advice.target_2_net_profit)
    assert nets == sorted(nets)

    # No equity is not "zero profit": it is an unusable projection.
    for equity in (0.0, -100.0):
        unusable = _economic_quality(plan(), snapshot, equity)
        assert unusable.qualified is False
        assert unusable.target_2_net_profit == 0

    # Non-finite geometry cannot become a profitable candidate.
    for field, value in (("target_2", float("nan")), ("stop_price", float("inf"))):
        broken = _economic_quality(replace(plan(), **{field: value}), snapshot, 500)
        assert broken.qualified is False
        assert broken.target_2_net_profit == 0

    # Invalid cost assumptions are rejected, never silently treated as zero.
    for bad in (-0.5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            _economic_quality(
                plan(), snapshot, 500,
                advisory_policy={"estimated_round_trip_cost_pct": bad},
            )
    with pytest.raises(ValueError):
        _economic_quality(
            plan(), snapshot, 500, advisory_policy={"min_net_profit_usd": 75}
        )


@pytest.mark.acceptance
def test_ac_006_short_geometry_is_mirrored_and_not_confused_with_long():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-006: direction is part of the projection."""
    from app.services.chief_analyst import (
        SHORT_MARGIN_COST_RESERVE_PCT,
        SHORT_VALIDATION_LEVERAGE,
    )

    short_plan = EntryExitPlan(
        symbol="TESTUSD", valid_now=True, entry_style="ready", entry_low=100,
        entry_high=100, chase_limit=99, stop_price=102, target_1=97, target_2=94,
        reward_to_risk_1=1.5, reward_to_risk_2=3, risk_level="low",
        reason="test", direction="SHORT",
    )
    long_advice = _economic_quality(plan(), SimpleNamespace(trade_direction="LONG"), 500)
    short_advice = _economic_quality(
        short_plan, SimpleNamespace(trade_direction="SHORT"), 500
    )
    assert short_advice.qualified is True
    # The mirrored geometry produces the mirrored Target 2 movement...
    assert short_advice.direction == "SHORT"
    assert short_advice.target_2_move_pct == pytest.approx(
        long_advice.target_2_move_pct, rel=1e-9
    )
    # ...and the SHORT-specific cost model is actually applied rather than ignored,
    # which is why the projected net is deliberately not identical to the LONG one.
    assert short_advice.leverage == pytest.approx(SHORT_VALIDATION_LEVERAGE)
    assert short_advice.estimated_margin_cost_pct == pytest.approx(
        SHORT_MARGIN_COST_RESERVE_PCT
    )
    assert short_advice.target_2_net_profit > 0
    # A LONG geometry evaluated as a SHORT cannot qualify on mirror symmetry alone.
    mismatched = _economic_quality(
        plan(), SimpleNamespace(trade_direction="SHORT"), 500
    )
    assert mismatched.qualified is False


@pytest.mark.acceptance
def test_ac_007_no_discontinuity_at_seventy_five_dollars():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-007: $75 is not an advisory cliff."""
    snapshot = SimpleNamespace(trade_direction="LONG")
    observed = {}
    for target in (10.0, 20.0, 50.0, 74.99, 75.0, 100.0):
        advice = _economic_quality(plan(), snapshot, _equity_for_net(target))
        assert advice.qualified is True, target
        assert advice.target_2_net_profit == pytest.approx(target, rel=1e-9)
        observed[target] = advice.target_2_net_profit
    # Monotone, with no step at the old $75 bar: each dollar of projected profit
    # costs the same capital either side of 75.
    ordered = [observed[key] for key in (10.0, 20.0, 50.0, 74.99, 75.0, 100.0)]
    assert ordered == sorted(ordered)
    per_dollar_below = (observed[75.0] - observed[74.99]) / 0.01
    per_dollar_above = (observed[100.0] - observed[75.0]) / 25.0
    assert per_dollar_below == pytest.approx(per_dollar_above, rel=1e-6)


@pytest.mark.acceptance
def test_execution_gate_has_no_fixed_dollar_bar():
    """Phase 2A: the shared economic gate default has no fixed dollar bar."""
    from app.opip.decision.gates import evaluate_economic_quality_gate
    from app.opip.decision.models import GateStatus

    snapshot = SimpleNamespace(trade_direction="LONG")
    advice = _economic_quality(plan(), snapshot, 500)
    assert advice.qualified is True

    # The same candidate is now admitted by the shared economic gate because
    # positive projected net (not a $75 floor) is the only default requirement.
    execution = evaluate_economic_quality_gate(plan(), snapshot, account_equity=500)
    assert execution.status is GateStatus.PASS

    # The shared gate's own default is now disabled (zero), not $75.
    shared = evaluate_economic_quality(
        plan(), available_capital=500,
        max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
    )
    assert shared.qualified is True
    assert "$75.00" not in (shared.rejection_reason or "")


@pytest.mark.acceptance
def test_ac_008_chief_advisory_prefilter_cannot_reintroduce_a_fixed_floor(monkeypatch):
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-008: the review stage cannot re-veto on profit."""
    from app.services import chief_analyst

    captured: list[dict] = []

    class _Plan:
        direction = "LONG"
        reward_to_risk_2 = 3.0

    class _Target:
        qualified = True
        attainability_score = 90
        warnings: list = []
        rejection_reasons: list = []
        target_2_atr_multiple = 1.0
        clearance_to_24h_resistance_pct = 1.0
        clearance_to_72h_resistance_pct = 1.0
        momentum_context = "steady"

    class _Economic:
        qualified = True
        rejection_reason = None
        recommended_capital = 100.0
        leverage = 1.0
        target_2_move_pct = 6.0
        account_risk_at_stop_pct = 1.0
        target_2_net_profit = 10.0
        stop_pct = 2.0

    def fake_economic(plan, equity, **kwargs):
        captured.append(kwargs)
        return _Economic()

    monkeypatch.setattr(
        chief_analyst, "build_entry_exit_plan",
        lambda snapshot, level, direction=None: _Plan(),
    )
    monkeypatch.setattr(
        chief_analyst, "evaluate_target_attainability", lambda plan, snapshot: _Target()
    )
    monkeypatch.setattr(
        chief_analyst, "evaluate_short_target_attainability",
        lambda plan, snapshot: _Target(),
    )
    monkeypatch.setattr(chief_analyst, "evaluate_economic_quality", fake_economic)

    snapshot = SimpleNamespace(trade_direction="LONG")
    quality_by_risk_level, viable = chief_analyst._quality_by_risk_level(snapshot, 500.0)
    assert viable is True
    assert set(quality_by_risk_level) == {"low", "medium"}
    assert captured, "the advisory prefilter must evaluate economics"
    for kwargs in captured:
        # A candidate the scanner admitted on $10 of projected profit must not be
        # rejected again by the review stage for being under $75.
        assert kwargs["min_net_profit"] == 0.0
        assert kwargs["min_target_2_move_pct"] == 0.0
        assert kwargs["min_reward_to_risk"] == 0.0
        assert kwargs["max_capital_fraction"] == PRODUCTION_MAX_CAPITAL_FRACTION

    # An explicitly configured, stricter owner filter is still honoured.
    captured.clear()
    chief_analyst._quality_by_risk_level(snapshot, 500.0, {"min_net_profit": 75})
    assert captured and captured[0]["min_net_profit"] == 75.0


@pytest.mark.acceptance
def test_execution_side_paths_share_disabled_defaults():
    """Phase 2A: execution-side defaults are disabled (zero), not $75/4%/2.5."""
    import inspect

    from app.services import trade_action_gate, tradevisor_evaluation
    from app.services.economic_quality_gate import (
        MIN_NET_PROFIT,
        MIN_REWARD_TO_RISK,
        MIN_TARGET_2_MOVE_PCT,
    )

    # The named economic constants are disabled by default.
    assert MIN_NET_PROFIT == 0.0
    assert MIN_TARGET_2_MOVE_PCT == 0.0
    assert MIN_REWARD_TO_RISK == 0.0

    # The shared gate still defaults to these (now disabled) constants.
    signature = inspect.signature(evaluate_economic_quality).parameters
    assert signature["min_net_profit"].default == MIN_NET_PROFIT
    assert signature["min_target_2_move_pct"].default == MIN_TARGET_2_MOVE_PCT
    assert signature["min_reward_to_risk"].default == MIN_REWARD_TO_RISK

    # The action gate enforces break-even (positive net), not a fixed $75 bar.
    assert "MIN_NET_PROFIT" in inspect.getsource(trade_action_gate)

    # The Tradevisor evaluation path consumes the shared defaults; the floor is
    # removed system-wide through the shared constant, not re-implemented locally.
    tradevisor = inspect.getsource(tradevisor_evaluation)
    assert "min_net_profit" not in tradevisor

