"""Sustained shadow-equivalence evaluation for O'Pip BUILD 5.2A."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable

from app.opip.decision.equivalence import EquivalenceObservation, PairingState


class PromotionEvaluationStatus(str, Enum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    BLOCKED_INSTRUMENTATION = "BLOCKED_INSTRUMENTATION"
    BLOCKED_DIVERGENCE = "BLOCKED_DIVERGENCE"
    BLOCKED_VERSION_MIX = "BLOCKED_VERSION_MIX"
    READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"


@dataclass(frozen=True)
class PromotionCriteria:
    """Governance inputs for an equivalence evaluation.

    BUILD 5.2A deliberately does not choose production promotion thresholds.
    A caller must supply the minimum observation/scans/days it wants to demand.
    Exact engine equivalence itself is non-negotiable: zero divergences.
    """

    min_comparable_observations: int
    min_distinct_scans: int
    min_distinct_days: int
    min_instrumentation_coverage_pct: float = 100.0

    def __post_init__(self) -> None:
        if self.min_comparable_observations < 1:
            raise ValueError("min_comparable_observations must be positive")
        if self.min_distinct_scans < 1:
            raise ValueError("min_distinct_scans must be positive")
        if self.min_distinct_days < 1:
            raise ValueError("min_distinct_days must be positive")
        coverage = float(self.min_instrumentation_coverage_pct)
        if not math.isfinite(coverage) or coverage < 0 or coverage > 100:
            raise ValueError("instrumentation coverage must be finite in [0, 100]")


@dataclass(frozen=True)
class PromotionEvaluation:
    status: PromotionEvaluationStatus
    evaluated_observations: int
    comparable_observations: int
    instrumentation_complete_observations: int
    instrumentation_coverage_pct: float
    exact_matches: int
    divergences: int
    distinct_scans: int
    distinct_days: int
    policy_fingerprints: tuple[str, ...]
    engine_code_fingerprints: tuple[str, ...]
    blockers: tuple[str, ...]
    ledger_complete: bool
    ledger_warnings: tuple[str, ...]

    AUTHORITATIVE = False
    CAN_PROMOTE = False
    CAN_CHANGE_POLICY = False

    @property
    def ready_for_human_review(self) -> bool:
        return self.status is PromotionEvaluationStatus.READY_FOR_HUMAN_REVIEW

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "evaluated_observations": self.evaluated_observations,
            "comparable_observations": self.comparable_observations,
            "instrumentation_complete_observations": (
                self.instrumentation_complete_observations
            ),
            "instrumentation_coverage_pct": self.instrumentation_coverage_pct,
            "exact_matches": self.exact_matches,
            "divergences": self.divergences,
            "distinct_scans": self.distinct_scans,
            "distinct_days": self.distinct_days,
            "policy_fingerprints": list(self.policy_fingerprints),
            "engine_code_fingerprints": list(self.engine_code_fingerprints),
            "blockers": list(self.blockers),
            "ledger_complete": self.ledger_complete,
            "ledger_warnings": list(self.ledger_warnings),
            "ready_for_human_review": self.ready_for_human_review,
            "opip_engine_authoritative": False,
            "automatic_promotion": False,
        }


def _dedupe(
    observations: Iterable[EquivalenceObservation],
) -> tuple[EquivalenceObservation, ...]:
    by_id: dict[str, EquivalenceObservation] = {}
    for row in observations:
        existing = by_id.get(row.observation_id)
        if existing is not None and existing.as_dict() != row.as_dict():
            raise ValueError("equivalence observation ID collision")
        by_id[row.observation_id] = row
    return tuple(
        sorted(
            by_id.values(),
            key=lambda item: (
                item.observed_at_utc,
                item.scan_id,
                item.candidate_id,
                item.observation_id,
            ),
        )
    )


def evaluate_shadow_equivalence(
    observations: Iterable[EquivalenceObservation],
    *,
    criteria: PromotionCriteria,
    ledger_complete: bool = True,
    ledger_warnings: tuple[str, ...] = (),
) -> PromotionEvaluation:
    """Evaluate evidence readiness; never perform or authorize promotion."""
    rows = _dedupe(observations)
    total = len(rows)
    complete = tuple(
        row for row in rows if row.pairing_state is PairingState.COMPLETE
    )
    exact = tuple(row for row in complete if row.exact_match)
    divergent = tuple(row for row in complete if not row.exact_match)
    coverage = round(100.0 * len(complete) / total, 6) if total else 0.0
    scans = {row.scan_id for row in complete}
    days = {row.observed_at_utc.date().isoformat() for row in complete}
    policies = tuple(
        sorted(
            {
                str(row.gate_policy_fingerprint)
                for row in complete
                if row.gate_policy_fingerprint
            }
        )
    )
    code_fingerprints = tuple(
        sorted(
            {
                str(row.engine_code_fingerprint)
                for row in complete
                if row.engine_code_fingerprint
            }
        )
    )

    instrumentation_blockers: list[str] = []
    if not ledger_complete:
        instrumentation_blockers.append("LEDGER_COVERAGE_INCOMPLETE")
    if coverage < criteria.min_instrumentation_coverage_pct:
        instrumentation_blockers.append("INSTRUMENTATION_COVERAGE_BELOW_MINIMUM")
    if any(row.pairing_state is PairingState.INVALID for row in rows):
        instrumentation_blockers.append("INVALID_DECISION_PAIRING_PRESENT")
    if any(row.pairing_state is PairingState.INCOMPLETE for row in rows):
        instrumentation_blockers.append("MISSING_DECISION_SIDE_PRESENT")

    version_blockers: list[str] = []
    if len(policies) != 1 and complete:
        version_blockers.append("MIXED_OR_MISSING_POLICY_FINGERPRINT")
    if len(code_fingerprints) != 1 and complete:
        version_blockers.append("MIXED_OR_MISSING_ENGINE_CODE_FINGERPRINT")

    evidence_blockers: list[str] = []
    if len(complete) < criteria.min_comparable_observations:
        evidence_blockers.append("INSUFFICIENT_COMPARABLE_OBSERVATIONS")
    if len(scans) < criteria.min_distinct_scans:
        evidence_blockers.append("INSUFFICIENT_DISTINCT_SCANS")
    if len(days) < criteria.min_distinct_days:
        evidence_blockers.append("INSUFFICIENT_DISTINCT_DAYS")

    divergence_blockers: list[str] = []
    if divergent:
        divergence_blockers.append("EXACT_EQUIVALENCE_DIVERGENCE_PRESENT")

    blockers = tuple(
        instrumentation_blockers
        + divergence_blockers
        + version_blockers
        + evidence_blockers
    )

    if instrumentation_blockers:
        status = PromotionEvaluationStatus.BLOCKED_INSTRUMENTATION
    elif divergence_blockers:
        status = PromotionEvaluationStatus.BLOCKED_DIVERGENCE
    elif version_blockers:
        status = PromotionEvaluationStatus.BLOCKED_VERSION_MIX
    elif evidence_blockers:
        status = PromotionEvaluationStatus.INSUFFICIENT_EVIDENCE
    else:
        status = PromotionEvaluationStatus.READY_FOR_HUMAN_REVIEW

    return PromotionEvaluation(
        status=status,
        evaluated_observations=total,
        comparable_observations=len(complete),
        instrumentation_complete_observations=len(complete),
        instrumentation_coverage_pct=coverage,
        exact_matches=len(exact),
        divergences=len(divergent),
        distinct_scans=len(scans),
        distinct_days=len(days),
        policy_fingerprints=policies,
        engine_code_fingerprints=code_fingerprints,
        blockers=blockers,
        ledger_complete=bool(ledger_complete),
        ledger_warnings=tuple(str(item) for item in ledger_warnings),
    )
