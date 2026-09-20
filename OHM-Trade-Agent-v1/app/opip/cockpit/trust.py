"""B/C-4 cockpit trust semantics.

The cockpit's trust model keeps four dimensions **independent** and never collapses
them into a single health score. This module defines that vocabulary and the
envelope every analytical response carries.

Why separate dimensions matter: "the analytics plane has not ingested yet", "the
economic evidence is incomplete", "we cannot grade simulation fidelity", and "the
sample is too thin to support an interval" are four different truths with four
different operator actions. A single green/red badge destroys that distinction and
lets a stale number read as a healthy one.

The governing rule is that ``UNKNOWN`` is never rendered as ``0``, ``healthy`` or
``complete``. ``NOT_EVALUATED`` exists precisely so a dimension that has not been
assessed is stated rather than implied to be fine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Freshness(str, Enum):
    """Availability of the evidence an analytical response was built from.

    Mirrors the canonical freshness vocabulary used by the analytics plane so the
    cockpit does not invent a competing set of words.
    """

    LIVE = "LIVE"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class Completeness(str, Enum):
    """Whether the economics underpinning a response are proven complete."""

    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN = "UNKNOWN"


class Fidelity(str, Enum):
    """Simulation fidelity / coverage grading.

    ``NOT_EVALUATED`` is the truthful value for this slice: no fidelity-grade
    vocabulary is bound to Paper-v2 trades, and inventing one would fabricate
    evidence. It is deliberately not ``COMPLETE``.
    """

    NOT_EVALUATED = "NOT_EVALUATED"


class Uncertainty(str, Enum):
    """Statistical uncertainty posture, taken from the metric registry.

    Values mirror ``app.opip.contracts.paper_metrics.UncertaintyRequirement`` plus
    the registered abstention verdict, so a thin cohort abstains rather than
    passing an invented threshold.
    """

    NONE = "NONE"
    SHOW_SUPPORT = "SHOW_SUPPORT"
    SHOW_INTERVAL = "SHOW_INTERVAL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class CorrectionState(str, Enum):
    """Correction / supersession posture of a displayed row.

    A superseding correction never mutates the row it supersedes; the superseded
    row stays visible and is labelled, and a conflicting correction withholds the
    affected result rather than overwriting history.
    """

    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True)
class TrustEnvelope:
    """The independent trust dimensions attached to an analytical response or row.

    ``reasons`` carries the machine-readable codes that explain any non-healthy
    dimension, so a panel can say *why* it is stale or incomplete instead of only
    that it is.
    """

    freshness: Freshness
    completeness: Completeness
    fidelity: Fidelity = Fidelity.NOT_EVALUATED
    uncertainty: Uncertainty = Uncertainty.NONE
    correction_state: CorrectionState = CorrectionState.CURRENT
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name, expected in (
            ("freshness", Freshness),
            ("completeness", Completeness),
            ("fidelity", Fidelity),
            ("uncertainty", Uncertainty),
            ("correction_state", CorrectionState),
        ):
            value = getattr(self, name)
            if not isinstance(value, expected):
                try:
                    object.__setattr__(self, name, expected(str(value)))
                except ValueError as exc:
                    raise ValueError(f"unsupported {name}") from exc
        if not isinstance(self.reasons, tuple):
            object.__setattr__(self, "reasons", tuple(self.reasons))

    @property
    def is_healthy(self) -> bool:
        """Whether the row may be presented as a settled, current fact.

        Deliberately *not* a single score: it is a conjunction over independent
        dimensions, and it is used only to decide whether a value may be shown as
        definitive - never to hide which dimension caused the doubt.
        """
        return (
            self.freshness is Freshness.LIVE
            and self.completeness is Completeness.COMPLETE
            and self.correction_state is CorrectionState.CURRENT
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "freshness": self.freshness.value,
            "completeness": self.completeness.value,
            "fidelity": self.fidelity.value,
            "uncertainty": self.uncertainty.value,
            "correction_state": self.correction_state.value,
            "reasons": list(self.reasons),
            "is_healthy": self.is_healthy,
        }


def unavailable(reason: str) -> TrustEnvelope:
    """Trust envelope for evidence that could not be authoritatively read.

    The store being unreadable is ``UNAVAILABLE`` freshness and ``UNKNOWN``
    completeness: it must never be reported as an empty-but-healthy result.
    """
    return TrustEnvelope(
        freshness=Freshness.UNAVAILABLE,
        completeness=Completeness.UNKNOWN,
        reasons=(str(reason),),
    )


def from_read_status(status: str, *, reason: str | None = None) -> TrustEnvelope:
    """Map a canonical read status onto the freshness dimension."""
    normalized = str(status or "").strip().upper()
    if normalized == "OK":
        return TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=Completeness.COMPLETE,
            reasons=(),
        )
    if normalized == "RETRYABLE":
        return TrustEnvelope(
            freshness=Freshness.DEGRADED,
            completeness=Completeness.UNKNOWN,
            reasons=(reason or "CANONICAL_READ_RETRYABLE",),
        )
    return unavailable(reason or f"CANONICAL_READ_{normalized or 'UNKNOWN'}")


__all__ = [
    "Completeness",
    "CorrectionState",
    "Fidelity",
    "Freshness",
    "TrustEnvelope",
    "Uncertainty",
    "from_read_status",
    "unavailable",
]
