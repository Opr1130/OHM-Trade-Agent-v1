from __future__ import annotations

from dataclasses import asdict, dataclass
import math
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
    relative_volume_change: float | None
    atr_pct: float
    atr_percentile: float
    atr_percentile_change: float | None
    bandwidth_percentile: float
    bandwidth_percentile_change: float | None
    compression_release_score: float
    distance_to_24h_high_pct: float
    distance_to_24h_low_pct: float
    distance_to_high_velocity_pct: float | None
    base_displacement_pct: float
    base_displacement_velocity_pct: float | None
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


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    parsed = _finite(value, math.nan)
    return parsed if math.isfinite(parsed) else None


def _clamp(value: float, low: float, high: float) -> float:
    safe = _finite(value, low)
    return max(low, min(high, safe))


def _ema_structure_score(snapshot: MarketSnapshot) -> int:
    last = _finite(snapshot.last_price)
    ema20 = _finite(snapshot.ema20)
    ema50 = _finite(snapshot.ema50)
    ema200 = _finite(snapshot.ema200)
    if min(last, ema20, ema50, ema200) <= 0:
        return 0
    if last > ema20 > ema50 > ema200:
        return 2
    if last > ema20 > ema50:
        return 1
    if last < ema20 < ema50 < ema200:
        return -2
    if last < ema20 < ema50:
        return -1
    return 0


def _phase_from_features(
    *,
    one_hour: float,
    six_hour: float,
    day: float,
    acceleration: float,
    relative_volume: float,
    relative_volume_change: float | None,
    compression_release: float,
    atr_percentile_change: float | None,
    bandwidth_percentile_change: float | None,
    near_high: float,
    distance_to_high_velocity: float | None,
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
        score += min(12.0, (relative_volume - 1.0) * 10.0)
        reasons.append(f"relative volume elevated at {relative_volume:.2f}x")
    if relative_volume_change is not None and relative_volume_change >= 0.20:
        score += min(8.0, relative_volume_change * 10.0)
        reasons.append(f"relative volume accelerated by {relative_volume_change:+.2f}x")
    if compression_release >= 0.35:
        score += compression_release * 14.0
        reasons.append("volatility state is transitioning from compression toward expansion")
    if atr_percentile_change is not None and atr_percentile_change >= 8.0:
        score += min(6.0, atr_percentile_change / 4.0)
        reasons.append(f"ATR percentile expanded by {atr_percentile_change:+.1f} points")
    if bandwidth_percentile_change is not None and bandwidth_percentile_change >= 8.0:
        score += min(6.0, bandwidth_percentile_change / 4.0)
        reasons.append(f"bandwidth percentile expanded by {bandwidth_percentile_change:+.1f} points")
    if distance_to_high_velocity is not None and distance_to_high_velocity >= 1.0:
        score += min(6.0, distance_to_high_velocity * 2.0)
        reasons.append(f"price closed {distance_to_high_velocity:.2f} points of distance to the 24h high")
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

    bounded = int(round(_clamp(score, 0.0, 100.0)))
    if exhaustion:
        warnings.append("large prior move is decelerating; exhaustion risk elevated")
        return "EXHAUSTION_RISK", bounded, tuple(reasons), tuple(warnings)
    if late_extension:
        warnings.append("move is already substantially extended")
        return "LATE_EXTENSION", bounded, tuple(reasons), tuple(warnings)

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
    """Build a decision-time-only state vector for early expansion learning."""
    raw_core = (
        snapshot.last_price,
        snapshot.confirmed_price_change_1h_pct,
        snapshot.momentum_6h_pct,
        snapshot.momentum_24h_pct,
        snapshot.momentum_72h_pct,
        snapshot.movement_volume_ratio or snapshot.volume_ratio or 0.0,
        snapshot.atr_pct,
        snapshot.atr_percentile,
        snapshot.bollinger_bandwidth_percentile,
        snapshot.distance_to_24h_high_pct,
        snapshot.distance_to_24h_low_pct,
    )
    invalid_core = any(not math.isfinite(_finite(value, math.nan)) for value in raw_core)

    one_hour = _finite(snapshot.confirmed_price_change_1h_pct)
    six_hour = _finite(snapshot.momentum_6h_pct)
    day = _finite(snapshot.momentum_24h_pct)
    seventy_two = _finite(snapshot.momentum_72h_pct)
    hourly_from_6h = six_hour / 6.0
    hourly_from_24h = day / 24.0
    velocity_1h = one_hour
    velocity_6h = hourly_from_6h
    short_acceleration = velocity_1h - velocity_6h
    medium_acceleration = velocity_6h - hourly_from_24h
    acceleration = short_acceleration * 0.7 + medium_acceleration * 0.3

    relative_volume = max(0.0, _finite(snapshot.movement_volume_ratio or snapshot.volume_ratio or 0.0))
    volume_expansion = max(0.0, relative_volume - 1.0)
    atr_pctile = _clamp(_finite(snapshot.atr_percentile), 0.0, 100.0)
    bandwidth_pctile = _clamp(_finite(snapshot.bollinger_bandwidth_percentile), 0.0, 100.0)
    near_high = max(0.0, _finite(snapshot.distance_to_24h_high_pct))
    distance_low = max(0.0, _finite(snapshot.distance_to_24h_low_pct))
    base_displacement = distance_low

    relative_volume_change: float | None = None
    atr_percentile_change: float | None = None
    bandwidth_percentile_change: float | None = None
    distance_to_high_velocity: float | None = None
    base_displacement_velocity: float | None = None

    previous_same_symbol = previous is not None and previous.symbol.upper() == snapshot.symbol.upper()
    if previous_same_symbol and previous is not None:
        relative_volume_change = relative_volume - _finite(previous.relative_volume)
        atr_percentile_change = atr_pctile - _finite(previous.atr_percentile)
        bandwidth_percentile_change = bandwidth_pctile - _finite(previous.bandwidth_percentile)
        distance_to_high_velocity = _finite(previous.distance_to_24h_high_pct) - near_high
        base_displacement_velocity = base_displacement - _finite(previous.base_displacement_pct)

    current_compression_depth = max(0.0, (40.0 - min(bandwidth_pctile, atr_pctile)) / 40.0)
    directional_expansion = _clamp(max(one_hour, 0.0) / 2.0, 0.0, 1.0)
    if previous_same_symbol and previous is not None:
        prior_compression_depth = max(
            0.0,
            (40.0 - min(_finite(previous.bandwidth_percentile), _finite(previous.atr_percentile))) / 40.0,
        )
        percentile_release = _clamp(
            max(
                atr_percentile_change or 0.0,
                bandwidth_percentile_change or 0.0,
            ) / 30.0,
            0.0,
            1.0,
        )
        compression_release = _clamp(
            prior_compression_depth * 0.45
            + percentile_release * 0.35
            + directional_expansion * 0.20,
            0.0,
            1.0,
        )
    else:
        compression_release = _clamp(
            current_compression_depth * 0.50 + directional_expansion * 0.50,
            0.0,
            1.0,
        )

    ema_score = _ema_structure_score(snapshot)
    trade_accel = _finite_optional(native_flow.trade_count_acceleration) if native_flow is not None else None
    aggressor = _finite_optional(native_flow.aggressor_imbalance) if native_flow is not None else None
    large_print = _finite_optional(native_flow.large_print_concentration) if native_flow is not None else None
    book_imbalance = _finite_optional(native_flow.book_notional_imbalance) if native_flow is not None else None

    persistence = 0
    if previous_same_symbol and previous is not None:
        persistence += int(one_hour > 0 and _finite(previous.momentum_1h_pct) > 0)
        persistence += int(acceleration > 0 and _finite(previous.momentum_acceleration) > 0)
        persistence += int(relative_volume >= 1.0 and _finite(previous.relative_volume) >= 1.0)
        persistence += int(ema_score > 0 and previous.ema_structure_score > 0)

    if invalid_core:
        phase, phase_score, reasons, warnings = "DORMANT", 0, (), ("non-finite core market evidence sanitized; state forced dormant",)
    else:
        phase, phase_score, reasons, warnings = _phase_from_features(
            one_hour=one_hour,
            six_hour=six_hour,
            day=day,
            acceleration=acceleration,
            relative_volume=relative_volume,
            relative_volume_change=relative_volume_change,
            compression_release=compression_release,
            atr_percentile_change=atr_percentile_change,
            bandwidth_percentile_change=bandwidth_percentile_change,
            near_high=near_high,
            distance_to_high_velocity=distance_to_high_velocity,
            ema_score=ema_score,
            trade_accel=trade_accel,
            aggressor=aggressor,
            persistence_score=persistence,
        )

    if native_flow is None:
        warnings = (*warnings, "Kraken-native flow unavailable for this observation")
    if not previous_same_symbol:
        warnings = (*warnings, "transition deltas unavailable until a prior observation exists")

    return ExplosionStateVector(
        version=VERSION,
        symbol=snapshot.symbol.upper(),
        reference_price=max(0.0, _finite(snapshot.last_price)),
        momentum_1h_pct=round(one_hour, 6),
        momentum_6h_pct=round(six_hour, 6),
        momentum_24h_pct=round(day, 6),
        momentum_72h_pct=round(seventy_two, 6),
        momentum_velocity_1h=round(velocity_1h, 6),
        momentum_velocity_6h=round(velocity_6h, 6),
        momentum_acceleration=round(acceleration, 6),
        relative_volume=round(relative_volume, 6),
        volume_expansion=round(volume_expansion, 6),
        relative_volume_change=round(relative_volume_change, 6) if relative_volume_change is not None else None,
        atr_pct=round(max(0.0, _finite(snapshot.atr_pct)), 6),
        atr_percentile=round(atr_pctile, 6),
        atr_percentile_change=round(atr_percentile_change, 6) if atr_percentile_change is not None else None,
        bandwidth_percentile=round(bandwidth_pctile, 6),
        bandwidth_percentile_change=round(bandwidth_percentile_change, 6) if bandwidth_percentile_change is not None else None,
        compression_release_score=round(compression_release, 6),
        distance_to_24h_high_pct=round(near_high, 6),
        distance_to_24h_low_pct=round(distance_low, 6),
        distance_to_high_velocity_pct=round(distance_to_high_velocity, 6) if distance_to_high_velocity is not None else None,
        base_displacement_pct=round(base_displacement, 6),
        base_displacement_velocity_pct=round(base_displacement_velocity, 6) if base_displacement_velocity is not None else None,
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
