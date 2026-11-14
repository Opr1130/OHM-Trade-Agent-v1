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
CONDITION_DEPTH = "C3B_VERIFIED_DEPTH"
CONDITION_PROBABILITY = "C5B_CALIBRATED_PROBABILITY"
CONDITION_PROTECTION = "C6B_PROTECTION_ADMISSION"
CONDITION_ROLLING_CAP = "C7B_ROLLING_24H_CAP"

#: Reason codes. Each names what is missing rather than how a card could have been made to look.
REASON_VENUE_IDENTITY_INVALID = "VENUE_IDENTITY_INVALID"
REASON_EVIDENCE_TIMESTAMP_MISSING = "EVIDENCE_TIMESTAMP_MISSING"
REASON_EVIDENCE_STALE = "EVIDENCE_STALE"
REASON_DUPLICATE_EPISODE = "DUPLICATE_EPISODE"

REASON_DIRECTION_MISSING = "DIRECTION_MISSING"
REASON_NO_ENTRY_TRIGGER = "NO_ENTRY_TRIGGER"
REASON_TOO_EXTENDED = "TOO_EXTENDED_NOT_ENTRY"
REASON_ENTRY_ZONE_MISSING = "ENTRY_ZONE_MISSING"
REASON_ENTRY_PRICE_EVIDENCE_MISSING = "ENTRY_PRICE_EVIDENCE_MISSING"
REASON_ENTRY_NOT_FEASIBLE = "ENTRY_NOT_FEASIBLE"
REASON_LIMIT_ENTRY_NOT_EXECUTABLE = "LIMIT_ENTRY_NOT_YET_EXECUTABLE"

REASON_EXECUTION_EVIDENCE_MISSING = "EXECUTION_EVIDENCE_MISSING"
REASON_LIQUIDITY_INSUFFICIENT = "MARKET_LIQUIDITY_INSUFFICIENT"
REASON_CAPACITY_INSUFFICIENT = "POSITION_CAPACITY_INSUFFICIENT"
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

REASON_DEPTH_INSUFFICIENT = "MARKET_DEPTH_INSUFFICIENT"
REASON_DEPTH_UNKNOWN = "MARKET_DEPTH_EVIDENCE_UNKNOWN"
REASON_CONFIDENCE_NOT_CALIBRATED = "CONFIDENCE_NOT_CALIBRATED"
REASON_CONFIDENCE_INVALID = "CALIBRATED_PROBABILITY_INVALID"
REASON_CONFIDENCE_BELOW = "CALIBRATED_PROBABILITY_BELOW_THRESHOLD"
REASON_CALIBRATION_UNTRUSTED = "CALIBRATION_ARTIFACT_UNTRUSTED"
REASON_PROTECTION_SUSPENDED = "PROTECTION_ADMISSIONS_SUSPENDED"
REASON_PROTECTION_UNKNOWN = "PROTECTION_ADMISSIONS_SUSPENDED_UNKNOWN"
REASON_ROLLING_CAP = "ROLLING_24H_NOTIFICATION_CAP"
REASON_ROLLING_CAP_UNKNOWN = "ROLLING_24H_COUNT_UNKNOWN"

#: Entry styles that describe a limit entry at a planned zone. They are admitted only when the
#: limit is *executable now* (see the trigger rule); a plan still waiting for its condition is a
#: future setup, not an actionable card.
LIMIT_ENTRY_STYLES = frozenset(
    {
        "wait_for_pullback",
        "wait_for_rebound",
        "pullback_or_retest",
        "breakout_or_retest",
        "retest",
        "place_limit",
        "limit",
        "limit_entry",
    }
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
    #: Cost model. ``round_trip_fee_pct`` is fees/venue friction only; the measured, direction
    #: specific market impact travels separately in the evidence and is added exactly once, so no
    #: cost term is counted twice and the card states the same number the gate used.
    round_trip_fee_pct: float = 0.52
    #: Market 24h turnover floor. This is a *market* quality threshold and is never compared with
    #: the position-capacity ceiling, which is a different fact with a different test.
    min_market_liquidity_usd: float = 100_000.0
    #: Share of the venue's visible position-capacity ceiling the contemplated order may consume.
    max_capacity_utilization_pct: float = 100.0
    max_spread_bps: float = 50.0
    max_size_slippage_pct: float = 1.00
    require_fully_covered_depth: bool = True
    #: An executable limit entry is actionable; an unconfirmed future setup never is.
    admit_executable_limit_entries: bool = True
    require_target_2: bool = False
    # -- ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1 guards (all default OFF) --
    # An OFF guard adds no refusal, so the shipped notification policy is
    # unchanged until an owner activates a condition deliberately. No guard can
    # widen authority: each either refuses or does nothing.
    require_verified_depth: bool = False
    min_weaker_side_depth_usd: float = 600_000.0
    depth_band_pct: float = 0.50
    require_calibrated_probability: bool = False
    min_calibrated_probability: float = 0.90
    require_protection_admission: bool = False
    rolling_24h_max_new_entries: int = 0

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
            round_trip_fee_pct=_float(
                "opip_actionable_round_trip_fee_pct", base.round_trip_fee_pct
            ),
            min_market_liquidity_usd=_float(
                "opip_actionable_min_market_liquidity_usd", base.min_market_liquidity_usd
            ),
            max_capacity_utilization_pct=_float(
                "opip_actionable_max_capacity_utilization_pct",
                base.max_capacity_utilization_pct,
            ),
            max_spread_bps=_float("opip_actionable_max_spread_bps", base.max_spread_bps),
            max_size_slippage_pct=_float(
                "opip_actionable_max_size_slippage_pct", base.max_size_slippage_pct
            ),
            require_fully_covered_depth=_bool(
                "opip_actionable_require_fully_covered_depth",
                base.require_fully_covered_depth,
            ),
            admit_executable_limit_entries=_bool(
                "opip_actionable_admit_executable_limit_entries",
                base.admit_executable_limit_entries,
            ),
            require_target_2=_bool(
                "opip_actionable_require_target_2", base.require_target_2
            ),
            require_verified_depth=_bool(
                "opip_actionable_require_verified_depth",
                base.require_verified_depth,
            ),
            min_weaker_side_depth_usd=_float(
                "opip_actionable_min_weaker_side_depth_usd",
                base.min_weaker_side_depth_usd,
            ),
            depth_band_pct=_float(
                "opip_actionable_depth_band_pct", base.depth_band_pct
            ),
            require_calibrated_probability=_bool(
                "opip_actionable_require_calibrated_probability",
                base.require_calibrated_probability,
            ),
            min_calibrated_probability=_float(
                "opip_actionable_min_calibrated_probability",
                base.min_calibrated_probability,
            ),
            require_protection_admission=_bool(
                "opip_actionable_require_protection_admission",
                base.require_protection_admission,
            ),
            rolling_24h_max_new_entries=_int(
                "opip_actionable_rolling_24h_max_new_entries",
                base.rolling_24h_max_new_entries,
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
    #: The price the decision was actually taken at. Without it the gate cannot judge whether a
    #: limit is executable now, so it refuses instead of assuming one.
    current_price: float | None = None
    entry_price: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    chase_limit: float | None = None

    # C3 -- market and execution quality. Two different facts, two different tests:
    #:   * ``market_liquidity_usd``  -- 24h market turnover: is this market deep enough at all?
    #:   * ``position_capacity_usd`` -- largest order the visible book can absorb: does the
    #:     contemplated size fit?
    market_liquidity_usd: float | None = None
    position_capacity_usd: float | None = None
    order_size_usd: float | None = None
    spread_bps: float | None = None
    size_slippage_pct: float | None = None
    depth_covered: bool | None = None

    # C4 -- trade geometry
    stop_price: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    invalidation: str | None = None

    # C5 -- net economics
    economics_available: bool | None = None
    reward_to_risk_1: float | None = None
    #: Assumed venue fees for a round trip. The measured market impact is ``size_slippage_pct``
    #: above; the gate adds each term once and reports the total it used.
    round_trip_fee_pct: float | None = None

    # C6 -- risk and authority
    deterministic_qualified: bool | None = None
    risk_gate_passed: bool | None = None
    ai_override_attempted: bool | None = None
    funded_execution_authorized: bool | None = None

    # C7 -- delivery eligibility
    delivery_in_flight: bool | None = None

    # -- ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1 optional evidence --
    # None means the producer did not supply the fact; that refuses only when the
    # matching policy guard is enabled, so an off guard changes nothing.
    depth_status: str | None = None
    weaker_side_depth_usd: float | None = None
    depth_band_pct: float | None = None
    depth_evidence_reason: str | None = None
    calibrated_probability: float | None = None
    calibration_status: str | None = None
    calibration_model_version: str | None = None
    protection_admissions_suspended: bool | None = None
    protection_stamp_fresh: bool | None = None
    new_entry_admissions_last_24h: int | None = None


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
    #: Total round-trip cost actually applied to ``net_reward_to_risk``: assumed fees plus the
    #: measured, direction-specific market impact, each counted once.
    effective_cost_pct: float | None = None
    expires_at: datetime | None = None
    expiry_source: str | None = None
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    current_price: float | None = None
    entry_trigger_kind: str | None = None

    @property
    def admitted_plan(self) -> bool:
        return self.admitted

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
            "effective_cost_pct": self.effective_cost_pct,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "expiry_source": self.expiry_source,
            "entry_trigger_kind": self.entry_trigger_kind,
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


ENTRY_TRIGGER_MARKET = "MARKET_ENTRY_CONFIRMED"
ENTRY_TRIGGER_LIMIT = "EXECUTABLE_LIMIT_ENTRY"
ENTRY_TRIGGER_UNCONFIRMED = "UNCONFIRMED_FUTURE_SETUP"


def _entry_trigger_kind(
    evidence: ActionableEvidence, policy: ActionableNotificationPolicy
) -> tuple[str, str | None]:
    """Classify the trigger, separating an executable entry from a future setup.

    A **future setup** is a plan whose condition has not happened yet: the platform is still
    waiting for a pullback/rebound/retest, so there is nothing to execute. An **executable limit
    entry** is a resting order the operator can place now at a zone the quote has not run away
    from. The distinction is decided from authoritative upstream facts --
    ``entry_trigger_confirmed``, ``valid_now``, the plan's entry style, the analysed
    ``current_price`` and the chase boundary -- never from "a zone exists".
    """
    direction = str(evidence.direction or "").strip().upper()
    confirmed = evidence.entry_trigger_confirmed
    if confirmed is False:
        return ENTRY_TRIGGER_UNCONFIRMED, REASON_NO_ENTRY_TRIGGER

    current = _number(evidence.current_price)
    low = _number(evidence.entry_low)
    high = _number(evidence.entry_high)
    if current is None:
        # No analysed price: neither an ENTER NOW nor a placeable limit can be proven.
        return ENTRY_TRIGGER_UNCONFIRMED, REASON_ENTRY_PRICE_EVIDENCE_MISSING
    if low is None or high is None or low <= 0 or high <= 0 or low > high:
        return ENTRY_TRIGGER_UNCONFIRMED, REASON_ENTRY_ZONE_MISSING

    chase_limit = _number(evidence.chase_limit)
    style = str(evidence.entry_style or "").strip().lower()

    if evidence.valid_now is True or (confirmed is True and style not in LIMIT_ENTRY_STYLES):
        # Market entry: the quote must actually be inside the approved zone.
        if low <= current <= high:
            return ENTRY_TRIGGER_MARKET, None
        if chase_limit is not None:
            if direction == "LONG" and current > chase_limit:
                return ENTRY_TRIGGER_UNCONFIRMED, REASON_TOO_EXTENDED
            if direction == "SHORT" and current < chase_limit:
                return ENTRY_TRIGGER_UNCONFIRMED, REASON_TOO_EXTENDED
        return ENTRY_TRIGGER_UNCONFIRMED, REASON_ENTRY_NOT_FEASIBLE

    if style in LIMIT_ENTRY_STYLES and policy.admit_executable_limit_entries:
        # The limit is placeable now only while the quote has not left the executable window:
        # beyond the chase boundary the setup is too extended, and past the zone the entry has
        # already been traded through.
        if direction == "LONG":
            if chase_limit is not None and current > chase_limit:
                return ENTRY_TRIGGER_UNCONFIRMED, REASON_TOO_EXTENDED
            if current < low:
                return ENTRY_TRIGGER_UNCONFIRMED, REASON_ENTRY_NOT_FEASIBLE
        elif direction == "SHORT":
            if chase_limit is not None and current < chase_limit:
                return ENTRY_TRIGGER_UNCONFIRMED, REASON_TOO_EXTENDED
            if current > high:
                return ENTRY_TRIGGER_UNCONFIRMED, REASON_ENTRY_NOT_FEASIBLE
        else:
            return ENTRY_TRIGGER_UNCONFIRMED, REASON_DIRECTION_MISSING
        return ENTRY_TRIGGER_LIMIT, None

    if style in LIMIT_ENTRY_STYLES:
        return ENTRY_TRIGGER_UNCONFIRMED, REASON_LIMIT_ENTRY_NOT_EXECUTABLE
    return ENTRY_TRIGGER_UNCONFIRMED, REASON_NO_ENTRY_TRIGGER


def _check_trigger(
    evidence: ActionableEvidence,
    *,
    policy: ActionableNotificationPolicy,
) -> str | None:
    direction = str(evidence.direction or "").strip().upper()
    if direction not in _DIRECTIONS:
        return REASON_DIRECTION_MISSING
    if evidence.too_extended is True:
        return REASON_TOO_EXTENDED
    _, reason = _entry_trigger_kind(evidence, policy)
    return reason


def contemplated_order_notional_usd(candidate: dict[str, Any] | None) -> float | None:
    """The order size the card actually recommends, for the capacity test.

    The action gate may cut the allocation to the visible-book capacity ceiling and records the
    resulting notional as ``recommended_position_notional``. That is the order the operator is
    shown, so it is the order the capacity test must judge. When that field is PRESENT it is the
    only basis: a value that is not a positive finite number is an inconsistent payload and yields
    ``None`` (the caller refuses on ``ORDER_SIZE_UNUSABLE``) -- it never falls back to the larger
    pre-cap size. ``economic_position_notional`` is used only when the action gate recorded no
    recommended size at all (field absent or ``None``), which preserves the previous behaviour for
    candidates it did not size.
    """
    if not isinstance(candidate, dict):
        return None

    def _positive(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(float(value)) and float(value) > 0 else None

    recommended = candidate.get("recommended_position_notional")
    if recommended is not None:
        return _positive(recommended)
    return _positive(candidate.get("economic_position_notional"))


def _check_execution(
    evidence: ActionableEvidence, *, policy: ActionableNotificationPolicy
) -> str | None:
    """Market quality and size feasibility, tested separately.

    ``market_liquidity_usd`` answers "is this market deep enough at all" and is compared with the
    configured market floor. ``position_capacity_usd`` answers "can the visible book absorb the
    contemplated order" and is compared with the order size -- never with the market floor.
    Missing evidence blocks; nothing passes by default.
    """
    market_liquidity = _number(evidence.market_liquidity_usd)
    if market_liquidity is None:
        return REASON_EXECUTION_EVIDENCE_MISSING
    if market_liquidity < policy.min_market_liquidity_usd:
        return REASON_LIQUIDITY_INSUFFICIENT

    capacity = _number(evidence.position_capacity_usd)
    if capacity is None:
        return REASON_EXECUTION_EVIDENCE_MISSING
    order_size = _number(evidence.order_size_usd)
    if order_size is None or order_size <= 0:
        return REASON_ORDER_SIZE_UNUSABLE
    allowed_notional = capacity * max(0.0, policy.max_capacity_utilization_pct) / 100.0
    if capacity <= 0 or order_size > allowed_notional:
        return REASON_CAPACITY_INSUFFICIENT

    spread = _number(evidence.spread_bps)
    if spread is None or spread < 0:
        return REASON_EXECUTION_EVIDENCE_MISSING
    if spread > policy.max_spread_bps:
        return REASON_SPREAD_TOO_WIDE
    slippage = _number(evidence.size_slippage_pct)
    if slippage is None or slippage < 0:
        return REASON_EXECUTION_EVIDENCE_MISSING
    if slippage > policy.max_size_slippage_pct:
        return REASON_SIZE_SLIPPAGE_EXCESSIVE
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


def effective_round_trip_cost_pct(
    evidence: ActionableEvidence, policy: ActionableNotificationPolicy
) -> float | None:
    """Total round-trip cost used by *both* the net R:R and the rendered card.

    Assumed venue fees (``round_trip_fee_pct``) and the measured, direction-specific market
    impact (``size_slippage_pct``) are added exactly once each. A measured impact that is absent
    cannot be defaulted, so the result is ``None`` and the gate refuses.
    """
    fee = _number(evidence.round_trip_fee_pct)
    if fee is None:
        fee = policy.round_trip_fee_pct
    drag = _number(evidence.size_slippage_pct)
    if fee is None or drag is None or fee < 0 or drag < 0:
        return None
    return fee + drag


def _check_economics(
    evidence: ActionableEvidence, *, policy: ActionableNotificationPolicy
) -> str | None:
    """Cost-adjusted economics. The floor is a *net reward-to-risk* policy, not a fixed profit."""
    if evidence.economics_available is not True:
        return REASON_ECONOMICS_UNAVAILABLE
    reference = _entry_reference(evidence)
    cost_pct = effective_round_trip_cost_pct(evidence, policy)
    if cost_pct is None:
        return REASON_NET_RR_NOT_COMPUTABLE
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


def _check_depth(evidence: ActionableEvidence, *, policy) -> str | None:
    """Verify that a depth PASS also proves this policy's threshold and band."""
    if not policy.require_verified_depth:
        return None
    status = str(evidence.depth_status or "").strip().upper()
    if status == "FAIL":
        return REASON_DEPTH_INSUFFICIENT
    if status != "PASS":
        return REASON_DEPTH_UNKNOWN

    depth = _number(evidence.weaker_side_depth_usd)
    measured_band = _number(evidence.depth_band_pct)
    minimum = _number(policy.min_weaker_side_depth_usd)
    allowed_band = _number(policy.depth_band_pct)
    if (depth is None or depth < 0 or
            measured_band is None or measured_band <= 0 or
            minimum is None or minimum <= 0 or
            allowed_band is None or allowed_band <= 0):
        return REASON_DEPTH_UNKNOWN
    # A measurement within a narrower band remains a valid lower-bound proof.
    if measured_band > allowed_band:
        return REASON_DEPTH_UNKNOWN
    if depth < minimum:
        return REASON_DEPTH_INSUFFICIENT
    return None


def _check_probability(evidence: ActionableEvidence, *, policy) -> str | None:
    """Calibrated probability. A heuristic score can never satisfy this."""
    if not policy.require_calibrated_probability:
        return None
    if str(evidence.calibration_status or "").strip().upper() != "CALIBRATED":
        return REASON_CONFIDENCE_NOT_CALIBRATED
    probability = _number(evidence.calibrated_probability)
    if probability is None or probability < 0.0 or probability > 1.0:
        return REASON_CONFIDENCE_INVALID
    if not _text(evidence.calibration_model_version):
        return REASON_CALIBRATION_UNTRUSTED
    if probability < policy.min_calibrated_probability:
        return REASON_CONFIDENCE_BELOW
    return None


def _check_protection(evidence: ActionableEvidence, *, policy) -> str | None:
    """F11 admission state. Read-only: this can only ever refuse."""
    if not policy.require_protection_admission:
        return None
    if evidence.protection_stamp_fresh is not True:
        return REASON_PROTECTION_UNKNOWN
    suspended = evidence.protection_admissions_suspended
    if suspended is None:
        return REASON_PROTECTION_UNKNOWN
    if suspended is True:
        return REASON_PROTECTION_SUSPENDED
    return None


def _check_rolling_cap(evidence: ActionableEvidence, *, policy) -> str | None:
    """Rolling 24h new-entry cap. 0 disables; an unknown count refuses."""
    cap = int(policy.rolling_24h_max_new_entries or 0)
    if cap <= 0:
        return None
    count = evidence.new_entry_admissions_last_24h
    if count is None:
        return REASON_ROLLING_CAP_UNKNOWN
    if int(count) >= cap:
        return REASON_ROLLING_CAP
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
        (CONDITION_DEPTH, _check_depth(evidence, policy=applied)),
        (CONDITION_GEOMETRY, _check_geometry(evidence, policy=applied, now=moment)),
        (CONDITION_ECONOMICS, _check_economics(evidence, policy=applied)),
        (CONDITION_PROBABILITY, _check_probability(evidence, policy=applied)),
        (CONDITION_RISK, _check_risk(evidence)),
        (CONDITION_PROTECTION, _check_protection(evidence, policy=applied)),
        (CONDITION_DELIVERY, _check_delivery(evidence)),
        (CONDITION_ROLLING_CAP, _check_rolling_cap(evidence, policy=applied)),
    )
    failures = [(condition, reason) for condition, reason in evaluated if reason]
    expiry = _resolved_expiry(evidence, applied, now=moment)
    expiry_source = (
        "explicit"
        if _utc(evidence.expires_at) is not None
        else ("policy_validity_window" if expiry is not None else None)
    )
    cost_pct = effective_round_trip_cost_pct(evidence, applied)
    reference = _entry_reference(evidence)
    target = _number(evidence.target_1)
    net_rr: float | None = None
    net_edge: float | None = None
    if cost_pct is not None:
        net_rr = cost_adjusted_reward_to_risk(
            entry=reference,
            stop=_number(evidence.stop_price),
            target=target,
            round_trip_cost_pct=cost_pct,
        )
        if reference is not None and reference > 0 and target is not None:
            net_edge = projected_net_edge_pct(
                gross_target_move_pct=abs(target - reference) / reference * 100.0,
                round_trip_fee_pct=cost_pct,
            )
    trigger_kind, _ = _entry_trigger_kind(evidence, applied)
    common = {
        "policy_version": applied.version,
        "net_reward_to_risk": net_rr,
        "net_edge_pct": net_edge,
        "effective_cost_pct": cost_pct,
        "expires_at": expiry,
        "expiry_source": expiry_source,
        "entry_zone_low": _number(evidence.entry_low),
        "entry_zone_high": _number(evidence.entry_high),
        "current_price": _number(evidence.current_price),
        "entry_trigger_kind": trigger_kind,
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

    Nothing is computed here. Every value is the venue's own measurement from
    :class:`app.scanner.execution_validation.ExecutionValidation`, and the two direction-specific
    facts are read from the matching leg, because a LONG entry buys into the asks while a SHORT
    entry sells into the bids:

    * LONG  -> ``buy_fully_covered`` and the long round-trip drag;
    * SHORT -> ``sell_fully_covered`` and the short round-trip drag.

    A missing ``execution_validation`` yields an empty mapping so the gate refuses with
    ``EXECUTION_EVIDENCE_MISSING`` instead of a default pass.
    """
    execution = getattr(snapshot, "execution_validation", None)
    if execution is None:
        return {}
    trade_direction = str(direction or "").strip().upper()
    if trade_direction == "SHORT":
        drag = _number(
            getattr(execution, "estimated_visible_short_round_trip_market_drag_pct", None)
        )
        covered = getattr(execution, "sell_fully_covered", None)
    else:
        drag = _number(getattr(execution, "estimated_visible_round_trip_market_drag_pct", None))
        covered = getattr(execution, "buy_fully_covered", None)
    return {
        "spread_bps": _number(getattr(execution, "spread_bps", None)),
        "size_slippage_pct": drag,
        "depth_covered": None if covered is None else bool(covered),
        "current_price": _number(getattr(snapshot, "last_price", None)),
        # Market 24h turnover is a different fact from the position-capacity ceiling; the producer
        # supplies the turnover here and the ceiling through its own capacity evidence.
        "market_liquidity_usd": _number(
            getattr(snapshot, "combined_24h_liquidity_usd", None)
        ),
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
    observed_at: Any = None,
) -> ActionableEvidence:
    """Map a qualified candidate and its existing plan onto :class:`ActionableEvidence`.

    Every value is read from evidence another component produced. Nothing is defaulted here: the
    venue and its identity verdict, the evidence timestamp, the analysed price, the market
    liquidity, the position-capacity ceiling and the measured impact all stay ``None`` when the
    upstream producer did not supply them, so the gate fails closed with a specific reason
    instead of synthesising a pass. ``observed_at`` lets the caller state the scan's own evidence
    time without mutating the candidate artifact.
    """
    mapping: dict[str, Any] = candidate if isinstance(candidate, dict) else {}
    execution_map: dict[str, Any] = execution if isinstance(execution, dict) else {}

    def _first(*names: str) -> Any:
        for name in names:
            value = mapping.get(name)
            if value is not None:
                return value
        return None

    def _order_size_basis() -> Any:
        # One basis for the capacity test. A producer that states ``order_size_usd`` (the scan always
        # does, from contemplated_order_notional_usd) has decided the order size: ``None`` there means
        # "unusable" and must refuse, not fall through to the larger pre-cap economic size. Only
        # callers that supply no such key use the shared candidate rule.
        if "order_size_usd" in execution_map:
            return execution_map["order_size_usd"]
        explicit = _first("order_size_usd", "position_notional_usd")
        return explicit if explicit is not None else contemplated_order_notional_usd(mapping)

    def _exec_first(*names: str) -> Any:
        for name in names:
            value = execution_map.get(name)
            if value is not None:
                return value
            value = mapping.get(name)
            if value is not None:
                return value
        return None

    def _flag(*names: str) -> bool | None:
        value = _first(*names)
        return None if value is None else bool(value)

    def _exec_flag(*names: str) -> bool | None:
        value = _exec_first(*names)
        return None if value is None else bool(value)

    symbol = _text(getattr(plan, "symbol", None)) or _text(_first("symbol", "pair"))
    direction = (
        _text(getattr(plan, "direction", None))
        or _text(_first("direction", "trade_direction"))
        or "LONG"
    ).upper()
    depth_covered = _exec_first("depth_covered")
    return ActionableEvidence(
        instrument=symbol,
        # Authoritative venue identity supplied by the producer that owns the book/snapshot.
        venue=_text(_exec_first("venue", "execution_venue")),
        venue_identity_valid=_exec_flag("venue_identity_valid"),
        observed_at=_utc(observed_at)
        or _utc(_first("evidence_observed_at", "observed_at", "generated_at", "alerted_at")),
        expires_at=_utc(_first("plan_expires_at", "expires_at", "entry_expires_at")),
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
        current_price=_number(
            _exec_first("current_price", "reference_price", "price", "last_price")
        ),
        entry_price=_number(getattr(plan, "entry_high", None)),
        entry_low=_number(getattr(plan, "entry_low", None)),
        entry_high=_number(getattr(plan, "entry_high", None)),
        chase_limit=_number(getattr(plan, "chase_limit", None)),
        # Market turnover and position capacity are distinct facts and are never aliased.
        market_liquidity_usd=_number(
            _exec_first(
                "market_liquidity_usd",
                "combined_24h_liquidity_usd",
                "liquidity_24h_usd",
                "liquidity_usd",
            )
        ),
        position_capacity_usd=_number(
            _exec_first("position_capacity_usd", "liquidity_capacity_ceiling_usd")
        ),
        order_size_usd=_number(_order_size_basis()),
        spread_bps=_number(_exec_first("spread_bps")),
        size_slippage_pct=_number(_exec_first("size_slippage_pct")),
        depth_covered=None if depth_covered is None else bool(depth_covered),
        stop_price=_number(getattr(plan, "stop_price", None)),
        target_1=_number(getattr(plan, "target_1", None)),
        target_2=_number(getattr(plan, "target_2", None)),
        invalidation=_text(
            _first("invalidation", "invalidation_reason")
            or getattr(plan, "reason", None)
        ),
        economics_available=_flag("economic_qualified"),
        reward_to_risk_1=_number(getattr(plan, "reward_to_risk_1", None)),
        round_trip_fee_pct=_number(_first("round_trip_fee_pct")),
        deterministic_qualified=_flag("economic_qualified"),
        risk_gate_passed=_flag("action_gate_allowed"),
        ai_override_attempted=_flag("ai_override_attempted"),
        funded_execution_authorized=_flag("funded_execution_authorized"),
        delivery_in_flight=_flag("delivery_in_flight", "notification_in_flight"),
        depth_status=_text(_exec_first("depth_status")),
        weaker_side_depth_usd=_number(_exec_first("weaker_side_depth_usd")),
        depth_band_pct=_number(_exec_first("depth_band_pct")),
        depth_evidence_reason=_text(_exec_first("depth_evidence_reason")),
        calibrated_probability=_number(_exec_first("calibrated_probability")),
        calibration_status=_text(_exec_first("calibration_status")),
        calibration_model_version=_text(_exec_first("calibration_model_version")),
        protection_admissions_suspended=_exec_flag("protection_admissions_suspended"),
        protection_stamp_fresh=_exec_flag("protection_stamp_fresh"),
        new_entry_admissions_last_24h=(
            None
            if _exec_first("new_entry_admissions_last_24h") is None
            else int(_exec_first("new_entry_admissions_last_24h"))
        ),
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
    "ENTRY_TRIGGER_LIMIT",
    "ENTRY_TRIGGER_MARKET",
    "ENTRY_TRIGGER_UNCONFIRMED",
    "LIMIT_ENTRY_STYLES",
    "POLICY_VERSION",
    "SUPPRESSED",
    "SUPPORTED_VENUES",
    "contemplated_order_notional_usd",
    "cost_adjusted_reward_to_risk",
    "effective_round_trip_cost_pct",
    "evaluate_actionable_admission",
    "evidence_from_trade_plan",
    "execution_evidence_from_snapshot",
]
