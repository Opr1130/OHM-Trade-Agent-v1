"""Evidence-only screening evaluations for Stage 0.

One row represents one venue instrument evaluated by one scanner in one scan.
Directional scores coexist in the row because the production selector makes an
instrument-level advance decision before a direction becomes a candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Mapping

from app.opip.identity import ResolvedInstrumentIdentity
from app.opip.decision.versioning import STRATEGY_VERSION


class ScannerType(str, Enum):
    EARLY_WATCH = "EARLY_WATCH"
    BROAD_SEARCH = "BROAD_SEARCH"


class ScreeningOutcome(str, Enum):
    ADVANCED = "ADVANCED"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    BELOW_COARSE_THRESHOLD = "BELOW_COARSE_THRESHOLD"
    COARSE_RANK_LIMIT = "COARSE_RANK_LIMIT"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    EXCLUDED_MARKET = "EXCLUDED_MARKET"


def _score(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("screening scores must be finite")
    return number


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ScreeningEvaluation:
    observed_at: datetime
    scan_id: str
    scanner_type: ScannerType
    venue_instrument: ResolvedInstrumentIdentity
    outcome: ScreeningOutcome
    long_score: float | None = None
    short_score: float | None = None
    advanced_direction: str | None = None
    reason: str | None = None
    metadata: Mapping[str, Any] | None = None
    strategy_version: str = STRATEGY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        if not str(self.scan_id or "").strip():
            raise ValueError("scan_id is required")
        object.__setattr__(self, "long_score", _score(self.long_score))
        object.__setattr__(self, "short_score", _score(self.short_score))
        direction = (
            str(self.advanced_direction).strip().upper()
            if self.advanced_direction is not None
            else None
        )
        if direction not in {None, "LONG", "SHORT"}:
            raise ValueError("advanced_direction must be LONG, SHORT, or None")
        if self.outcome is not ScreeningOutcome.ADVANCED and direction is not None:
            raise ValueError("only ADVANCED evaluations may select a direction")
        object.__setattr__(self, "advanced_direction", direction)
        if not str(self.strategy_version or "").strip():
            raise ValueError("strategy_version is required")

    @property
    def identity_tuple(self) -> tuple[str, str, str, str]:
        return (
            self.observed_at.isoformat(),
            self.scan_id,
            self.scanner_type.value,
            self.venue_instrument.venue_instrument_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "observed_at": self.observed_at.isoformat(),
            "scan_id": self.scan_id,
            "scanner_type": self.scanner_type.value,
            "venue_instrument": self.venue_instrument.to_dict(),
            "venue_instrument_id": self.venue_instrument.venue_instrument_id,
            "outcome": self.outcome.value,
            "long_score": self.long_score,
            "short_score": self.short_score,
            "advanced_direction": self.advanced_direction,
            "reason": self.reason,
            "metadata": dict(self.metadata) if self.metadata is not None else None,
            "strategy_version": self.strategy_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScreeningEvaluation":
        identity = payload.get("venue_instrument")
        if not isinstance(identity, Mapping):
            raise ValueError("venue_instrument is required")
        metadata = payload.get("metadata")
        return cls(
            observed_at=datetime.fromisoformat(str(payload["observed_at"])),
            scan_id=str(payload["scan_id"]),
            scanner_type=ScannerType(payload["scanner_type"]),
            venue_instrument=ResolvedInstrumentIdentity.from_dict(identity),
            outcome=ScreeningOutcome(payload["outcome"]),
            long_score=payload.get("long_score"),
            short_score=payload.get("short_score"),
            advanced_direction=(
                str(payload["advanced_direction"])
                if payload.get("advanced_direction") is not None
                else None
            ),
            reason=(
                str(payload["reason"])
                if payload.get("reason") is not None
                else None
            ),
            metadata=(dict(metadata) if isinstance(metadata, Mapping) else None),
            strategy_version=str(payload.get("strategy_version") or STRATEGY_VERSION),
        )
