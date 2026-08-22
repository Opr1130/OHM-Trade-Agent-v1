import math
import os
from dataclasses import dataclass

from app.scanner.models import MarketSnapshot
from app.services.entry_exit_advisor import EntryExitPlan


RESISTANCE_WEIGHT = 30
HISTORICAL_MOVE_WEIGHT = 30
MOMENTUM_WEIGHT = 20
VOLUME_CONTINUATION_WEIGHT = 10
VOLATILITY_WEIGHT = 10
T1_RESISTANCE_WEIGHT = 12
T1_HISTORICAL_MOVE_WEIGHT = 12
VOLUME_WEIGHT = 6

MIN_QUALIFYING_SCORE = 65
NEAR_RESISTANCE_ATR = 0.25
MIN_BREAKOUT_VOLUME_RATIO = 1.1
MATERIAL_P90_EXCESS_RATIO = 1.00
NORMAL_VOLATILITY_MIN_RATIO = 0.75
NORMAL_VOLATILITY_MAX_RATIO = 1.50
USABLE_VOLATILITY_MIN_RATIO = 0.50
USABLE_VOLATILITY_MAX_RATIO = 2.00
MAX_LONG_SPREAD_BPS = 30.0
MAX_LONG_ROUND_TRIP_DRAG_PCT = 0.75


@dataclass(frozen=True)
class TargetAttainabilityResult:
    symbol: str
    qualified: bool
    target_1_move_pct: float
    target_2_move_pct: float
    target_1_atr_multiple: float
    target_2_atr_multiple: float
    clearance_to_24h_resistance_pct: float
    clearance_to_72h_resistance_pct: float
    momentum_context: str
    volatility_context: str
    attainability_score: int
    strengths: list[str]
    warnings: list[str]
    rejection_reasons: list[str]


def _reject(plan: EntryExitPlan, reason: str) -> TargetAttainabilityResult:
    return TargetAttainabilityResult(
        symbol=plan.symbol,
        qualified=False,
        target_1_move_pct=0.0,
        target_2_move_pct=0.0,
        target_1_atr_multiple=0.0,
        target_2_atr_multiple=0.0,
        clearance_to_24h_resistance_pct=0.0,
        clearance_to_72h_resistance_pct=0.0,
        momentum_context="unavailable",
        volatility_context="unavailable",
        attainability_score=0,
        strengths=[],
        warnings=[],
        rejection_reasons=[reason],
    )


def _valid_long_geometry(plan: EntryExitPlan, entry: float, atr_value: float) -> bool:
    values = (
        plan.entry_low,
        plan.entry_high,
        plan.chase_limit,
        plan.stop_price,
        plan.target_1,
        plan.target_2,
        plan.reward_to_risk_1,
        plan.reward_to_risk_2,
        entry,
        atr_value,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    return (
        plan.entry_low > 0
        and plan.entry_high > 0
        and plan.entry_low <= plan.entry_high
        and plan.chase_limit >= plan.entry_high
        and 0 < plan.stop_price < entry < plan.target_1 < plan.target_2
        and atr_value > 0
        and plan.reward_to_risk_1 > 0
        and plan.reward_to_risk_2 > 0
    )


def _long_execution_rejections(snapshot: MarketSnapshot) -> list[str]:
    """Apply executable-cost limits to LONGs before targets can qualify."""
    execution = snapshot.execution_validation
    production = os.getenv("APP_ENV", "").strip().lower() == "production"
    if execution is None:
        return ["LONG execution evidence is unavailable"] if production else []

    reasons: list[str] = []
    if str(execution.status or "").upper() in {"INVALID", "UNAVAILABLE"} and production:
        reasons.append(f"LONG execution validation status is {execution.status}")

    spread = execution.spread_bps
    if spread is None or not math.isfinite(float(spread)):
        if production:
            reasons.append("LONG spread evidence is unavailable")
    elif float(spread) > MAX_LONG_SPREAD_BPS:
        reasons.append(
            f"LONG spread {float(spread):.2f}bps exceeds quality limit {MAX_LONG_SPREAD_BPS:.2f}bps"
        )

    drag = execution.estimated_visible_round_trip_market_drag_pct
    if drag is None or not math.isfinite(float(drag)):
        if production:
            reasons.append("LONG round-trip drag evidence is unavailable")
    elif float(drag) > MAX_LONG_ROUND_TRIP_DRAG_PCT:
        reasons.append(
            f"LONG round-trip drag {float(drag):.2f}% exceeds quality limit {MAX_LONG_ROUND_TRIP_DRAG_PCT:.2f}%"
        )
    return reasons


def _aligned_momentum(snapshot: MarketSnapshot) -> bool:
    values = (
        snapshot.momentum_6h_pct,
        snapshot.momentum_24h_pct,
        snapshot.momentum_72h_pct,
    )
    return all(math.isfinite(float(value)) and value > 0 for value in values)


def _healthy_structure(snapshot: MarketSnapshot) -> bool:
    return (
        all(
            math.isfinite(float(value))
            for value in (snapshot.last_price, snapshot.ema20, snapshot.ema50)
        )
        and snapshot.trend == "bullish"
        and snapshot.last_price >= snapshot.ema20
        and snapshot.ema20 > snapshot.ema50
    )


def _breakout_confirmed(snapshot: MarketSnapshot) -> bool:
    return (
        _aligned_momentum(snapshot)
        and _healthy_structure(snapshot)
        and math.isfinite(float(snapshot.volume_ratio))
        and snapshot.volume_ratio >= MIN_BREAKOUT_VOLUME_RATIO
    )


def _resistance_points(
    target: float,
    resistance: float,
    atr_value: float,
    weight: int,
    breakout_confirmed: bool,
) -> tuple[int, str]:
    if not all(math.isfinite(float(value)) for value in (target, resistance, atr_value)):
        return 0, "unavailable"
    if resistance <= 0 or atr_value <= 0:
        return 0, "unavailable"
    clearance_atr = (resistance - target) / atr_value
    if clearance_atr >= NEAR_RESISTANCE_ATR:
        return weight, "clear"
    if clearance_atr >= 0:
        return weight // 3, "near"
    if breakout_confirmed:
        return (weight * 2) // 3, "confirmed_breakout"
    return 0, "unconfirmed_breakout"


def _historical_move_points(
    move_pct: float,
    median_pct: float,
    p75_pct: float,
    p90_pct: float,
    weight: int,
) -> tuple[int, str]:
    values = (move_pct, median_pct, p75_pct, p90_pct)
    if not all(math.isfinite(float(value)) for value in values):
        return 0, "unavailable"
    if (
        min(median_pct, p75_pct, p90_pct) < 0
        or not median_pct <= p75_pct <= p90_pct
    ):
        return 0, "unavailable"
    if p90_pct == 0:
        return (
            (weight, "normal")
            if move_pct <= 0
            else (0, "materially_above_p90")
        )
    if move_pct <= median_pct:
        return weight, "normal"
    if move_pct <= p75_pct:
        return (weight * 5) // 6, "within_p75"
    if move_pct <= p90_pct:
        return weight // 2, "within_p90"
    if move_pct <= p90_pct * MATERIAL_P90_EXCESS_RATIO:
        return weight // 6, "above_p90"
    return 0, "materially_above_p90"


def evaluate_target_attainability(
    plan: EntryExitPlan,
    snapshot: MarketSnapshot,
) -> TargetAttainabilityResult:
    """Score deterministic opportunity quality; this is not a probability."""
    strengths: list[str] = []
    warnings: list[str] = []
    rejection_reasons: list[str] = []
    entry = (plan.entry_low + plan.entry_high) / 2

    if not _valid_long_geometry(plan, entry, snapshot.atr):
        return _reject(plan, "Invalid LONG target/stop geometry or non-finite plan values")

    rejection_reasons.extend(_long_execution_rejections(snapshot))

    t1_move_pct = (plan.target_1 - entry) / entry * 100
    t2_move_pct = (plan.target_2 - entry) / entry * 100
    t1_atr = (plan.target_1 - entry) / snapshot.atr
    t2_atr = (plan.target_2 - entry) / snapshot.atr
    clearance_24 = (snapshot.recent_24h_high - plan.target_1) / entry * 100
    clearance_72 = (snapshot.recent_72h_high - plan.target_2) / entry * 100
    score = 0

    confirmed_breakout = _breakout_confirmed(snapshot)
    for target, resistance, weight, label in (
        (plan.target_1, snapshot.recent_24h_high, T1_RESISTANCE_WEIGHT, "T1/24h"),
        (plan.target_2, snapshot.recent_72h_high, RESISTANCE_WEIGHT - T1_RESISTANCE_WEIGHT, "T2/72h"),
    ):
        points, state = _resistance_points(target, resistance, snapshot.atr, weight, confirmed_breakout)
        score += points
        if state == "clear":
            strengths.append(f"{label} has resistance clearance")
        elif state == "near":
            warnings.append(f"{label} sits directly below recent resistance")
        elif state == "confirmed_breakout":
            strengths.append(f"{label} breakout has momentum, volume, and structure confirmation")
        elif state == "unconfirmed_breakout":
            warnings.append(f"{label} crosses resistance without deterministic breakout confirmation")

    historical_specs = (
        (t1_move_pct, snapshot.rolling_24h_upside_median_pct, snapshot.rolling_24h_upside_p75_pct, snapshot.rolling_24h_upside_p90_pct, T1_HISTORICAL_MOVE_WEIGHT, "Target 1/24h"),
        (t2_move_pct, snapshot.rolling_72h_upside_median_pct, snapshot.rolling_72h_upside_p75_pct, snapshot.rolling_72h_upside_p90_pct, HISTORICAL_MOVE_WEIGHT - T1_HISTORICAL_MOVE_WEIGHT, "Target 2/72h"),
    )
    for move_pct, median, p75, p90, weight, label in historical_specs:
        points, state = _historical_move_points(move_pct, median, p75, p90, weight)
        score += points
        if state == "normal":
            strengths.append(f"{label} fits the median historical move")
        elif state == "within_p75":
            strengths.append(f"{label} fits within the historical 75th percentile")
        elif state == "within_p90":
            warnings.append(f"{label} requires an uncommon p75-p90 move")
        elif state == "above_p90":
            warnings.append(f"{label} is above the historical 90th percentile")
        elif state == "materially_above_p90":
            rejection_reasons.append(
                f"{label} move {move_pct:.2f}% exceeds "
                f"{MATERIAL_P90_EXCESS_RATIO:.2f}x historical p90 {p90:.2f}%"
            )
        else:
            rejection_reasons.append(f"{label} historical range data is unavailable")

    momentum = (
        snapshot.momentum_6h_pct,
        snapshot.momentum_24h_pct,
        snapshot.momentum_72h_pct,
    )
    if not all(math.isfinite(float(value)) for value in momentum):
        momentum_context = "unavailable due to non-finite momentum evidence"
        rejection_reasons.append("Momentum evidence contains non-finite values")
    elif _aligned_momentum(snapshot):
        score += MOMENTUM_WEIGHT
        momentum_context = "positive across 6h, 24h, and 72h"
        strengths.append("Short- and medium-term momentum agree")
    elif momentum[0] > 0 and momentum[1] > 0:
        score += 14
        momentum_context = "positive short term, mixed over 72h"
        warnings.append("72h momentum does not confirm continuation")
    elif momentum[1] > 0 and momentum[2] > 0:
        score += 10
        momentum_context = "6h momentum is weakening against positive 24h/72h structure"
        warnings.append("Short-term momentum is weakening")
    elif any(value > 0 for value in momentum):
        score += 5
        momentum_context = "conflicting across 6h, 24h, and 72h"
        warnings.append("Momentum timeframes conflict")
    else:
        momentum_context = "negative across 6h, 24h, and 72h"
        warnings.append("Momentum does not support bullish continuation")

    if math.isfinite(float(snapshot.volume_ratio)):
        if snapshot.volume_ratio >= 1.5:
            score += VOLUME_WEIGHT
            strengths.append("Strong volume confirms continuation")
        elif snapshot.volume_ratio >= MIN_BREAKOUT_VOLUME_RATIO:
            score += 4
            strengths.append("Above-average volume supports continuation")
        elif snapshot.volume_ratio >= 0.8:
            score += 2
            warnings.append("Volume is only near average")
        else:
            warnings.append("Below-average volume does not confirm continuation")
    else:
        rejection_reasons.append("Volume evidence contains a non-finite value")

    if _healthy_structure(snapshot):
        score += VOLUME_CONTINUATION_WEIGHT - VOLUME_WEIGHT
        strengths.append("Trend structure supports continuation")
    elif all(math.isfinite(float(value)) for value in (snapshot.last_price, snapshot.ema20)) and snapshot.last_price >= snapshot.ema20:
        score += 2
        warnings.append("Continuation structure is only partially aligned")
    else:
        warnings.append("Trend structure does not support continuation")

    median_24h = snapshot.rolling_24h_range_median_pct
    if math.isfinite(float(median_24h)) and math.isfinite(float(snapshot.realized_range_24h_pct)) and median_24h > 0:
        volatility_ratio = snapshot.realized_range_24h_pct / median_24h
        if NORMAL_VOLATILITY_MIN_RATIO <= volatility_ratio <= NORMAL_VOLATILITY_MAX_RATIO:
            score += VOLATILITY_WEIGHT
            strengths.append("Current volatility is near its historical norm")
        elif USABLE_VOLATILITY_MIN_RATIO <= volatility_ratio <= USABLE_VOLATILITY_MAX_RATIO:
            score += 6
            warnings.append("Current volatility is outside its preferred historical band")
        else:
            score += 2
            warnings.append("Current volatility is abnormal versus recent history")
        volatility_context = (
            f"Current 24h range is {volatility_ratio:.2f}x rolling median; "
            f"T1 {t1_move_pct:.2f}% vs 24h upside p50/p75/p90 "
            f"{snapshot.rolling_24h_upside_median_pct:.2f}/"
            f"{snapshot.rolling_24h_upside_p75_pct:.2f}/"
            f"{snapshot.rolling_24h_upside_p90_pct:.2f}%; "
            f"T2 {t2_move_pct:.2f}% vs 72h upside p50/p75/p90 "
            f"{snapshot.rolling_72h_upside_median_pct:.2f}/"
            f"{snapshot.rolling_72h_upside_p75_pct:.2f}/"
            f"{snapshot.rolling_72h_upside_p90_pct:.2f}%; "
            f"T1={t1_atr:.2f} ATR and T2={t2_atr:.2f} ATR (diagnostic only)"
        )
    else:
        volatility_context = "rolling volatility history unavailable"
        rejection_reasons.append("Rolling volatility history is unavailable")

    score = max(0, min(100, score))
    if score < MIN_QUALIFYING_SCORE:
        rejection_reasons.append(f"Attainability score {score} is below minimum {MIN_QUALIFYING_SCORE}")

    return TargetAttainabilityResult(
        symbol=plan.symbol,
        qualified=not rejection_reasons,
        target_1_move_pct=round(t1_move_pct, 2),
        target_2_move_pct=round(t2_move_pct, 2),
        target_1_atr_multiple=round(t1_atr, 2),
        target_2_atr_multiple=round(t2_atr, 2),
        clearance_to_24h_resistance_pct=round(clearance_24, 2),
        clearance_to_72h_resistance_pct=round(clearance_72, 2),
        momentum_context=momentum_context,
        volatility_context=volatility_context,
        attainability_score=score,
        strengths=strengths,
        warnings=warnings,
        rejection_reasons=rejection_reasons,
    )
