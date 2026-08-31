"""Deterministic O'Pip Event Risk policy (Sequence 3).

This module is pure. It performs no I/O, makes no network call, consults no
model and holds no state. Given identical inputs it always produces an
identical outcome, which is what makes replay meaningful.

HARD INVARIANT
    DETERMINISTIC SAFETY WARNING  >  AI / LLM / external intelligence opinion

Nothing outside this module may lower a risk state that this module raised.
Degraded provider health is recorded as reduced evidence confidence; it never
reduces a risk state, because the absence of evidence is not evidence of
safety.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.opip.events.contract import EventSeverity, EventType
from app.opip.events.provider_health import ProviderHealthState
from app.opip.risk.contract import (
    Direction,
    EvidenceConfidence,
    ExposureView,
    POLICY_VERSION,
    Relevance,
    RiskState,
    risk_state_rank,
)


MAX_STATE_RANK = 4
MAX_SEVERITY_RANK = 4


# An event older than this is stale for escalation purposes. Stale evidence
# may still inform WATCH, but it cannot by itself raise an urgent review.
DEFAULT_STALE_EVENT_SECONDS = 24 * 60 * 60

SEVERITY_RANK: dict[EventSeverity, int] = {
    EventSeverity.INFO: 0,
    EventSeverity.LOW: 1,
    EventSeverity.MEDIUM: 2,
    EventSeverity.HIGH: 3,
    EventSeverity.CRITICAL: 4,
}

# Directional polarity of an event type, independent of exposure direction.
NEGATIVE_EVENT_TYPES = frozenset(
    {
        EventType.NEWS_SECURITY,
        EventType.NEWS_REGULATORY,
        EventType.TOKEN_UNLOCK,
    }
)
POSITIVE_EVENT_TYPES = frozenset(
    {
        EventType.NEWS_LISTING,
        EventType.LISTING,
        EventType.MAINNET,
        EventType.PARTNERSHIP,
        EventType.AIRDROP,
    }
)

DEGRADED_HEALTH_STATES = frozenset(
    {
        ProviderHealthState.STALE,
        ProviderHealthState.RATE_LIMITED,
        ProviderHealthState.DEGRADED,
    }
)
UNAVAILABLE_HEALTH_STATES = frozenset(
    {
        ProviderHealthState.UNAVAILABLE,
        ProviderHealthState.MISSING_CREDENTIALS,
    }
)


class Polarity(str):
    ADVERSE = "ADVERSE"
    SUPPORTIVE = "SUPPORTIVE"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class PolicyInputs:
    exposure: ExposureView
    relevance: Relevance
    event_type: EventType
    event_severity: EventSeverity
    freshness_seconds: float | None
    provider_health_state: ProviderHealthState | None = None
    stale_event_seconds: int = DEFAULT_STALE_EVENT_SECONDS

    @property
    def stale(self) -> bool:
        if self.freshness_seconds is None:
            # Unknown age is treated as stale for escalation. It is never
            # treated as absence of risk.
            return True
        return float(self.freshness_seconds) > float(self.stale_event_seconds)


@dataclass(frozen=True)
class PolicyOutcome:
    risk_state: RiskState
    risk_score: float
    reasons: tuple[str, ...]
    rules_triggered: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence_confidence: EvidenceConfidence
    policy_version: str = POLICY_VERSION


def event_polarity(event_type: EventType) -> str:
    if event_type in NEGATIVE_EVENT_TYPES:
        return Polarity.ADVERSE
    if event_type in POSITIVE_EVENT_TYPES:
        return Polarity.SUPPORTIVE
    return Polarity.NEUTRAL


def directional_polarity(event_type: EventType, direction: Direction) -> str:
    """Resolve event polarity against exposure direction.

    A negative token event harms a LONG and may support a SHORT. A positive
    catalyst harms a SHORT. This is why the shield never implements
    "bad news means exit everything".
    """
    base = event_polarity(event_type)
    if base == Polarity.NEUTRAL:
        return Polarity.NEUTRAL
    if direction == Direction.LONG:
        return Polarity.ADVERSE if base == Polarity.ADVERSE else Polarity.SUPPORTIVE
    return Polarity.SUPPORTIVE if base == Polarity.ADVERSE else Polarity.ADVERSE


def _evidence_confidence(
    state: ProviderHealthState | None,
) -> tuple[EvidenceConfidence, tuple[str, ...]]:
    if state is None:
        return EvidenceConfidence.NORMAL, ()
    if state in UNAVAILABLE_HEALTH_STATES:
        return (
            EvidenceConfidence.UNAVAILABLE,
            (
                f"PROVIDER_COVERAGE_UNAVAILABLE:{state.value}",
                "ABSENT_EVIDENCE_IS_NOT_A_CLEAN_BILL_OF_HEALTH",
            ),
        )
    if state in DEGRADED_HEALTH_STATES:
        return (
            EvidenceConfidence.DEGRADED,
            (f"PROVIDER_EVIDENCE_DEGRADED:{state.value}",),
        )
    # HEALTHY and NO_EVENT are both normal. NO_EVENT means the provider
    # reported nothing, not that the provider failed.
    return EvidenceConfidence.NORMAL, ()


def _score(state: RiskState, severity: EventSeverity) -> float:
    """Bounded deterministic severity in [0.0, 1.0].

    This is an ordering/telemetry aid for later outcome analysis. It is not a
    trading threshold and must never be used as one.
    """
    state_component = risk_state_rank(state) / MAX_STATE_RANK * 0.8
    severity_component = SEVERITY_RANK[severity] / MAX_SEVERITY_RANK * 0.2
    return round(min(1.0, max(0.0, state_component + severity_component)), 4)


def _direct_asset_state(
    inputs: PolicyInputs,
    polarity: str,
) -> tuple[RiskState, tuple[str, ...], tuple[str, ...]]:
    severity = SEVERITY_RANK[inputs.event_severity]
    pending = inputs.exposure.pending
    event_type = inputs.event_type

    if polarity == Polarity.ADVERSE:
        if pending:
            if severity >= SEVERITY_RANK[EventSeverity.MEDIUM]:
                return (
                    RiskState.AVOID_NEW_ENTRY,
                    ("R110_PENDING_ADVERSE_DIRECT_EVENT",),
                    (
                        "Adverse direct-asset event against a pending entry; "
                        "new entry is not advised until reviewed.",
                    ),
                )
            return (RiskState.NONE, (), ())

        if (
            event_type == EventType.NEWS_SECURITY
            and severity >= SEVERITY_RANK[EventSeverity.CRITICAL]
        ):
            return (
                RiskState.EXIT_REVIEW,
                ("R100_CRITICAL_SECURITY_DIRECT_ADVERSE",),
                (
                    "Critical security/exploit event directly affects this open "
                    "exposure's asset in the adverse direction.",
                ),
            )
        if severity >= SEVERITY_RANK[EventSeverity.CRITICAL]:
            return (
                RiskState.EXIT_REVIEW,
                ("R101_CRITICAL_DIRECT_ADVERSE",),
                (
                    "Critical adverse direct-asset event against an open "
                    "exposure.",
                ),
            )
        if severity >= SEVERITY_RANK[EventSeverity.HIGH]:
            return (
                RiskState.PROTECT_REVIEW,
                ("R102_HIGH_DIRECT_ADVERSE",),
                (
                    "High-severity adverse direct-asset event; protective "
                    "review of stop and size is advised.",
                ),
            )
        if severity >= SEVERITY_RANK[EventSeverity.MEDIUM]:
            return (
                RiskState.WATCH,
                ("R103_MEDIUM_DIRECT_ADVERSE",),
                ("Medium-severity adverse direct-asset event.",),
            )
        return (RiskState.NONE, (), ())

    if polarity == Polarity.SUPPORTIVE:
        # A supportive event is not risk-free: it can still bring volatility
        # and gap risk, and it can invalidate the original thesis timing.
        if severity >= SEVERITY_RANK[EventSeverity.CRITICAL]:
            return (
                RiskState.WATCH,
                ("R120_CRITICAL_DIRECT_SUPPORTIVE_VOLATILITY",),
                (
                    "Critical supportive event may still raise volatility and "
                    "gap risk on this exposure.",
                ),
            )
        return (RiskState.NONE, (), ())

    if severity >= SEVERITY_RANK[EventSeverity.CRITICAL]:
        if pending:
            return (
                RiskState.AVOID_NEW_ENTRY,
                ("R130_CRITICAL_DIRECT_NEUTRAL_PENDING",),
                (
                    "Critical direct-asset event of undetermined direction; "
                    "new entry is not advised until reviewed.",
                ),
            )
        return (
            RiskState.WATCH,
            ("R131_CRITICAL_DIRECT_NEUTRAL_OPEN",),
            ("Critical direct-asset event of undetermined direction.",),
        )
    return (RiskState.NONE, (), ())


def _broad_scope_state(
    inputs: PolicyInputs,
) -> tuple[RiskState, tuple[str, ...], tuple[str, ...]]:
    """Deterministic handling of non-asset-specific relevance classes."""
    severity = SEVERITY_RANK[inputs.event_severity]
    relevance = inputs.relevance
    pending = inputs.exposure.pending

    if severity < SEVERITY_RANK[EventSeverity.HIGH]:
        return (RiskState.NONE, (), ())

    if relevance == Relevance.MARKET_WIDE:
        if pending:
            return (
                RiskState.AVOID_NEW_ENTRY,
                ("R200_MARKET_WIDE_PENDING",),
                (
                    "High-severity market-wide event; new entry is not advised "
                    "until reviewed.",
                ),
            )
        return (
            RiskState.WATCH,
            ("R201_MARKET_WIDE_OPEN",),
            ("High-severity market-wide event affects open exposure context.",),
        )

    if relevance == Relevance.VENUE:
        if pending:
            return (
                RiskState.AVOID_NEW_ENTRY,
                ("R210_VENUE_PENDING",),
                (
                    "High-severity venue event on the exposure's venue; new "
                    "entry is not advised until reviewed.",
                ),
            )
        return (
            RiskState.WATCH,
            ("R211_VENUE_OPEN",),
            ("High-severity venue event on the exposure's venue.",),
        )

    if relevance in {Relevance.ECOSYSTEM, Relevance.MACRO}:
        return (
            RiskState.WATCH,
            (
                "R220_ECOSYSTEM_CONTEXT"
                if relevance == Relevance.ECOSYSTEM
                else "R221_MACRO_CONTEXT",
            ),
            (f"High-severity {relevance.value.lower()} context event.",),
        )

    return (RiskState.NONE, (), ())


def evaluate(inputs: PolicyInputs) -> PolicyOutcome:
    """Deterministically decide the advisory risk state for one pairing."""
    confidence, health_warnings = _evidence_confidence(inputs.provider_health_state)
    warnings: list[str] = list(health_warnings)

    if inputs.relevance == Relevance.UNRELATED:
        return PolicyOutcome(
            risk_state=RiskState.NONE,
            risk_score=0.0,
            reasons=(),
            rules_triggered=("R000_UNRELATED_EVENT",),
            warnings=tuple(warnings),
            evidence_confidence=confidence,
        )

    polarity = directional_polarity(inputs.event_type, inputs.exposure.direction)

    if inputs.relevance == Relevance.DIRECT_ASSET:
        state, rules, reasons = _direct_asset_state(inputs, polarity)
    else:
        state, rules, reasons = _broad_scope_state(inputs)

    rules_list = list(rules)
    reasons_list = list(reasons)

    # Stale evidence may inform, but may not by itself raise urgency above
    # WATCH. This rule can only lower an escalation driven by old evidence;
    # it never suppresses a warning entirely.
    if inputs.stale and risk_state_rank(state) > risk_state_rank(RiskState.WATCH):
        state = RiskState.WATCH
        rules_list.append("R010_STALE_EVENT_ESCALATION_CAPPED")
        reasons_list.append(
            "Evidence is older than the freshness window; urgency is capped at "
            "WATCH pending fresher confirmation."
        )
        warnings.append("STALE_EVENT_ESCALATION_CAPPED")

    if confidence != EvidenceConfidence.NORMAL:
        # Recorded as context only. Provider health never lowers a state.
        reasons_list.append(
            "Evidence confidence is reduced by provider health; risk state was "
            "not lowered."
        )

    return PolicyOutcome(
        risk_state=state,
        risk_score=_score(state, inputs.event_severity),
        reasons=tuple(reasons_list),
        rules_triggered=tuple(rules_list),
        warnings=tuple(warnings),
        evidence_confidence=confidence,
    )
