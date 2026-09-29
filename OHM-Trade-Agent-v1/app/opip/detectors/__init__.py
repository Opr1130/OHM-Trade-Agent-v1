"""R3 F3 detector package: the pure stateful detector runtime.

One package, one detector family. v1 has a single active detector, IGNITION,
and the package deliberately contains no lifecycle, persistence, scheduler,
registry or Feature Bus wiring. ``evaluate()`` is a pure function of a sealed
``FeatureSnapshot``, a prior ``DetectorState`` and an explicit
``evaluation_time``.
"""

from __future__ import annotations

from app.opip.detectors.ignition import (
    DEBOUNCE_INTERVALS,
    DETECTOR_VERSION,
    ENTRY_MIN_CONFIRMATIONS,
    ENTRY_PERSISTENCE_SECONDS,
    EVALUATION_GRID_SECONDS,
    HOLD_MIN_CONFIRMATIONS,
    POLICY_VERSION,
    RELEASE_PERSISTENCE_SECONDS,
    REQUIRED_FEATURES,
    evaluate,
)

__all__ = [
    "DEBOUNCE_INTERVALS",
    "DETECTOR_VERSION",
    "ENTRY_MIN_CONFIRMATIONS",
    "ENTRY_PERSISTENCE_SECONDS",
    "EVALUATION_GRID_SECONDS",
    "HOLD_MIN_CONFIRMATIONS",
    "POLICY_VERSION",
    "RELEASE_PERSISTENCE_SECONDS",
    "REQUIRED_FEATURES",
    "evaluate",
]
