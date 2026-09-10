"""Forward outcome labels for Broad Search / Stage-0 observations.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

Decision-time evidence lives on ScreeningEvaluation. This module writes a
separate, joinable label keyed by observation_id. Incomplete horizons stay
incomplete; they are never written as a zero return.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Mapping

from app.opip.discovery.constants import (
    DISCOVERY_FORWARD_OUTCOME_SCHEMA_VERSION,
    DISCOVERY_HORIZONS,
    DISCOVERY_MARKET_OPPORTUNITY_DEFINITION,
    DISCOVERY_OUTCOME_DEFINITION,
    DISCOVERY_OUTCOME_LABEL_SCHEMA_VERSION,
    DISCOVERY_PRIMARY_HORIZON,
    DISCOVERY_V1_ATR_ADVERSE_MULTIPLE,
    DISCOVERY_V1_ATR_FAVORABLE_MULTIPLE,
    DISCOVERY_V1_MIN_ADVERSE_PCT,
    DISCOVERY_V1_MIN_FAVORABLE_PCT,
    MATURATION_COMPLETE,
    MATURATION_INCOMPLETE,
    MATURATION_NO_FORWARD_DATA,
    MATURATION_PARTIAL,
    WINNER_INCOMPLETE,
    WINNER_NON_WINNER,
    WINNER_WINNER,
)
from app.opip.early.point_in_time import parse_timestamp
from app.services.signal_quality_phase2 import SymbolTimeline


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("outcome timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


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


def directional_return_pct(
    price: float,
    reference_price: float,
    direction: str,
) -> float:
    """Signed percent move in the trade direction. SHORT profits when price falls."""
    if direction == "SHORT":
        return (1.0 - price / reference_price) * 100.0
    return (price / reference_price - 1.0) * 100.0


def discovery_v1_barriers(
    *,
    atr_pct: float | None,
) -> tuple[float, float, str]:
    """Return (favorable_pct, adverse_pct, barrier_basis)."""
    measured_atr = _finite(atr_pct)
    if measured_atr is not None and measured_atr > 0:
        favorable = max(
            DISCOVERY_V1_MIN_FAVORABLE_PCT,
            DISCOVERY_V1_ATR_FAVORABLE_MULTIPLE * measured_atr,
        )
        adverse = -max(
            DISCOVERY_V1_MIN_ADVERSE_PCT,
            DISCOVERY_V1_ATR_ADVERSE_MULTIPLE * measured_atr,
        )
        return favorable, adverse, "ATR_NORMALIZED"
    return (
        DISCOVERY_V1_MIN_FAVORABLE_PCT,
        -DISCOVERY_V1_MIN_ADVERSE_PCT,
        "PERCENT_FLOOR",
    )


def _window_points(
    timeline: SymbolTimeline,
    *,
    reference_at: datetime,
    horizon: timedelta,
) -> list[tuple[datetime, float]]:
    end = reference_at + horizon
    lo, hi = timeline.slice_indices(reference_at, end)
    points: list[tuple[datetime, float]] = []
    for index in range(lo, hi):
        observed_at = timeline.times[index]
        price = timeline.prices[index]
        if observed_at <= reference_at or observed_at > end:
            continue
        if _positive(price) is None:
            continue
        points.append((observed_at, float(price)))
    return points


def _horizon_payload(
    timeline: SymbolTimeline,
    *,
    reference_at: datetime,
    reference_price: float,
    direction: str,
    horizon: timedelta,
    favorable_barrier_pct: float,
    adverse_barrier_pct: float,
) -> dict[str, Any]:
    observed = timeline.has_forward_observation(reference_at, horizon)
    if not observed:
        return {
            "window_complete": False,
            "horizon_observed": False,
            "horizon_return_pct": None,
            "mfe_pct": None,
            "mfe_at": None,
            "time_to_mfe_seconds": None,
            "mae_pct": None,
            "mae_at": None,
            "time_to_mae_seconds": None,
            "max_adverse_excursion_pct": None,
            "favorable_barrier_pct": favorable_barrier_pct,
            "adverse_barrier_pct": adverse_barrier_pct,
            "favorable_barrier_reached": False,
            "adverse_barrier_reached": False,
            "target_before_stop": None,
            "time_to_favorable_barrier_seconds": None,
            "time_to_adverse_barrier_seconds": None,
            "favorable_barrier_at": None,
            "adverse_barrier_at": None,
            "maturation_status": MATURATION_NO_FORWARD_DATA,
            "last_forward_price": None,
            "last_forward_at": None,
            "long_mfe_pct": None,
            "long_mae_pct": None,
            "short_mfe_pct": None,
            "short_mae_pct": None,
            "long_target_before_stop": None,
            "short_target_before_stop": None,
            "long_discovery_outcome_v1": WINNER_INCOMPLETE,
            "short_discovery_outcome_v1": WINNER_INCOMPLETE,
        }

    points = _window_points(
        timeline, reference_at=reference_at, horizon=horizon
    )
    complete = timeline.has_complete_window(reference_at, horizon)
    mfe_pct = mae_pct = None
    mfe_at = mae_at = None
    favorable_at = adverse_at = None
    last_price = None
    last_at = None
    for observed_at, price in points:
        move = directional_return_pct(price, reference_price, direction)
        last_price = price
        last_at = observed_at
        if mfe_pct is None or move > mfe_pct:
            mfe_pct = move
            mfe_at = observed_at
        if mae_pct is None or move < mae_pct:
            mae_pct = move
            mae_at = observed_at
        if favorable_at is None and move >= favorable_barrier_pct:
            favorable_at = observed_at
        if adverse_at is None and move <= adverse_barrier_pct:
            adverse_at = observed_at

    horizon_return = None
    asof = timeline.price_asof(reference_at + horizon)
    if asof is not None and _positive(asof) is not None:
        # price_asof can return the reference print; only keep it when a real
        # forward observation existed in this window (already gated above).
        if last_price is not None:
            horizon_return = directional_return_pct(
                float(asof), reference_price, direction
            )

    target_before_stop = None
    if favorable_at is not None and (
        adverse_at is None or favorable_at <= adverse_at
    ):
        target_before_stop = True
    elif adverse_at is not None and (
        favorable_at is None or adverse_at < favorable_at
    ):
        target_before_stop = False
    elif complete:
        target_before_stop = False

    if complete:
        maturation = MATURATION_COMPLETE
    elif target_before_stop is not None:
        maturation = MATURATION_PARTIAL
    else:
        maturation = MATURATION_INCOMPLETE

    return {
        "window_complete": complete,
        "horizon_observed": True,
        "horizon_return_pct": horizon_return,
        "mfe_pct": mfe_pct,
        "mfe_at": mfe_at.isoformat() if mfe_at is not None else None,
        "time_to_mfe_seconds": (
            (mfe_at - reference_at).total_seconds() if mfe_at is not None else None
        ),
        "mae_pct": mae_pct,
        "mae_at": mae_at.isoformat() if mae_at is not None else None,
        "time_to_mae_seconds": (
            (mae_at - reference_at).total_seconds() if mae_at is not None else None
        ),
        "max_adverse_excursion_pct": (
            min(0.0, mae_pct) if mae_pct is not None else None
        ),
        "favorable_barrier_pct": favorable_barrier_pct,
        "adverse_barrier_pct": adverse_barrier_pct,
        "favorable_barrier_reached": favorable_at is not None,
        "adverse_barrier_reached": adverse_at is not None,
        "target_before_stop": target_before_stop,
        "time_to_favorable_barrier_seconds": (
            (favorable_at - reference_at).total_seconds()
            if favorable_at is not None
            else None
        ),
        "time_to_adverse_barrier_seconds": (
            (adverse_at - reference_at).total_seconds()
            if adverse_at is not None
            else None
        ),
        "favorable_barrier_at": (
            favorable_at.isoformat() if favorable_at is not None else None
        ),
        "adverse_barrier_at": (
            adverse_at.isoformat() if adverse_at is not None else None
        ),
        "maturation_status": maturation,
        "last_forward_price": last_price,
        "last_forward_at": last_at.isoformat() if last_at is not None else None,
    }


def discovery_v1_winner_label(primary: Mapping[str, Any]) -> str:
    """Evaluation label only. Zero trade authority."""
    target_before_stop = primary.get("target_before_stop")
    complete = bool(primary.get("window_complete"))
    if target_before_stop is True:
        return WINNER_WINNER
    if target_before_stop is False and complete:
        return WINNER_NON_WINNER
    if target_before_stop is False:
        return WINNER_NON_WINNER
    return WINNER_INCOMPLETE


def _prefer_opportunity_direction(
    long_label: str,
    short_label: str,
    long_mfe: float | None,
    short_mfe: float | None,
) -> str | None:
    """Choose the realized opportunity direction. Tie-break LONG. Evaluation only."""
    long_win = long_label == WINNER_WINNER
    short_win = short_label == WINNER_WINNER
    if long_win and not short_win:
        return "LONG"
    if short_win and not long_win:
        return "SHORT"
    if long_win and short_win:
        long_val = long_mfe if long_mfe is not None else float("-inf")
        short_val = short_mfe if short_mfe is not None else float("-inf")
        if short_val > long_val:
            return "SHORT"
        return "LONG"
    return None


def market_opportunity_label(long_label: str, short_label: str) -> str:
    """Whether a qualifying directional opportunity occurred, ignoring scorer preference."""
    if long_label == WINNER_WINNER or short_label == WINNER_WINNER:
        return WINNER_WINNER
    if long_label == WINNER_INCOMPLETE or short_label == WINNER_INCOMPLETE:
        return WINNER_INCOMPLETE
    return WINNER_NON_WINNER


def label_screening_observation(
    row: Mapping[str, Any],
    timeline: SymbolTimeline | None,
    *,
    labeled_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a joinable forward-outcome record for one screening row."""
    observed_at = parse_timestamp(row.get("observed_at"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    features = (
        metadata.get("decision_features")
        if isinstance(metadata, Mapping)
        and isinstance(metadata.get("decision_features"), Mapping)
        else {}
    )
    reference_price = _positive(
        (metadata or {}).get("reference_price")
    ) or _positive((features or {}).get("last_price"))
    atr_pct = _finite((features or {}).get("atr_pct")) if features else None

    stronger = str(
        (metadata or {}).get("production_preferred_direction")
        or (metadata or {}).get("stronger_direction")
        or row.get("advanced_direction")
        or "LONG"
    )
    if stronger not in {"LONG", "SHORT"}:
        stronger = "LONG"
    favorable, adverse, barrier_basis = discovery_v1_barriers(atr_pct=atr_pct)

    identity = row.get("venue_instrument") if isinstance(row.get("venue_instrument"), Mapping) else {}
    observation_id = str((metadata or {}).get("observation_id") or "")
    payload: dict[str, Any] = {
        "schema_version": DISCOVERY_FORWARD_OUTCOME_SCHEMA_VERSION,
        "label_schema_version": DISCOVERY_OUTCOME_LABEL_SCHEMA_VERSION,
        "outcome_definition": DISCOVERY_OUTCOME_DEFINITION,
        "market_opportunity_definition": DISCOVERY_MARKET_OPPORTUNITY_DEFINITION,
        "measurement_only": True,
        "offline_label_only": True,
        "trade_authority_changed": False,
        "affects_ranking": False,
        "affects_telegram": False,
        "production_execution_gate_changed": False,
        "observation_id": observation_id,
        "scan_id": str(row.get("scan_id") or ""),
        "scanner_type": str(row.get("scanner_type") or ""),
        "venue_instrument_id": str(row.get("venue_instrument_id") or identity.get("venue_instrument_id") or ""),
        "canonical_underlying_asset": identity.get("canonical_asset_id"),
        "observed_at": observed_at.isoformat() if observed_at is not None else None,
        "reference_at": observed_at.isoformat() if observed_at is not None else None,
        "reference_price": reference_price,
        "production_preferred_direction": stronger,
        "direction": stronger,
        "labeled_at": (labeled_at or datetime.now(timezone.utc)).isoformat(),
        "barrier_basis": barrier_basis,
        "favorable_barrier_pct": favorable,
        "adverse_barrier_pct": adverse,
        "horizons": {},
    }
    empty_horizon = {
        "horizon_observed": False,
        "horizon_return_pct": None,
        "window_complete": False,
        "maturation_status": MATURATION_NO_FORWARD_DATA,
        "target_before_stop": None,
        "long_target_before_stop": None,
        "short_target_before_stop": None,
        "long_mfe_pct": None,
        "long_mae_pct": None,
        "short_mfe_pct": None,
        "short_mae_pct": None,
        "long_discovery_outcome_v1": WINNER_INCOMPLETE,
        "short_discovery_outcome_v1": WINNER_INCOMPLETE,
    }
    if observed_at is None or reference_price is None or timeline is None or len(timeline) == 0:
        payload["maturation_status"] = MATURATION_NO_FORWARD_DATA
        payload["discovery_outcome_v1"] = WINNER_INCOMPLETE
        payload["production_discovery_outcome_v1"] = WINNER_INCOMPLETE
        payload["market_discovery_opportunity_v1"] = WINNER_INCOMPLETE
        payload["realized_opportunity_direction"] = None
        payload["long_target_before_stop"] = None
        payload["short_target_before_stop"] = None
        payload["window_complete"] = False
        payload["horizons"] = {label: dict(empty_horizon) for label in DISCOVERY_HORIZONS}
        return payload

    horizons: dict[str, Any] = {}
    for label, delta in DISCOVERY_HORIZONS.items():
        long_payload = _horizon_payload(
            timeline,
            reference_at=observed_at,
            reference_price=reference_price,
            direction="LONG",
            horizon=delta,
            favorable_barrier_pct=favorable,
            adverse_barrier_pct=adverse,
        )
        short_payload = _horizon_payload(
            timeline,
            reference_at=observed_at,
            reference_price=reference_price,
            direction="SHORT",
            horizon=delta,
            favorable_barrier_pct=favorable,
            adverse_barrier_pct=adverse,
        )
        preferred = long_payload if stronger == "LONG" else short_payload
        merged = dict(preferred)
        merged["long_mfe_pct"] = long_payload["mfe_pct"]
        merged["long_mae_pct"] = long_payload["mae_pct"]
        merged["short_mfe_pct"] = short_payload["mfe_pct"]
        merged["short_mae_pct"] = short_payload["mae_pct"]
        merged["long_target_before_stop"] = long_payload.get("target_before_stop")
        merged["short_target_before_stop"] = short_payload.get("target_before_stop")
        merged["long_discovery_outcome_v1"] = discovery_v1_winner_label(long_payload)
        merged["short_discovery_outcome_v1"] = discovery_v1_winner_label(short_payload)
        horizons[label] = merged

    payload["horizons"] = horizons
    primary = horizons[DISCOVERY_PRIMARY_HORIZON]
    long_label = str(primary.get("long_discovery_outcome_v1") or WINNER_INCOMPLETE)
    short_label = str(primary.get("short_discovery_outcome_v1") or WINNER_INCOMPLETE)
    production_label = long_label if stronger == "LONG" else short_label
    market_label = market_opportunity_label(long_label, short_label)
    realized = _prefer_opportunity_direction(
        long_label,
        short_label,
        primary.get("long_mfe_pct"),
        primary.get("short_mfe_pct"),
    )
    payload["maturation_status"] = primary["maturation_status"]
    payload["window_complete"] = bool(primary.get("window_complete"))
    payload["discovery_outcome_v1"] = market_label
    payload["production_discovery_outcome_v1"] = production_label
    payload["market_discovery_opportunity_v1"] = market_label
    payload["realized_opportunity_direction"] = realized
    payload["target_before_stop"] = primary.get("target_before_stop")
    payload["long_target_before_stop"] = primary.get("long_target_before_stop")
    payload["short_target_before_stop"] = primary.get("short_target_before_stop")
    payload["mfe_pct"] = primary.get("mfe_pct")
    payload["mae_pct"] = primary.get("mae_pct")
    payload["long_mfe_pct"] = primary.get("long_mfe_pct")
    payload["long_mae_pct"] = primary.get("long_mae_pct")
    payload["short_mfe_pct"] = primary.get("short_mfe_pct")
    payload["short_mae_pct"] = primary.get("short_mae_pct")
    return payload
