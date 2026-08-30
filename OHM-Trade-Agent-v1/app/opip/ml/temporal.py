"""Point-in-time integrity primitives for O'Pip ML evidence.

This module is intentionally dependency-free and has no trading, exchange,
network, or notification imports. It defines the epistemic boundary used by
ML training and replay: a value is eligible only when it was visible to O'Pip
no later than the deterministic decision timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


class TemporalIntegrityError(ValueError):
    """Raised when evidence violates point-in-time eligibility."""


def require_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TemporalIntegrityError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class AvailabilityStamp:
    """Availability provenance for one feature or source value."""

    source_at_utc: datetime
    ingested_at_utc: datetime
    visible_at_utc: datetime
    source_version: str = "v1"

    def __post_init__(self) -> None:
        source = require_utc(self.source_at_utc, field_name="source_at_utc")
        ingested = require_utc(self.ingested_at_utc, field_name="ingested_at_utc")
        visible = require_utc(self.visible_at_utc, field_name="visible_at_utc")
        if visible < ingested:
            raise TemporalIntegrityError(
                "visible_at_utc cannot precede ingested_at_utc"
            )
        if not str(self.source_version or "").strip():
            raise TemporalIntegrityError("source_version is required")
        object.__setattr__(self, "source_at_utc", source)
        object.__setattr__(self, "ingested_at_utc", ingested)
        object.__setattr__(self, "visible_at_utc", visible)

    def eligible_at(self, decision_at_utc: datetime) -> bool:
        decision = require_utc(decision_at_utc, field_name="decision_at_utc")
        return self.visible_at_utc <= decision


def max_visible_at(stamps: Iterable[AvailabilityStamp]) -> datetime | None:
    values = [stamp.visible_at_utc for stamp in stamps]
    return max(values) if values else None


def assert_point_in_time(
    stamps: Iterable[AvailabilityStamp], *, decision_at_utc: datetime
) -> None:
    decision = require_utc(decision_at_utc, field_name="decision_at_utc")
    latest = max_visible_at(stamps)
    if latest is not None and latest > decision:
        raise TemporalIntegrityError(
            f"feature visibility {latest.isoformat()} exceeds decision_at "
            f"{decision.isoformat()}"
        )
