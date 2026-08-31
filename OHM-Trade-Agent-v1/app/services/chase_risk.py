"""Deterministic, advisory-only chase-risk assessment for Phase 3B."""

from __future__ import annotations

from dataclasses import dataclass


BAND_LOW = "LOW"
BAND_MODERATE = "MODERATE"
BAND_HIGH = "HIGH"
BAND_EXTREME = "EXTREME"


@dataclass(frozen=True)
class ChaseRiskInput:
    current_price: float
    breakout_level: float | None = None
    recent_high: float | None = None
    distance_from_24h_high_pct: float | None = None
    lift_from_24h_low_pct: float | None = None
    move_completed_fraction_pct: float | None = None
    persistence_scans: int | None = None
    exhaustion_penalty: int | None = None
    retest_state: str | None = None


@dataclass(frozen=True)
class ChaseRiskAssessment:
    score: int
    band: str
    extension_pct_from_breakout: float | None
    distance_from_recent_high_pct: float | None
    retest_available: bool
    late_entry: bool
    reasons: tuple[str, ...]
    advisory_only: bool = True


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _band(score: int) -> str:
    if score >= 80:
        return BAND_EXTREME
    if score >= 60:
        return BAND_HIGH
    if score >= 35:
        return BAND_MODERATE
    return BAND_LOW


def assess_chase_risk(data: ChaseRiskInput) -> ChaseRiskAssessment:
    """Score late-entry/chase risk without authorizing or blocking a trade.

    The priors are interpretable Phase 3B heuristics, not calibrated
    probabilities. Missing data contributes no invented certainty.
    """
    if data.current_price <= 0:
        return ChaseRiskAssessment(
            score=0,
            band=BAND_LOW,
            extension_pct_from_breakout=None,
            distance_from_recent_high_pct=None,
            retest_available=False,
            late_entry=False,
            reasons=("insufficient valid current price; neutral risk",),
        )

    score = 0.0
    reasons: list[str] = []

    extension = None
    if data.breakout_level is not None and data.breakout_level > 0:
        extension = (data.current_price / data.breakout_level - 1.0) * 100.0
        positive_extension = max(0.0, extension)
        contribution = _clamp(positive_extension * 4.0, 0.0, 35.0)
        score += contribution
        if contribution >= 20:
            reasons.append(f"price extended {extension:.1f}% beyond breakout")
        elif contribution > 0:
            reasons.append(f"price {extension:.1f}% above breakout")

    distance_recent_high = None
    if data.recent_high is not None and data.recent_high > 0:
        distance_recent_high = max(0.0, (data.recent_high - data.current_price) / data.current_price * 100.0)
        if distance_recent_high <= 1.0:
            score += 18.0
            reasons.append("price within 1% of recent high")
        elif distance_recent_high <= 3.0:
            score += 10.0
            reasons.append("price within 3% of recent high")
    elif data.distance_from_24h_high_pct is not None:
        distance_recent_high = max(0.0, data.distance_from_24h_high_pct)
        if distance_recent_high <= 1.0:
            score += 15.0
            reasons.append("price within 1% of 24h high")
        elif distance_recent_high <= 3.0:
            score += 8.0
            reasons.append("price within 3% of 24h high")

    if data.lift_from_24h_low_pct is not None:
        lift = max(0.0, data.lift_from_24h_low_pct)
        if lift >= 40:
            score += 18.0
            reasons.append(f"already {lift:.1f}% above 24h low")
        elif lift >= 20:
            score += 10.0
            reasons.append(f"already {lift:.1f}% above 24h low")

    if data.move_completed_fraction_pct is not None:
        completed = _clamp(data.move_completed_fraction_pct)
        if completed >= 80:
            score += 20.0
            reasons.append(f"{completed:.0f}% of measured move already completed")
        elif completed >= 60:
            score += 12.0
            reasons.append(f"{completed:.0f}% of measured move already completed")
        elif completed >= 40:
            score += 5.0

    if data.persistence_scans is not None and data.persistence_scans >= 4:
        score += min(8.0, float(data.persistence_scans - 3) * 2.0)
        reasons.append(f"late confirmation persistence={data.persistence_scans}")

    if data.exhaustion_penalty is not None:
        exhaustion = _clamp(float(data.exhaustion_penalty), 0.0, 100.0)
        contribution = min(18.0, exhaustion * 0.45)
        score += contribution
        if contribution >= 5:
            reasons.append(f"exhaustion evidence {int(round(exhaustion))}/100")

    retest_available = data.retest_state == "HELD"
    if retest_available:
        score -= 15.0
        reasons.append("breakout retest held; chase risk reduced")
    elif data.retest_state == "FAILED":
        score += 18.0
        reasons.append("breakout retest failed")
    elif data.retest_state == "NOT_SEEN" and extension is not None and extension > 5:
        score += 8.0
        reasons.append("extended breakout without observed retest")

    final = int(round(_clamp(score)))
    band = _band(final)
    late_entry = final >= 60
    if not reasons:
        reasons.append("no material chase-risk evidence from supplied point-in-time data")

    return ChaseRiskAssessment(
        score=final,
        band=band,
        extension_pct_from_breakout=extension,
        distance_from_recent_high_pct=distance_recent_high,
        retest_available=retest_available,
        late_entry=late_entry,
        reasons=tuple(reasons),
    )
