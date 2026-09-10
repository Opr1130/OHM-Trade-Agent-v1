"""Point-in-time Stage-0 features from a production market snapshot.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

Unavailable values stay ``None``. Zero is never used as a stand-in for
"not measured". Movement-radar fields are admitted only when the snapshot
says they were actually computed.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from app.opip.early.point_in_time import assert_point_in_time_safe
from app.opip.early.stage0_evidence import Stage0DecisionFeatures
from app.opip.early.taxonomy import MarketPhase


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive(value: Any) -> float | None:
    parsed = _finite(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _movement_measured(snapshot: Any) -> bool:
    status = str(getattr(snapshot, "movement_data_status", "") or "").strip().upper()
    return status not in {"", "UNAVAILABLE"}


def decision_features_from_snapshot(
    snapshot: Any,
    *,
    observed_at: datetime | None = None,
    decision_at: datetime | None = None,
) -> Stage0DecisionFeatures:
    """Build PIT-safe features from one analyzed snapshot.

    Fields the snapshot never measured remain ``None``. This function does
    not read later scans, forward prices, or outcome stores.
    """
    last = _positive(
        getattr(snapshot, "ticker_last", None) or getattr(snapshot, "last_price", None)
    )
    high_24h = _positive(getattr(snapshot, "recent_24h_high", None))
    low_24h = _positive(getattr(snapshot, "recent_24h_low", None))
    lift = None
    distance_high = None
    if last is not None and low_24h is not None:
        lift = (last - low_24h) / low_24h * 100.0
    if last is not None and high_24h is not None:
        distance_high = (high_24h - last) / high_24h * 100.0

    notional = _positive(getattr(snapshot, "combined_24h_liquidity_usd", None))
    relative_volume = _finite(getattr(snapshot, "volume_ratio", None))

    momentum_6h = _finite(getattr(snapshot, "momentum_6h_pct", None))
    momentum_24h = _finite(getattr(snapshot, "momentum_24h_pct", None))
    momentum_72h = _finite(getattr(snapshot, "momentum_72h_pct", None))

    atr = _positive(getattr(snapshot, "atr", None))
    atr_pct = _finite(getattr(snapshot, "atr_pct", None))
    if atr_pct is not None and atr_pct < 0:
        atr_pct = None

    bandwidth = None
    bandwidth_pctile = None
    atr_pctile = None
    if _movement_measured(snapshot):
        bandwidth = _finite(getattr(snapshot, "bollinger_bandwidth_pct", None))
        bandwidth_pctile = _finite(
            getattr(snapshot, "bollinger_bandwidth_percentile", None)
        )
        atr_pctile = _finite(getattr(snapshot, "atr_percentile", None))

    phase = None
    signal = getattr(snapshot, "price_movement_signal", None)
    raw_phase = signal.get("market_phase") or signal.get("phase") if isinstance(signal, dict) else None
    if isinstance(raw_phase, MarketPhase):
        phase = raw_phase.value
    elif raw_phase is not None:
        try:
            phase = MarketPhase(raw_phase).value
        except (ValueError, TypeError):
            phase = None

    features = Stage0DecisionFeatures(
        lift_from_24h_low_pct=lift,
        distance_from_24h_high_pct=distance_high,
        notional_usd=notional,
        last_price=last,
        high_24h=high_24h,
        low_24h=low_24h,
        volume_24h=None,
        relative_volume=relative_volume,
        relative_volume_change=None,
        trade_count_acceleration=None,
        momentum_6h_pct=momentum_6h,
        momentum_24h_pct=momentum_24h,
        momentum_72h_pct=momentum_72h,
        momentum_acceleration=None,
        atr=atr,
        atr_pct=atr_pct,
        bandwidth_pct=bandwidth,
        bandwidth_percentile=bandwidth_pctile,
        atr_percentile=atr_pctile,
        prior_observation_at=None,
        observed_at=observed_at.isoformat() if observed_at is not None else None,
        decision_at=decision_at.isoformat() if decision_at is not None else None,
        prior_observation_count=None,
        market_phase=phase,
    )
    assert_point_in_time_safe(features.as_dict())
    return features
