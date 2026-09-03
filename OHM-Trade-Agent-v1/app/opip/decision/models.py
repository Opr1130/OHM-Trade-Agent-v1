"""Typed domain objects for a single O'Pip admission decision.

One decision is represented once. A ``GateResult`` is what one gate concluded
about one candidate; an ``AdmissionDecision`` is the ordered sequence of those
results plus the terminal attribution. Both serialise deterministically (sorted
keys, no NaN/Inf) so the append-only JSONL streams are diffable and joinable.

These objects carry decisions and evidence only. They hold no exchange client,
no credentials, and expose no method that can place, modify, or cancel an
order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from app.opip.decision.versioning import (
    GATE_POLICY_VERSION,
    INTELLIGENCE_VERSION,
    STRATEGY_VERSION,
)


class GateStatus(str, Enum):
    """What a gate concluded."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class DecisionOutcome(str, Enum):
    """Terminal state of one candidate in one scan.

    ``COUNTERFACTUAL_ELIGIBLE`` is reserved for a future build. Build 1 records
    counterfactual *eligibility* as a flag on a REJECTED decision and never
    assigns this outcome; see ``AdmissionDecision.counterfactual_eligible`` and
    the counterfactual isolation test.
    """

    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"
    OPERATIONAL_FAILURE = "OPERATIONAL_FAILURE"
    INCOMPLETE = "INCOMPLETE"
    COUNTERFACTUAL_ELIGIBLE = "COUNTERFACTUAL_ELIGIBLE"


class ReasonClass(str, Enum):
    """Why a candidate stopped, at a coarser grain than the reason code.

    These are not equivalent events and must never be collapsed into one
    another: a policy rejection means the system worked and said no, an
    operational failure means the system could not answer, a budget
    suppression means the system declined to ask, and a model result means the
    model answered and the answer did not clear a threshold.
    """

    POLICY = "POLICY"
    OPERATIONAL = "OPERATIONAL"
    BUDGET = "BUDGET"
    MODEL = "MODEL"
    INFORMATIONAL = "INFORMATIONAL"


class GateName(str, Enum):
    """Ordered qualification funnel stages, from candidate creation onward."""

    CANDIDATE_CREATED = "CANDIDATE_CREATED"
    DIRECTION_SELECTED = "DIRECTION_SELECTED"
    MARGIN_ELIGIBILITY = "MARGIN_ELIGIBILITY"
    EXECUTION_VALIDATION = "EXECUTION_VALIDATION"
    CROSS_MARKET_CONFIRMATION = "CROSS_MARKET_CONFIRMATION"
    REFERENCE_VALIDATION = "REFERENCE_VALIDATION"
    MARKET_INTELLIGENCE = "MARKET_INTELLIGENCE"
    DETERMINISTIC_QUALITY = "DETERMINISTIC_QUALITY"
    AI_ELIGIBILITY = "AI_ELIGIBILITY"
    AI_INVOCATION = "AI_INVOCATION"
    AI_RESULT = "AI_RESULT"
    AI_CONFIDENCE = "AI_CONFIDENCE"
    RECOMMENDATION_GATE = "RECOMMENDATION_GATE"
    TRADE_QUALITY = "TRADE_QUALITY"
    TARGET_QUALITY = "TARGET_QUALITY"
    ECONOMIC_QUALITY = "ECONOMIC_QUALITY"
    CAPITAL_PORTFOLIO_GATE = "CAPITAL_PORTFOLIO_GATE"
    FINAL_QUALIFICATION = "FINAL_QUALIFICATION"
    PAPER_ADMISSION_ELIGIBILITY = "PAPER_ADMISSION_ELIGIBILITY"


#: Canonical evaluation order. The engine walks this sequence and the funnel
#: uses it to decide which stage is "deepest reached" in a scan.
GATE_ORDER: tuple[GateName, ...] = (
    GateName.CANDIDATE_CREATED,
    GateName.DIRECTION_SELECTED,
    GateName.MARGIN_ELIGIBILITY,
    GateName.EXECUTION_VALIDATION,
    GateName.CROSS_MARKET_CONFIRMATION,
    GateName.REFERENCE_VALIDATION,
    GateName.MARKET_INTELLIGENCE,
    GateName.DETERMINISTIC_QUALITY,
    GateName.AI_ELIGIBILITY,
    GateName.AI_INVOCATION,
    GateName.AI_RESULT,
    GateName.AI_CONFIDENCE,
    GateName.RECOMMENDATION_GATE,
    GateName.TRADE_QUALITY,
    GateName.TARGET_QUALITY,
    GateName.ECONOMIC_QUALITY,
    GateName.CAPITAL_PORTFOLIO_GATE,
    GateName.FINAL_QUALIFICATION,
    GateName.PAPER_ADMISSION_ELIGIBILITY,
)

GATE_INDEX: dict[GateName, int] = {gate: index for index, gate in enumerate(GATE_ORDER)}

#: The first gate at which a full entry/exit plan exists for the candidate, so
#: a future counterfactual build could replay it. Used only to record
#: eligibility; Build 1 never routes a counterfactual trade.
COUNTERFACTUAL_EVIDENCE_GATE = GateName.DETERMINISTIC_QUALITY


class ReasonCode(str, Enum):
    """Machine-readable terminal/step reason.

    Every code maps to exactly one :class:`ReasonClass` through
    :data:`REASON_CLASSES`.
    """

    # Lifecycle / pass
    CANDIDATE_ADMITTED = "CANDIDATE_ADMITTED"
    GATE_PASSED = "GATE_PASSED"
    QUALIFIED = "QUALIFIED"

    # Policy rejections
    MARGIN_INELIGIBLE = "MARGIN_INELIGIBLE"
    EXECUTION_VALIDATION_FAILED = "EXECUTION_VALIDATION_FAILED"
    SHORT_EXECUTION_QUALITY_FAILED = "SHORT_EXECUTION_QUALITY_FAILED"
    CROSS_MARKET_UNCONFIRMED = "CROSS_MARKET_UNCONFIRMED"
    DETERMINISTIC_VIABILITY_FAILED = "DETERMINISTIC_VIABILITY_FAILED"
    TRADE_QUALITY_REJECTED = "TRADE_QUALITY_REJECTED"
    TARGET_ATTAINABILITY_FAILED = "TARGET_ATTAINABILITY_FAILED"
    ECONOMIC_GATE_FAILED = "ECONOMIC_GATE_FAILED"
    NO_CAPITAL = "NO_CAPITAL"

    # Budget suppression
    AI_BUDGET_LIMIT = "AI_BUDGET_LIMIT"

    # Operational failures
    AI_SERVICE_UNAVAILABLE = "AI_SERVICE_UNAVAILABLE"
    MARGIN_VALIDATION_UNAVAILABLE = "MARGIN_VALIDATION_UNAVAILABLE"
    REFERENCE_EVIDENCE_UNAVAILABLE = "REFERENCE_EVIDENCE_UNAVAILABLE"
    MARKET_INTELLIGENCE_UNAVAILABLE = "MARKET_INTELLIGENCE_UNAVAILABLE"
    SNAPSHOT_MISSING = "SNAPSHOT_MISSING"
    GATE_EVALUATION_ERROR = "GATE_EVALUATION_ERROR"
    TRADE_QUALITY_UNAVAILABLE = "TRADE_QUALITY_UNAVAILABLE"

    # Model / confidence results
    AI_RETURNED_NO_CANDIDATES = "AI_RETURNED_NO_CANDIDATES"
    AI_NOT_SELECTED = "AI_NOT_SELECTED"
    AI_CONFIDENCE_BELOW_THRESHOLD = "AI_CONFIDENCE_BELOW_THRESHOLD"
    AI_CONFIDENCE_COUNTERFACTUAL = "AI_CONFIDENCE_COUNTERFACTUAL"
    AI_CONFIDENCE_INVALID = "AI_CONFIDENCE_INVALID"
    AI_RISK_LEVEL_REJECTED = "AI_RISK_LEVEL_REJECTED"
    AI_DIRECTION_REJECTED = "AI_DIRECTION_REJECTED"
    AI_DECISION_WATCH = "AI_DECISION_WATCH"
    AI_DECISION_REJECT = "AI_DECISION_REJECT"

    # Informational
    AI_CACHE_REUSED = "AI_CACHE_REUSED"
    PAPER_ENGINE_DIRECTION_UNSUPPORTED = "PAPER_ENGINE_DIRECTION_UNSUPPORTED"
    PAPER_ENGINE_DISABLED = "PAPER_ENGINE_DISABLED"
    PAPER_ADMISSION_ELIGIBLE = "PAPER_ADMISSION_ELIGIBLE"
    CAPITAL_GATE_NOT_APPLICABLE_TO_SHADOW_ENGINE = (
        "CAPITAL_GATE_NOT_APPLICABLE_TO_SHADOW_ENGINE"
    )

    # Funnel bookkeeping
    FUNNEL_INCOMPLETE = "FUNNEL_INCOMPLETE"


REASON_CLASSES: dict[ReasonCode, ReasonClass] = {
    ReasonCode.CANDIDATE_ADMITTED: ReasonClass.INFORMATIONAL,
    ReasonCode.GATE_PASSED: ReasonClass.INFORMATIONAL,
    ReasonCode.QUALIFIED: ReasonClass.INFORMATIONAL,
    ReasonCode.MARGIN_INELIGIBLE: ReasonClass.POLICY,
    ReasonCode.EXECUTION_VALIDATION_FAILED: ReasonClass.POLICY,
    ReasonCode.SHORT_EXECUTION_QUALITY_FAILED: ReasonClass.POLICY,
    ReasonCode.CROSS_MARKET_UNCONFIRMED: ReasonClass.POLICY,
    ReasonCode.DETERMINISTIC_VIABILITY_FAILED: ReasonClass.POLICY,
    ReasonCode.TRADE_QUALITY_REJECTED: ReasonClass.POLICY,
    ReasonCode.TARGET_ATTAINABILITY_FAILED: ReasonClass.POLICY,
    ReasonCode.ECONOMIC_GATE_FAILED: ReasonClass.POLICY,
    ReasonCode.NO_CAPITAL: ReasonClass.POLICY,
    ReasonCode.AI_BUDGET_LIMIT: ReasonClass.BUDGET,
    ReasonCode.AI_SERVICE_UNAVAILABLE: ReasonClass.OPERATIONAL,
    ReasonCode.MARGIN_VALIDATION_UNAVAILABLE: ReasonClass.OPERATIONAL,
    ReasonCode.REFERENCE_EVIDENCE_UNAVAILABLE: ReasonClass.OPERATIONAL,
    ReasonCode.MARKET_INTELLIGENCE_UNAVAILABLE: ReasonClass.OPERATIONAL,
    ReasonCode.SNAPSHOT_MISSING: ReasonClass.OPERATIONAL,
    ReasonCode.GATE_EVALUATION_ERROR: ReasonClass.OPERATIONAL,
    ReasonCode.TRADE_QUALITY_UNAVAILABLE: ReasonClass.OPERATIONAL,
    ReasonCode.AI_RETURNED_NO_CANDIDATES: ReasonClass.MODEL,
    ReasonCode.AI_NOT_SELECTED: ReasonClass.MODEL,
    ReasonCode.AI_CONFIDENCE_BELOW_THRESHOLD: ReasonClass.MODEL,
    ReasonCode.AI_CONFIDENCE_COUNTERFACTUAL: ReasonClass.INFORMATIONAL,
    ReasonCode.AI_CONFIDENCE_INVALID: ReasonClass.MODEL,
    ReasonCode.AI_RISK_LEVEL_REJECTED: ReasonClass.MODEL,
    ReasonCode.AI_DIRECTION_REJECTED: ReasonClass.MODEL,
    ReasonCode.AI_DECISION_WATCH: ReasonClass.MODEL,
    ReasonCode.AI_DECISION_REJECT: ReasonClass.MODEL,
    ReasonCode.AI_CACHE_REUSED: ReasonClass.INFORMATIONAL,
    ReasonCode.PAPER_ENGINE_DIRECTION_UNSUPPORTED: ReasonClass.INFORMATIONAL,
    ReasonCode.PAPER_ENGINE_DISABLED: ReasonClass.INFORMATIONAL,
    ReasonCode.PAPER_ADMISSION_ELIGIBLE: ReasonClass.INFORMATIONAL,
    ReasonCode.CAPITAL_GATE_NOT_APPLICABLE_TO_SHADOW_ENGINE: ReasonClass.INFORMATIONAL,
    ReasonCode.FUNNEL_INCOMPLETE: ReasonClass.OPERATIONAL,
}


def reason_class(code: ReasonCode | str) -> ReasonClass:
    """Return the class of a reason code, defaulting to OPERATIONAL.

    An unmapped code is treated as operational rather than policy: an unknown
    stop is by definition not a decision the system can claim it made.
    """
    try:
        return REASON_CLASSES[ReasonCode(code)]
    except (KeyError, ValueError):
        return ReasonClass.OPERATIONAL


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalized_threshold_distance(
    measured: Any,
    threshold: Any,
    *,
    higher_is_better: bool = True,
) -> float | None:
    """Return the signed relative distance from ``threshold`` to ``measured``.

    Positive means the candidate is on the passing side of the gate. The value
    is a fraction of the threshold's magnitude, so ``0.0034`` is "0.34% away".
    Returns ``None`` when either side is missing or the threshold is zero,
    because a relative distance from zero is not meaningful.
    """
    measured_value = _finite(measured)
    threshold_value = _finite(threshold)
    if measured_value is None or threshold_value is None:
        return None
    if threshold_value == 0:
        return None
    gap = (measured_value - threshold_value) / abs(threshold_value)
    return gap if higher_is_better else -gap


def _utc_iso(value: datetime | None) -> str:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _clean_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return JSON-safe supporting metadata.

    Non-finite floats are dropped rather than serialised, because the shared
    JSONL writers reject NaN/Inf and one bad float must not cost the whole row.
    """
    if not value:
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key)
        if isinstance(item, float):
            number = _finite(item)
            if number is None:
                continue
            cleaned[name] = number
        elif isinstance(item, (str, int, bool)) or item is None:
            cleaned[name] = item
        elif isinstance(item, Mapping):
            cleaned[name] = _clean_metadata(item)
        elif isinstance(item, (list, tuple)):
            cleaned[name] = [
                sub if isinstance(sub, (str, int, bool)) or sub is None else str(sub)
                for sub in item
            ]
        else:
            cleaned[name] = str(item)
    return cleaned


@dataclass(frozen=True)
class GateResult:
    """What one gate concluded about one candidate."""

    gate: GateName
    status: GateStatus
    reason_code: ReasonCode
    reason: str = ""
    measured_value: float | None = None
    threshold: float | None = None
    threshold_distance: float | None = None
    evaluated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    gate_policy_version: str = GATE_POLICY_VERSION

    @classmethod
    def build(
        cls,
        gate: GateName,
        status: GateStatus,
        reason_code: ReasonCode,
        *,
        reason: str = "",
        measured_value: Any = None,
        threshold: Any = None,
        higher_is_better: bool = True,
        evaluated_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "GateResult":
        measured = _finite(measured_value)
        limit = _finite(threshold)
        return cls(
            gate=gate,
            status=status,
            reason_code=reason_code,
            reason=str(reason or ""),
            measured_value=measured,
            threshold=limit,
            threshold_distance=normalized_threshold_distance(
                measured,
                limit,
                higher_is_better=higher_is_better,
            ),
            evaluated_at=_utc_iso(evaluated_at),
            metadata=_clean_metadata(metadata),
        )

    @property
    def reason_class(self) -> ReasonClass:
        return reason_class(self.reason_code)

    @property
    def is_terminal(self) -> bool:
        """True when this result stops the candidate's progress."""
        return self.status in (GateStatus.FAIL, GateStatus.ERROR)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate.value,
            "status": self.status.value,
            "reason_code": self.reason_code.value,
            "reason_class": self.reason_class.value,
            "reason": self.reason,
            "measured_value": self.measured_value,
            "threshold": self.threshold,
            "threshold_distance": self.threshold_distance,
            "evaluated_at": self.evaluated_at,
            "metadata": self.metadata,
            "gate_policy_version": self.gate_policy_version,
        }


@dataclass(frozen=True)
class AdmissionDecision:
    """The complete, attributed outcome for one candidate in one scan."""

    candidate_id: str
    episode_id: str | None
    asset: str
    pair: str
    market_type: str
    direction: str
    decided_at: str
    decision: DecisionOutcome
    gate_results: tuple[GateResult, ...] = ()
    signal_id: str | None = None
    asset_display_name: str | None = None
    first_terminal_gate: GateName | None = None
    terminal_reason_code: ReasonCode | None = None
    terminal_reason: str = ""
    counterfactual_eligible: bool = False
    strategy_version: str = STRATEGY_VERSION
    intelligence_version: str = INTELLIGENCE_VERSION
    gate_policy_version: str = GATE_POLICY_VERSION

    @property
    def terminal_reason_class(self) -> ReasonClass:
        if self.terminal_reason_code is None:
            return ReasonClass.INFORMATIONAL
        return reason_class(self.terminal_reason_code)

    @property
    def deepest_gate(self) -> GateName | None:
        if not self.gate_results:
            return None
        return max(
            (result.gate for result in self.gate_results),
            key=lambda gate: GATE_INDEX.get(gate, -1),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "episode_id": self.episode_id,
            "signal_id": self.signal_id,
            "asset": self.asset,
            "asset_display_name": self.asset_display_name,
            "pair": self.pair,
            "market_type": self.market_type,
            "direction": self.direction,
            "decided_at": self.decided_at,
            "decision": self.decision.value,
            "first_terminal_gate": (
                self.first_terminal_gate.value
                if self.first_terminal_gate is not None
                else None
            ),
            "terminal_reason_code": (
                self.terminal_reason_code.value
                if self.terminal_reason_code is not None
                else None
            ),
            "terminal_reason_class": self.terminal_reason_class.value,
            "terminal_reason": self.terminal_reason,
            "deepest_gate": (
                self.deepest_gate.value if self.deepest_gate is not None else None
            ),
            "counterfactual_eligible": bool(self.counterfactual_eligible),
            "gate_results": [result.as_dict() for result in self.gate_results],
            "strategy_version": self.strategy_version,
            "intelligence_version": self.intelligence_version,
            "gate_policy_version": self.gate_policy_version,
        }


def terminal_attribution(
    results: Sequence[GateResult],
) -> tuple[DecisionOutcome, GateName | None, ReasonCode | None, str]:
    """Derive the terminal state from an ordered sequence of gate results.

    The first FAIL or ERROR wins: a candidate stops at the first gate that
    stopped it, and later gates are not evaluated. A sequence that reaches
    FINAL_QUALIFICATION with no terminal result is QUALIFIED; anything else is
    INCOMPLETE, which is an operationally unresolved state, never a rejection.
    """
    for result in results:
        if result.status is GateStatus.ERROR:
            return (
                DecisionOutcome.OPERATIONAL_FAILURE,
                result.gate,
                result.reason_code,
                result.reason,
            )
        if result.status is GateStatus.FAIL:
            outcome = (
                DecisionOutcome.OPERATIONAL_FAILURE
                if result.reason_class is ReasonClass.OPERATIONAL
                else DecisionOutcome.REJECTED
            )
            return outcome, result.gate, result.reason_code, result.reason

    reached = {result.gate for result in results}
    if GateName.FINAL_QUALIFICATION in reached:
        return DecisionOutcome.QUALIFIED, None, ReasonCode.QUALIFIED, "qualified"
    return (
        DecisionOutcome.INCOMPLETE,
        None,
        ReasonCode.FUNNEL_INCOMPLETE,
        "candidate did not reach a terminal gate before the scan ended",
    )
