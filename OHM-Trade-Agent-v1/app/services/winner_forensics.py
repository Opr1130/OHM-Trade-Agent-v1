from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from app.services.daily_gainer_reconciliation import DailyGainerTrace


@dataclass(frozen=True)
class WinnerForensicCase:
    symbol: str
    gain_today_pct: float
    liquidity_24h_usd_approx: float
    classification: str
    first_phase: str | None
    gain_when_first_seen_pct: float | None
    remaining_upside_after_first_seen_pct: float | None
    reason: str
    retrospective_only: bool = True
    production_decision_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_winner_trace(trace: DailyGainerTrace) -> WinnerForensicCase:
    seen_gain = trace.gain_when_first_seen_pct
    remaining = trace.remaining_upside_after_first_seen_pct
    phase = trace.first_ohm_phase

    if trace.trace_status == "NOT_SEEN_BY_WAVE5":
        classification = "COVERAGE_MISS"
        reason = "top gainer was never admitted to Wave 5 observation"
    elif seen_gain is not None and seen_gain <= 3.0 and phase == "DORMANT" and (remaining or 0.0) >= 8.0:
        classification = "SEEN_EARLY_UNDERESTIMATED"
        reason = "OHM observed the asset early, but DORMANT interpretation missed substantial later upside"
    elif seen_gain is not None and seen_gain <= 4.0 and phase in {"IGNITION", "EARLY_EXPANSION"} and (remaining or 0.0) >= 5.0:
        classification = "EARLY_DETECTION_SUCCESS"
        reason = "OHM identified an early expansion state before substantial additional upside"
    elif seen_gain is not None and seen_gain >= 8.0:
        classification = "LATE_DETECTION"
        reason = "OHM detected the move only after a material portion of the daily gain had already occurred"
    elif phase in {"LATE_EXTENSION", "EXHAUSTION_RISK"} and seen_gain is not None and seen_gain <= 4.0 and (remaining or 0.0) >= 8.0:
        classification = "PHASE_MISCLASSIFICATION"
        reason = "OHM observed the asset early but labeled it as late/exhausted before large continuation"
    else:
        classification = "PARTIAL_OR_AMBIGUOUS"
        reason = "trace does not yet fit a high-confidence coverage, timing, or interpretation failure bucket"

    return WinnerForensicCase(
        symbol=trace.symbol,
        gain_today_pct=trace.gain_today_pct,
        liquidity_24h_usd_approx=trace.liquidity_24h_usd_approx,
        classification=classification,
        first_phase=phase,
        gain_when_first_seen_pct=seen_gain,
        remaining_upside_after_first_seen_pct=remaining,
        reason=reason,
    )


def classify_winner_traces(traces: Iterable[DailyGainerTrace]) -> list[WinnerForensicCase]:
    return [classify_winner_trace(trace) for trace in traces]
