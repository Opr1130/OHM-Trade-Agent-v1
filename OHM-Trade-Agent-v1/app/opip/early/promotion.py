"""High-confidence promotion gates (Issue #223 §16).

The reserved-cohort selector may become operator-facing only when every gate
below is satisfied by real captured evidence. The gates are codified here so
the decision is mechanical rather than a judgement call, and so the absence of
evidence is an explicit ``INSUFFICIENT_EVIDENCE`` verdict rather than a silent
pass.

Deliberate design choice: a missing measurement never satisfies a gate.
:func:`evaluate_promotion` returns ``INSUFFICIENT_EVIDENCE`` whenever any gate
lacks data, which is why merging this module cannot make an unproven detector
promotable. ``OPIP_EARLY_SELECTOR_PROMOTED`` stays dark regardless of what
this module returns; a human still has to flip it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Mapping, Sequence

PROMOTION_CRITERIA_VERSION = "opip-early-promotion-gates-v1"

#: Minimum resolved cohort members before any precision claim is admissible.
MIN_RESOLVED_COHORT_MEMBERS = 200
#: Minimum distinct observation days.
MIN_OBSERVATION_DAYS = 21.0
#: Operator alert volume may not rise by more than this fraction.
MAX_ALERT_VOLUME_INCREASE_FRACTION = 0.0
#: Alert churn (card edits per delivered notification) headroom.
MAX_ALERT_CHURN_INCREASE_FRACTION = 0.10
#: Required improvement in median move consumed before alert, in points.
MIN_MOVE_CONSUMED_IMPROVEMENT_PCT = 2.0


class PromotionStatus(str, Enum):
    PROMOTION_ELIGIBLE = "PROMOTION_ELIGIBLE"
    BLOCKED = "BLOCKED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class GateVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


GATE_QUALIFIED_PRECISION = "qualified_precision_not_reduced"
GATE_ALERT_VOLUME = "operator_alert_volume_not_increased"
GATE_MOVE_CONSUMED = "median_move_consumed_before_alert_improved"
GATE_EARLY_FIRST_OBSERVATION = "more_movers_first_observed_early"
GATE_FEWER_LATE_DISCOVERIES = "fewer_movers_first_discovered_late"
GATE_REJECTION_RECONSTRUCTABLE = "every_rejection_has_gate_and_margin"
GATE_ALERT_CHURN = "no_material_alert_churn_increase"
GATE_AUTHORITY_UNCHANGED = "no_execution_or_risk_authority_change"
GATE_NO_LOOKAHEAD = "point_in_time_replay_shows_no_lookahead"
GATE_DELIVERED_TIMING = "delivered_notification_timing_improved"

#: All ten gates, in the order the issue specifies them.
GATE_ORDER = (
    GATE_QUALIFIED_PRECISION,
    GATE_ALERT_VOLUME,
    GATE_MOVE_CONSUMED,
    GATE_EARLY_FIRST_OBSERVATION,
    GATE_FEWER_LATE_DISCOVERIES,
    GATE_REJECTION_RECONSTRUCTABLE,
    GATE_ALERT_CHURN,
    GATE_AUTHORITY_UNCHANGED,
    GATE_NO_LOOKAHEAD,
    GATE_DELIVERED_TIMING,
)


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


@dataclass(frozen=True)
class GateResult:
    """One promotion gate with its measured values."""

    name: str
    verdict: GateVerdict
    detail: str
    baseline_value: float | None = None
    candidate_value: float | None = None
    threshold: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "verdict": self.verdict.value,
            "detail": self.detail,
            "baseline_value": self.baseline_value,
            "candidate_value": self.candidate_value,
            "threshold": self.threshold,
        }


@dataclass(frozen=True)
class PromotionEvaluation:
    """Aggregate promotion verdict for the reserved-cohort selector."""

    criteria_version: str
    status: PromotionStatus
    gates: tuple[GateResult, ...]

    @property
    def eligible(self) -> bool:
        return self.status is PromotionStatus.PROMOTION_ELIGIBLE

    @property
    def failing_gates(self) -> tuple[str, ...]:
        return tuple(gate.name for gate in self.gates if gate.verdict is GateVerdict.FAIL)

    @property
    def unproven_gates(self) -> tuple[str, ...]:
        return tuple(
            gate.name
            for gate in self.gates
            if gate.verdict is GateVerdict.INSUFFICIENT_EVIDENCE
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "criteria_version": self.criteria_version,
            "status": self.status.value,
            "eligible": self.eligible,
            "failing_gates": list(self.failing_gates),
            "unproven_gates": list(self.unproven_gates),
            "gates": [gate.as_dict() for gate in self.gates],
            "operator_promotion_requires_human_flag": True,
            "trade_authority_changed": False,
        }


def _comparison_gate(
    name: str,
    *,
    baseline: Any,
    candidate: Any,
    higher_is_better: bool,
    min_improvement: float = 0.0,
    detail_pass: str,
    detail_fail: str,
) -> GateResult:
    base = _finite_optional(baseline)
    cand = _finite_optional(candidate)
    if base is None or cand is None:
        return GateResult(
            name,
            GateVerdict.INSUFFICIENT_EVIDENCE,
            "baseline or candidate measurement is unavailable",
            baseline_value=base,
            candidate_value=cand,
            threshold=min_improvement,
        )
    delta = (cand - base) if higher_is_better else (base - cand)
    ok = delta >= min_improvement
    return GateResult(
        name,
        GateVerdict.PASS if ok else GateVerdict.FAIL,
        detail_pass if ok else detail_fail,
        baseline_value=base,
        candidate_value=cand,
        threshold=min_improvement,
    )


def evaluate_promotion(
    *,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    resolved_cohort_members: Any = None,
    observation_days: Any = None,
    unreconstructable_rejections: Any = None,
    lookahead_detected: Any = None,
    authority_changed: Any = None,
) -> PromotionEvaluation:
    """Evaluate all ten gates from baseline and candidate cohort metrics.

    ``baseline`` and ``candidate`` accept the dictionaries produced by
    :func:`app.opip.early.replay.evaluate_cohort_metrics`.
    """
    gates: list[GateResult] = []

    members = _finite_optional(resolved_cohort_members)
    days = _finite_optional(observation_days)
    sample_sufficient = (
        members is not None
        and members >= MIN_RESOLVED_COHORT_MEMBERS
        and days is not None
        and days >= MIN_OBSERVATION_DAYS
    )

    if not sample_sufficient:
        gates.append(
            GateResult(
                GATE_QUALIFIED_PRECISION,
                GateVerdict.INSUFFICIENT_EVIDENCE,
                "cohort sample or observation window is below the admissibility floor",
                baseline_value=members,
                candidate_value=days,
                threshold=float(MIN_RESOLVED_COHORT_MEMBERS),
            )
        )
    else:
        gates.append(
            _comparison_gate(
                GATE_QUALIFIED_PRECISION,
                baseline=baseline.get("qualified_precision_pct"),
                candidate=candidate.get("qualified_precision_pct"),
                higher_is_better=True,
                detail_pass="qualified precision did not decrease",
                detail_fail="qualified precision decreased",
            )
        )

    baseline_volume = _finite_optional(baseline.get("operator_alert_volume"))
    candidate_volume = _finite_optional(candidate.get("operator_alert_volume"))
    if baseline_volume is None or candidate_volume is None:
        gates.append(
            GateResult(
                GATE_ALERT_VOLUME,
                GateVerdict.INSUFFICIENT_EVIDENCE,
                "operator alert volume was not measured on both arms",
                baseline_value=baseline_volume,
                candidate_value=candidate_volume,
            )
        )
    else:
        ceiling = baseline_volume * (1.0 + MAX_ALERT_VOLUME_INCREASE_FRACTION)
        ok = candidate_volume <= ceiling
        gates.append(
            GateResult(
                GATE_ALERT_VOLUME,
                GateVerdict.PASS if ok else GateVerdict.FAIL,
                "operator alert volume did not increase"
                if ok
                else "operator alert volume increased",
                baseline_value=baseline_volume,
                candidate_value=candidate_volume,
                threshold=ceiling,
            )
        )

    gates.append(
        _comparison_gate(
            GATE_MOVE_CONSUMED,
            baseline=baseline.get("median_move_consumed_before_alert_pct"),
            candidate=candidate.get("median_move_consumed_before_alert_pct"),
            higher_is_better=False,
            min_improvement=MIN_MOVE_CONSUMED_IMPROVEMENT_PCT,
            detail_pass="median move consumed before alert materially improved",
            detail_fail="median move consumed before alert did not materially improve",
        )
    )
    gates.append(
        _comparison_gate(
            GATE_EARLY_FIRST_OBSERVATION,
            baseline=baseline.get("first_observation_early_phase_share_pct"),
            candidate=candidate.get("first_observation_early_phase_share_pct"),
            higher_is_better=True,
            detail_pass="more eventual movers are first observed early",
            detail_fail="early first-observation share did not improve",
        )
    )
    baseline_early = _finite_optional(baseline.get("first_observation_early_phase_share_pct"))
    candidate_early = _finite_optional(candidate.get("first_observation_early_phase_share_pct"))
    if baseline_early is None or candidate_early is None:
        gates.append(
            GateResult(
                GATE_FEWER_LATE_DISCOVERIES,
                GateVerdict.INSUFFICIENT_EVIDENCE,
                "late-discovery share was not measured on both arms",
            )
        )
    else:
        # Late discovery is the complement of early first observation.
        ok = (100.0 - candidate_early) <= (100.0 - baseline_early)
        gates.append(
            GateResult(
                GATE_FEWER_LATE_DISCOVERIES,
                GateVerdict.PASS if ok else GateVerdict.FAIL,
                "fewer movers are first discovered late"
                if ok
                else "more movers are first discovered late",
                baseline_value=100.0 - baseline_early,
                candidate_value=100.0 - candidate_early,
            )
        )

    unreconstructable = _finite_optional(unreconstructable_rejections)
    if unreconstructable is None:
        gates.append(
            GateResult(
                GATE_REJECTION_RECONSTRUCTABLE,
                GateVerdict.INSUFFICIENT_EVIDENCE,
                "rejection reconstructability was not measured",
            )
        )
    else:
        ok = unreconstructable <= 0
        gates.append(
            GateResult(
                GATE_REJECTION_RECONSTRUCTABLE,
                GateVerdict.PASS if ok else GateVerdict.FAIL,
                "every rejection carries a gate and a margin"
                if ok
                else "some rejections cannot be reconstructed",
                candidate_value=unreconstructable,
                threshold=0.0,
            )
        )

    baseline_churn = _churn(baseline)
    candidate_churn = _churn(candidate)
    if baseline_churn is None or candidate_churn is None:
        gates.append(
            GateResult(
                GATE_ALERT_CHURN,
                GateVerdict.INSUFFICIENT_EVIDENCE,
                "alert churn was not measured on both arms",
                baseline_value=baseline_churn,
                candidate_value=candidate_churn,
            )
        )
    else:
        ceiling = baseline_churn * (1.0 + MAX_ALERT_CHURN_INCREASE_FRACTION)
        ok = candidate_churn <= ceiling
        gates.append(
            GateResult(
                GATE_ALERT_CHURN,
                GateVerdict.PASS if ok else GateVerdict.FAIL,
                "alert churn did not increase materially"
                if ok
                else "alert churn increased materially",
                baseline_value=baseline_churn,
                candidate_value=candidate_churn,
                threshold=ceiling,
            )
        )

    if authority_changed is None:
        gates.append(
            GateResult(
                GATE_AUTHORITY_UNCHANGED,
                GateVerdict.INSUFFICIENT_EVIDENCE,
                "authority-change assertion was not supplied",
            )
        )
    else:
        ok = not bool(authority_changed)
        gates.append(
            GateResult(
                GATE_AUTHORITY_UNCHANGED,
                GateVerdict.PASS if ok else GateVerdict.FAIL,
                "no execution, risk, or trading authority change"
                if ok
                else "an execution, risk, or trading authority change was detected",
            )
        )

    if lookahead_detected is None:
        gates.append(
            GateResult(
                GATE_NO_LOOKAHEAD,
                GateVerdict.INSUFFICIENT_EVIDENCE,
                "point-in-time replay result was not supplied",
            )
        )
    else:
        ok = not bool(lookahead_detected)
        gates.append(
            GateResult(
                GATE_NO_LOOKAHEAD,
                GateVerdict.PASS if ok else GateVerdict.FAIL,
                "point-in-time replay shows no lookahead"
                if ok
                else "point-in-time replay detected lookahead",
            )
        )

    # Measured from first observation to delivered notification, so a smaller
    # value is better. Named as a delay rather than a "lead time" because the
    # two read in opposite directions and conflating them would invert the
    # gate. Card-created timing is deliberately not admissible here.
    gates.append(
        _comparison_gate(
            GATE_DELIVERED_TIMING,
            baseline=baseline.get("median_observation_to_delivery_seconds"),
            candidate=candidate.get("median_observation_to_delivery_seconds"),
            higher_is_better=False,
            detail_pass="delivered-notification timing improved",
            detail_fail="delivered-notification timing did not improve",
        )
    )

    ordered = _in_gate_order(gates)
    if any(gate.verdict is GateVerdict.FAIL for gate in ordered):
        status = PromotionStatus.BLOCKED
    elif any(gate.verdict is GateVerdict.INSUFFICIENT_EVIDENCE for gate in ordered):
        status = PromotionStatus.INSUFFICIENT_EVIDENCE
    else:
        status = PromotionStatus.PROMOTION_ELIGIBLE
    return PromotionEvaluation(
        criteria_version=PROMOTION_CRITERIA_VERSION,
        status=status,
        gates=ordered,
    )


def _churn(metrics: Mapping[str, Any]) -> float | None:
    """Card edits per delivered notification."""
    edits = _finite_optional(metrics.get("total_card_edits"))
    delivered = _finite_optional(metrics.get("delivered_notification_volume"))
    if edits is None or delivered is None or delivered <= 0:
        return None
    return round(edits / delivered, 6)


def _in_gate_order(gates: Sequence[GateResult]) -> tuple[GateResult, ...]:
    by_name = {gate.name: gate for gate in gates}
    ordered = [by_name[name] for name in GATE_ORDER if name in by_name]
    extras = [gate for gate in gates if gate.name not in set(GATE_ORDER)]
    return tuple(ordered + extras)


def promotion_criteria() -> dict[str, Any]:
    """Report the codified criteria for documentation and PR evidence."""
    return {
        "criteria_version": PROMOTION_CRITERIA_VERSION,
        "gates": list(GATE_ORDER),
        "min_resolved_cohort_members": MIN_RESOLVED_COHORT_MEMBERS,
        "min_observation_days": MIN_OBSERVATION_DAYS,
        "max_alert_volume_increase_fraction": MAX_ALERT_VOLUME_INCREASE_FRACTION,
        "max_alert_churn_increase_fraction": MAX_ALERT_CHURN_INCREASE_FRACTION,
        "min_move_consumed_improvement_pct": MIN_MOVE_CONSUMED_IMPROVEMENT_PCT,
        "missing_measurement_never_satisfies_a_gate": True,
        "operator_promotion_requires_human_flag": True,
    }
