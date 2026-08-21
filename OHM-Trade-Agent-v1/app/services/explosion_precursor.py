from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.services.explosion_state import ExplosionStateVector


VERSION = "explosion-precursor-v1"


@dataclass(frozen=True)
class ExplosionPrecursor:
    version: str
    symbol: str
    score: int
    watch: bool
    evidence_count: int
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    shadow_only: bool = True
    production_decision_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def evaluate_explosion_precursor(
    current: ExplosionStateVector,
    *,
    previous: ExplosionStateVector | None = None,
) -> ExplosionPrecursor:
    """Score quiet pre-expansion evidence without relabeling the live phase.

    This challenger is intentionally different from the expansion phase score.
    It looks for *transition potential* in compressed or initially weak states.
    Only current and prior decision-time observations are used; future outcomes
    are never inspected here.
    """
    score = 0.0
    evidence = 0
    reasons: list[str] = []
    warnings: list[str] = []

    # Quiet/compressed states are valuable only when another transition clue is
    # present. Compression alone is common and must not create a watch signal.
    compressed = current.bandwidth_percentile <= 35.0 or current.atr_percentile <= 35.0
    if compressed:
        score += 12.0
        evidence += 1
        reasons.append("volatility remains compressed enough for expansion potential")

    if current.ema_structure_score > 0:
        score += 12.0
        evidence += 1
        reasons.append("price structure is constructive despite limited current momentum")

    if current.distance_to_24h_high_pct <= 6.0:
        score += 10.0
        evidence += 1
        reasons.append("price remains close enough to the 24h high for a breakout transition")

    if current.momentum_6h_pct > 0.5 and current.momentum_1h_pct < 0.75:
        score += 10.0
        evidence += 1
        reasons.append("medium-horizon strength remains while the latest hour is still quiet")

    if current.relative_volume_change is not None and current.relative_volume_change >= 0.15:
        score += min(15.0, current.relative_volume_change * 12.0)
        evidence += 1
        reasons.append(f"relative volume is beginning to rise ({current.relative_volume_change:+.2f}x change)")

    if current.atr_percentile_change is not None and current.atr_percentile_change >= 5.0:
        score += min(12.0, current.atr_percentile_change / 2.0)
        evidence += 1
        reasons.append(f"ATR percentile is lifting ({current.atr_percentile_change:+.1f} points)")

    if current.bandwidth_percentile_change is not None and current.bandwidth_percentile_change >= 5.0:
        score += min(12.0, current.bandwidth_percentile_change / 2.0)
        evidence += 1
        reasons.append(f"bandwidth percentile is lifting ({current.bandwidth_percentile_change:+.1f} points)")

    if current.distance_to_high_velocity_pct is not None and current.distance_to_high_velocity_pct >= 0.5:
        score += min(12.0, current.distance_to_high_velocity_pct * 3.0)
        evidence += 1
        reasons.append("price is closing distance to the 24h high")

    if current.base_displacement_velocity_pct is not None and current.base_displacement_velocity_pct >= 0.35:
        score += min(10.0, current.base_displacement_velocity_pct * 3.0)
        evidence += 1
        reasons.append("price is displacing upward from its recent base")

    if current.trade_count_acceleration is not None and current.trade_count_acceleration >= 1.4:
        score += min(16.0, (current.trade_count_acceleration - 1.0) * 12.0)
        evidence += 1
        reasons.append(f"trade activity is accelerating before full price confirmation ({current.trade_count_acceleration:.2f}x)")

    if current.aggressor_imbalance is not None and current.aggressor_imbalance >= 0.15:
        score += min(10.0, current.aggressor_imbalance * 12.0)
        evidence += 1
        reasons.append("buyer aggression is beginning to dominate native flow")

    if previous is not None and previous.symbol.upper() == current.symbol.upper():
        if current.momentum_acceleration > previous.momentum_acceleration + 0.20:
            score += 12.0
            evidence += 1
            reasons.append("momentum acceleration improved materially versus the prior observation")
        if current.phase_score > previous.phase_score + 8:
            score += 10.0
            evidence += 1
            reasons.append("expansion evidence strengthened materially versus the prior observation")
    else:
        warnings.append("prior-observation transition evidence is not yet available")

    # Already-expanded states do not need a latent-precursor watch label. Their
    # normal Wave 5 phase remains the right interpretation.
    if current.phase in {"EARLY_EXPANSION", "CONFIRMED_EXPANSION", "LATE_EXTENSION", "EXHAUSTION_RISK"}:
        warnings.append("current phase is already beyond latent-precursor classification")
        watch = False
    else:
        # Require multiple independent clues. This prevents the new learner from
        # simply weakening the old gate and flooding DORMANT names into alerts.
        watch = bool(score >= 36.0 and evidence >= 3)

    bounded = int(round(_clamp(score, 0.0, 100.0)))
    return ExplosionPrecursor(
        version=VERSION,
        symbol=current.symbol,
        score=bounded,
        watch=watch,
        evidence_count=evidence,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )
