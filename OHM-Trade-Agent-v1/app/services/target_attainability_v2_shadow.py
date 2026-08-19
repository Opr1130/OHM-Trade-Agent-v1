from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Iterable


MIN_SCALP_MOVE_PCT = 0.75
MIN_FULL_CONFIDENCE_SCORE = 70
MIN_REDUCED_CONFIDENCE_SCORE = 55


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


def _directional_percentiles(snapshot: Any, direction: str) -> tuple[float, float, float, float, float, float]:
    if direction == "SHORT":
        return (
            float(getattr(snapshot, "rolling_24h_downside_median_pct", 0.0) or 0.0),
            float(getattr(snapshot, "rolling_24h_downside_p75_pct", 0.0) or 0.0),
            float(getattr(snapshot, "rolling_24h_downside_p90_pct", 0.0) or 0.0),
            float(getattr(snapshot, "rolling_72h_downside_median_pct", 0.0) or 0.0),
            float(getattr(snapshot, "rolling_72h_downside_p75_pct", 0.0) or 0.0),
            float(getattr(snapshot, "rolling_72h_downside_p90_pct", 0.0) or 0.0),
        )
    return (
        float(getattr(snapshot, "rolling_24h_upside_median_pct", 0.0) or 0.0),
        float(getattr(snapshot, "rolling_24h_upside_p75_pct", 0.0) or 0.0),
        float(getattr(snapshot, "rolling_24h_upside_p90_pct", 0.0) or 0.0),
        float(getattr(snapshot, "rolling_72h_upside_median_pct", 0.0) or 0.0),
        float(getattr(snapshot, "rolling_72h_upside_p75_pct", 0.0) or 0.0),
        float(getattr(snapshot, "rolling_72h_upside_p90_pct", 0.0) or 0.0),
    )


def _aligned_momentum(snapshot: Any, direction: str) -> tuple[int, str]:
    values = [
        float(getattr(snapshot, "momentum_6h_pct", 0.0) or 0.0),
        float(getattr(snapshot, "momentum_24h_pct", 0.0) or 0.0),
        float(getattr(snapshot, "momentum_72h_pct", 0.0) or 0.0),
    ]
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
    """Shadow-only target challenger. It never changes production qualification."""
    symbol = str(getattr(snapshot, "symbol", "UNKNOWN"))
    direction = str(getattr(snapshot, "trade_direction", "LONG") or "LONG").upper()
    p50_24, p75_24, p90_24, p50_72, p75_72, p90_72 = _directional_percentiles(snapshot, direction)
    reasons: list[str] = []
    warnings: list[str] = []

    if min(p50_24, p75_24, p90_24, p50_72, p75_72, p90_72) <= 0:
        return TargetV2ShadowResult(
            symbol=symbol,
            direction=direction,
            target_class="REJECT",
            confidence_score=0,
            proposed_t1_move_pct=None,
            proposed_t2_move_pct=None,
            estimated_time_horizon="UNAVAILABLE",
            reasons=["directional historical excursion percentiles unavailable"],
            warnings=[],
        )

    score = 0
    momentum_score, momentum_reason = _aligned_momentum(snapshot, direction)
    score += momentum_score
    reasons.append(momentum_reason)

    structure_score, structure_reason = _structure_score(snapshot, direction)
    score += structure_score
    reasons.append(structure_reason)

    volume_ratio = float(getattr(snapshot, "volume_ratio", 0.0) or 0.0)
    if volume_ratio >= 1.5:
        score += 15
        reasons.append("strong relative volume")
    elif volume_ratio >= 1.1:
        score += 10
        reasons.append("above-average relative volume")
    elif volume_ratio >= 0.8:
        score += 5
        warnings.append("volume is only near average")
    else:
        warnings.append("below-average volume")

    realized_24 = float(getattr(snapshot, "realized_range_24h_pct", 0.0) or 0.0)
    median_range = float(getattr(snapshot, "rolling_24h_range_median_pct", 0.0) or 0.0)
    if median_range > 0:
        ratio = realized_24 / median_range
        if 0.75 <= ratio <= 1.5:
            score += 15
            reasons.append("realized range is near historical norm")
        elif 0.5 <= ratio <= 2.0:
            score += 9
            warnings.append("realized range is usable but outside preferred band")
        else:
            score += 3
            warnings.append("realized range is abnormal")
    else:
        warnings.append("rolling range baseline unavailable")

    technical = float(getattr(snapshot, "technical_score", 0.0) or 0.0)
    score += 15 if technical >= 80 else 10 if technical >= 70 else 5 if technical >= 60 else 0
    if technical < 60:
        warnings.append("technical score is weak")

    score = max(0, min(100, int(round(score))))

    # Targets are intentionally derived from observed excursion distributions,
    # not flat ATR multiples. P75 is the ceiling for normal target proposals;
    # P90 is diagnostic only and never used as a default shadow target.
    t1 = round(min(p50_24, p75_24), 2)
    t2 = round(min(p75_72, p90_72), 2)

    if score >= MIN_FULL_CONFIDENCE_SCORE and t1 >= MIN_SCALP_MOVE_PCT and t2 >= max(t1, MIN_SCALP_MOVE_PCT * 1.5):
        target_class = "FULL_TARGET"
        horizon = "24H_TO_72H"
    elif score >= MIN_REDUCED_CONFIDENCE_SCORE and t1 >= MIN_SCALP_MOVE_PCT:
        target_class = "REDUCED_TARGET"
        t2 = round(max(t1, min(p50_72, p75_72)), 2)
        horizon = "24H_TO_72H"
        warnings.append("use reduced target profile versus production mechanical target")
    elif t1 >= MIN_SCALP_MOVE_PCT:
        target_class = "SCALP_TARGET"
        t2 = None
        horizon = "UP_TO_24H"
        warnings.append("only a smaller directional move is supported")
    elif momentum_score >= 16 or structure_score >= 10:
        target_class = "WATCH_FOR_ENTRY"
        t1 = None
        t2 = None
        horizon = "REASSESS"
        warnings.append("directional context exists but current move budget is too small")
    else:
        target_class = "REJECT"
        t1 = None
        t2 = None
        horizon = "NONE"

    return TargetV2ShadowResult(
        symbol=symbol,
        direction=direction,
        target_class=target_class,
        confidence_score=score,
        proposed_t1_move_pct=t1,
        proposed_t2_move_pct=t2,
        estimated_time_horizon=horizon,
        reasons=reasons,
        warnings=warnings,
    )


def _shadow_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    intelligence = row.get("market_intelligence")
    if not isinstance(intelligence, dict):
        return None
    payload = intelligence.get("target_v2_shadow")
    return payload if isinstance(payload, dict) else None


def _best_move(row: dict[str, Any]) -> float | None:
    values = []
    for item in (row.get("observations") or {}).values():
        if isinstance(item, dict) and isinstance(item.get("directional_move_pct"), (int, float)):
            values.append(float(item["directional_move_pct"]))
    return max(values) if values else None


def build_target_v2_shadow_summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for row in records:
        payload = _shadow_payload(row)
        if payload is not None:
            rows.append((row, payload))

    by_class: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    validated = 0
    class_hits = Counter()
    for row, payload in rows:
        label = str(payload.get("target_class") or "UNKNOWN")
        by_class[label].append((row, payload))
        best = _best_move(row)
        proposed = payload.get("proposed_t1_move_pct")
        if best is not None and isinstance(proposed, (int, float)):
            validated += 1
            if best >= float(proposed):
                class_hits[label] += 1

    breakdown = {}
    for label, entries in sorted(by_class.items()):
        hit_denominator = 0
        hits = 0
        scores = []
        for row, payload in entries:
            score = payload.get("confidence_score")
            if isinstance(score, (int, float)):
                scores.append(float(score))
            best = _best_move(row)
            proposed = payload.get("proposed_t1_move_pct")
            if best is not None and isinstance(proposed, (int, float)):
                hit_denominator += 1
                hits += int(best >= float(proposed))
        breakdown[label] = {
            "samples": len(entries),
            "validated": hit_denominator,
            "t1_shadow_hit_rate_pct": round(hits / hit_denominator * 100.0, 2) if hit_denominator else None,
            "average_confidence_score": round(mean(scores), 2) if scores else None,
        }

    return {
        "version": "target-attainability-v2-shadow",
        "status": "OK" if rows else "NO_SHADOW_SAMPLES",
        "samples": len(rows),
        "validated_samples": validated,
        "by_target_class": breakdown,
        "production_target_gate_changed": False,
        "automatic_promotion": False,
        "shadow_only": True,
    }
