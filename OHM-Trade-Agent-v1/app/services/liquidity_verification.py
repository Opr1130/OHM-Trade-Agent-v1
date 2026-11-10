"""Bounded liquidity stability verification (ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1).

A single order-book snapshot cannot distinguish a genuinely deep market from a
displayed book that is about to vanish. This module takes a small, bounded
sequence of independent depth observations for a candidate that is about to
become actionable and refuses the notification unless qualifying depth persists.

Design boundaries:

* **Bounded, never unbounded sleeps.** The whole sequence obeys one absolute
  deadline; if the observations cannot complete inside it the candidate is
  refused rather than delaying the scan worker.
* **No global cache and no retained books.** Each observation is consumed and
  released; only scalars survive.
* **It cannot qualify anything.** It can only refuse. Depth is measured in
  ``execution_validation.depth_evidence``; this module never re-implements the
  band rule, the threshold or execution feasibility.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

STABLE = "STABLE"
VANISHING = "VANISHING"
UNKNOWN = "UNKNOWN"

REASON_STABILITY_UNKNOWN = "LIQUIDITY_STABILITY_UNKNOWN"
REASON_LIQUIDITY_VANISHING = "LIQUIDITY_INSTABILITY"
REASON_OBSERVATION_FAILED = "LIQUIDITY_OBSERVATION_FAILED"
REASON_DEADLINE_EXCEEDED = "LIQUIDITY_VERIFICATION_DEADLINE"
REASON_INCONSISTENT_IDENTITY = "LIQUIDITY_BOOK_IDENTITY_INCONSISTENT"

DEFAULT_OBSERVATIONS = 3
DEFAULT_SPACING_SECONDS = 10.0
DEFAULT_DEADLINE_SECONDS = 45.0
DEFAULT_MAX_DROP_PCT = 35.0


@dataclass(frozen=True)
class DepthObservation:
    """One scalar summary of a depth measurement. Holds no book levels."""

    observed_at_utc: Any
    weaker_side_usd: float | None
    depth_status: str
    venue: str
    instrument: str


@dataclass(frozen=True)
class StabilityVerdict:
    status: str
    reason: str | None
    observations: int
    required: int
    minimum_weaker_side_usd: float | None
    maximum_drop_pct: float | None

    @property
    def stable(self) -> bool:
        return self.status == STABLE


def assess_depth_stability(
    observations: Sequence[DepthObservation],
    *,
    required: int = DEFAULT_OBSERVATIONS,
    threshold_usd: float | None = None,
    max_drop_pct: float = DEFAULT_MAX_DROP_PCT,
) -> StabilityVerdict:
    """Decide whether qualifying depth persisted across every observation.

    Refuses on: too few observations, any non-PASS depth status, a missing
    measurement, a mixed venue/instrument identity, qualifying depth falling
    below the threshold, or a decline beyond ``max_drop_pct``.
    """
    count = len(observations)
    wanted = max(1, int(required))
    if count < wanted:
        return StabilityVerdict(UNKNOWN, REASON_STABILITY_UNKNOWN, count,
                                wanted, None, None)

    identities = {(o.venue, o.instrument) for o in observations}
    if len(identities) != 1:
        return StabilityVerdict(UNKNOWN, REASON_INCONSISTENT_IDENTITY, count,
                                wanted, None, None)

    values: list[float] = []
    for observation in observations:
        if str(observation.depth_status).upper() != "PASS":
            return StabilityVerdict(UNKNOWN, REASON_OBSERVATION_FAILED, count,
                                    wanted, None, None)
        if observation.weaker_side_usd is None:
            return StabilityVerdict(UNKNOWN, REASON_OBSERVATION_FAILED, count,
                                    wanted, None, None)
        value = float(observation.weaker_side_usd)
        if threshold_usd is not None and value < float(threshold_usd):
            return StabilityVerdict(VANISHING, REASON_LIQUIDITY_VANISHING,
                                    count, wanted, min(values + [value]), None)
        values.append(value)

    floor = min(values)
    peak = max(values)
    drop_pct = 0.0 if peak <= 0 else (peak - floor) / peak * 100.0
    if drop_pct > float(max_drop_pct):
        return StabilityVerdict(VANISHING, REASON_LIQUIDITY_VANISHING, count,
                                wanted, floor, round(drop_pct, 4))
    return StabilityVerdict(STABLE, None, count, wanted, floor,
                            round(drop_pct, 4))


def collect_depth_observations(
    measure: Callable[[], DepthObservation],
    *,
    required: int = DEFAULT_OBSERVATIONS,
    spacing_seconds: float = DEFAULT_SPACING_SECONDS,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> StabilityVerdict:
    """Take ``required`` independent depth observations inside one deadline.

    ``sleep`` and ``monotonic`` are injected so tests never actually wait. The
    verdict never reports STABLE unless every observation completed inside the
    deadline; any failure or overrun refuses the candidate.
    """
    wanted = max(1, int(required))
    spacing = max(0.0, float(spacing_seconds))
    budget = float(deadline_seconds)
    started = monotonic()
    observations: list[DepthObservation] = []

    for index in range(wanted):
        if index:
            if monotonic() - started + spacing > budget:
                return StabilityVerdict(UNKNOWN, REASON_DEADLINE_EXCEEDED,
                                        len(observations), wanted, None, None)
            sleep(spacing)
        if monotonic() - started > budget:
            return StabilityVerdict(UNKNOWN, REASON_DEADLINE_EXCEEDED,
                                    len(observations), wanted, None, None)
        try:
            observations.append(measure())
        except Exception:
            # A failed observation is never treated as a passing one.
            return StabilityVerdict(UNKNOWN, REASON_OBSERVATION_FAILED,
                                    len(observations), wanted, None, None)

    return assess_depth_stability(observations, required=wanted)


__all__ = [
    "DEFAULT_DEADLINE_SECONDS",
    "DEFAULT_MAX_DROP_PCT",
    "DEFAULT_OBSERVATIONS",
    "DEFAULT_SPACING_SECONDS",
    "DepthObservation",
    "REASON_DEADLINE_EXCEEDED",
    "REASON_INCONSISTENT_IDENTITY",
    "REASON_LIQUIDITY_VANISHING",
    "REASON_OBSERVATION_FAILED",
    "REASON_STABILITY_UNKNOWN",
    "STABLE",
    "StabilityVerdict",
    "UNKNOWN",
    "VANISHING",
    "assess_depth_stability",
    "collect_depth_observations",
]

