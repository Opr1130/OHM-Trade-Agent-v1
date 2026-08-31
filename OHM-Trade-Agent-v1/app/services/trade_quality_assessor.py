from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from app.core.config import Settings
from app.opip.ml.contracts import FeatureSnapshot
from app.services.entry_exit_advisor import EntryExitPlan


@dataclass(frozen=True)
class ContinuationAssessment:
    snapshot_id: str
    decision: str
    score: int
    evidence_quality: str
    supporting_factors: tuple[str, ...]
    vetoes: tuple[str, ...]
    exhaustion_state: str


@dataclass(frozen=True)
class EntryAssessment:
    snapshot_id: str
    decision: str
    quality_score: int
    reasons: tuple[str, ...]
    exhaustion_risk: str


@dataclass(frozen=True)
class TradePlanAssessment:
    snapshot_id: str
    actionable: bool
    decision: str
    continuation: ContinuationAssessment
    entry: EntryAssessment


def _number(features: dict[str, Any], name: str) -> float | None:
    value = features.get(name)
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _directional(value: float | None, direction: str) -> float | None:
    if value is None:
        return None
    return -value if direction == "SHORT" else value


def _exhaustion(features: dict[str, Any]) -> tuple[str, float]:
    price = _number(features, "last_price")
    ema20 = _number(features, "ema20")
    atr = _number(features, "atr")
    if price is None or ema20 is None or atr is None or atr <= 0:
        return "UNKNOWN", 0.0
    extension = abs(price - ema20) / atr
    if extension >= 2.5:
        return "HIGH", extension
    if extension >= 1.5:
        return "MODERATE", extension
    return "LOW", extension


def assess_continuation(
    snapshot: FeatureSnapshot,
    *,
    min_liquidity_usd: float | None = None,
) -> ContinuationAssessment:
    features = snapshot.ml_feature_mapping()
    direction = snapshot.direction
    score = 0.0
    supporting: list[str] = []
    vetoes: list[str] = []

    liquidity = _number(features, "combined_24h_liquidity_usd")
    execution_status = str(features.get("execution_availability") or "UNAVAILABLE").upper()
    drag = _number(features, "execution_drag_pct")
    exhaustion_state, _ = _exhaustion(features)
    if min_liquidity_usd is None:
        min_liquidity_usd = float(
            Settings.model_fields["signal_quality_min_liquidity_usd"].default
        )

    if liquidity is None:
        vetoes.append("LIQUIDITY_UNAVAILABLE")
    elif liquidity < float(min_liquidity_usd):
        vetoes.append("LIQUIDITY_BELOW_CONFIGURED_MINIMUM")
    if execution_status not in {"VALID", "WARN"}:
        vetoes.append("EXECUTION_UNAVAILABLE")
    elif drag is None:
        vetoes.append("EXECUTION_DRAG_UNAVAILABLE")
    elif drag > 2.0:
        vetoes.append("EXECUTION_DRAG_EXCESSIVE")
    if exhaustion_state == "HIGH":
        vetoes.append("SEVERE_EXTENSION")

    price = _number(features, "last_price")
    ema20 = _number(features, "ema20")
    ema50 = _number(features, "ema50")
    ema200 = _number(features, "ema200")
    ema_aligned = False
    if all(value is not None for value in (price, ema20, ema50, ema200)):
        if direction == "SHORT":
            ema_aligned = bool(price < ema20 < ema50 < ema200)
        else:
            ema_aligned = bool(price > ema20 > ema50 > ema200)
    if ema_aligned:
        score += 20.0
        supporting.append("EMA_STRUCTURE_ALIGNED")

    trend = str(features.get("trend") or "neutral").lower()
    aligned = (direction == "LONG" and trend == "bullish") or (
        direction == "SHORT" and trend == "bearish"
    )
    if aligned:
        score += 15.0
        supporting.append("TREND_ALIGNED")
    elif trend == "neutral":
        score += 5.0

    rsi = _number(features, "rsi")
    if rsi is not None:
        if direction == "LONG":
            if rsi >= 82.0:
                vetoes.append("RSI_EXHAUSTED")
            elif 45.0 <= rsi <= 70.0:
                score += 10.0
                supporting.append("RSI_CONTINUATION_ZONE")
        else:
            if rsi <= 18.0:
                vetoes.append("RSI_EXHAUSTED")
            elif 30.0 <= rsi <= 55.0:
                score += 10.0
                supporting.append("RSI_CONTINUATION_ZONE")

    macd_histogram = _directional(
        _number(features, "macd_histogram"),
        direction,
    )
    if macd_histogram is not None and macd_histogram > 0:
        score += 10.0
        supporting.append("MACD_DIRECTION_ALIGNED")

    volume = _number(features, "volume_ratio")
    if volume is not None:
        score += max(0.0, min(15.0, volume / 2.0 * 15.0))
        if volume >= 1.5:
            supporting.append("PARTICIPATION_EXPANDING")

    m6 = _directional(_number(features, "momentum_6h_pct"), direction)
    m24 = _directional(_number(features, "momentum_24h_pct"), direction)
    if m6 is not None:
        if m6 > 0:
            score += 5.0
        if m6 >= 2.0:
            score += 5.0
            supporting.append("MOMENTUM_6H_ALIGNED")
    if m24 is not None and m24 > 0:
        score += 5.0
        if m24 >= 5.0:
            supporting.append("MOMENTUM_24H_ALIGNED")

    cross = str(features.get("cross_pair_confirmation_status") or "UNAVAILABLE").upper()
    if cross == "CONFIRMED":
        score += 10.0
        supporting.append("CROSS_PAIR_CONFIRMED")
    elif cross == "MIXED":
        score += 5.0
    elif cross == "SINGLE_MARKET":
        score += 3.0

    if execution_status in {"VALID", "WARN"}:
        score += 5.0

    if exhaustion_state == "MODERATE":
        score -= 10.0

    score_int = round(max(0.0, min(100.0, score)))
    unavailable_families = sum(
        str(features.get(name) or "UNAVAILABLE").upper()
        in {"UNAVAILABLE", "UNRESOLVED", "UNKNOWN"}
        for name in (
            "execution_availability",
            "reference_availability",
            "news_availability",
            "catalyst_availability",
            "movement_availability",
        )
    )
    evidence_quality = (
        "DEGRADED" if unavailable_families >= 4
        else "PARTIAL" if unavailable_families >= 2
        else "GOOD"
    )

    if vetoes:
        decision = "FAIL"
    elif score_int >= 70:
        decision = "PASS"
    elif score_int >= 55:
        decision = "MONITOR"
    else:
        decision = "FAIL"

    return ContinuationAssessment(
        snapshot_id=snapshot.snapshot_id,
        decision=decision,
        score=score_int,
        evidence_quality=evidence_quality,
        supporting_factors=tuple(supporting),
        vetoes=tuple(vetoes),
        exhaustion_state=exhaustion_state,
    )


def assess_entry(
    snapshot: FeatureSnapshot,
    plan: EntryExitPlan,
    continuation: ContinuationAssessment,
) -> EntryAssessment:
    if continuation.snapshot_id != snapshot.snapshot_id:
        raise ValueError("continuation assessment must be computed from the same snapshot")

    features = snapshot.ml_feature_mapping()
    exhaustion_state, extension_atr = _exhaustion(features)
    reasons: list[str] = []
    score = float(continuation.score) * 0.55

    if continuation.decision == "FAIL":
        return EntryAssessment(
            snapshot_id=snapshot.snapshot_id,
            decision="VETO",
            quality_score=round(score),
            reasons=("CONTINUATION_FAILED",),
            exhaustion_risk=exhaustion_state,
        )

    geometry = (
        plan.entry_low > 0
        and plan.entry_high >= plan.entry_low
        and plan.stop_price > 0
        and plan.target_1 > 0
        and plan.target_2 > 0
        and plan.reward_to_risk_1 >= 1.2
        and plan.reward_to_risk_2 >= 2.0
    )
    if not geometry:
        return EntryAssessment(
            snapshot_id=snapshot.snapshot_id,
            decision="VETO",
            quality_score=int(round(score)),
            reasons=("INVALID_RISK_REWARD_GEOMETRY",),
            exhaustion_risk=exhaustion_state,
        )

    score += min(20.0, float(plan.reward_to_risk_1) / 2.0 * 12.0)
    score += min(15.0, float(plan.reward_to_risk_2) / 3.0 * 15.0)

    if exhaustion_state == "LOW":
        score += 10.0
        reasons.append("LOW_EXTENSION")
    elif exhaustion_state == "MODERATE":
        score -= 10.0
        reasons.append(f"MODERATE_EXTENSION_{extension_atr:.2f}ATR")
    else:
        score -= 25.0
        reasons.append("HIGH_OR_UNKNOWN_EXTENSION")

    score_int = int(round(max(0.0, min(100.0, score))))

    if not plan.valid_now:
        decision = "WAIT"
        reasons.append("ENTRY_NOT_VALID_NOW")
    elif continuation.decision != "PASS":
        decision = "WAIT"
        reasons.append("CONTINUATION_NOT_YET_PASS")
    elif exhaustion_state == "HIGH":
        decision = "VETO"
        reasons.append("DO_NOT_CHASE")
    elif score_int >= 70:
        decision = "PASS"
        reasons.append("ENTRY_QUALITY_PASS")
    else:
        decision = "WAIT"
        reasons.append("ENTRY_SCORE_BELOW_PASS")

    return EntryAssessment(
        snapshot_id=snapshot.snapshot_id,
        decision=decision,
        quality_score=score_int,
        reasons=tuple(reasons),
        exhaustion_risk=exhaustion_state,
    )


def assess_trade_quality(
    snapshot: FeatureSnapshot,
    plan: EntryExitPlan,
    *,
    min_liquidity_usd: float | None = None,
) -> TradePlanAssessment:
    continuation = assess_continuation(
        snapshot,
        min_liquidity_usd=min_liquidity_usd,
    )
    entry = assess_entry(snapshot, plan, continuation)
    actionable = continuation.decision == "PASS" and entry.decision == "PASS"
    return TradePlanAssessment(
        snapshot_id=snapshot.snapshot_id,
        actionable=actionable,
        decision="ACTIONABLE" if actionable else "MONITOR",
        continuation=continuation,
        entry=entry,
    )