"""Pure, versioned after-cost economic gate contract.

Properties (each covered by tests):

* Pure: no I/O, clock, environment, randomness or module-level mutable state.
* Fail closed: any missing, non-numeric, boolean, non-finite or malformed input
  yields ``qualified=False`` with explicit reason codes. Missing is never zero.
* Never raises for bad data; callers cannot turn an exception into a pass.
* Versioned: ``interface_version`` and the policy digest travel in the result.
* Deterministic ordered reasons: every failed check is reported; the first one
  is ``primary_reason`` and matches the production gate's single rejection.

Numeric semantics intentionally mirror
``app.services.economic_quality_gate.evaluate_economic_quality`` so a later
integration can be proven equivalent; the equivalence harness lives in tests.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

GATE_INTERFACE_VERSION = "opip.gate.v1"


class ReasonCode(str, Enum):
    INPUT_MISSING = "INPUT_MISSING"
    INPUT_INVALID = "INPUT_INVALID"
    POLICY_INVALID = "POLICY_INVALID"
    GEOMETRY_INVALID = "GEOMETRY_INVALID"
    REWARD_TO_RISK_BELOW_MIN = "REWARD_TO_RISK_BELOW_MIN"
    TARGET_MOVE_BELOW_MIN = "TARGET_MOVE_BELOW_MIN"
    NET_PROFIT_BELOW_MIN = "NET_PROFIT_BELOW_MIN"
    ACCOUNT_RISK_ABOVE_MAX = "ACCOUNT_RISK_ABOVE_MAX"
    NUMERIC_OVERFLOW = "NUMERIC_OVERFLOW"
    INTERNAL_ERROR = "INTERNAL_ERROR"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num(value: Any) -> float | None:
    """Finite float or None. Never coerces strings/bools/None."""
    if not _is_number(value):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def quantize_usd_cents(value: float) -> float:
    """Deterministic half-up to whole cents (same algorithm as production)."""
    scaled = float(value) * 100.0
    if scaled >= 0:
        return math.floor(scaled + 0.5) / 100.0
    return math.ceil(scaled - 0.5) / 100.0


@dataclass(frozen=True)
class GatePolicy:
    policy_id: str
    min_net_profit: float = 0.0
    min_target_2_move_pct: float = 0.0
    min_reward_to_risk: float = 0.0
    estimated_round_trip_cost_pct: float = 0.0
    max_capital_fraction: float = 1.0
    max_account_risk_at_stop_pct: float | None = None

    def validation_errors(self) -> tuple[str, ...]:
        errs: list[str] = []
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            errs.append("policy_id")
        for name in (
            "min_net_profit",
            "min_target_2_move_pct",
            "min_reward_to_risk",
            "estimated_round_trip_cost_pct",
        ):
            v = _num(getattr(self, name))
            if v is None or v < 0:
                errs.append(name)
        cf = _num(self.max_capital_fraction)
        if cf is None or not 0 < cf <= 1:
            errs.append("max_capital_fraction")
        if self.max_account_risk_at_stop_pct is not None:
            ar = _num(self.max_account_risk_at_stop_pct)
            if ar is None or ar < 0:
                errs.append("max_account_risk_at_stop_pct")
        return tuple(errs)

    def digest(self) -> str:
        payload = json.dumps(
            {
                "v": GATE_INTERFACE_VERSION,
                "policy_id": self.policy_id,
                "min_net_profit": self.min_net_profit,
                "min_target_2_move_pct": self.min_target_2_move_pct,
                "min_reward_to_risk": self.min_reward_to_risk,
                "estimated_round_trip_cost_pct": self.estimated_round_trip_cost_pct,
                "max_capital_fraction": self.max_capital_fraction,
                "max_account_risk_at_stop_pct": self.max_account_risk_at_stop_pct,
            },
            sort_keys=True,
            allow_nan=False,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class GateInput:
    """Candidate economics. ``None`` means unknown and fails closed."""

    direction: str | None
    entry_low: float | None
    entry_high: float | None
    stop_price: float | None
    target_1: float | None
    target_2: float | None
    reward_to_risk_2: float | None
    available_capital: float | None
    leverage: float | None = 1.0
    estimated_margin_cost_pct: float | None = 0.0


@dataclass(frozen=True)
class GateResult:
    interface_version: str
    policy_id: str
    policy_digest: str
    qualified: bool
    reasons: tuple[ReasonCode, ...] = ()
    detail: tuple[str, ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)

    @property
    def primary_reason(self) -> ReasonCode | None:
        return self.reasons[0] if self.reasons else None


def _reject(policy: GatePolicy, code: ReasonCode, *detail: str) -> GateResult:
    return GateResult(
        GATE_INTERFACE_VERSION,
        str(policy.policy_id),
        _safe_digest(policy),
        False,
        (code,),
        tuple(detail),
    )


def _safe_digest(policy: GatePolicy) -> str:
    try:
        return policy.digest()
    except (TypeError, ValueError):
        return "unhashable-policy"


def evaluate_gate(candidate: GateInput, policy: GatePolicy) -> GateResult:
    """Evaluate one candidate against one policy. Never raises: any unexpected
    arithmetic/type failure is converted to a fail-closed rejection."""
    try:
        return _evaluate(candidate, policy)
    except (ArithmeticError, ValueError, TypeError, AttributeError):
        pid = policy.policy_id if isinstance(policy, GatePolicy) and isinstance(policy.policy_id, str) else ""
        return GateResult(GATE_INTERFACE_VERSION, pid, "", False, (ReasonCode.INTERNAL_ERROR,))


def _evaluate(candidate: GateInput, policy: GatePolicy) -> GateResult:
    if not isinstance(policy, GatePolicy):
        return GateResult(GATE_INTERFACE_VERSION, "", "", False, (ReasonCode.POLICY_INVALID,))
    bad = policy.validation_errors()
    if bad:
        return _reject(policy, ReasonCode.POLICY_INVALID, *bad)
    if not isinstance(candidate, GateInput):
        return _reject(policy, ReasonCode.INPUT_MISSING, "candidate")

    direction = candidate.direction.upper() if isinstance(candidate.direction, str) else None
    if direction not in {"LONG", "SHORT"}:
        return _reject(policy, ReasonCode.INPUT_INVALID, "direction")

    fields = (
        "entry_low", "entry_high", "stop_price", "target_1", "target_2",
        "reward_to_risk_2", "available_capital", "leverage", "estimated_margin_cost_pct",
    )
    missing = [n for n in fields if getattr(candidate, n) is None]
    if missing:
        return _reject(policy, ReasonCode.INPUT_MISSING, *missing)
    vals = {n: _num(getattr(candidate, n)) for n in fields}
    invalid = [n for n, v in vals.items() if v is None]
    if invalid:
        return _reject(policy, ReasonCode.INPUT_INVALID, *invalid)
    if vals["available_capital"] <= 0 or vals["leverage"] <= 0 or vals["estimated_margin_cost_pct"] < 0:
        return _reject(policy, ReasonCode.INPUT_INVALID, "capital/leverage/margin_cost")

    entry = (vals["entry_low"] + vals["entry_high"]) / 2
    stop, t1, t2 = vals["stop_price"], vals["target_1"], vals["target_2"]
    if min(vals["entry_low"], vals["entry_high"], stop, t1, t2) <= 0 or entry <= 0:
        return _reject(policy, ReasonCode.GEOMETRY_INVALID, "non-positive price")
    ok = (0 < t2 < t1 < entry < stop) if direction == "SHORT" else (0 < stop < entry < t1 < t2)
    if not ok:
        return _reject(policy, ReasonCode.GEOMETRY_INVALID, "target/stop ordering")

    stop_pct = abs(entry - stop) / entry * 100
    t2_move = (entry - t2) / entry * 100 if direction == "SHORT" else (t2 - entry) / entry * 100
    capital = vals["available_capital"] * policy.max_capital_fraction
    notional = capital * vals["leverage"]
    cost = notional * (policy.estimated_round_trip_cost_pct + vals["estimated_margin_cost_pct"]) / 100
    raw_net = notional * t2_move / 100 - cost
    net_t2 = quantize_usd_cents(raw_net) if math.isfinite(raw_net * 100.0) else float("nan")
    account_risk = stop_pct * vals["leverage"] * policy.max_capital_fraction

    derived = (stop_pct, t2_move, capital, notional, cost, net_t2, account_risk)
    if not all(math.isfinite(d) for d in derived):
        return _reject(policy, ReasonCode.NUMERIC_OVERFLOW, "derived value not finite")

    reasons: list[ReasonCode] = []
    detail: list[str] = []
    if vals["reward_to_risk_2"] < policy.min_reward_to_risk:
        reasons.append(ReasonCode.REWARD_TO_RISK_BELOW_MIN)
        detail.append(f"rr={vals['reward_to_risk_2']:.2f}<{policy.min_reward_to_risk:.2f}")
    if t2_move < policy.min_target_2_move_pct:
        reasons.append(ReasonCode.TARGET_MOVE_BELOW_MIN)
        detail.append(f"move={t2_move:.2f}<{policy.min_target_2_move_pct:.2f}")
    if net_t2 <= 0 or net_t2 < policy.min_net_profit:
        reasons.append(ReasonCode.NET_PROFIT_BELOW_MIN)
        detail.append(f"net={net_t2:.2f}<max(0+,{policy.min_net_profit:.2f})")
    limit = policy.max_account_risk_at_stop_pct
    if limit is not None and account_risk > limit:
        reasons.append(ReasonCode.ACCOUNT_RISK_ABOVE_MAX)
        detail.append(f"risk={account_risk:.2f}>{limit:.2f}")

    return GateResult(
        GATE_INTERFACE_VERSION,
        policy.policy_id,
        policy.digest(),
        not reasons,
        tuple(reasons),
        tuple(detail),
        {
            "entry_reference": entry,
            "stop_pct": stop_pct,
            "target_2_move_pct": t2_move,
            "position_notional": notional,
            "estimated_costs": cost,
            "target_2_net_profit": net_t2,
            "account_risk_at_stop_pct": account_risk,
        },
    )
