import math
from dataclasses import dataclass

from app.scanner.models import MarketSnapshot
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.target_attainability import (
    HISTORICAL_MOVE_WEIGHT,
    MATERIAL_P90_EXCESS_RATIO,
    MIN_BREAKOUT_VOLUME_RATIO,
    MIN_QUALIFYING_SCORE,
    MOMENTUM_WEIGHT,
    NEAR_RESISTANCE_ATR,
    NORMAL_VOLATILITY_MAX_RATIO,
    NORMAL_VOLATILITY_MIN_RATIO,
    RESISTANCE_WEIGHT,
    T1_HISTORICAL_MOVE_WEIGHT,
    T1_RESISTANCE_WEIGHT,
    USABLE_VOLATILITY_MAX_RATIO,
    USABLE_VOLATILITY_MIN_RATIO,
    VOLATILITY_WEIGHT,
    VOLUME_CONTINUATION_WEIGHT,
    VOLUME_WEIGHT,
)


@dataclass(frozen=True)
class ShortTargetAttainabilityResult:
    symbol: str
    qualified: bool
    target_1_move_pct: float
    target_2_move_pct: float
    target_1_atr_multiple: float
    target_2_atr_multiple: float
    clearance_to_24h_support_pct: float
    clearance_to_72h_support_pct: float
    momentum_context: str
    volatility_context: str
    attainability_score: int
    strengths: list[str]
    warnings: list[str]
    rejection_reasons: list[str]

    @property
    def clearance_to_24h_resistance_pct(self) -> float:
        return self.clearance_to_24h_support_pct

    @property
    def clearance_to_72h_resistance_pct(self) -> float:
        return self.clearance_to_72h_support_pct


def _reject(plan: EntryExitPlan, reason: str) -> ShortTargetAttainabilityResult:
    return ShortTargetAttainabilityResult(
        plan.symbol, False, 0, 0, 0, 0, 0, 0,
        "unavailable", "unavailable", 0, [], [], [reason],
    )


def _valid_short_geometry(plan: EntryExitPlan, entry: float, atr_value: float) -> bool:
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
        0 < plan.chase_limit <= plan.entry_low <= plan.entry_high
        and 0 < plan.target_2 < plan.target_1 < entry < plan.stop_price
        and atr_value > 0
        and plan.reward_to_risk_1 > 0
        and plan.reward_to_risk_2 > 0
    )


def _negative_momentum(snapshot: MarketSnapshot) -> bool:
    values = (
        snapshot.momentum_6h_pct,
        snapshot.momentum_24h_pct,
        snapshot.momentum_72h_pct,
    )
    return all(math.isfinite(float(value)) and value < 0 for value in values)


def _healthy_bearish_structure(snapshot: MarketSnapshot) -> bool:
    return (
        all(math.isfinite(float(value)) for value in (snapshot.last_price, snapshot.ema20, snapshot.ema50))
        and snapshot.trend == "bearish"
        and snapshot.last_price <= snapshot.ema20
        and snapshot.ema20 < snapshot.ema50
    )


def _breakdown_confirmed(snapshot: MarketSnapshot) -> bool:
    return (
        _negative_momentum(snapshot)
        and _healthy_bearish_structure(snapshot)
        and math.isfinite(float(snapshot.volume_ratio))
        and snapshot.volume_ratio >= MIN_BREAKOUT_VOLUME_RATIO
    )


def _support_points(
    target: float,
    support: float,
    atr_value: float,
    weight: int,
    breakdown_confirmed: bool,
) -> tuple[int, str]:
    if not all(math.isfinite(float(value)) for value in (target, support, atr_value)):
        return 0, "unavailable"
    if support <= 0 or atr_value <= 0:
        return 0, "unavailable"
    clearance_atr = (target - support) / atr_value
    if clearance_atr >= NEAR_RESISTANCE_ATR:
        return weight, "clear"
    if clearance_atr >= 0:
        return weight // 3, "near"
    if breakdown_confirmed:
        return (weight * 2) // 3, "confirmed_breakdown"
    return 0, "unconfirmed_breakdown"


def _historical_points(
    move_pct: float,
    median_pct: float,
    p75_pct: float,
    p90_pct: float,
    weight: int,
) -> tuple[int, str]:
    if not all(math.isfinite(float(value)) for value in (move_pct, median_pct, p75_pct, p90_pct)):
        return 0, "unavailable"
    if min(median_pct, p75_pct, p90_pct) < 0 or not median_pct <= p75_pct <= p90_pct:
        return 0, "unavailable"
    if p90_pct == 0:
        return (weight, "normal") if move_pct <= 0 else (0, "materially_above_p90")
    if move_pct <= median_pct:
        return weight, "normal"
    if move_pct <= p75_pct:
        return (weight * 5) // 6, "within_p75"
    if move_pct <= p90_pct:
        return weight // 2, "within_p90"
    if move_pct <= p90_pct * MATERIAL_P90_EXCESS_RATIO:
        return weight // 6, "above_p90"
    return 0, "materially_above_p90"


def evaluate_short_target_attainability(
    plan: EntryExitPlan,
    snapshot: MarketSnapshot,
) -> ShortTargetAttainabilityResult:
    """Deterministic short-side realism gate; score is not a probability."""
    strengths: list[str] = []
    warnings: list[str] = []
    rejection_reasons: list[str] = []
    entry = (plan.entry_low + plan.entry_high) / 2
    if not _valid_short_geometry(plan, entry, snapshot.atr):
        return _reject(plan, "Invalid SHORT target/stop geometry or non-finite plan values")

    t1_move_pct = (entry - plan.target_1) / entry * 100
    t2_move_pct = (entry - plan.target_2) / entry * 100
    t1_atr = (entry - plan.target_1) / snapshot.atr
    t2_atr = (entry - plan.target_2) / snapshot.atr
    clearance_24 = (plan.target_1 - snapshot.recent_24h_low) / entry * 100
    clearance_72 = (plan.target_2 - snapshot.recent_72h_low) / entry * 100
    score = 0

    confirmed_breakdown = _breakdown_confirmed(snapshot)
    for target, support, weight, label in (
        (plan.target_1, snapshot.recent_24h_low, T1_RESISTANCE_WEIGHT, "T1/24h"),
        (plan.target_2, snapshot.recent_72h_low, RESISTANCE_WEIGHT - T1_RESISTANCE_WEIGHT, "T2/72h"),
    ):
        points, state = _support_points(target, support, snapshot.atr, weight, confirmed_breakdown)
        score += points
        if state == "clear":
            strengths.append(f"{label} has support clearance")
        elif state == "near":
            warnings.append(f"{label} sits directly above recent support")
        elif state == "confirmed_breakdown":
            strengths.append(f"{label} breakdown has momentum, volume, and structure confirmation")
        elif state == "unconfirmed_breakdown":
            warnings.append(f"{label} crosses support without deterministic breakdown confirmation")

    historical_specs = (
        (t1_move_pct, snapshot.rolling_24h_downside_median_pct, snapshot.rolling_24h_downside_p75_pct, snapshot.rolling_24h_downside_p90_pct, T1_HISTORICAL_MOVE_WEIGHT, "Target 1/24h"),
        (t2_move_pct, snapshot.rolling_72h_downside_median_pct, snapshot.rolling_72h_downside_p75_pct, snapshot.rolling_72h_downside_p90_pct, HISTORICAL_MOVE_WEIGHT - T1_HISTORICAL_MOVE_WEIGHT, "Target 2/72h"),
    )
    for move_pct, median, p75, p90, weight, label in historical_specs:
        points, state = _historical_points(move_pct, median, p75, p90, weight)
        score += points
        if state == "normal":
            strengths.append(f"{label} fits the median historical downside")
        elif state == "within_p75":
            strengths.append(f"{label} fits within historical downside p75")
        elif state == "within_p90":
            warnings.append(f"{label} requires an uncommon p75-p90 downside move")
        elif state == "above_p90":
            warnings.append(f"{label} is above historical downside p90")
        elif state == "materially_above_p90":
            rejection_reasons.append(
                f"{label} move {move_pct:.2f}% exceeds "
                f"{MATERIAL_P90_EXCESS_RATIO:.2f}x historical downside p90 {p90:.2f}%"
            )
        else:
            rejection_reasons.append(f"{label} historical downside data is unavailable")

    momentum = (snapshot.momentum_6h_pct, snapshot.momentum_24h_pct, snapshot.momentum_72h_pct)
    if not all(math.isfinite(float(value)) for value in momentum):
        momentum_context = "unavailable due to non-finite momentum evidence"
        rejection_reasons.append("Momentum evidence contains non-finite values")
    elif _negative_momentum(snapshot):
        score += MOMENTUM_WEIGHT
        momentum_context = "negative across 6h, 24h, and 72h"
        strengths.append("Short- and medium-term bearish momentum agree")
    elif momentum[0] < 0 and momentum[1] < 0:
        score += 14
        momentum_context = "negative short term, mixed over 72h"
        warnings.append("72h momentum does not confirm bearish continuation")
    elif momentum[1] < 0 and momentum[2] < 0:
        score += 10
        momentum_context = "6h momentum is recovering against negative 24h/72h structure"
        warnings.append("Short-term bearish momentum is weakening")
    elif any(value < 0 for value in momentum):
        score += 5
        momentum_context = "conflicting across 6h, 24h, and 72h"
        warnings.append("Momentum timeframes conflict")
    else:
        momentum_context = "positive across 6h, 24h, and 72h"
        warnings.append("Momentum does not support bearish continuation")

    if math.isfinite(float(snapshot.volume_ratio)):
        if snapshot.volume_ratio >= 1.5:
            score += VOLUME_WEIGHT
            strengths.append("Strong volume confirms bearish continuation")
        elif snapshot.volume_ratio >= MIN_BREAKOUT_VOLUME_RATIO:
            score += 4
            strengths.append("Above-average volume supports bearish continuation")
        elif snapshot.volume_ratio >= 0.8:
            score += 2
            warnings.append("Volume is only near average")
        else:
            warnings.append("Below-average volume does not confirm continuation")
    else:
        rejection_reasons.append("Volume evidence contains a non-finite value")

    if _healthy_bearish_structure(snapshot):
        score += VOLUME_CONTINUATION_WEIGHT - VOLUME_WEIGHT
        strengths.append("Trend structure supports bearish continuation")
    elif all(math.isfinite(float(value)) for value in (snapshot.last_price, snapshot.ema20)) and snapshot.last_price <= snapshot.ema20:
        score += 2
        warnings.append("Bearish continuation structure is only partially aligned")
    else:
        warnings.append("Trend structure does not support bearish continuation")

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
            f"T1 {t1_move_pct:.2f}% vs 24h downside p50/p75/p90 "
            f"{snapshot.rolling_24h_downside_median_pct:.2f}/"
            f"{snapshot.rolling_24h_downside_p75_pct:.2f}/"
            f"{snapshot.rolling_24h_downside_p90_pct:.2f}%; "
            f"T2 {t2_move_pct:.2f}% vs 72h downside p50/p75/p90 "
            f"{snapshot.rolling_72h_downside_median_pct:.2f}/"
            f"{snapshot.rolling_72h_downside_p75_pct:.2f}/"
            f"{snapshot.rolling_72h_downside_p90_pct:.2f}%"
        )
    else:
        volatility_context = "rolling volatility history unavailable"
        rejection_reasons.append("Rolling volatility history is unavailable")

    score = max(0, min(100, score))
    if score < MIN_QUALIFYING_SCORE:
        rejection_reasons.append(f"Attainability score {score} is below minimum {MIN_QUALIFYING_SCORE}")

    return ShortTargetAttainabilityResult(
        symbol=plan.symbol,
        qualified=not rejection_reasons,
        target_1_move_pct=round(t1_move_pct, 2),
        target_2_move_pct=round(t2_move_pct, 2),
        target_1_atr_multiple=round(t1_atr, 2),
        target_2_atr_multiple=round(t2_atr, 2),
        clearance_to_24h_support_pct=round(clearance_24, 2),
        clearance_to_72h_support_pct=round(clearance_72, 2),
        momentum_context=momentum_context,
        volatility_context=volatility_context,
        attainability_score=score,
        strengths=strengths,
        warnings=warnings,
        rejection_reasons=rejection_reasons,
    )
