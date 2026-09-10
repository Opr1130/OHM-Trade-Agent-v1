"""Earliness / move-consumed evaluation metrics.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

When first-observation or first-admission prices are not in the captured
history, the metric is ``None`` (unavailable). This module never invents a
prior print.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from app.opip.discovery.constants import DISCOVERY_EARLINESS_SCHEMA_VERSION
from app.opip.early.point_in_time import parse_timestamp


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


def _row_price(row: Mapping[str, Any]) -> float | None:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    features = (
        metadata.get("decision_features")
        if isinstance(metadata.get("decision_features"), Mapping)
        else {}
    )
    return _positive(metadata.get("reference_price")) or _positive(
        features.get("last_price")
    )


def _sort_key(row: Mapping[str, Any]) -> tuple:
    observed = parse_timestamp(row.get("observed_at"))
    return (observed or parse_timestamp("1970-01-01T00:00:00+00:00"), str(row.get("scan_id") or ""))


def earliness_metrics(
    history: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    favorable_price: float | None,
    favorable_at: str | None = None,
) -> dict[str, Any]:
    """Compute move-consumed metrics from ordered screening history.

    ``history`` must already be the same instrument. Missing history is
    recorded as unavailable, not zero.
    """
    ordered = sorted(history, key=_sort_key)
    first_obs_price = _row_price(ordered[0]) if ordered else None
    first_obs_at = ordered[0].get("observed_at") if ordered else None
    first_adm_price = None
    first_adm_at = None
    for row in ordered:
        outcome = str(row.get("outcome") or "")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        admitted = outcome == "ADVANCED" or metadata.get("production_admission_result") == "ADMITTED"
        if admitted:
            first_adm_price = _row_price(row)
            first_adm_at = row.get("observed_at")
            break

    consumed_pct = None
    unavailable: list[str] = []
    if first_obs_price is None:
        unavailable.append("price_at_first_observation")
    if first_adm_price is None:
        unavailable.append("price_at_first_admission")
    fav = _positive(favorable_price)
    if fav is None:
        unavailable.append("price_at_favorable_move")
    elif first_obs_price is not None and first_adm_price is not None:
        if direction == "SHORT":
            total = first_obs_price - fav
            already = first_obs_price - first_adm_price
        else:
            total = fav - first_obs_price
            already = first_adm_price - first_obs_price
        if total == 0:
            unavailable.append("percent_of_move_consumed_at_admission")
        else:
            consumed_pct = already / total * 100.0

    return {
        "schema_version": DISCOVERY_EARLINESS_SCHEMA_VERSION,
        "measurement_only": True,
        "price_at_first_observation": first_obs_price,
        "first_observation_at": first_obs_at,
        "price_at_first_admission": first_adm_price,
        "first_admission_at": first_adm_at,
        "price_at_favorable_move": fav,
        "favorable_move_at": favorable_at,
        "percent_of_move_consumed_at_admission": consumed_pct,
        "unavailable_metrics": unavailable,
        "unavailable_reason": (
            "required historical screening state was not captured"
            if unavailable
            else None
        ),
    }
