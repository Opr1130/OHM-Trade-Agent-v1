"""AI advisory isolation (ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1, slice P6).

The production producer currently does ``alerts = qualified_alerts(review)``, so
an AI timeout, budget exhaustion or provider failure can erase an otherwise
valid deterministic opportunity. This module separates the deterministic record
from the AI annotation so that:

* a deterministic candidate is **preserved** whatever the AI does;
* an AI opinion can only ever *refuse* or *annotate* - it can never create,
  rank or authorise a candidate;
* AI absence is recorded with an explicit reason code, never defaulted to a
  permissive value;
* no calibrated probability is synthesised from an AI score.

NOTIFICATION-NEUTRAL BY CONSTRUCTION. This module performs no delivery and is
not wired into the producer: it returns annotated records only. Nothing here can
admit, notify, size or trade, so activating it cannot widen notification
authority. Wiring it in remains a separate, owner-gated activation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

AI_STATUS_OK = "OK"
AI_STATUS_UNAVAILABLE = "UNAVAILABLE"
AI_STATUS_TIMEOUT = "TIMEOUT"
AI_STATUS_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
AI_STATUS_ERROR = "ERROR"

REASON_AI_UNAVAILABLE = "AI_ADVISORY_UNAVAILABLE"
REASON_AI_TIMEOUT = "AI_ADVISORY_TIMEOUT"
REASON_AI_BUDGET_EXHAUSTED = "AI_ADVISORY_BUDGET_EXHAUSTED"
REASON_AI_ERROR = "AI_ADVISORY_ERROR"

_REASON_BY_STATUS = {
    AI_STATUS_UNAVAILABLE: REASON_AI_UNAVAILABLE,
    AI_STATUS_TIMEOUT: REASON_AI_TIMEOUT,
    AI_STATUS_BUDGET_EXHAUSTED: REASON_AI_BUDGET_EXHAUSTED,
    AI_STATUS_ERROR: REASON_AI_ERROR,
}


@dataclass(frozen=True)
class AiAnnotation:
    """The AI verdict attached to a deterministic candidate."""

    status: str
    reason_code: str | None = None
    recommendation: str | None = None
    confidence_score: float | None = None

    @property
    def available(self) -> bool:
        return self.status == AI_STATUS_OK


def annotation_for_status(status: str, *, recommendation: str | None = None,
                          confidence_score: float | None = None) -> AiAnnotation:
    """Build an annotation whose reason code follows from the status."""
    normalized = str(status or "").strip().upper() or AI_STATUS_UNAVAILABLE
    return AiAnnotation(
        status=normalized,
        reason_code=_REASON_BY_STATUS.get(normalized),
        recommendation=recommendation,
        confidence_score=confidence_score,
    )


@dataclass(frozen=True)
class CandidateWithAdvisory:
    """A deterministic candidate plus an advisory annotation. Never dropped."""

    candidate: Any
    annotation: AiAnnotation

    @property
    def ai_available(self) -> bool:
        return self.annotation.available

    @property
    def advisory_reason(self) -> str | None:
        return self.annotation.reason_code


def attach_advisory(
    deterministic_candidates: Sequence[Any],
    annotation: AiAnnotation,
) -> tuple[CandidateWithAdvisory, ...]:
    """Attach an annotation to every deterministic candidate.

    The candidate list is never filtered by AI state: a failed AI call removes
    nothing. Order is preserved so deterministic ranking is untouched.
    """
    return tuple(
        CandidateWithAdvisory(candidate=candidate, annotation=annotation)
        for candidate in deterministic_candidates
    )


def advisory_cannot_admit(annotation: AiAnnotation) -> bool:
    """An AI opinion can never be the thing that authorises a notification."""
    return not annotation.available


__all__ = [
    "AI_STATUS_BUDGET_EXHAUSTED",
    "AI_STATUS_ERROR",
    "AI_STATUS_OK",
    "AI_STATUS_TIMEOUT",
    "AI_STATUS_UNAVAILABLE",
    "AiAnnotation",
    "CandidateWithAdvisory",
    "REASON_AI_BUDGET_EXHAUSTED",
    "REASON_AI_ERROR",
    "REASON_AI_TIMEOUT",
    "REASON_AI_UNAVAILABLE",
    "advisory_cannot_admit",
    "annotation_for_status",
    "attach_advisory",
]
