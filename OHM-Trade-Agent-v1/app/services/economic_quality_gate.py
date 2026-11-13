import math
from dataclasses import dataclass

from app.services.entry_exit_advisor import EntryExitPlan


DEFAULT_MAX_ACCOUNT_RISK_AT_STOP_PCT = 5.0
DEFAULT_MAX_CAPITAL_FRACTION = 1.0
PRODUCTION_MAX_CAPITAL_FRACTION = 0.20

# Execution-compatible default: advisory policy must not change this globally.
# Advisory-specific callers may supply min_net_profit=0.0 explicitly.
# Trade execution remains governed by separately approved protections.
MIN_TARGET_2_MOVE_PCT = 4.0
PREFERRED_TARGET_2_MOVE_PCT = 7.0
MIN_NET_PROFIT = 75.0
MIN_REWARD_TO_RISK = 2.5
ESTIMATED_ROUND_TRIP_COST_PCT = 0.60

# --- advisory screen policy ---------------------------------------------------
# The constants above are the EXECUTION-compatible defaults: they still govern
# whether a candidate may be traded, and they are unchanged.
#
# Advisory opportunity discovery is a different question. It asks "is there
# positive economic potential worth telling the operator about?", not "may we
# enter?". The owner rejected fixed dollar-profit, percentage-move and
# reward/risk rejection floors for that screen, so its defaults are zero and its
# one definition lives here so a fixed floor cannot reappear through a second
# helper, the Chief review or a selection path.
ADVISORY_POLICY_ID = "OPIP-ADVISORY-ECONOMIC-POLICY-V2"
ADVISORY_POLICY_VERSION = 2
#: Identity recorded for callers that deliberately keep the execution-compatible
#: defaults. Evidence must never label an execution comparison as advisory.
EXECUTION_GATE_POLICY_ID = "OPIP-EXECUTION-ECONOMIC-GATE-V1"

ADVISORY_ECONOMIC_KEYS = (
    "min_net_profit",
    "min_target_2_move_pct",
    "min_reward_to_risk",
)
ADVISORY_OPTIONAL_KEYS = ("estimated_round_trip_cost_pct",)
ADVISORY_DEFAULT_MIN_NET_PROFIT = 0.0
ADVISORY_DEFAULT_MIN_TARGET_2_MOVE_PCT = 0.0
ADVISORY_DEFAULT_MIN_REWARD_TO_RISK = 0.0

#: The versioned strategy profile owns the numeric bound of every knob. This map
#: is the only translation between the profile's field names and the advisory
#: policy keys, so no caller restates either side and a renamed knob cannot
#: diverge between the editor, the validator and the gate.
ADVISORY_PROFILE_FIELD_MAP = {
    "min_net_profit_usd": "min_net_profit",
    "min_target_2_move_pct": "min_target_2_move_pct",
    "min_reward_to_risk": "min_reward_to_risk",
    "estimated_round_trip_cost_pct": "estimated_round_trip_cost_pct",
}


def advisory_economic_policy(
    overrides: dict | None = None,
) -> dict[str, float | str]:
    """Return the bounded advisory-screen policy, including its identity.

    Every optional advisory filter defaults to OFF (``0.0``), which is a
    disabled filter - not "a filter set to zero dollars". Disabling a filter
    never removes the independent requirement that modelled net profit after
    costs be strictly positive (BR-002).

    ``overrides`` are the owner's explicitly configured advisory filters (the
    versioned ``advisory_economics`` profile). Only finite, non-negative knobs
    are accepted; an unknown or invalid key raises rather than being ignored, so
    a typo cannot silently disable or silently reset an owner-configured filter,
    and an invalid value can never fall back to a historical fixed floor.
    """
    policy: dict[str, float | str] = {
        "policy_id": ADVISORY_POLICY_ID,
        "min_net_profit": ADVISORY_DEFAULT_MIN_NET_PROFIT,
        "min_target_2_move_pct": ADVISORY_DEFAULT_MIN_TARGET_2_MOVE_PCT,
        "min_reward_to_risk": ADVISORY_DEFAULT_MIN_REWARD_TO_RISK,
    }
    if not overrides:
        return policy
    allowed = set(ADVISORY_ECONOMIC_KEYS) | set(ADVISORY_OPTIONAL_KEYS)
    unknown = sorted(set(overrides) - allowed)
    if unknown:
        raise ValueError(f"Unsupported advisory economic key(s): {unknown}")
    for key, value in overrides.items():
        if isinstance(value, bool) or value is None:
            # ``float(True)`` and ``float(None)`` must not become a policy.
            raise ValueError(f"Invalid advisory economic value for {key}")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid advisory economic value for {key}") from exc
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"Invalid advisory economic bound for {key}")
        policy[key] = numeric
    return policy


def advisory_policy_from_profile(
    advisory_economics: dict | None,
) -> dict[str, float | str] | None:
    """Translate one validated profile section into the advisory policy keys.

    Returns ``None`` when no profile is selected, so the caller keeps the
    default (all filters OFF) rather than an empty mapping that could be mistaken
    for an explicitly configured zero.
    """
    if advisory_economics is None:
        return None
    unknown = sorted(set(advisory_economics) - set(ADVISORY_PROFILE_FIELD_MAP))
    if unknown:
        raise ValueError(f"Unsupported advisory profile field(s): {unknown}")
    return {
        target: advisory_economics[source]
        for source, target in ADVISORY_PROFILE_FIELD_MAP.items()
    }


def describe_advisory_economic_policy(
    policy: dict | None = None,
) -> dict:
    """Return the effective advisory filter policy as immutable evidence.

    ``enabled`` is derived from the resolved value, so a recorded rejection can
    never claim a filter that was not applied. ``requires_positive_net_profit``
    is always true: it is the independent BR-002 requirement, not an optional
    filter.
    """
    effective = advisory_economic_policy(policy)
    filters: dict[str, dict] = {}
    for key in ADVISORY_ECONOMIC_KEYS:
        value = float(effective[key])
        filters[key] = {"enabled": value > 0, "value": value}
    cost = effective.get("estimated_round_trip_cost_pct")
    return {
        "policy_id": ADVISORY_POLICY_ID,
        "policy_version": ADVISORY_POLICY_VERSION,
        "regime": "ADVISORY",
        "filters": filters,
        "estimated_round_trip_cost_pct": (
            float(cost) if cost is not None else None
        ),
        "requires_positive_net_profit": True,
    }


def quantize_usd_cents(value: float) -> float:
    """Round a USD amount to whole cents with deterministic half-up semantics.

    ``round`` uses banker's rounding on binary floats, so a boundary such as
    $24.995 is not reproducible for a human reader. A cent-exact half-up value
    keeps the recorded amount and the comparison identical, so a stored decision
    can always be re-derived from the stored number.
    """
    scaled = float(value) * 100.0
    if scaled >= 0:
        return math.floor(scaled + 0.5) / 100.0
    return math.ceil(scaled - 0.5) / 100.0


def net_profit_is_positive(value: float) -> bool:
    """Return whether modelled net profit is strictly positive in whole cents.

    Financial rounding must never turn a true loss or a zero-return opportunity
    into qualifying profit, and the recorded 2-decimal value must agree with the
    decision that produced it. A cent-quantised value of exactly zero is not
    profitable.
    """
    return quantize_usd_cents(value) > 0.0


@dataclass
class EconomicGateResult:
    qualified: bool
    entry_reference: float
    stop_pct: float
    target_1_move_pct: float
    target_2_move_pct: float
    recommended_capital: float
    target_1_gross_profit: float
    target_2_gross_profit: float
    estimated_costs: float
    target_1_net_profit: float
    target_2_net_profit: float
    rejection_reason: str | None = None
    direction: str = "LONG"
    leverage: float = 1.0
    position_notional: float = 0.0
    account_risk_at_stop_pct: float = 0.0
    estimated_margin_cost_pct: float = 0.0
    # Exact rule attribution: which policy produced this verdict and which
    # numeric floors were actually applied. A stored rejection is only
    # interpretable if these agree with the values the comparison used.
    policy_id: str = EXECUTION_GATE_POLICY_ID
    min_net_profit_applied: float = MIN_NET_PROFIT
    min_target_2_move_pct_applied: float = MIN_TARGET_2_MOVE_PCT
    min_reward_to_risk_applied: float = MIN_REWARD_TO_RISK


def _invalid_result(
    direction: str,
    leverage: float,
    reason: str,
    *,
    policy: dict | None = None,
) -> EconomicGateResult:
    safe_leverage = leverage if math.isfinite(float(leverage)) and leverage > 0 else 1.0
    attribution = _policy_attribution(policy)
    return EconomicGateResult(
        False, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        reason, direction=direction, leverage=safe_leverage,
        **attribution,
    )


def _applied_floor(source: dict, name: str, fallback: float) -> float:
    """Read an applied floor from either attribution shape.

    ``_policy_attribution`` output uses ``<name>_applied`` keys; a raw policy
    mapping uses the bare name. Reading both keeps an invalid-result rejection
    attributed to the floor that was actually in force.
    """
    for key in (f"{name}_applied", name):
        if key in source:
            return float(source[key])
    return float(fallback)


def _policy_attribution(
    policy: dict | None,
    *,
    min_net_profit: float | None = None,
    min_target_2_move_pct: float | None = None,
    min_reward_to_risk: float | None = None,
) -> dict[str, object]:
    """Return the exact policy identity and applied floors for evidence."""
    source = policy or {}
    resolved_id = str(source.get("policy_id") or EXECUTION_GATE_POLICY_ID)
    return {
        "policy_id": resolved_id,
        "min_net_profit_applied": (
            float(min_net_profit)
            if min_net_profit is not None
            else _applied_floor(source, "min_net_profit", MIN_NET_PROFIT)
        ),
        "min_target_2_move_pct_applied": (
            float(min_target_2_move_pct)
            if min_target_2_move_pct is not None
            else _applied_floor(
                source, "min_target_2_move_pct", MIN_TARGET_2_MOVE_PCT
            )
        ),
        "min_reward_to_risk_applied": (
            float(min_reward_to_risk)
            if min_reward_to_risk is not None
            else _applied_floor(source, "min_reward_to_risk", MIN_REWARD_TO_RISK)
        ),
    }


def _resolved_capital_fraction(value: float | None) -> float:
    """Resolve the reusable gate's capital envelope.

    The shared economic gate is deliberately environment-agnostic so tests,
    analysis and non-production callers retain the historical full-capital
    default. Production decision pipelines must pass their explicit allocation
    envelope; OHM Chief uses ``PRODUCTION_MAX_CAPITAL_FRACTION`` (20%).
    """
    if value is not None:
        return float(value)
    return DEFAULT_MAX_CAPITAL_FRACTION


def evaluate_economic_quality(
    plan: EntryExitPlan,
    available_capital: float,
    *,
    min_target_2_move_pct: float = MIN_TARGET_2_MOVE_PCT,
    preferred_target_2_move_pct: float = PREFERRED_TARGET_2_MOVE_PCT,
    min_net_profit: float = MIN_NET_PROFIT,
    min_reward_to_risk: float = MIN_REWARD_TO_RISK,
    estimated_round_trip_cost_pct: float = ESTIMATED_ROUND_TRIP_COST_PCT,
    max_capital_fraction: float | None = None,
    direction: str | None = None,
    leverage: float = 1.0,
    estimated_margin_cost_pct: float = 0.0,
    max_account_risk_at_stop_pct: float | None = DEFAULT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
    policy_id: str | None = None,
) -> EconomicGateResult:
    """Evaluate one plan's after-cost economics.

    The keyword defaults above are the historical EXECUTION-compatible bar and
    are unchanged. Advisory callers pass the resolved advisory policy instead
    (``app.services.economic_quality_gate.advisory_economic_policy``), which
    disables the optional fixed floors. ``policy_id`` travels into the result so
    evidence can never attribute an advisory verdict to the execution policy or
    the reverse.
    """
    direction = (direction or getattr(plan, "direction", "LONG") or "LONG").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    max_capital_fraction = _resolved_capital_fraction(max_capital_fraction)
    applied = _policy_attribution(
        {"policy_id": policy_id} if policy_id else None,
        min_net_profit=min_net_profit,
        min_target_2_move_pct=min_target_2_move_pct,
        min_reward_to_risk=min_reward_to_risk,
    )

    def _reject(reason: str) -> EconomicGateResult:
        return _invalid_result(direction, leverage, reason, policy=applied)

    scalar_values = (
        available_capital,
        min_target_2_move_pct,
        preferred_target_2_move_pct,
        min_net_profit,
        min_reward_to_risk,
        estimated_round_trip_cost_pct,
        max_capital_fraction,
        leverage,
        estimated_margin_cost_pct,
    )
    if not all(math.isfinite(float(value)) for value in scalar_values):
        return _reject("Invalid non-finite economic-gate input")
    if max_account_risk_at_stop_pct is not None and not math.isfinite(float(max_account_risk_at_stop_pct)):
        return _reject("Invalid non-finite account-risk limit")
    if min_net_profit < 0 or min_target_2_move_pct < 0 or min_reward_to_risk < 0:
        # A negative floor is not a disabled filter and must never behave like a
        # permissive one: it is a malformed policy and is refused outright.
        return _reject("Invalid negative economic floor")
    if available_capital <= 0:
        return _reject("Available capital must be positive")
    if leverage <= 0:
        return _reject("Leverage must be positive")
    if not 0 < max_capital_fraction <= 1:
        return _reject("Capital fraction must be between 0 and 1")
    if estimated_round_trip_cost_pct < 0 or estimated_margin_cost_pct < 0:
        return _reject("Estimated costs cannot be negative")

    entry_low = float(getattr(plan, "entry_low", 0.0))
    entry_high = float(getattr(plan, "entry_high", 0.0))
    entry_reference = (entry_low + entry_high) / 2
    chase_limit = float(getattr(plan, "chase_limit", entry_high if direction == "LONG" else entry_low))
    stop_price = float(getattr(plan, "stop_price", 0.0))
    target_1 = float(getattr(plan, "target_1", 0.0))
    target_2 = float(getattr(plan, "target_2", 0.0))
    reward_to_risk_1 = float(getattr(plan, "reward_to_risk_1", 0.0))
    reward_to_risk_2 = float(getattr(plan, "reward_to_risk_2", 0.0))

    plan_values = (
        entry_low,
        entry_high,
        chase_limit,
        stop_price,
        target_1,
        target_2,
        reward_to_risk_1,
        reward_to_risk_2,
    )
    if not all(math.isfinite(value) for value in plan_values):
        return _reject("Invalid non-finite plan geometry")

    if entry_reference <= 0 or min(entry_low, entry_high, chase_limit, stop_price, target_1, target_2) <= 0:
        return _reject("Invalid non-positive plan geometry")

    if direction == "SHORT":
        geometry_valid = 0 < target_2 < target_1 < entry_reference < stop_price
    else:
        geometry_valid = 0 < stop_price < entry_reference < target_1 < target_2
    if not geometry_valid:
        return _reject("Invalid target/stop geometry")

    stop_pct = abs(entry_reference - stop_price) / entry_reference * 100
    if direction == "SHORT":
        target_1_move_pct = (entry_reference - target_1) / entry_reference * 100
        target_2_move_pct = (entry_reference - target_2) / entry_reference * 100
    else:
        target_1_move_pct = (target_1 - entry_reference) / entry_reference * 100
        target_2_move_pct = (target_2 - entry_reference) / entry_reference * 100

    calculated = (stop_pct, target_1_move_pct, target_2_move_pct)
    if not all(math.isfinite(value) and value > 0 for value in calculated):
        return _reject("Invalid calculated economic geometry")

    recommended_capital = available_capital * max_capital_fraction
    position_notional = recommended_capital * leverage
    target_1_gross_profit = position_notional * target_1_move_pct / 100
    target_2_gross_profit = position_notional * target_2_move_pct / 100
    total_cost_pct = estimated_round_trip_cost_pct + estimated_margin_cost_pct
    estimated_costs = position_notional * total_cost_pct / 100
    target_1_net_profit = target_1_gross_profit - estimated_costs
    target_2_net_profit = target_2_gross_profit - estimated_costs
    # The cent-exact Target 2 profit is the value the rule compares, so it is
    # also the value recorded: a stored verdict is always reproducible from the
    # stored number.
    target_2_net_at_decision = quantize_usd_cents(target_2_net_profit)
    account_risk_at_stop_pct = stop_pct * leverage * max_capital_fraction

    rejection_reason = None
    if reward_to_risk_2 < min_reward_to_risk:
        rejection_reason = (
            f"Reward/risk {reward_to_risk_2:.2f}:1 is below minimum "
            f"{min_reward_to_risk:.2f}:1"
        )
    elif target_2_move_pct < min_target_2_move_pct:
        rejection_reason = (
            f"Projected Target 2 move {target_2_move_pct:.2f}% is below minimum "
            f"{min_target_2_move_pct:.2f}%"
        )
    elif (
        target_2_net_at_decision <= 0
        or target_2_net_at_decision < min_net_profit
    ):
        # BR-002: independent of every optional filter, a recommendation the
        # operator is told about must model profit after applicable costs. When
        # the profit filter is OFF the applied floor is $0.00 and this branch is
        # a strict-positivity test, so a zero or negative after-cost outcome is
        # never reported as profitable.
        rejection_reason = (
            f"Projected net profit ${target_2_net_at_decision:.2f} at the "
            f"{max_capital_fraction * 100:.0f}% capital envelope is below minimum "
            f"${min_net_profit:.2f} after estimated costs"
        )
    elif (
        max_account_risk_at_stop_pct is not None
        and account_risk_at_stop_pct > max_account_risk_at_stop_pct
    ):
        rejection_reason = (
            f"stop exposure {account_risk_at_stop_pct:.2f}% exceeds "
            f"maximum {max_account_risk_at_stop_pct:.2f}% of account equity"
        )

    return EconomicGateResult(
        qualified=rejection_reason is None,
        entry_reference=round(entry_reference, 8),
        stop_pct=round(stop_pct, 2),
        target_1_move_pct=round(target_1_move_pct, 2),
        target_2_move_pct=round(target_2_move_pct, 2),
        recommended_capital=round(recommended_capital, 2),
        target_1_gross_profit=round(target_1_gross_profit, 2),
        target_2_gross_profit=round(target_2_gross_profit, 2),
        estimated_costs=round(estimated_costs, 2),
        target_1_net_profit=round(target_1_net_profit, 2),
        target_2_net_profit=target_2_net_at_decision,
        rejection_reason=rejection_reason,
        direction=direction,
        leverage=round(leverage, 2),
        position_notional=round(position_notional, 2),
        account_risk_at_stop_pct=round(account_risk_at_stop_pct, 2),
        estimated_margin_cost_pct=round(estimated_margin_cost_pct, 4),
        **applied,
    )
