from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from typing import Any

from app.opip.ml.snapshot import seal_feature_snapshot
from app.opip.ml.temporal import AvailabilityStamp
from app.scanner.models import MarketSnapshot


FEATURE_SCHEMA_VERSION = "wave9-trade-quality-v1"
FEATURE_CALC_VERSION = "wave9-trade-quality-v1"


def _finite(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _status(value: Any, default: str = "UNAVAILABLE") -> str:
    raw = getattr(value, "status", None)
    if raw is None and isinstance(value, dict):
        raw = value.get("status")
    return str(raw or default).upper()


def _feature_values(snapshot: MarketSnapshot) -> dict[str, Any]:
    execution = snapshot.execution_validation
    reference = snapshot.independent_market_reference
    news = snapshot.news_context
    catalyst = snapshot.scheduled_catalyst_context
    market_data = snapshot.market_data_validation

    return {
        "last_price": _finite(snapshot.last_price),
        "ema20": _finite(snapshot.ema20),
        "ema50": _finite(snapshot.ema50),
        "ema200": _finite(snapshot.ema200),
        "rsi": _finite(snapshot.rsi),
        "macd_histogram": _finite(snapshot.macd_histogram),
        "atr": _finite(snapshot.atr),
        "atr_pct": _finite(snapshot.atr_pct),
        "atr_percentile": _finite(snapshot.atr_percentile),
        "volume_ratio": _finite(snapshot.volume_ratio),
        "movement_volume_ratio": _finite(snapshot.movement_volume_ratio),
        "momentum_6h_pct": _finite(snapshot.momentum_6h_pct),
        "momentum_24h_pct": _finite(snapshot.momentum_24h_pct),
        "momentum_72h_pct": _finite(snapshot.momentum_72h_pct),
        "distance_to_24h_high_pct": _finite(snapshot.distance_to_24h_high_pct),
        "distance_to_72h_high_pct": _finite(snapshot.distance_to_72h_high_pct),
        "realized_range_24h_pct": _finite(snapshot.realized_range_24h_pct),
        "average_hourly_range_24h_pct": _finite(snapshot.average_hourly_range_24h_pct),
        "bollinger_bandwidth_percentile": _finite(snapshot.bollinger_bandwidth_percentile),
        "technical_score_input": _finite(snapshot.technical_score),
        "trend": str(snapshot.trend or "UNKNOWN"),
        "combined_24h_liquidity_usd": _finite(snapshot.combined_24h_liquidity_usd),
        "liquidity_rank": int(snapshot.liquidity_rank or 0),
        "cross_pair_confirmation_status": str(
            snapshot.cross_pair_confirmation_status or "UNAVAILABLE"
        ),
        "market_data_availability": _status(market_data),
        "execution_availability": _status(execution),
        "reference_availability": _status(reference),
        "news_availability": _status(news),
        "catalyst_availability": _status(catalyst, "UNRESOLVED"),
        "movement_availability": str(snapshot.movement_data_status or "UNAVAILABLE").upper(),
        "execution_drag_pct": (
            _finite(getattr(execution, "estimated_visible_round_trip_market_drag_pct", None))
            if execution is not None
            else None
        ),
        "reference_price_divergence_pct": (
            _finite(getattr(reference, "price_divergence_pct", None))
            if reference is not None
            else None
        ),
    }


def build_trade_feature_snapshot(
    snapshot: MarketSnapshot,
    *,
    decision_at: datetime,
    episode_id: str,
    candidate_id: str | None,
    regime: str | None,
    lane: str = "TRADE_QUALITY_V2",
):
    """Seal one point-in-time immutable trade-quality feature snapshot.

    All fields are values already visible on the supplied MarketSnapshot.
    Missing evidence is retained as None/status=UNAVAILABLE; it is never
    converted to supportive zero evidence.
    """
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    decision = decision_at.astimezone(timezone.utc)
    features = _feature_values(snapshot)
    availability = {
        name: AvailabilityStamp(
            source_at_utc=None,
            ingested_at_utc=decision,
            visible_at_utc=decision,
            source_version="market-snapshot-v1",
        )
        for name in features
    }
    dag = hashlib.sha256(
        "|".join(sorted(features)).encode("utf-8")
    ).hexdigest()

    asset = str(snapshot.underlying_asset or snapshot.symbol).upper()
    return seal_feature_snapshot(
        episode_id=episode_id,
        candidate_id=candidate_id,
        decision_at_utc=decision,
        canonical_asset_id=asset,
        venue="KRAKEN",
        pair=str(snapshot.primary_pair or snapshot.symbol).upper(),
        direction=str(snapshot.trade_direction or "LONG").upper(),
        lane=lane,
        regime=regime,
        feature_values=features,
        availability=availability,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_calc_version=FEATURE_CALC_VERSION,
        feature_dag_hash=dag,
        source_versions={
            "market_snapshot": "v1",
            "event_semantics": "point-in-time",
        },
        serialization_version=1,
    )
