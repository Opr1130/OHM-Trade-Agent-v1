"""Parity between feature-bus values and existing production calculations.

The purpose is to prove the feature bus is not a second opinion. Given the same
minute series, its values must equal what the production indicator functions
produce when called directly.

Tolerance is exactly zero and there is no parameter to relax it. Identical
inputs through identical math must produce identical numbers; anything else is a
defect in the adapter, not a rounding question. A mismatch is reported as
evidence rather than absorbed.

One genuine divergence already exists in production and is reported here rather
than silently resolved: ``app.indicators.technical.percentile_rank`` counts
values at or below the target, while ``app.services.signal_features.percentile_rank``
splits ties at their midpoint. On a flat series the first returns 100 and the
second returns 50. The feature bus uses the indicator definition because its
inputs are one instrument's own history; choosing a single project-wide
definition is a scoring-policy decision, not a PR3 decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from app.indicators.technical import (
    atr_percentage_series,
    bollinger_bandwidth,
    ema,
    percentile_rank as indicator_percentile_rank,
    volume_ratio,
)
from app.opip.contracts.identity import InstrumentVersion
from app.opip.features.engine import (
    ATR_PERIOD,
    BANDWIDTH_PERIOD,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    FEATURE_WINDOW_INTERVALS,
    VALUE_PRECISION,
    VOLUME_PERIOD,
    compute_features,
)
from app.opip.market.aggregates import AlignmentResult, contiguous_tail
from app.services.signal_features import percentile_rank as scan_percentile_rank

#: Not configurable on purpose. See the module docstring.
ABSOLUTE_TOLERANCE = 0.0


@dataclass(frozen=True)
class ParityCheck:
    feature: str
    feature_bus_value: float | None
    reference_value: float | None
    reference: str

    @property
    def matched(self) -> bool:
        if self.feature_bus_value is None or self.reference_value is None:
            return self.feature_bus_value == self.reference_value
        return (
            abs(float(self.feature_bus_value) - float(self.reference_value))
            <= ABSOLUTE_TOLERANCE
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "feature_bus_value": self.feature_bus_value,
            "reference_value": self.reference_value,
            "reference": self.reference,
            "matched": self.matched,
        }


@dataclass(frozen=True)
class ParityReport:
    checks: tuple[ParityCheck, ...]
    intervals: int

    @property
    def mismatches(self) -> tuple[ParityCheck, ...]:
        return tuple(check for check in self.checks if not check.matched)

    @property
    def matched(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "intervals": self.intervals,
            "matched": self.matched,
            "checks": [check.to_dict() for check in self.checks],
            "mismatch_count": len(self.mismatches),
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), VALUE_PRECISION)


def compare_against_production_indicators(
    alignment: AlignmentResult,
    *,
    instrument_version: InstrumentVersion,
    evaluated_at_utc: datetime,
) -> ParityReport:
    """Recompute selected features by calling production math directly."""
    tail = contiguous_tail(alignment)[-FEATURE_WINDOW_INTERVALS:]
    closes = [float(item.values["close"]) for item in tail]
    highs = [float(item.values["high"]) for item in tail]
    lows = [float(item.values["low"]) for item in tail]
    volumes = [float(item.values["volume"]) for item in tail]

    computed = compute_features(
        alignment,
        instrument_version=instrument_version,
        evaluated_at_utc=evaluated_at_utc,
    ).values

    checks: list[ParityCheck] = []

    def add(feature: str, reference: float | None, source: str) -> None:
        checks.append(
            ParityCheck(
                feature=feature,
                feature_bus_value=computed.get(feature),
                reference_value=_round(reference),
                reference=source,
            )
        )

    add(
        "ema_fast_9",
        ema(closes, EMA_FAST_PERIOD) if len(closes) >= EMA_FAST_PERIOD else None,
        "app.indicators.technical.ema",
    )
    add(
        "ema_slow_21",
        ema(closes, EMA_SLOW_PERIOD) if len(closes) >= EMA_SLOW_PERIOD else None,
        "app.indicators.technical.ema",
    )
    add(
        "atr_pct_14",
        atr_percentage_series(highs, lows, closes, ATR_PERIOD)[-1]
        if len(closes) >= ATR_PERIOD + 1
        else None,
        "app.indicators.technical.atr_percentage_series",
    )
    add(
        "bandwidth_20",
        bollinger_bandwidth(closes, BANDWIDTH_PERIOD)
        if len(closes) >= BANDWIDTH_PERIOD
        else None,
        "app.indicators.technical.bollinger_bandwidth",
    )
    add(
        "relative_volume_20m",
        volume_ratio(volumes, VOLUME_PERIOD)
        if len(volumes) >= VOLUME_PERIOD + 1
        else None,
        "app.indicators.technical.volume_ratio",
    )
    return ParityReport(checks=tuple(checks), intervals=len(tail))


@dataclass(frozen=True)
class PercentileDefinitionDivergence:
    """Two production percentile definitions, reported side by side."""

    values: tuple[float, ...]
    indicator_definition: float
    scan_definition: float

    @property
    def diverges(self) -> bool:
        return self.indicator_definition != self.scan_definition

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_size": len(self.values),
            "indicator_definition": self.indicator_definition,
            "scan_definition": self.scan_definition,
            "diverges": self.diverges,
            "indicator_source": "app.indicators.technical.percentile_rank",
            "scan_source": "app.services.signal_features.percentile_rank",
            "disposition": (
                "Known divergence. Reported as PR3 parity evidence; choosing a "
                "single project-wide percentile definition is a scoring-policy "
                "decision outside PR3 scope."
            ),
        }


def percentile_definition_divergence(
    values: Sequence[float], target: float | None = None
) -> PercentileDefinitionDivergence:
    if not values:
        raise ValueError("percentile comparison requires at least one value")
    probe = float(values[-1] if target is None else target)
    return PercentileDefinitionDivergence(
        values=tuple(float(item) for item in values),
        indicator_definition=indicator_percentile_rank(values, probe),
        scan_definition=scan_percentile_rank(values, probe),
    )


__all__ = [
    "ABSOLUTE_TOLERANCE",
    "ParityCheck",
    "ParityReport",
    "PercentileDefinitionDivergence",
    "compare_against_production_indicators",
    "percentile_definition_divergence",
]
