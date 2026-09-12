"""Shared vocabulary for the O'Pip feature bus (Contract B).

Pure vocabulary only: no exchange, storage, canonical-writer, detector,
notification, or risk dependency. Values are the durable strings written to
canonical evidence, so they may not be renamed without a contract change.
"""

from __future__ import annotations

from enum import Enum


class CoverageState(str, Enum):
    """Whether the evidence window backing a record is known to be complete."""

    COMPLETE = "COMPLETE"
    INCOMPLETE_COVERAGE = "INCOMPLETE_COVERAGE"


class Missingness(str, Enum):
    """Why a named input is or is not present in a snapshot.

    ``NOT_RETAINED`` is distinct from ``MISSING``: the first records a
    deliberate retention decision (raw trades are ephemeral by contract), the
    second records evidence O'Pip expected and did not get.
    """

    PRESENT = "PRESENT"
    NOT_RETAINED = "NOT_RETAINED"
    MISSING = "MISSING"


class RestartState(str, Enum):
    """Warm-up provenance for a feature state.

    These are separable facts, not one "not ready" bucket. A brand new listing
    is epistemically different from a restart that has retained history.
    """

    NEW_LISTING_COLD_START = "NEW_LISTING_COLD_START"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    RESTART_WARMUP = "RESTART_WARMUP"
    WARM = "WARM"


class PayloadKind(str, Enum):
    """What one observation physically carries."""

    FIXED_INTERVAL_AGGREGATE = "fixed_interval_aggregate"
    TRADE = "trade"
    TICKER = "ticker"


class CompressionState(str, Enum):
    """Compression is a feature, never a lifecycle state.

    Ruling D3: COILED is informative but does not become a seventh detector
    lifecycle state. It is carried here as a feature-level condition alongside
    compression depth, duration and release score.
    """

    COILED = "COILED"
    NEUTRAL = "NEUTRAL"
    EXPANDED = "EXPANDED"
    UNKNOWN = "UNKNOWN"


class TrendState(str, Enum):
    """EMA structure classification. Carries no probability or ranking."""

    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"
    UNKNOWN = "UNKNOWN"


class VolatilityState(str, Enum):
    """ATR regime classification relative to the instrument's own history."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


__all__ = [
    "CompressionState",
    "CoverageState",
    "Missingness",
    "PayloadKind",
    "RestartState",
    "TrendState",
    "VolatilityState",
]
