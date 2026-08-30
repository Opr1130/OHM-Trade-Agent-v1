"""Sustained shadow-equivalence evaluation for O'Pip BUILD 5.2A."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
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
class ScanCoverageExpectation:
    """Independent expected denominator for one canonical scan."""

    scan_id: str
    expected_at_utc: datetime
    expected_candidate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not str(self.scan_id or ""):
            raise ValueError("scan_id is required")
        if self.expected_at_utc.tzinfo is None or self.expected_at_utc.utcoffset() is None:
            raise ValueError("expected_at_utc must be timezone-aware")
        object.__setattr__(
            self,
            "expected_at_utc",
            self.expected_at_utc.astimezone(timezone.utc),
        )
        normalized = tuple(
            sorted({str(item) for item in self.expected_candidate_ids if str(item)})
        )
        if not normalized:
            raise ValueError("expected_candidate_ids must not be empty")
        object.__setattr__(self, "expected_candidate_ids", normalized)


@dataclass(frozen=True)
class PromotionCriteria:
    """Explicit governance inputs; BUILD 5.2A chooses no production threshold."""

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
    expected_observations: int
    covered_expected_observations: int
    missing_expected_observations: int
    unexpected_observations: int
    duplicate_candidate_observations: int
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
            "expected_observations": self.expected_observations,
            "covered_expected_observations": self.covered_expected_observations,
            "missing_expected_observations": self.missing_expected_observations,
            "unexpected_observations": self.unexpected_observations,
            "duplicate_candidate_observations": self.duplicate_candidate_observations,
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


def _expectations(
    values: Iterable[ScanCoverageExpectation] | None,
) -> tuple[ScanCoverageExpectation, ...]:
    if values is None:
        return ()
    by_scan: dict[str, ScanCoverageExpectation] = {}
    for item in values:
        prior = by_scan.get(item.scan_id)
        if prior is not None and prior != item:
            raise ValueError("conflicting coverage expectation for scan")
        by_scan[item.scan_id] = item
    return tuple(sorted(by_scan.values(), key=lambda item: item.scan_id))


def evaluate_shadow_equivalence(
    observations: Iterable[EquivalenceObservation],
    *,
    criteria: PromotionCriteria,
    coverage_expectations: Iterable[ScanCoverageExpectation] | None = None,
    ledger_complete: bool = True,
    ledger_warnings: tuple[str, ...] = (),
) -> PromotionEvaluation:
    """Evaluate evidence readiness; never perform or authorize promotion."""
    rows = _dedupe(observations)
    expectations = _expectations(coverage_expectations)
    expected_keys = {
        (expectation.scan_id, candidate_id)
        for expectation in expectations
        for candidate_id in expectation.expected_candidate_ids
    }
    rows_by_key: dict[tuple[str, str], list[EquivalenceObservation]] = {}
    for row in rows:
        rows_by_key.setdefault((row.scan_id, row.candidate_id), []).append(row)

    counts = Counter(
        key for key, matching in rows_by_key.items() for _ in matching
    )
    duplicate_keys = {key for key, count in counts.items() if count > 1}
    observed_keys = set(rows_by_key)
    missing_keys = expected_keys - observed_keys
    unexpected_keys = observed_keys - expected_keys if expectations else observed_keys

    complete_expected: list[EquivalenceObservation] = []
    for key in sorted(expected_keys):
        matching = rows_by_key.get(key, [])
        if len(matching) == 1 and matching[0].pairing_state is PairingState.COMPLETE:
            complete_expected.append(matching[0])

    total_expected = len(expected_keys)
    covered = len(complete_expected)
    coverage = (
        round(100.0 * covered / total_expected, 6) if total_expected else 0.0
    )
    exact = tuple(row for row in complete_expected if row.exact_match)
    divergent = tuple(row for row in complete_expected if not row.exact_match)
    scans = {row.scan_id for row in complete_expected}
    days = {row.observed_at_utc.date().isoformat() for row in complete_expected}
    policies = tuple(
        sorted(
            {
                str(row.gate_policy_fingerprint)
                for row in complete_expected
                if row.gate_policy_fingerprint
            }
        )
    )
    code_fingerprints = tuple(
        sorted(
            {
                str(row.engine_code_fingerprint)
                for row in complete_expected
                if row.engine_code_fingerprint
            }
        )
    )

    instrumentation_blockers: list[str] = []
    if not ledger_complete:
        instrumentation_blockers.append("LEDGER_COVERAGE_INCOMPLETE")
    if not expectations:
        instrumentation_blockers.append("EXPECTED_COVERAGE_NOT_PROVIDED")
    if missing_keys:
        instrumentation_blockers.append("EXPECTED_COMPARISON_MISSING")
    if unexpected_keys:
        instrumentation_blockers.append("UNEXPECTED_COMPARISON_PRESENT")
    if duplicate_keys:
        instrumentation_blockers.append("DUPLICATE_CANDIDATE_OBSERVATION_PRESENT")
    if coverage < criteria.min_instrumentation_coverage_pct:
        instrumentation_blockers.append("INSTRUMENTATION_COVERAGE_BELOW_MINIMUM")
    if any(row.pairing_state is PairingState.INVALID for row in rows):
        instrumentation_blockers.append("INVALID_DECISION_PAIRING_PRESENT")
    if any(row.pairing_state is PairingState.INCOMPLETE for row in rows):
        instrumentation_blockers.append("MISSING_DECISION_SIDE_PRESENT")

    version_blockers: list[str] = []
    if len(policies) != 1 and complete_expected:
        version_blockers.append("MIXED_OR_MISSING_POLICY_FINGERPRINT")
    if len(code_fingerprints) != 1 and complete_expected:
        version_blockers.append("MIXED_OR_MISSING_ENGINE_CODE_FINGERPRINT")

    evidence_blockers: list[str] = []
    if len(complete_expected) < criteria.min_comparable_observations:
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
        evaluated_observations=len(rows),
        expected_observations=total_expected,
        covered_expected_observations=covered,
        missing_expected_observations=len(missing_keys),
        unexpected_observations=len(unexpected_keys),
        duplicate_candidate_observations=len(duplicate_keys),
        comparable_observations=len(complete_expected),
        instrumentation_complete_observations=len(complete_expected),
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
