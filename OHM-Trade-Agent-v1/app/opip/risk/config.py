"""Bounded configuration for the O'Pip Event Risk Shield."""

from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_LOOKBACK_SECONDS = 6 * 60 * 60
DEFAULT_MAX_EVENTS = 500
DEFAULT_MAX_RAW_EVENTS = 4_000
DEFAULT_STALE_EVENT_SECONDS = 24 * 60 * 60
DEFAULT_MAX_EXPOSURES = 200
DEFAULT_MAX_ASSESSMENTS_PER_CYCLE = 500
DEFAULT_MAX_ARCHIVE_SEGMENTS = 16

MIN_LOOKBACK_SECONDS = 60
MAX_LOOKBACK_SECONDS = 7 * 24 * 60 * 60
MIN_MAX_EVENTS = 1
MAX_MAX_EVENTS = 5_000


def _clamped_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return max(low, min(high, value))


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EventRiskShieldConfig:
    """Every runtime bound the shield applies during one cycle.

    Production defaults remain dark until a separate activation change.
    """

    enabled: bool = False
    lookback_seconds: int = DEFAULT_LOOKBACK_SECONDS
    max_events: int = DEFAULT_MAX_EVENTS
    max_raw_events: int = DEFAULT_MAX_RAW_EVENTS
    max_exposures: int = DEFAULT_MAX_EXPOSURES
    max_assessments_per_cycle: int = DEFAULT_MAX_ASSESSMENTS_PER_CYCLE
    max_archive_segments: int = DEFAULT_MAX_ARCHIVE_SEGMENTS
    stale_event_seconds: int = DEFAULT_STALE_EVENT_SECONDS
    include_paper: bool = True

    def __post_init__(self) -> None:
        for name in (
            "lookback_seconds",
            "max_events",
            "max_raw_events",
            "max_exposures",
            "max_assessments_per_cycle",
            "max_archive_segments",
            "stale_event_seconds",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_env(cls) -> "EventRiskShieldConfig":
        return cls(
            enabled=_bool_env("OPIP_EVENT_RISK_SHIELD_ENABLED", False),
            lookback_seconds=_clamped_int(
                "OPIP_EVENT_RISK_LOOKBACK_SECONDS",
                DEFAULT_LOOKBACK_SECONDS,
                MIN_LOOKBACK_SECONDS,
                MAX_LOOKBACK_SECONDS,
            ),
            max_events=_clamped_int(
                "OPIP_EVENT_RISK_MAX_EVENTS",
                DEFAULT_MAX_EVENTS,
                MIN_MAX_EVENTS,
                MAX_MAX_EVENTS,
            ),
            max_raw_events=_clamped_int(
                "OPIP_EVENT_RISK_MAX_RAW_EVENTS",
                DEFAULT_MAX_RAW_EVENTS,
                DEFAULT_MAX_EVENTS,
                50_000,
            ),
            max_exposures=_clamped_int(
                "OPIP_EVENT_RISK_MAX_EXPOSURES", DEFAULT_MAX_EXPOSURES, 1, 2_000
            ),
            max_assessments_per_cycle=_clamped_int(
                "OPIP_EVENT_RISK_MAX_ASSESSMENTS",
                DEFAULT_MAX_ASSESSMENTS_PER_CYCLE,
                1,
                10_000,
            ),
            max_archive_segments=_clamped_int(
                "OPIP_EVENT_RISK_MAX_ARCHIVE_SEGMENTS",
                DEFAULT_MAX_ARCHIVE_SEGMENTS,
                1,
                256,
            ),
            stale_event_seconds=_clamped_int(
                "OPIP_EVENT_RISK_STALE_EVENT_SECONDS",
                DEFAULT_STALE_EVENT_SECONDS,
                60,
                30 * 24 * 60 * 60,
            ),
            include_paper=_bool_env("OPIP_EVENT_RISK_INCLUDE_PAPER", True),
        )
