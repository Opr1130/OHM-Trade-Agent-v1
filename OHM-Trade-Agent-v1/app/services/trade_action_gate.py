from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any

from app.services.active_trade_registry import get_active_trades
from app.services.capital_efficiency_ranking import MIN_EXECUTABLE_NOTIONAL_USD
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.economic_quality_gate import (
    advisory_economic_policy,
    describe_advisory_economic_policy,
    net_profit_is_positive,
    quantize_usd_cents,
)
from app.services.portfolio_risk import evaluate_portfolio_risk
from app.services.trade_decision_intelligence import (
    TradeDecisionIntelligence,
    evaluate_trade_decision,
)

#: Structured classification of an action-gate stop. The shadow observer used
#: to record every disallowed gate as ``NO_CAPITAL``, which mislabelled an
#: economic-policy stop as a missing-capital stop. Each class names the rule that
#: actually fired; ``reason_code`` maps it onto the O'Pip reason-code vocabulary.
REJECTION_ECONOMIC_NOT_QUALIFIED = "ECONOMIC_NOT_QUALIFIED"
REJECTION_CAPITAL_UNAVAILABLE = "CAPITAL_UNAVAILABLE"
REJECTION_GATE_UNAVAILABLE = "GATE_UNAVAILABLE"
REJECTION_PROFITABILITY = "PROFITABILITY"
REJECTION_ECONOMIC_FLOOR = "ECONOMIC_FLOOR"
REJECTION_ECONOMIC_UNAVAILABLE = "ECONOMIC_UNAVAILABLE"
REJECTION_LIQUIDITY_CAPACITY = "LIQUIDITY_CAPACITY"
REJECTION_MIN_EXECUTABLE_NOTIONAL = "MIN_EXECUTABLE_NOTIONAL"
REJECTION_PORTFOLIO = "PORTFOLIO_EXPOSURE"

_REJECTION_REASON_CODES = {
    REJECTION_ECONOMIC_NOT_QUALIFIED: "ECONOMIC_GATE_FAILED",
    REJECTION_PROFITABILITY: "ECONOMIC_GATE_FAILED",
    REJECTION_ECONOMIC_FLOOR: "ECONOMIC_GATE_FAILED",
    REJECTION_ECONOMIC_UNAVAILABLE: "GATE_EVALUATION_ERROR",
    REJECTION_LIQUIDITY_CAPACITY: "NO_CAPITAL",
    REJECTION_MIN_EXECUTABLE_NOTIONAL: "NO_CAPITAL",
    REJECTION_PORTFOLIO: "NO_CAPITAL",
    REJECTION_CAPITAL_UNAVAILABLE: "GATE_EVALUATION_ERROR",
    REJECTION_GATE_UNAVAILABLE: "GATE_EVALUATION_ERROR",
}


@dataclass(frozen=True)
class ActionGateDecision:
    allowed: bool
    reason: str
    intelligence: TradeDecisionIntelligence | None = None
    rejection_class: str | None = None
    reason_code: str | None = None


def _reject(
    candidate: dict[str, Any],
    reason: str,
    rejection_class: str,
) -> ActionGateDecision:
    """Record one classified action-gate stop on the candidate.

    The classification is written next to the free-text reason so telemetry and
    the shadow observer report the rule that actually fired. A positive-profit
    candidate stopped by a configured economic floor is never mislabelled as a
    missing-capital stop.
    """
    reason_code = _REJECTION_REASON_CODES[rejection_class]
    candidate["action_gate_evaluated"] = True
    candidate["action_gate_allowed"] = False
    candidate["action_gate_rejection_class"] = rejection_class
    candidate["action_gate_reason_code"] = reason_code
    candidate["portfolio_risk_allowed"] = False
    candidate["portfolio_risk_reason"] = reason
    return ActionGateDecision(False, reason, None, rejection_class, reason_code)


def apply_action_gate(
    *,
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    account_capital: float,
    active_trades: list[Any] | None = None,
    advisory_policy: dict | None = None,
) -> ActionGateDecision:
    """Evaluate capital/portfolio eligibility before notification.

    This is intentionally upstream of Telegram. The notifier consumes the
    resulting decision; it does not decide portfolio eligibility itself.

    The *economic* question asked here is the advisory one: is this still worth
    telling the operator about at the capital that can actually be deployed? It
    is answered with the resolved advisory policy (optional fixed floors OFF by
    default) plus the independent requirement that modelled net profit after
    costs be strictly positive. Liquidity capacity, executable notional,
    portfolio exposure, risk ceilings and every downstream execution gate stay
    independently enforced - none of them is relaxed by a disabled filter.
    """
    if candidate.get("economic_qualified") is not True:
        return _reject(
            candidate,
            "economic qualification is required for an actionable trade",
            REJECTION_ECONOMIC_NOT_QUALIFIED,
        )

    try:
        account_capital = float(account_capital)
    except (TypeError, ValueError):
        account_capital = math.nan
    if not math.isfinite(account_capital) or account_capital <= 0:
        return _reject(
            candidate,
            "account capital is unavailable or invalid",
            REJECTION_CAPITAL_UNAVAILABLE,
        )

    # Fail closed on an invalid advisory policy: a malformed filter must reject
    # the configuration rather than silently falling back to a fixed floor.
    policy = advisory_economic_policy(advisory_policy)
    min_net_profit = float(policy["min_net_profit"])
    policy_evidence = describe_advisory_economic_policy(advisory_policy)
    candidate["advisory_economic_policy"] = policy_evidence

    trades = list(active_trades) if active_trades is not None else get_active_trades()
    try:
        intelligence = evaluate_trade_decision(
            candidate=candidate,
            plan=plan,
            account_capital=account_capital,
            active_trades=trades,
        )
    except Exception as exc:
        return _reject(
            candidate,
            f"action gate unavailable: {type(exc).__name__}: {exc}",
            REJECTION_GATE_UNAVAILABLE,
        )

    capacity_reason: str | None = None
    capacity_class: str | None = None
    direction = str(
        candidate.get("direction") or plan.direction or "LONG"
    ).upper()
    try:
        leverage = float(
            candidate.get("margin_leverage")
            or (2.0 if direction == "SHORT" else 1.0)
        )
    except (TypeError, ValueError):
        leverage = math.nan
    if direction not in {"LONG", "SHORT"} or not math.isfinite(leverage) or leverage <= 0:
        capacity_reason = "invalid direction or leverage at action gate"
        capacity_class = REJECTION_GATE_UNAVAILABLE

    # Post-cap net profit at the deployable capital, or ``None`` when this
    # candidate was not capacity-capped (so no re-verification is owed).
    capacity_adjusted_net: float | None = None
    capacity_projection_missing = False

    ceiling = candidate.get("liquidity_capacity_ceiling_usd")
    if (
        capacity_reason is None
        and isinstance(ceiling, (int, float))
        and math.isfinite(float(ceiling))
        and float(ceiling) > 0
    ):
        max_capital_by_capacity = float(ceiling) / leverage
        original_capital = float(
            intelligence.allocation.recommended_capital
        )
        if original_capital > max_capital_by_capacity:
            capped_capital = max(0.0, max_capital_by_capacity)
            ratio = (
                capped_capital / original_capital
                if original_capital > 0
                else 0.0
            )
            capped_allocation = replace(
                intelligence.allocation,
                recommended_capital=round(capped_capital, 2),
                risk_dollars=round(
                    intelligence.allocation.risk_dollars * ratio,
                    2,
                ),
                position_pct=round(
                    capped_capital / account_capital * 100.0,
                    2,
                ),
                reason=(
                    intelligence.allocation.reason
                    + "; capped by Wave 9 liquidity capacity"
                ),
            )
            capped_portfolio = evaluate_portfolio_risk(
                active_trades=trades,
                proposed_symbol=plan.symbol,
                proposed_direction=direction,
                proposed_capital=capped_allocation.recommended_capital,
                proposed_leverage=leverage,
                account_capital=account_capital,
            )
            intelligence = replace(
                intelligence,
                allocation=capped_allocation,
                portfolio_risk=capped_portfolio,
            )
            candidate["liquidity_capacity_capped"] = True
            candidate["liquidity_capacity_max_capital"] = round(
                max_capital_by_capacity,
                2,
            )

            validation_capital = candidate.get(
                "economic_validation_capital"
            )
            validation_net = candidate.get(
                "economic_validation_net_t2"
            )
            if (
                isinstance(validation_capital, (int, float))
                and not isinstance(validation_capital, bool)
                and math.isfinite(float(validation_capital))
                and float(validation_capital) > 0
                and isinstance(validation_net, (int, float))
                and not isinstance(validation_net, bool)
            ):
                economic_scale = min(
                    1.0,
                    capped_capital / float(validation_capital),
                )
                capacity_adjusted_net = quantize_usd_cents(
                    float(validation_net) * economic_scale
                )
                candidate["capacity_adjusted_validation_net_t2"] = (
                    capacity_adjusted_net
                )
                candidate["capacity_adjusted_validation_capital"] = round(
                    capped_capital,
                    2,
                )
                candidate["capacity_adjusted_economic_status"] = "EVALUATED"
            else:
                # The projection that admitted this candidate is unavailable at
                # the capped size. Post-cap profitability therefore cannot be
                # proven, and an unprovable profit is never assumed.
                candidate["capacity_adjusted_economic_status"] = "UNAVAILABLE"
                candidate["capacity_adjusted_validation_net_t2"] = None
                capacity_projection_missing = True
        else:
            candidate["liquidity_capacity_capped"] = False
    final_notional = (
        float(intelligence.allocation.recommended_capital) * leverage
        if math.isfinite(leverage) and leverage > 0
        else 0.0
    )
    candidate["recommended_position_notional"] = round(final_notional, 2)
    # Executable-notional and post-cap profit checks are independent rules; each
    # is evaluated in its own right so one rejection never hides another.
    if (
        capacity_reason is None
        and (
            not math.isfinite(final_notional)
            or final_notional < MIN_EXECUTABLE_NOTIONAL_USD
        )
    ):
        capacity_reason = (
            f"recommended position notional USD {final_notional:.2f} is below "
            f"minimum executable notional USD {MIN_EXECUTABLE_NOTIONAL_USD:.2f}"
        )
        capacity_class = REJECTION_MIN_EXECUTABLE_NOTIONAL
    if capacity_reason is None and capacity_projection_missing:
        capacity_reason = (
            "capacity-capped economic projection is unavailable; "
            "post-cap net profit cannot be verified"
        )
        capacity_class = REJECTION_ECONOMIC_UNAVAILABLE
    if capacity_reason is None and capacity_adjusted_net is not None:
        if not math.isfinite(capacity_adjusted_net):
            capacity_reason = (
                "capacity-capped validation net profit is not finite; "
                "post-cap profitability cannot be verified"
            )
            capacity_class = REJECTION_ECONOMIC_UNAVAILABLE
        elif not net_profit_is_positive(capacity_adjusted_net):
            capacity_reason = (
                f"capacity-capped validation net profit "
                f"USD {capacity_adjusted_net:.2f} at deployable capital "
                f"USD {float(intelligence.allocation.recommended_capital):.2f} "
                f"is not positive after estimated costs"
            )
            capacity_class = REJECTION_PROFITABILITY
        elif capacity_adjusted_net < min_net_profit:
            capacity_reason = (
                f"capacity-capped validation net profit "
                f"USD {capacity_adjusted_net:.2f} is below the configured "
                f"advisory minimum USD {min_net_profit:.2f} "
                f"({policy_evidence['policy_id']})"
            )
            capacity_class = REJECTION_ECONOMIC_FLOOR

    candidate["recommended_capital"] = intelligence.allocation.recommended_capital
    candidate["recommended_risk_dollars"] = intelligence.allocation.risk_dollars
    candidate["projected_net_edge_pct"] = intelligence.projected_net_edge_pct
    if capacity_adjusted_net is not None and account_capital > 0:
        # After a capacity cut, the honest edge is the deployable one. The
        # allocation was already sized, so only the reported basis changes; an
        # inflated validation-envelope figure must not be shown to the operator.
        candidate["projected_net_edge_pct"] = round(
            max(0.0, capacity_adjusted_net / account_capital * 100.0), 4
        )
        candidate["projected_net_edge_basis"] = "CAPACITY_ADJUSTED_VALIDATION"
    else:
        candidate["projected_net_edge_basis"] = "VALIDATION_ENVELOPE"
    candidate["calibration_status"] = intelligence.calibration_status
    candidate["calibration_multiplier"] = intelligence.calibration_multiplier
    candidate["portfolio_risk_allowed"] = intelligence.portfolio_risk.allowed
    candidate["portfolio_risk_reason"] = intelligence.portfolio_risk.reason
    candidate["action_gate_evaluated"] = True
    allowed = intelligence.allowed and capacity_reason is None
    candidate["action_gate_allowed"] = allowed

    reason = (
        capacity_reason
        if capacity_reason is not None
        else (
            "capital, liquidity-capacity, and portfolio guardrails passed"
            if intelligence.allowed
            else intelligence.portfolio_risk.reason
            or intelligence.allocation.reason
        )
    )
    if allowed:
        capacity_class = None
        candidate.pop("action_gate_rejection_class", None)
        candidate.pop("action_gate_reason_code", None)
    else:
        if capacity_class is None:
            # Every remaining stop is the portfolio/exposure authority.
            capacity_class = REJECTION_PORTFOLIO
        candidate["action_gate_rejection_class"] = capacity_class
        candidate["action_gate_reason_code"] = _REJECTION_REASON_CODES[
            capacity_class
        ]

    return ActionGateDecision(
        allowed,
        reason,
        intelligence,
        capacity_class,
        _REJECTION_REASON_CODES[capacity_class] if capacity_class else None,
    )