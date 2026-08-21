from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.scanner.models import MarketSnapshot
from app.services.native_flow_evidence import NativeFlowMetrics


VERSION = "explosion-state-v1"
PHASES = (
    "DORMANT",
    "IGNITION",
    "EARLY_EXPANSION",
    "CONFIRMED_EXPANSION",
    "LATE_EXTENSION",
    "EXHAUSTION_RISK",
)


@dataclass(frozen=True)
class ExplosionStateVector:
    version: str
    symbol: str
    reference_price: float
    momentum_1h_pct: float
    momentum_6h_pct: float
    momentum_24h_pct: float
    momentum_72h_pct: float
    momentum_velocity_1h: float
    momentum_velocity_6h: float
    momentum_acceleration: float
    relative_volume: float
    volume_expansion: float
    atr_pct: float
    atr_percentile: float
    bandwidth_percentile: float
    compression_release_score: float
    distance_to_24h_high_pct: float
    distance_to_24h_low_pct: float
    base_displacement_pct: float
    trend: str
    ema_structure_score: int
    trade_count_acceleration: float | None
    aggressor_imbalance: float | None
    large_print_concentration: float | None
    book_notional_imbalance: float | None
    flow_available: bool
    persistence_score: int
    phase: str
    phase_score: int
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    shadow_only: bool = True
    production_decision_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _ema_structure_score(snapshot: MarketSnapshot) -> int:
    if snapshot.last_price > snapshot.ema20 > snapshot.ema50 > snapshot.ema200:
        return 2
    if snapshot.last_price > snapshot.ema20 > snapshot.ema50:
        return 1
    if snapshot.last_price < snapshot.ema20 < snapshot.ema50 < snapshot.ema200:
        return -2
    if snapshot.last_price < snapshot.ema20 < snapshot.ema50:
        return -1
    return 0


def _phase_from_features(
    *,
    one_hour: float,
    six_hour: float,
    day: float,
    acceleration: float,
    relative_volume: float,
    compression_release: float,
    near_high: float,
    ema_score: int,
    trade_accel: float | None,
    aggressor: float | None,
    persistence_score: int,
) -> tuple[str, int, tuple[str, ...], tuple[str, ...]]:
    score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []

    if one_hour >= 0.5:
        score += min(18.0, one_hour * 6.0)
        reasons.append(f"1h momentum positive at {one_hour:+.2f}%")
    if six_hour >= 1.5:
        score += min(14.0, six_hour * 2.0)
        reasons.append(f"6h momentum building at {six_hour:+.2f}%")
    if acceleration >= 0.35:
        score += min(18.0, acceleration * 12.0)
        reasons.append(f"momentum acceleration positive at {acceleration:+.2f}")
    if relative_volume >= 1.25:
        score += min(14.0, (relative_volume - 1.0) * 12.0)
        reasons.append(f"relative volume expanding at {relative_volume:.2f}x")
    if compression_release >= 0.35:
        score += compression_release * 14.0
        reasons.append("volatility is transitioning from compression toward expansion")
    if ema_score > 0:
        score += ema_score * 4.0
        reasons.append("EMA structure supports expansion")
    if trade_accel is not None and trade_accel >= 1.25:
        score += min(12.0, (trade_accel - 1.0) * 10.0)
        reasons.append(f"trade-count acceleration is {trade_accel:.2f}x baseline")
    if aggressor is not None and aggressor >= 0.20:
        score += min(8.0, aggressor * 10.0)
        reasons.append("native trade aggression is buyer-skewed")
    if persistence_score >= 2:
        score += min(8.0, persistence_score * 2.0)
        reasons.append("expansion evidence persists across observations")

    late_extension = day >= 18.0 or (day >= 12.0 and near_high <= 1.0 and one_hour >= 2.5)
    exhaustion = (
        day >= 12.0
        and one_hour <= 0.25
        and six_hour >= 4.0
    ) or (day >= 18.0 and acceleration < -0.25)

    if exhaustion:
        warnings.append("large prior move is decelerating; exhaustion risk elevated")
        return "EXHAUSTION_RISK", int(round(_clamp(score, 0.0, 100.0))), tuple(reasons), tuple(warnings)
    if late_extension:
        warnings.append("move is already substantially extended")
        return "LATE_EXTENSION", int(round(_clamp(score, 0.0, 100.0))), tuple(reasons), tuple(warnings)

    bounded = int(round(_clamp(score, 0.0, 100.0)))
    if bounded >= 62 and one_hour >= 1.0 and six_hour >= 2.0:
        phase = "CONFIRMED_EXPANSION"
    elif bounded >= 42 and one_hour >= 0.75:
        phase = "EARLY_EXPANSION"
    elif bounded >= 24 and (one_hour > 0.25 or acceleration > 0.25):
        phase = "IGNITION"
    else:
        phase = "DORMANT"
    return phase, bounded, tuple(reasons), tuple(warnings)


def build_explosion_state_vector(
    snapshot: MarketSnapshot,
    *,
    native_flow: NativeFlowMetrics | None = None,
    previous: ExplosionStateVector | None = None,
) -> ExplosionStateVector:
    """Build a decision-time-only state vector for early expansion learning.

    The function consumes only current/previous observations and completed-candle
    features already present on MarketSnapshot. It must never inspect future
    prices or outcome labels.
    """
    one_hour = float(snapshot.confirmed_price_change_1h_pct)
    six_hour = float(snapshot.momentum_6h_pct)
    day = float(snapshot.momentum_24h_pct)
    seventy_two = float(snapshot.momentum_72h_pct)
    hourly_from_6h = six_hour / 6.0
    hourly_from_24h = day / 24.0
    velocity_1h = one_hour
    velocity_6h = hourly_from_6h
    acceleration = velocity_1h - velocity_6h
    medium_acceleration = velocity_6h - hourly_from_24h
    acceleration = acceleration * 0.7 + medium_acceleration * 0.3

    relative_volume = float(snapshot.movement_volume_ratio or snapshot.volume_ratio or 0.0)
    volume_expansion = max(0.0, relative_volume - 1.0)

    bandwidth_pctile = float(snapshot.bollinger_bandwidth_percentile)
    atr_pctile = float(snapshot.atr_percentile)
    compression_depth = max(0.0, (40.0 - min(bandwidth_pctile, atr_pctile)) / 40.0)
    directional_expansion = _clamp(max(one_hour, 0.0) / 2.0, 0.0, 1.0)
    compression_release = _clamp(compression_depth * 0.55 + directional_expansion * 0.45, 0.0, 1.0)

    ema_score = _ema_structure_score(snapshot)
    base_displacement = float(snapshot.distance_to_24h_low_pct)

    trade_accel = native_flow.trade_count_acceleration if native_flow is not None else None
    aggressor = native_flow.aggressor_imbalance if native_flow is not None else None
    large_print = native_flow.large_print_concentration if native_flow is not None else None
    book_imbalance = native_flow.book_notional_imbalance if native_flow is not None else None

    persistence = 0
    if previous is not None and previous.symbol.upper() == snapshot.symbol.upper():
        persistence += int(one_hour > 0 and previous.momentum_1h_pct > 0)
        persistence += int(acceleration > 0 and previous.momentum_acceleration > 0)
        persistence += int(relative_volume >= 1.0 and previous.relative_volume >= 1.0)
        persistence += int(ema_score > 0 and previous.ema_structure_score > 0)

    phase, phase_score, reasons, warnings = _phase_from_features(
        one_hour=one_hour,
        six_hour=six_hour,
        day=day,
        acceleration=acceleration,
        relative_volume=relative_volume,
        compression_release=compression_release,
        near_high=float(snapshot.distance_to_24h_high_pct),
        ema_score=ema_score,
        trade_accel=trade_accel,
        aggressor=aggressor,
        persistence_score=persistence,
    )

    if native_flow is None:
        warnings = (*warnings, "Kraken-native flow unavailable for this observation")

    return ExplosionStateVector(
        version=VERSION,
        symbol=snapshot.symbol.upper(),
        reference_price=float(snapshot.last_price),
        momentum_1h_pct=round(one_hour, 6),
        momentum_6h_pct=round(six_hour, 6),
        momentum_24h_pct=round(day, 6),
        momentum_72h_pct=round(seventy_two, 6),
        momentum_velocity_1h=round(velocity_1h, 6),
        momentum_velocity_6h=round(velocity_6h, 6),
        momentum_acceleration=round(acceleration, 6),
        relative_volume=round(relative_volume, 6),
        volume_expansion=round(volume_expansion, 6),
        atr_pct=round(float(snapshot.atr_pct), 6),
        atr_percentile=round(atr_pctile, 6),
        bandwidth_percentile=round(bandwidth_pctile, 6),
        compression_release_score=round(compression_release, 6),
        distance_to_24h_high_pct=round(float(snapshot.distance_to_24h_high_pct), 6),
        distance_to_24h_low_pct=round(float(snapshot.distance_to_24h_low_pct), 6),
        base_displacement_pct=round(base_displacement, 6),
        trend=str(snapshot.trend),
        ema_structure_score=ema_score,
        trade_count_acceleration=round(trade_accel, 6) if trade_accel is not None else None,
        aggressor_imbalance=round(aggressor, 6) if aggressor is not None else None,
        large_print_concentration=round(large_print, 6) if large_print is not None else None,
        book_notional_imbalance=round(book_imbalance, 6) if book_imbalance is not None else None,
        flow_available=native_flow is not None,
        persistence_score=persistence,
        phase=phase,
        phase_score=phase_score,
        reasons=reasons,
        warnings=warnings,
    )
