"""OPIP-ALERT-ACTIONABLE-ONLY-001 -- the single actionable-notification admission policy.

Why this module exists
----------------------

Telegram is the operator's attention channel. Before this policy, *which* producer emitted a
card decided whether a card could be sent, so a 68/100 Early Watch observation and a fully
qualified BUY with entry, stop, targets, expiry and net economics travelled different code paths
with different gates. This module gives every actionable producer exactly one ordered admission
contract to satisfy, and one closed vocabulary of refusal reasons to record.

What it is
----------

* **Pure.** No I/O, no delivery, no registry writes, no clock of its own: the caller supplies
  ``now``. It cannot send anything, cannot retry anything and cannot record anything.
* **Notification-only authority.** It decides whether a *notification* may be delivered. It
  never decides whether a trade is qualified, never sizes a position, never places, modifies or
  cancels an order, and never widens funded authority. ``QUALIFIED``/``REJECTED``/``EXPIRED``/
  ``WAIT`` remain the trade outcomes of the existing deterministic pipeline; this module only
  produces notification outcomes (``ADMITTED``/``SUPPRESSED`` plus a reason).
* **A consumer, not a second source of truth.** Every input is a fact another component already
  computed -- execution validation (spread, visible depth coverage, measured round-trip drag),
  the liquidity capacity ceiling, the economic gate, the action gate, the plan geometry kernel,
  the governor/delivery identity. Nothing is recomputed as a trading value and no threshold here
  can rescue a candidate the deterministic pipeline rejected.
* **Fail closed.** A missing mandatory fact is a refusal with a specific reason, never an
  implicit pass. Heuristic confidence and risk scores are *not* inputs: an uncalibrated score can
  never satisfy, and can never be rendered as, a probability of winning.

The seven ordered conditions are the requirement's gate, in the requirement's order. The first
failing condition is reported as ``condition``/``reason``; every failing condition is listed in
``reasons`` so suppression metrics remain attributable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.fee_pnl import projected_net_edge_pct

POLICY_VERSION = "opip-actionable-only-v1"

ADMITTED = "ADMITTED"
SUPPRESSED = "SUPPRESSED"

CONDITION_IDENTITY = "C1_IDENTITY_AND_FRESHNESS"
CONDITION_TRIGGER = "C2_ENTRY_TRIGGER"
CONDITION_EXECUTION = "C3_MARKET_AND_EXECUTION_QUALITY"
CONDITION_GEOMETRY = "C4_TRADE_GEOMETRY"
CONDITION_ECONOMICS = "C5_NET_ECONOMICS"
CONDITION_RISK = "C6_RISK_AND_AUTHORITY"
CONDITION_DELIVERY = "C7_DELIVERY_ELIGIBILITY"

#: Reason codes. Each names what is missing rather than how a card could have been made to look.
REASON_VENUE_IDENTITY_INVALID = "VENUE_IDENTITY_INVALID"
REASON_EVIDENCE_TIMESTAMP_MISSING = "EVIDENCE_TIMESTAMP_MISSING"
REASON_EVIDENCE_STALE = "EVIDENCE_STALE"
REASON_DUPLICATE_EPISODE = "DUPLICATE_EPISODE"

REASON_DIRECTION_MISSING = "DIRECTION_MISSING"
REASON_NO_ENTRY_TRIGGER = "NO_ENTRY_TRIGGER"
REASON_TOO_EXTENDED = "TOO_EXTENDED_NOT_ENTRY"
REASON_ENTRY_ZONE_MISSING = "ENTRY_ZONE_MISSING"
REASON_ENTRY_NOT_FEASIBLE = "ENTRY_NOT_FEASIBLE"

REASON_EXECUTION_EVIDENCE_MISSING = "EXECUTION_EVIDENCE_MISSING"
REASON_LIQUIDITY_INSUFFICIENT = "LIQUIDITY_INSUFFICIENT"
REASON_SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
REASON_DEPTH_INSUFFICIENT = "DEPTH_INSUFFICIENT"
REASON_SIZE_SLIPPAGE_EXCESSIVE = "SIZE_SLIPPAGE_EXCESSIVE"
REASON_ORDER_SIZE_UNUSABLE = "ORDER_SIZE_UNUSABLE"

REASON_STOP_MISSING = "STOP_MISSING"
REASON_TARGET_MISSING = "TARGET_1_MISSING"
REASON_TARGET_2_MISSING = "TARGET_2_MISSING"
REASON_INVALIDATION_MISSING = "INVALIDATION_MISSING"
REASON_EXPIRY_MISSING = "EXPIRY_MISSING"
REASON_EXPIRY_PASSED = "EXPIRY_PASSED"
REASON_GEOMETRY_INCOHERENT = "GEOMETRY_INCOHERENT"

REASON_ECONOMICS_UNAVAILABLE = "ECONOMICS_UNAVAILABLE"
REASON_NET_RR_NOT_COMPUTABLE = "NET_REWARD_TO_RISK_NOT_COMPUTABLE"
REASON_NET_RR_BELOW_POLICY = "NET_REWARD_TO_RISK_BELOW_POLICY"
REASON_NET_EDGE_NOT_POSITIVE = "NET_EDGE_NOT_POSITIVE"

REASON_QUALIFICATION_NOT_SATISFIED = "DETERMINISTIC_QUALIFICATION_NOT_SATISFIED"
REASON_RISK_GATE_REJECTED = "RISK_GATE_REJECTED"
REASON_AI_CANNOT_OVERRIDE = "AI_ADVISORY_CANNOT_OVERRIDE_DETERMINISTIC"
REASON_FUNDED_AUTHORITY = "FUNDED_AUTHORITY_MUST_REMAIN_DISABLED"

REASON_DELIVERY_IN_FLIGHT = "DELIVERY_IN_FLIGHT"

#: Entry styles that describe a limit entry waiting for a better price. They are admitted only
#: when the plan also carries a complete, currently reachable entry zone; see the trigger rule.
LIMIT_ENTRY_STYLES = frozenset(
    {"wait_for_pullback", "wait_for_rebound", "place_limit", "limit", "limit_entry"}
)

_DIRECTIONS = frozenset({"LONG", "SHORT"})
#: Venues whose instrument identity this platform can resolve. A card for anything else cannot be
#: executed manually against the configured venue, so it fails closed.
SUPPORTED_VENUES = frozenset({"KRAKEN"})


@dataclass(frozen=True)
class ActionableNotificationPolicy:
    """Versioned, configurable admission thresholds.

    Defaults are deliberately conservative and never loosen an existing deterministic gate: this
    policy can only add refusals in front of the existing qualification, risk and execution
    safeguards. ``round_trip_cost_pct`` is a *notification* cost model of the same order as the
    execution gate's estimated round-trip cost; it is never an execution or sizing input.
    """

    version: str = POLICY_VERSION
    max_evidence_age_seconds: int = 900
    plan_validity_minutes: int = 720
    #: Notification-floor economics. This is deliberately *not* a second reward-to-risk policy:
    #: the authoritative minimum reward-to-risk requirement stays with the existing deterministic
    #: economic gate, and this floor only refuses a card whose own levels are not net-positive
    #: after costs. Raising it is an owner decision made through configuration.
    min_net_reward_to_risk: float = 1.0
    round_trip_cost_pct: float = 0.60
    min_liquidity_usd: float = 100_000.0
    max_spread_bps: float = 50.0
    max_size_slippage_pct: float = 1.00
    require_fully_covered_depth: bool = True
    admit_limit_entries: bool = True
    require_target_2: bool = False

    @classmethod
    def from_settings(cls, settings: Any) -> "ActionableNotificationPolicy":
        """Read the versioned settings, keeping class defaults for anything unusable."""
        base = cls()

        def _int(name: str, default: int) -> int:
            try:
                return int(getattr(settings, name, None))
            except (TypeError, ValueError):
                return default

        def _float(name: str, default: float) -> float:
            value = _number(getattr(settings, name, None))
            return default if value is None else value

        def _bool(name: str, default: bool) -> bool:
            value = getattr(settings, name, None)
            return default if value is None else bool(value)

        return cls(
            version=str(getattr(settings, "opip_actionable_policy_version", base.version)),
            max_evidence_age_seconds=_int(
                "opip_actionable_max_evidence_age_seconds", base.max_evidence_age_seconds
            ),
            plan_validity_minutes=_int(
                "opip_actionable_plan_validity_minutes", base.plan_validity_minutes
            ),
            min_net_reward_to_risk=_float(
                "opip_actionable_min_net_reward_to_risk", base.min_net_reward_to_risk
            ),
            round_trip_cost_pct=_float(
                "opip_actionable_round_trip_cost_pct", base.round_trip_cost_pct
            ),
            min_liquidity_usd=_float(
                "opip_actionable_min_liquidity_usd", base.min_liquidity_usd
            ),
            max_spread_bps=_float("opip_actionable_max_spread_bps", base.max_spread_bps),
            max_size_slippage_pct=_float(
                "opip_actionable_max_size_slippage_pct", base.max_size_slippage_pct
            ),
            require_fully_covered_depth=_bool(
                "opip_actionable_require_fully_covered_depth",
                base.require_fully_covered_depth,
            ),
            admit_limit_entries=_bool(
                "opip_actionable_admit_limit_entries", base.admit_limit_entries
            ),
            require_target_2=_bool(
                "opip_actionable_require_target_2", base.require_target_2
            ),
        )


@dataclass(frozen=True)
class ActionableEvidence:
    """Facts already computed upstream, in the shape the ordered gate reads.

    ``None`` means "the producer did not supply this fact" and is always handled as a refusal by
    the condition that needs it -- never as a zero, a guess or a pass.
    """

    # C1 -- identity and freshness
    instrument: str | None = None
    venue: str | None = None
    venue_identity_valid: bool | None = None
    observed_at: datetime | None = None
    expires_at: datetime | None = None
    episode_id: str | None = None
    material_change: bool | None = None
    already_delivered: bool | None = None

    # C2 -- entry trigger and feasible entry zone
    direction: str | None = None
    entry_style: str | None = None
    valid_now: bool | None = None
    entry_trigger_confirmed: bool | None = None
    too_extended: bool | None = None
    entry_price: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    chase_limit: float | None = None

    # C3 -- market and execution quality
    liquidity_usd: float | None = None
    spread_bps: float | None = None
    size_slippage_pct: float | None = None
    order_size_usd: float | None = None
    depth_covered: bool | None = None

    # C4 -- trade geometry
    stop_price: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    invalidation: str | None = None

    # C5 -- net economics
    economics_available: bool | None = None
    reward_to_risk_1: float | None = None
    round_trip_cost_pct: float | None = None

    # C6 -- risk and authority
    deterministic_qualified: bool | None = None
    risk_gate_passed: bool | None = None
    ai_override_attempted: bool | None = None
    funded_execution_authorized: bool | None = None

    # C7 -- delivery eligibility
    delivery_in_flight: bool | None = None


@dataclass(frozen=True)
class ActionableAdmission:
    """The notification verdict. This module renders nothing and returns no plan."""

    admitted: bool
    policy_version: str
    condition: str | None = None
    reason: str | None = None
    reasons: tuple[str, ...] = ()
    net_reward_to_risk: float | None = None
    net_edge_pct: float | None = None
    expires_at: datetime | None = None
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None

    @property
    def suppressed(self) -> bool:
        return not self.admitted

    def suppression_reason(self) -> str | None:
        """The auditable reason written to the delivery ledger, or None when admitted."""
        if self.admitted or not self.reason:
            return None
        return f"ACTIONABLE_GATE:{self.reason}"

    def evidence_payload(self) -> dict[str, Any]:
        """Bounded, JSON-safe evidence for the suppression/retry ledger."""
        return {
            "policy_version": self.policy_version,
            "admission": ADMITTED if self.admitted else SUPPRESSED,
            "condition": self.condition,
            "reason": self.reason,
            "reasons": list(self.reasons),
            "net_reward_to_risk": self.net_reward_to_risk,
            "net_edge_pct": self.net_edge_pct,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


def _utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def cost_adjusted_reward_to_risk(
    *,
    entry: Any,
    stop: Any,
    target: Any,
    round_trip_cost_pct: Any,
) -> float | None:
    """Net reward-to-risk after round-trip costs, from prices the caller already holds.

    This is arithmetic over supplied levels -- it invents no price and is not an execution input.
    The cost is modelled as a fixed percentage of the entry price applied on both sides, so a
    setup that only looks profitable before fees and slippage cannot pass.
    """
    entry_value = _number(entry)
    stop_value = _number(stop)
    target_value = _number(target)
    cost_pct = _number(round_trip_cost_pct)
    if entry_value is None or stop_value is None or target_value is None or cost_pct is None:
        return None
    if entry_value <= 0 or cost_pct < 0:
        return None
    cost_abs = entry_value * cost_pct / 100.0
    risk = abs(entry_value - stop_value) + cost_abs
    reward = abs(target_value - entry_value) - cost_abs
    if not math.isfinite(risk) or risk <= 0:
        return None
    return reward / risk


def _entry_reference(evidence: ActionableEvidence) -> float | None:
    price = _number(evidence.entry_price)
    if price is not None and price > 0:
        return price
    low = _number(evidence.entry_low)
    high = _number(evidence.entry_high)
    if low is None or high is None or low <= 0 or high <= 0:
        return None
    return (low + high) / 2.0


def _resolved_expiry(
    evidence: ActionableEvidence,
    policy: ActionableNotificationPolicy,
    *,
    now: datetime,
) -> datetime | None:
    """A card must state an expiry. An explicit expiry wins; otherwise a bounded policy TTL is
    applied to the evidence timestamp. The TTL is a policy value, never a fabricated level, and
    the resolved expiry is returned to the caller so the rendered card can state it."""
    explicit = _utc(evidence.expires_at)
    if explicit is not None:
        return explicit
    observed_at = _utc(evidence.observed_at) or now
    minutes = int(policy.plan_validity_minutes)
    if minutes <= 0:
        return None
    return observed_at + timedelta(minutes=minutes)


def _check_identity(
    evidence: ActionableEvidence,
    *,
    policy: ActionableNotificationPolicy,
    now: datetime,
) -> str | None:
    if evidence.venue_identity_valid is not True:
        return REASON_VENUE_IDENTITY_INVALID
    if not _text(evidence.instrument) or not _text(evidence.venue):
        return REASON_VENUE_IDENTITY_INVALID
    if str(evidence.venue or "").strip().upper() not in SUPPORTED_VENUES:
        return REASON_VENUE_IDENTITY_INVALID
    observed_at = _utc(evidence.observed_at)
    if observed_at is None:
        return REASON_EVIDENCE_TIMESTAMP_MISSING
    if (now - observed_at).total_seconds() > max(1, int(policy.max_evidence_age_seconds)):
        return REASON_EVIDENCE_STALE
    if evidence.already_delivered is True and evidence.material_change is not True:
        return REASON_DUPLICATE_EPISODE
    return None


def _trigger_confirmed(
    evidence: ActionableEvidence, policy: ActionableNotificationPolicy
) -> bool:
    if evidence.entry_trigger_confirmed is not None:
        return bool(evidence.entry_trigger_confirmed)
    if evidence.valid_now is True:
        return True
    style = str(evidence.entry_style or "").strip().lower()
    if style and style in LIMIT_ENTRY_STYLES:
        if not policy.admit_limit_entries:
            return False
        return _entry_reference(evidence) is not None
    return False


def _check_trigger(evidence: ActionableEvidence, *, policy: ActionableNotificationPolicy) -> str | None:
    direction = str(evidence.direction or "").strip().upper()
    if direction not in _DIRECTIONS:
        return REASON_DIRECTION_MISSING
    if evidence.too_extended is True:
        return REASON_TOO_EXTENDED
    if not _trigger_confirmed(evidence, policy):
        return REASON_NO_ENTRY_TRIGGER
    reference = _entry_reference(evidence)
    if reference is None:
        return REASON_ENTRY_ZONE_MISSING
    low = _number(evidence.entry_low)
    high = _number(evidence.entry_high)
    if low is not None and high is not None and low > high:
        return REASON_ENTRY_ZONE_MISSING
    chase_limit = _number(evidence.chase_limit)
    if chase_limit is not None and direction == "LONG" and reference > chase_limit:
        return REASON_ENTRY_NOT_FEASIBLE
    if chase_limit is not None and direction == "SHORT" and reference < chase_limit:
        return REASON_ENTRY_NOT_FEASIBLE
    return None


def _check_execution(
    evidence: ActionableEvidence, *, policy: ActionableNotificationPolicy
) -> str | None:
    """Market/execution quality. A missing mandatory fact blocks; it never passes by default."""
    liquidity = _number(evidence.liquidity_usd)
    if liquidity is None:
        return REASON_EXECUTION_EVIDENCE_MISSING
    if liquidity < policy.min_liquidity_usd:
        return REASON_LIQUIDITY_INSUFFICIENT
    spread = _number(evidence.spread_bps)
    if spread is None:
        return REASON_EXECUTION_EVIDENCE_MISSING
    if spread < 0:
        return REASON_EXECUTION_EVIDENCE_MISSING
    if spread > policy.max_spread_bps:
        return REASON_SPREAD_TOO_WIDE
    slippage = _number(evidence.size_slippage_pct)
    if slippage is None:
        return REASON_EXECUTION_EVIDENCE_MISSING
    if slippage < 0:
        return REASON_EXECUTION_EVIDENCE_MISSING
    if slippage > policy.max_size_slippage_pct:
        return REASON_SIZE_SLIPPAGE_EXCESSIVE
    order_size = _number(evidence.order_size_usd)
    if order_size is None or order_size <= 0:
        return REASON_ORDER_SIZE_UNUSABLE
    if policy.require_fully_covered_depth and evidence.depth_covered is not True:
        return REASON_DEPTH_INSUFFICIENT
    return None


def _check_geometry(
    evidence: ActionableEvidence,
    *,
    policy: ActionableNotificationPolicy,
    now: datetime,
) -> str | None:
    direction = str(evidence.direction or "").strip().upper()
    reference = _entry_reference(evidence)
    stop = _number(evidence.stop_price)
    target_1 = _number(evidence.target_1)
    if stop is None or stop <= 0:
        return REASON_STOP_MISSING
    if target_1 is None or target_1 <= 0:
        return REASON_TARGET_MISSING
    second = _number(evidence.target_2)
    if policy.require_target_2 and (second is None or second <= 0):
        return REASON_TARGET_2_MISSING
    if _text(evidence.invalidation) is None:
        return REASON_INVALIDATION_MISSING
    expiry = _resolved_expiry(evidence, policy, now=now)
    if expiry is None:
        return REASON_EXPIRY_MISSING
    if expiry <= now:
        return REASON_EXPIRY_PASSED
    if reference is None:
        # C2 already refused this; keep the geometry check honest rather than guessing an entry.
        return REASON_ENTRY_ZONE_MISSING
    if direction == "LONG":
        if not stop < reference < target_1:
            return REASON_GEOMETRY_INCOHERENT
        if second is not None and target_1 > second:
            return REASON_GEOMETRY_INCOHERENT
    elif direction == "SHORT":
        if not target_1 < reference < stop:
            return REASON_GEOMETRY_INCOHERENT
        if second is not None and target_1 < second:
            return REASON_GEOMETRY_INCOHERENT
    else:
        return REASON_DIRECTION_MISSING
    return None


def _check_economics(
    evidence: ActionableEvidence, *, policy: ActionableNotificationPolicy
) -> str | None:
    """Cost-adjusted economics. The floor is a *net reward-to-risk* policy, not a fixed profit."""
    if evidence.economics_available is not True:
        return REASON_ECONOMICS_UNAVAILABLE
    reference = _entry_reference(evidence)
    cost_pct = _number(evidence.round_trip_cost_pct)
    if cost_pct is None:
        cost_pct = policy.round_trip_cost_pct
    net_rr = cost_adjusted_reward_to_risk(
        entry=reference,
        stop=_number(evidence.stop_price),
        target=_number(evidence.target_1),
        round_trip_cost_pct=cost_pct,
    )
    if net_rr is None:
        return REASON_NET_RR_NOT_COMPUTABLE
    if net_rr < policy.min_net_reward_to_risk:
        return REASON_NET_RR_BELOW_POLICY
    target = _number(evidence.target_1)
    if reference is not None and reference > 0 and target is not None:
        net_edge = projected_net_edge_pct(
            gross_target_move_pct=abs(target - reference) / reference * 100.0,
            round_trip_fee_pct=cost_pct,
        )
        if net_edge <= 0:
            return REASON_NET_EDGE_NOT_POSITIVE
    return None


def _check_risk(evidence: ActionableEvidence) -> str | None:
    """Deterministic qualification and authority. AI is advisory and can never override them."""
    if evidence.deterministic_qualified is not True:
        return REASON_QUALIFICATION_NOT_SATISFIED
    if evidence.risk_gate_passed is not True:
        return REASON_RISK_GATE_REJECTED
    if evidence.ai_override_attempted is True:
        return REASON_AI_CANNOT_OVERRIDE
    if evidence.funded_execution_authorized is True:
        return REASON_FUNDED_AUTHORITY
    return None


def _check_delivery(evidence: ActionableEvidence) -> str | None:
    if evidence.delivery_in_flight is True:
        return REASON_DELIVERY_IN_FLIGHT
    return None


def _dedupe(reasons: list[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)
    return tuple(ordered)


def evaluate_actionable_admission(
    *,
    evidence: ActionableEvidence,
    policy: ActionableNotificationPolicy | None = None,
    now: datetime | None = None,
) -> ActionableAdmission:
    """Apply the seven ordered actionable conditions and return the notification verdict.

    The first failing condition is reported as ``condition``/``reason``; every failing condition
    is listed in ``reasons``. Delivery, dedupe, retry and reservation remain the caller's job: a
    refusal here is a *notification* outcome and never a trade rejection.
    """
    if not isinstance(evidence, ActionableEvidence):
        raise TypeError("evidence must be an ActionableEvidence")
    applied = policy or ActionableNotificationPolicy()
    moment = _utc(now) or datetime.now(timezone.utc)

    evaluated = (
        (CONDITION_IDENTITY, _check_identity(evidence, policy=applied, now=moment)),
        (CONDITION_TRIGGER, _check_trigger(evidence, policy=applied)),
        (CONDITION_EXECUTION, _check_execution(evidence, policy=applied)),
        (CONDITION_GEOMETRY, _check_geometry(evidence, policy=applied, now=moment)),
        (CONDITION_ECONOMICS, _check_economics(evidence, policy=applied)),
        (CONDITION_RISK, _check_risk(evidence)),
        (CONDITION_DELIVERY, _check_delivery(evidence)),
    )
    failures = [(condition, reason) for condition, reason in evaluated if reason]
    expiry = _resolved_expiry(evidence, applied, now=moment)
    cost_pct = _number(evidence.round_trip_cost_pct)
    if cost_pct is None:
        cost_pct = applied.round_trip_cost_pct
    reference = _entry_reference(evidence)
    target = _number(evidence.target_1)
    net_rr = cost_adjusted_reward_to_risk(
        entry=reference,
        stop=_number(evidence.stop_price),
        target=target,
        round_trip_cost_pct=cost_pct,
    )
    net_edge: float | None = None
    if reference is not None and reference > 0 and target is not None:
        net_edge = projected_net_edge_pct(
            gross_target_move_pct=abs(target - reference) / reference * 100.0,
            round_trip_fee_pct=cost_pct,
        )
    common = {
        "policy_version": applied.version,
        "net_reward_to_risk": net_rr,
        "net_edge_pct": net_edge,
        "expires_at": expiry,
        "entry_zone_low": _number(evidence.entry_low),
        "entry_zone_high": _number(evidence.entry_high),
    }
    if failures:
        condition, reason = failures[0]
        return ActionableAdmission(
            admitted=False,
            condition=condition,
            reason=reason,
            reasons=_dedupe([failed for _, failed in failures]),
            **common,
        )
    return ActionableAdmission(admitted=True, **common)


def execution_evidence_from_snapshot(
    snapshot: Any,
    *,
    direction: str,
    order_notional_usd: Any = None,
) -> dict[str, Any]:
    """Extract the deterministic execution facts the scanner already validated.

    Nothing is computed here: ``spread_bps``, the measured visible round-trip market drag and the
    full-coverage flags come from
    :class:`app.scanner.execution_validation.ExecutionValidation`. A missing
    ``execution_validation`` yields an empty mapping so the admission gate refuses with
    ``EXECUTION_EVIDENCE_MISSING`` instead of a default pass.
    """
    execution = getattr(snapshot, "execution_validation", None)
    if execution is None:
        return {}
    trade_direction = str(direction or "").strip().upper()
    drag = _number(getattr(execution, "estimated_visible_round_trip_market_drag_pct", None))
    if trade_direction == "SHORT":
        short_drag = _number(
            getattr(execution, "estimated_visible_short_round_trip_market_drag_pct", None)
        )
        if drag is None:
            drag = short_drag
        covered = getattr(execution, "sell_fully_covered", None)
    else:
        covered = getattr(execution, "buy_fully_covered", None)
    return {
        "spread_bps": _number(getattr(execution, "spread_bps", None)),
        "size_slippage_pct": drag,
        "depth_covered": None if covered is None else bool(covered),
        "order_size_usd": _number(order_notional_usd),
        "validation_notional_usd": _number(
            getattr(execution, "validation_notional_usd", None)
        ),
    }


def evidence_from_trade_plan(
    candidate: Any,
    plan: Any,
    *,
    execution: Any = None,
    now: datetime | None = None,
    observed_at: Any = None,
) -> ActionableEvidence:
    """Map a qualified candidate and its existing plan onto :class:`ActionableEvidence`.

    Every value is read from evidence another component produced. Keys that are absent stay
    ``None`` so the gate fails closed with a specific reason rather than assuming a value.
    ``observed_at`` lets the caller state the scan's own evidence time without mutating the
    candidate artifact.
    """
    mapping: dict[str, Any] = candidate if isinstance(candidate, dict) else {}
    execution_map: dict[str, Any] = execution if isinstance(execution, dict) else {}
    moment = _utc(now) or datetime.now(timezone.utc)

    def _first(*names: str) -> Any:
        for name in names:
            value = mapping.get(name)
            if value is not None:
                return value
        return None

    def _exec_first(*names: str) -> Any:
        for name in names:
            value = execution_map.get(name)
            if value is not None:
                return value
            value = mapping.get(name)
            if value is not None:
                return value
        return None

    symbol = _text(getattr(plan, "symbol", None)) or _text(_first("symbol", "pair"))
    direction = (
        _text(getattr(plan, "direction", None))
        or _text(_first("direction", "trade_direction"))
        or "LONG"
    ).upper()
    observed_at_value = _utc(observed_at) or _utc(
        _first("evidence_observed_at", "observed_at", "generated_at", "alerted_at")
    )
    expires_at = _utc(_first("plan_expires_at", "expires_at", "entry_expires_at"))
    venue = (_text(_first("venue", "exchange")) or "KRAKEN").upper()

    def _flag(*names: str) -> bool | None:
        value = _first(*names)
        return None if value is None else bool(value)

    depth_covered = _exec_first("depth_covered")

    return ActionableEvidence(
        instrument=symbol,
        venue=venue,
        venue_identity_valid=bool(symbol) and venue == "KRAKEN",
        observed_at=observed_at_value or moment,
        expires_at=expires_at,
        episode_id=_text(
            _first("episode_id", "trade_id", "setup_id", "signal_id", "journey_id")
        ),
        material_change=_flag("material_change"),
        already_delivered=_flag("already_delivered", "notification_already_delivered"),
        direction=direction,
        entry_style=_text(getattr(plan, "entry_style", None)) or _text(_first("entry_style")),
        valid_now=None if getattr(plan, "valid_now", None) is None else bool(plan.valid_now),
        entry_trigger_confirmed=_flag("entry_trigger_confirmed"),
        too_extended=_flag("too_extended"),
        entry_price=_number(getattr(plan, "entry_high", None)),
        entry_low=_number(getattr(plan, "entry_low", None)),
        entry_high=_number(getattr(plan, "entry_high", None)),
        chase_limit=_number(getattr(plan, "chase_limit", None)),
        liquidity_usd=_number(
            _first(
                "liquidity_capacity_ceiling_usd",
                "liquidity_usd",
                "liquidity_24h_usd",
                "combined_24h_liquidity_usd",
            )
        ),
        spread_bps=_number(_exec_first("spread_bps")),
        size_slippage_pct=_number(_exec_first("size_slippage_pct")),
        order_size_usd=_number(
            _exec_first(
                "order_size_usd",
                "economic_position_notional",
                "position_notional_usd",
            )
        ),
        depth_covered=None if depth_covered is None else bool(depth_covered),
        stop_price=_number(getattr(plan, "stop_price", None)),
        target_1=_number(getattr(plan, "target_1", None)),
        target_2=_number(getattr(plan, "target_2", None)),
        invalidation=_text(
            _first("invalidation", "invalidation_reason") or getattr(plan, "reason", None)
        ),
        economics_available=_flag("economic_qualified"),
        reward_to_risk_1=_number(getattr(plan, "reward_to_risk_1", None)),
        round_trip_cost_pct=_number(_first("round_trip_cost_pct")),
        deterministic_qualified=_flag("economic_qualified"),
        risk_gate_passed=_flag("action_gate_allowed"),
        ai_override_attempted=_flag("ai_override_attempted"),
        funded_execution_authorized=_flag("funded_execution_authorized"),
        delivery_in_flight=_flag("delivery_in_flight", "notification_in_flight"),
    )


__all__ = [
    "ADMITTED",
    "ActionableAdmission",
    "ActionableEvidence",
    "ActionableNotificationPolicy",
    "CONDITION_DELIVERY",
    "CONDITION_ECONOMICS",
    "CONDITION_EXECUTION",
    "CONDITION_GEOMETRY",
    "CONDITION_IDENTITY",
    "CONDITION_RISK",
    "CONDITION_TRIGGER",
    "POLICY_VERSION",
    "SUPPRESSED",
    "cost_adjusted_reward_to_risk",
    "evaluate_actionable_admission",
    "evidence_from_trade_plan",
    "execution_evidence_from_snapshot",
]
