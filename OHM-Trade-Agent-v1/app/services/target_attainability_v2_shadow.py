from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Iterable


MIN_SCALP_MOVE_PCT = 0.75
MIN_FULL_CONFIDENCE_SCORE = 70
MIN_REDUCED_CONFIDENCE_SCORE = 55
HORIZON_ORDER = {"1h": 1, "4h": 4, "12h": 12, "24h": 24, "72h": 72}


@dataclass(frozen=True)
class TargetV2ShadowResult:
    symbol: str
    direction: str
    target_class: str
    confidence_score: int
    proposed_t1_move_pct: float | None
    proposed_t2_move_pct: float | None
    estimated_time_horizon: str
    reasons: list[str]
    warnings: list[str]
    production_authoritative: bool = False
    shadow_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _reject(symbol: str, direction: str, reason: str) -> TargetV2ShadowResult:
    return TargetV2ShadowResult(symbol, direction, "REJECT", 0, None, None, "UNAVAILABLE", [reason], [])


def _finite_values(values: Iterable[float]) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def _directional_percentiles(snapshot: Any, direction: str) -> tuple[float, float, float, float, float, float]:
    names = (
        ("rolling_24h_downside_median_pct", "rolling_24h_downside_p75_pct", "rolling_24h_downside_p90_pct", "rolling_72h_downside_median_pct", "rolling_72h_downside_p75_pct", "rolling_72h_downside_p90_pct")
        if direction == "SHORT"
        else ("rolling_24h_upside_median_pct", "rolling_24h_upside_p75_pct", "rolling_24h_upside_p90_pct", "rolling_72h_upside_median_pct", "rolling_72h_upside_p75_pct", "rolling_72h_upside_p90_pct")
    )
    return tuple(float(getattr(snapshot, name, 0.0) or 0.0) for name in names)  # type: ignore[return-value]


def _aligned_momentum(snapshot: Any, direction: str) -> tuple[int, str]:
    values = [float(getattr(snapshot, name, 0.0) or 0.0) for name in ("momentum_6h_pct", "momentum_24h_pct", "momentum_72h_pct")]
    if not _finite_values(values):
        return 0, "momentum evidence is non-finite"
    signs = [value < 0 if direction == "SHORT" else value > 0 for value in values]
    count = sum(signs)
    if count == 3:
        return 25, "momentum aligned across 6h/24h/72h"
    if count == 2:
        return 16, "momentum aligned on two of three horizons"
    if count == 1:
        return 7, "momentum alignment is weak"
    return 0, "momentum is opposite the proposed direction"


def _structure_score(snapshot: Any, direction: str) -> tuple[int, str]:
    price = float(getattr(snapshot, "last_price", 0.0) or 0.0)
    ema20 = float(getattr(snapshot, "ema20", 0.0) or 0.0)
    ema50 = float(getattr(snapshot, "ema50", 0.0) or 0.0)
    if not _finite_values((price, ema20, ema50)) or min(price, ema20, ema50) <= 0:
        return 0, "structure evidence is invalid or non-finite"
    trend = str(getattr(snapshot, "trend", "") or "").lower()
    if direction == "SHORT":
        healthy = trend == "bearish" and price <= ema20 < ema50
        partial = price <= ema20
    else:
        healthy = trend == "bullish" and price >= ema20 > ema50
        partial = price >= ema20
    if healthy:
        return 20, "trend structure aligned"
    if partial:
        return 10, "price is aligned with EMA20 but broader structure is mixed"
    return 0, "trend structure is not aligned"


def evaluate_target_v2_shadow(snapshot: Any) -> TargetV2ShadowResult:
    symbol = str(getattr(snapshot, "symbol", "UNKNOWN"))
    direction = str(getattr(snapshot, "trade_direction", "LONG") or "LONG").upper()
    if direction not in {"LONG", "SHORT"}:
        return _reject(symbol, direction, "trade direction must be LONG or SHORT")

    try:
        percentiles = _directional_percentiles(snapshot, direction)
    except (TypeError, ValueError):
        return _reject(symbol, direction, "directional historical excursion percentiles are malformed")
    if not _finite_values(percentiles) or min(percentiles) <= 0:
        return _reject(symbol, direction, "directional historical excursion percentiles unavailable or non-finite")
    p50_24, p75_24, p90_24, p50_72, p75_72, p90_72 = percentiles
    if not (p50_24 <= p75_24 <= p90_24 and p50_72 <= p75_72 <= p90_72):
        return _reject(symbol, direction, "directional excursion percentiles are not monotonic")

    reasons: list[str] = []
    warnings: list[str] = []
    score = 0
    momentum_score, momentum_reason = _aligned_momentum(snapshot, direction)
    if "non-finite" in momentum_reason:
        return _reject(symbol, direction, momentum_reason)
    score += momentum_score
    reasons.append(momentum_reason)

    structure_score, structure_reason = _structure_score(snapshot, direction)
    if "invalid" in structure_reason or "non-finite" in structure_reason:
        return _reject(symbol, direction, structure_reason)
    score += structure_score
    reasons.append(structure_reason)

    volume_ratio = float(getattr(snapshot, "volume_ratio", 0.0) or 0.0)
    realized_24 = float(getattr(snapshot, "realized_range_24h_pct", 0.0) or 0.0)
    median_range = float(getattr(snapshot, "rolling_24h_range_median_pct", 0.0) or 0.0)
    technical = float(getattr(snapshot, "technical_score", 0.0) or 0.0)
    if not _finite_values((volume_ratio, realized_24, median_range, technical)):
        return _reject(symbol, direction, "target-v2 decision inputs contain non-finite values")

    if volume_ratio >= 1.5:
        score += 15; reasons.append("strong relative volume")
    elif volume_ratio >= 1.1:
        score += 10; reasons.append("above-average relative volume")
    elif volume_ratio >= 0.8:
        score += 5; warnings.append("volume is only near average")
    else:
        warnings.append("below-average volume")

    if median_range > 0:
        ratio = realized_24 / median_range
        if 0.75 <= ratio <= 1.5:
            score += 15; reasons.append("realized range is near historical norm")
        elif 0.5 <= ratio <= 2.0:
            score += 9; warnings.append("realized range is usable but outside preferred band")
        else:
            score += 3; warnings.append("realized range is abnormal")
    else:
        warnings.append("rolling range baseline unavailable")

    score += 15 if technical >= 80 else 10 if technical >= 70 else 5 if technical >= 60 else 0
    if technical < 60:
        warnings.append("technical score is weak")
    score = max(0, min(100, int(round(score))))

    t1 = round(min(p50_24, p75_24), 2)
    t2: float | None = round(min(p75_72, p90_72), 2)
    if not _finite_values((t1, t2)):
        return _reject(symbol, direction, "proposed target is non-finite")

    if score >= MIN_FULL_CONFIDENCE_SCORE and t1 >= MIN_SCALP_MOVE_PCT and t2 >= max(t1, MIN_SCALP_MOVE_PCT * 1.5):
        target_class, horizon = "FULL_TARGET", "24H_TO_72H"
    elif score >= MIN_REDUCED_CONFIDENCE_SCORE and t1 >= MIN_SCALP_MOVE_PCT:
        target_class = "REDUCED_TARGET"
        t2 = round(max(t1, min(p50_72, p75_72)), 2)
        horizon = "24H_TO_72H"
        warnings.append("use reduced target profile versus production mechanical target")
    elif t1 >= MIN_SCALP_MOVE_PCT:
        target_class, t2, horizon = "SCALP_TARGET", None, "UP_TO_24H"
        warnings.append("only a smaller directional move is supported")
    elif momentum_score >= 16 or structure_score >= 10:
        target_class, t1, t2, horizon = "WATCH_FOR_ENTRY", None, None, "REASSESS"
        warnings.append("directional context exists but current move budget is too small")
    else:
        target_class, t1, t2, horizon = "REJECT", None, None, "NONE"

    return TargetV2ShadowResult(symbol, direction, target_class, score, t1, t2, horizon, reasons, warnings)


def _shadow_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    intelligence = row.get("market_intelligence")
    if not isinstance(intelligence, dict):
        return None
    payload = intelligence.get("target_v2_shadow")
    return payload if isinstance(payload, dict) else None


def _allowed_horizon_hours(payload: dict[str, Any]) -> int | None:
    horizon = str(payload.get("estimated_time_horizon") or "").upper()
    if horizon == "UP_TO_24H":
        return 24
    if horizon == "24H_TO_72H":
        return 72
    return None


def _best_move(row: dict[str, Any], payload: dict[str, Any]) -> float | None:
    max_hours = _allowed_horizon_hours(payload)
    values: list[float] = []
    for horizon, item in (row.get("observations") or {}).items():
        if not isinstance(item, dict) or not isinstance(item.get("directional_move_pct"), (int, float)):
            continue
        hours = HORIZON_ORDER.get(str(horizon).lower())
        if max_hours is not None and (hours is None or hours > max_hours):
            continue
        value = float(item["directional_move_pct"])
        if math.isfinite(value):
            values.append(value)
    return max(values) if values else None


def build_target_v2_shadow_summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [(row, payload) for row in records if (payload := _shadow_payload(row)) is not None]
    by_class: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    validated = 0
    for row, payload in rows:
        by_class[str(payload.get("target_class") or "UNKNOWN")].append((row, payload))
        best = _best_move(row, payload)
        proposed = payload.get("proposed_t1_move_pct")
        if best is not None and isinstance(proposed, (int, float)) and math.isfinite(float(proposed)):
            validated += 1

    breakdown = {}
    for label, entries in sorted(by_class.items()):
        hit_denominator = hits = 0
        scores: list[float] = []
        for row, payload in entries:
            score = payload.get("confidence_score")
            if isinstance(score, (int, float)) and math.isfinite(float(score)):
                scores.append(float(score))
            best = _best_move(row, payload)
            proposed = payload.get("proposed_t1_move_pct")
            if best is not None and isinstance(proposed, (int, float)) and math.isfinite(float(proposed)):
                hit_denominator += 1
                hits += int(best >= float(proposed))
        breakdown[label] = {
            "samples": len(entries), "validated": hit_denominator,
            "t1_shadow_hit_rate_pct": round(hits / hit_denominator * 100.0, 2) if hit_denominator else None,
            "average_confidence_score": round(mean(scores), 2) if scores else None,
        }

    return {
        "version": "target-attainability-v2-shadow", "status": "OK" if rows else "NO_SHADOW_SAMPLES",
        "samples": len(rows), "validated_samples": validated, "by_target_class": breakdown,
        "production_target_gate_changed": False, "automatic_promotion": False, "shadow_only": True,
    }
