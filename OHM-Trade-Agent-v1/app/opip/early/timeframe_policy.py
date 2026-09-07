"""Phase 2 fine-timeframe lead-time experiment (Issue #223, root cause D).

``app.scanner.market_scanner.analyze_symbol`` selects 15-minute movement
evidence only when hourly volatility is compressed::

    compressed_hourly = (
        bollinger_bandwidth_percentile <= 40.0 or atr_percentile <= 40.0
    )
    if compressed_hourly:
        movement = _movement_metrics(get_ohlc(symbol, interval=15), 15)

Ignition raises both bandwidth and ATR percentiles. The production policy can
therefore switch *back* to completed hourly candles at exactly the moment
finer resolution would reduce information age. That inversion is the
structural lead-time penalty the review identified.

This module makes both policies explicit and comparable. It performs no I/O
and changes no production decision: ``current_timeframe_decision`` reproduces
today's behaviour bit-for-bit, and the candidate policy is evaluated in shadow
behind ``OPIP_EARLY_TIMEFRAME_SHADOW_ENABLED``.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from app.opip.early.taxonomy import MarketPhase, coerce_market_phase

TIMEFRAME_POLICY_VERSION = "opip-early-timeframe-policy-v1"

TIMEFRAME_HOURLY = "1H"
TIMEFRAME_FINE = "15M"

#: Verbatim mirror of ``MOVEMENT_COMPRESSION_PREFILTER_PERCENTILE``.
PRODUCTION_COMPRESSION_PERCENTILE = 40.0

#: Phases in which the candidate policy also requests fine candles.
CANDIDATE_FINE_TIMEFRAME_PHASES = frozenset(
    {
        MarketPhase.COILED,
        MarketPhase.IGNITION,
        MarketPhase.EARLY_EXPANSION,
        MarketPhase.CONFIRMED_EXPANSION,
    }
)

#: Nominal information age of completed candles, in seconds. A completed
#: hourly candle is on average half an interval old before it can be used.
_INTERVAL_SECONDS = {TIMEFRAME_HOURLY: 3600.0, TIMEFRAME_FINE: 900.0}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def nominal_information_age_seconds(timeframe: str) -> float:
    """Expected age of the newest usable completed candle."""
    return _INTERVAL_SECONDS.get(str(timeframe).upper(), 3600.0) * 1.5


@dataclass(frozen=True)
class TimeframeDecision:
    """Which timeframe a policy selects, and why."""

    policy: str
    timeframe: str
    fine_timeframe_selected: bool
    reason: str

    @property
    def information_age_seconds(self) -> float:
        return nominal_information_age_seconds(self.timeframe)

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "timeframe": self.timeframe,
            "fine_timeframe_selected": self.fine_timeframe_selected,
            "reason": self.reason,
            "information_age_seconds": self.information_age_seconds,
        }


def current_timeframe_decision(
    *,
    bandwidth_percentile: Any,
    atr_percentile: Any,
    compression_percentile: float = PRODUCTION_COMPRESSION_PERCENTILE,
) -> TimeframeDecision:
    """Reproduce the production fine-timeframe decision exactly.

    Kept as a separate function so a test can assert the production policy is
    unchanged while the candidate policy evolves.
    """
    bandwidth = _finite(bandwidth_percentile, 100.0)
    atr = _finite(atr_percentile, 100.0)
    compressed = bandwidth <= compression_percentile or atr <= compression_percentile
    if compressed:
        return TimeframeDecision(
            policy="PRODUCTION_COMPRESSION_ONLY",
            timeframe=TIMEFRAME_FINE,
            fine_timeframe_selected=True,
            reason="hourly volatility is compressed",
        )
    return TimeframeDecision(
        policy="PRODUCTION_COMPRESSION_ONLY",
        timeframe=TIMEFRAME_HOURLY,
        fine_timeframe_selected=False,
        reason="hourly volatility is not compressed",
    )


def candidate_timeframe_decision(
    *,
    bandwidth_percentile: Any,
    atr_percentile: Any,
    phase: MarketPhase | str,
    compression_percentile: float = PRODUCTION_COMPRESSION_PERCENTILE,
) -> TimeframeDecision:
    """Candidate policy: keep fine candles through ignition and expansion.

    Strictly a superset of the production policy — it never removes fine
    candles that production would have used, so it cannot increase information
    age relative to today.
    """
    production = current_timeframe_decision(
        bandwidth_percentile=bandwidth_percentile,
        atr_percentile=atr_percentile,
        compression_percentile=compression_percentile,
    )
    if production.fine_timeframe_selected:
        return TimeframeDecision(
            policy="CANDIDATE_PHASE_AWARE",
            timeframe=TIMEFRAME_FINE,
            fine_timeframe_selected=True,
            reason="hourly volatility is compressed",
        )
    resolved = coerce_market_phase(phase)
    if resolved in CANDIDATE_FINE_TIMEFRAME_PHASES:
        return TimeframeDecision(
            policy="CANDIDATE_PHASE_AWARE",
            timeframe=TIMEFRAME_FINE,
            fine_timeframe_selected=True,
            reason=f"expansion phase {resolved.value} retains fine-timeframe evidence",
        )
    return TimeframeDecision(
        policy="CANDIDATE_PHASE_AWARE",
        timeframe=TIMEFRAME_HOURLY,
        fine_timeframe_selected=False,
        reason=f"phase {resolved.value} does not require fine-timeframe evidence",
    )


@dataclass(frozen=True)
class TimeframeComparison:
    """One shadow observation comparing both timeframe policies."""

    version: str
    symbol: str
    phase: str
    production: TimeframeDecision
    candidate: TimeframeDecision
    shadow_only: bool = True
    production_decision_changed: bool = False

    @property
    def inversion_detected(self) -> bool:
        """Whether production dropped fine candles during an expansion phase.

        This is the concrete root-cause-D signature: the candidate policy wants
        finer resolution and production does not, while the market is igniting
        or expanding.
        """
        return (
            self.candidate.fine_timeframe_selected
            and not self.production.fine_timeframe_selected
        )

    @property
    def information_age_reduction_seconds(self) -> float:
        """Seconds of information age the candidate policy would remove."""
        return max(
            0.0,
            self.production.information_age_seconds - self.candidate.information_age_seconds,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "symbol": self.symbol,
            "phase": self.phase,
            "production": self.production.as_dict(),
            "candidate": self.candidate.as_dict(),
            "inversion_detected": self.inversion_detected,
            "information_age_reduction_seconds": self.information_age_reduction_seconds,
            "shadow_only": self.shadow_only,
            "production_decision_changed": self.production_decision_changed,
            "trade_authority_changed": False,
        }


def compare_timeframe_policies(
    *,
    symbol: str,
    bandwidth_percentile: Any,
    atr_percentile: Any,
    phase: MarketPhase | str,
    compression_percentile: float = PRODUCTION_COMPRESSION_PERCENTILE,
) -> TimeframeComparison:
    """Build one shadow timeframe comparison. Never changes production."""
    resolved = coerce_market_phase(phase)
    return TimeframeComparison(
        version=TIMEFRAME_POLICY_VERSION,
        symbol=str(symbol).upper(),
        phase=resolved.value,
        production=current_timeframe_decision(
            bandwidth_percentile=bandwidth_percentile,
            atr_percentile=atr_percentile,
            compression_percentile=compression_percentile,
        ),
        candidate=candidate_timeframe_decision(
            bandwidth_percentile=bandwidth_percentile,
            atr_percentile=atr_percentile,
            phase=resolved,
            compression_percentile=compression_percentile,
        ),
    )


def summarize_timeframe_comparisons(
    comparisons: list[TimeframeComparison],
) -> dict[str, Any]:
    """Aggregate lead-time diagnostics across shadow observations."""
    total = len(comparisons)
    inversions = [item for item in comparisons if item.inversion_detected]
    reductions = [item.information_age_reduction_seconds for item in inversions]
    return {
        "version": TIMEFRAME_POLICY_VERSION,
        "observations": total,
        "inversions": len(inversions),
        "inversion_rate_pct": round(len(inversions) / total * 100.0, 4) if total else None,
        "mean_information_age_reduction_seconds": (
            round(sum(reductions) / len(reductions), 4) if reductions else None
        ),
        "inverted_symbols": sorted({item.symbol for item in inversions}),
        "shadow_only": True,
        "production_decision_changed": False,
    }
