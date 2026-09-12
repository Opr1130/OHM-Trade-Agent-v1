"""Canonical import surface for point-in-time integrity primitives.

The shared architecture owns availability/visibility semantics. The proven
implementation still lives physically in ``app.opip.ml.temporal``; PR3 reuses
it rather than recoding a second, subtly different copy. A later retirement PR
moves the implementation here once every consumer imports through this module.

``app.opip.ml.temporal`` is dependency-free (stdlib only), so this re-export
does not weaken the contracts-layer dependency rule.
"""

from __future__ import annotations

from app.opip.ml.temporal import (
    AvailabilityStamp,
    TemporalIntegrityError,
    assert_point_in_time,
    max_visible_at,
    require_utc,
)

__all__ = [
    "AvailabilityStamp",
    "TemporalIntegrityError",
    "assert_point_in_time",
    "max_visible_at",
    "require_utc",
]
