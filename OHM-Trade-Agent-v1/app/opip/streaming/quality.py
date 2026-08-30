"""Evidence-quality model for streaming windows.

Critical invariant: degraded or incomplete streaming evidence must never
silently appear equivalent to complete evidence. This module makes that
distinction machine-readable so a later consumer (Sequence 4/5) can enforce
it without re-deriving quality from raw counters itself.

Fail-closed rule: a window carrying any unresolved degradation cannot, on its
own, produce a positive confirmation. `can_independently_confirm` is the one
function later components should call to check that — encoding the rule once
here rather than letting each caller re-implement the threshold logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.opip.streaming.contract import EVIDENCE_QUALITY_RANK, EvidenceQualityState
from app.opip.streaming.windows import WindowAccumulator


# Structural defaults, not statistically calibrated thresholds. Configuration,
# not a claim about "the right" values for live markets.
DEFAULT_MAX_OUT_OF_ORDER_RATIO = 0.05
DEFAULT_MAX_LATE_FRAME_RATIO = 0.05
DEFAULT_MAX_DROPPED_FRAME_RATIO = 0.02


DEGRADATION_SEQUENCE_GAP = "SEQUENCE_GAP"
DEGRADATION_QUEUE_OVERFLOW = "QUEUE_OVERFLOW"
DEGRADATION_FRAMES_DROPPED = "FRAMES_DROPPED"
DEGRADATION_EXCESSIVE_OUT_OF_ORDER = "EXCESSIVE_OUT_OF_ORDER"
DEGRADATION_LATE_EVENTS = "LATE_EVENTS"
DEGRADATION_RECONNECT_BOUNDARY = "RECONNECT_BOUNDARY"
DEGRADATION_UNKNOWN_SEQUENCE = "UNKNOWN_SEQUENCE"
DEGRADATION_INCOMPLETE_VENUE_COVERAGE = "INCOMPLETE_VENUE_COVERAGE"
DEGRADATION_INVALID_IDENTITY = "INVALID_IDENTITY"
DEGRADATION_EMPTY_WINDOW = "EMPTY_WINDOW"


@dataclass(frozen=True)
class EvidenceQuality:
    state: EvidenceQualityState
    degradations: frozenset[str]

    def __post_init__(self) -> None:
        if self.state == EvidenceQualityState.COMPLETE and self.degradations:
            raise ValueError("COMPLETE quality cannot carry degradation reasons")
        if self.state != EvidenceQualityState.COMPLETE and not self.degradations:
            raise ValueError("non-COMPLETE quality requires at least one reason")

    @property
    def rank(self) -> int:
        return EVIDENCE_QUALITY_RANK[self.state]


COMPLETE = EvidenceQuality(state=EvidenceQualityState.COMPLETE, degradations=frozenset())


def assess_window_quality(
    accumulator: WindowAccumulator,
    *,
    max_out_of_order_ratio: float = DEFAULT_MAX_OUT_OF_ORDER_RATIO,
    max_late_frame_ratio: float = DEFAULT_MAX_LATE_FRAME_RATIO,
    max_dropped_frame_ratio: float = DEFAULT_MAX_DROPPED_FRAME_RATIO,
) -> EvidenceQuality:
    """Deterministic quality verdict from one window's bounded counters."""
    if accumulator.is_empty:
        return EvidenceQuality(
            state=EvidenceQualityState.INCOMPLETE,
            degradations=frozenset({DEGRADATION_EMPTY_WINDOW}),
        )

    reasons: set[str] = set()
    total_raw = accumulator.observation_count + accumulator.dropped_frame_count

    if accumulator.has_sequence_gap:
        reasons.add(DEGRADATION_SEQUENCE_GAP)
    if accumulator.reconnect_boundary_count > 0:
        reasons.add(DEGRADATION_RECONNECT_BOUNDARY)
    if accumulator.unsupported_sequence_count > 0:
        reasons.add(DEGRADATION_UNKNOWN_SEQUENCE)

    if total_raw > 0 and (
        accumulator.dropped_frame_count / total_raw > max_dropped_frame_ratio
    ):
        reasons.add(DEGRADATION_FRAMES_DROPPED)
    if accumulator.observation_count > 0 and (
        accumulator.out_of_order_count / accumulator.observation_count
        > max_out_of_order_ratio
    ):
        reasons.add(DEGRADATION_EXCESSIVE_OUT_OF_ORDER)
    if accumulator.observation_count > 0 and (
        accumulator.late_frame_count / accumulator.observation_count
        > max_late_frame_ratio
    ):
        reasons.add(DEGRADATION_LATE_EVENTS)

    if not reasons:
        return COMPLETE

    # A sequence gap or heavy frame loss means the window's evidence cannot be
    # trusted as representative; everything else is DEGRADED (present but
    # imperfect), not INCOMPLETE (missing).
    if DEGRADATION_SEQUENCE_GAP in reasons or DEGRADATION_FRAMES_DROPPED in reasons:
        state = EvidenceQualityState.INCOMPLETE
    else:
        state = EvidenceQualityState.DEGRADED
    return EvidenceQuality(state=state, degradations=frozenset(reasons))


def combine_quality(qualities: list[EvidenceQuality]) -> EvidenceQuality:
    """Combine several quality verdicts into the worst one, reasons unioned.

    Used to fold e.g. per-venue quality into one cross-venue verdict. An empty
    input is UNUSABLE: no evidence at all is not the same as complete
    evidence, and must not default to COMPLETE.
    """
    if not qualities:
        return EvidenceQuality(
            state=EvidenceQualityState.UNUSABLE,
            degradations=frozenset({DEGRADATION_INCOMPLETE_VENUE_COVERAGE}),
        )
    worst = max(qualities, key=lambda item: item.rank)
    if worst.state == EvidenceQualityState.COMPLETE:
        return COMPLETE
    reasons: set[str] = set()
    for quality in qualities:
        reasons.update(quality.degradations)
    return EvidenceQuality(state=worst.state, degradations=frozenset(reasons))


def can_independently_confirm(quality: EvidenceQuality) -> bool:
    """Fail-closed rule: only COMPLETE evidence may independently confirm.

    This does not wire into the O'Pip Decision Engine or Sequence 3 — it just
    makes the rule machine-readable for whichever later component enforces
    it.
    """
    return quality.state == EvidenceQualityState.COMPLETE
