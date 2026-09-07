"""Phase 3 operator semantics and honest score rendering (Issue #223).

Two operator-facing defects are fixed here.

**Contradictory taxonomy.** ``movement_discovery_v2`` computes ``stage`` and
``entry_recommendation`` independently, so an already-extended asset rendered
as ``EARLY WATCH — READY`` on the same card that said ``WAIT_FOR_PULLBACK``
and ``WATCH ONLY``. Market phase, evidence grade and disposition are now
separate, explicit fields.

**Score presented as probability.** ``EarlyMoverSignal`` carries
``continuation_confidence_is_probability = False``, but the card rendered
``Confidence*: {continuation_confidence}%`` unconditionally.
:func:`format_heuristic_score` refuses to emit percentage-confidence language
for a value that is not a calibrated probability.

``READY`` survives only as the alert-governor transition token; see
:func:`app.opip.early.taxonomy.governor_stage_token`. Governor priority
behaviour is deliberately unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Sequence

from app.opip.early.taxonomy import (
    EvidenceGrade,
    MarketPhase,
    OperatorDisposition,
    coerce_evidence_grade,
    coerce_market_phase,
    coerce_operator_disposition,
    is_early_phase,
)

OPERATOR_SEMANTICS_VERSION = "opip-early-operator-semantics-v1"

#: Wording that claims a genuinely early discovery. Forbidden for extended
#: phases and for non-actionable dispositions.
EARLY_CLAIM_TOKENS = ("EARLY WATCH", "EARLY DISCOVERY", "EARLY SIGNAL", "READY")


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def format_heuristic_score(
    value: Any,
    *,
    is_probability: bool,
    scale: int = 100,
) -> str:
    """Render a score honestly.

    Only a genuinely calibrated probability may use ``%``. Everything else
    renders as ``N/100`` so the operator cannot read a heuristic sum as a
    likelihood.
    """
    parsed = _finite_optional(value)
    if parsed is None:
        return "unavailable"
    if is_probability:
        return f"{parsed:.0f}%"
    return f"{parsed:.0f}/{int(scale)}"


def continuation_score_label(signal: Any) -> str:
    """Label the continuation score using the signal's own honesty flag."""
    is_probability = bool(getattr(signal, "continuation_confidence_is_probability", False))
    rendered = format_heuristic_score(
        getattr(signal, "continuation_confidence", None),
        is_probability=is_probability,
    )
    prefix = "Continuation confidence" if is_probability else "Continuation score*"
    return f"{prefix}: {rendered}"


@dataclass(frozen=True)
class ScoreHeadroom:
    """Additive unsaturated score alongside the bounded score.

    ``evaluate_early_mover`` clamps its component sum with
    ``score = min(100, score)`` and ``continuation = max(0, min(100, ...))``.
    For a RAY-like input the components sum past 100, so ``100/100`` is
    saturated and carries no cross-sectional information. Preserving the raw
    additive total and a cross-sectional percentile restores that information
    without changing the bounded value any existing consumer reads.
    """

    bounded_score: float
    raw_score: float
    scale: int = 100
    cross_sectional_percentile: float | None = None

    @property
    def saturated(self) -> bool:
        return self.raw_score > float(self.scale) or self.bounded_score >= float(self.scale)

    @property
    def headroom(self) -> float:
        """How much raw score exceeded the bound. Zero when unsaturated."""
        return max(0.0, self.raw_score - float(self.scale))

    def as_dict(self) -> dict[str, Any]:
        return {
            "bounded_score": _finite_optional(self.bounded_score),
            "raw_score": _finite_optional(self.raw_score),
            "scale": int(self.scale),
            "saturated": self.saturated,
            "headroom": self.headroom,
            "cross_sectional_percentile": _finite_optional(self.cross_sectional_percentile),
            "is_probability": False,
        }


def score_headroom(
    *,
    bounded_score: Any,
    raw_score: Any = None,
    peer_scores: Sequence[Any] | None = None,
    scale: int = 100,
) -> ScoreHeadroom:
    """Build headroom metadata for a bounded heuristic score.

    ``raw_score`` defaults to the bounded value, which is correct whenever the
    caller has no unclamped total available; the result then simply reports no
    headroom rather than inventing one.
    """
    bounded = _finite_optional(bounded_score) or 0.0
    raw = _finite_optional(raw_score)
    if raw is None:
        raw = bounded

    percentile: float | None = None
    if peer_scores:
        peers = [value for value in (_finite_optional(item) for item in peer_scores) if value is not None]
        if peers:
            at_or_below = sum(1 for value in peers if value <= bounded)
            percentile = round(at_or_below / len(peers) * 100.0, 4)
    return ScoreHeadroom(
        bounded_score=bounded,
        raw_score=raw,
        scale=int(scale),
        cross_sectional_percentile=percentile,
    )


def disposition_label(disposition: OperatorDisposition | str) -> str:
    """Human-readable disposition, e.g. ``DO_NOT_CHASE`` -> ``DO NOT CHASE``."""
    return coerce_operator_disposition(disposition).value.replace("_", " ")


@dataclass(frozen=True)
class OperatorAssessment:
    """The three orthogonal operator-facing facts about one candidate."""

    version: str
    symbol: str
    phase: MarketPhase
    grade: EvidenceGrade
    disposition: OperatorDisposition
    why_qualified: tuple[str, ...] = ()
    why_not_actionable: tuple[str, ...] = ()

    @property
    def claims_early_discovery(self) -> bool:
        """Whether this assessment may use early-discovery wording at all.

        ``EARLY WATCH`` is reserved for :data:`EARLY_PHASES` only
        (``IGNITION``, ``EARLY_EXPANSION``). ``CONFIRMED_EXPANSION`` is not
        early merely because it is not yet ``LATE_EXTENSION``.
        """
        return is_early_phase(self.phase)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "symbol": self.symbol,
            "market_phase": self.phase.value,
            "evidence_grade": self.grade.value,
            "operator_disposition": self.disposition.value,
            "why_qualified": list(self.why_qualified),
            "why_not_actionable": list(self.why_not_actionable),
            "claims_early_discovery": self.claims_early_discovery,
            "trade_authority_changed": False,
        }


def build_operator_assessment(
    *,
    symbol: str,
    phase: MarketPhase | str,
    grade: EvidenceGrade | str,
    disposition: OperatorDisposition | str,
    why_qualified: Sequence[str] = (),
    why_not_actionable: Sequence[str] = (),
) -> OperatorAssessment:
    """Normalise operator-facing state into one immutable assessment."""
    resolved_phase = coerce_market_phase(phase)
    resolved_grade = coerce_evidence_grade(grade)
    resolved_disposition = coerce_operator_disposition(disposition)
    return OperatorAssessment(
        version=OPERATOR_SEMANTICS_VERSION,
        symbol=str(symbol).upper(),
        phase=resolved_phase,
        grade=resolved_grade,
        disposition=resolved_disposition,
        why_qualified=tuple(str(item) for item in why_qualified if str(item).strip()),
        why_not_actionable=tuple(
            str(item) for item in why_not_actionable if str(item).strip()
        ),
    )


def misleading_early_language(text: str, assessment: OperatorAssessment) -> tuple[str, ...]:
    """Return any early-discovery wording that the assessment cannot support.

    Used by the RAY regression test: a ``LATE_EXTENSION`` +
    ``DO_NOT_CHASE`` candidate must not contain ``EARLY WATCH`` or ``READY``
    anywhere in its operator text.
    """
    if assessment.claims_early_discovery and assessment.disposition not in {
        OperatorDisposition.DO_NOT_CHASE,
        OperatorDisposition.NO_ACTION,
    }:
        return ()
    upper = str(text or "").upper()
    # Word-bounded so an innocent substring cannot register as a claim:
    # "move is already extended" contains "READY" but claims nothing.
    return tuple(
        token
        for token in EARLY_CLAIM_TOKENS
        if re.search(rf"\b{re.escape(token)}\b", upper)
    )


def misleading_confidence_language(text: str, *, is_probability: bool) -> tuple[str, ...]:
    """Return percentage-confidence phrasing used for a non-probability score."""
    if is_probability:
        return ()
    upper = str(text or "").upper()
    offenders: list[str] = []
    for line in upper.splitlines():
        if "CONFIDENCE" in line and "%" in line:
            offenders.append(line.strip())
    return tuple(offenders)
