"""Point-in-time safety for runtime early-detection features.

A runtime feature computed for a decision at time ``t`` may only use
information known at or before ``t``. Forward information (peak, MFE, MAE,
final outcome, realised return) belongs exclusively to the offline learning
plane. This module makes that rule executable instead of aspirational, so a
counterfactual replay cannot quietly acquire lookahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

#: Substrings that identify forward-looking evidence. Any runtime feature name
#: containing one of these is rejected outright.
FORWARD_FEATURE_MARKERS = (
    "peak",
    "mfe",
    "mae",
    "outcome",
    "future",
    "forward",
    "realized_return",
    "realised_return",
    "final_",
    "exit_",
    "max_favorable",
    "max_adverse",
    "hindsight",
    "lookahead",
)

#: Names that are forward-looking but do not contain a marker substring.
FORWARD_FEATURE_NAMES = frozenset(
    {
        "winner",
        "loser",
        "resolved_label",
        "target_hit",
        "stop_hit",
    }
)


class LookaheadError(ValueError):
    """Raised when a runtime feature would consume forward information."""


def is_forward_feature(name: Any) -> bool:
    """Whether ``name`` denotes information unknowable at decision time."""
    token = str(name or "").strip().lower()
    if not token:
        return False
    if token in FORWARD_FEATURE_NAMES:
        return True
    return any(marker in token for marker in FORWARD_FEATURE_MARKERS)


def forward_feature_names(names: Iterable[Any]) -> tuple[str, ...]:
    """Return every offending name, sorted, for a single actionable error."""
    return tuple(sorted({str(name) for name in names if is_forward_feature(name)}))


def assert_point_in_time_safe(features: Mapping[str, Any] | Iterable[Any]) -> None:
    """Fail closed when a runtime feature set contains forward information."""
    names = features.keys() if isinstance(features, Mapping) else features
    offending = forward_feature_names(names)
    if offending:
        raise LookaheadError(
            "runtime features may not consume forward information: "
            + ", ".join(offending)
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("point-in-time timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PointInTimeWindow:
    """An inclusive upper bound on observable evidence for one decision."""

    decision_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_at", _utc(self.decision_at))

    def admits(self, observed_at: datetime | None) -> bool:
        """Whether an observation is visible to this decision."""
        if observed_at is None:
            return False
        return _utc(observed_at) <= self.decision_at

    def filter(self, rows: Iterable[Mapping[str, Any]], *, key: str = "observed_at") -> list[dict[str, Any]]:
        """Keep only rows observed at or before :attr:`decision_at`.

        Rows with a missing or unparseable timestamp are dropped rather than
        admitted, so malformed evidence cannot leak future information.
        """
        admitted: list[dict[str, Any]] = []
        for row in rows:
            raw = row.get(key) if isinstance(row, Mapping) else None
            parsed = parse_timestamp(raw)
            if parsed is not None and self.admits(parsed):
                admitted.append(dict(row))
        return admitted


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning ``None`` when unusable."""
    if isinstance(value, datetime):
        try:
            return _utc(value)
        except ValueError:
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        # Naive ISO strings are unusable: assuming UTC would admit a different
        # instant than a naive datetime object, which parse_timestamp already
        # rejects. Fail closed so replay cannot change the admitted set.
        return None
    return parsed.astimezone(timezone.utc)
