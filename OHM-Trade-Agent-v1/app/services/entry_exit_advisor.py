import math
from dataclasses import dataclass

from app.scanner.models import MarketSnapshot


# Target 1 is a partial-profit checkpoint, not the final economic target
# (Target 2 remains a flat ATR multiple and is separately gated by
# evaluate_economic_quality). Historically only ~25% of moves in a given
# direction exceed a symbol's own rolling p75 excursion for the matching
# lookback window (see target_attainability.py). Mechanically fixing Target 1
# at a flat reward multiple regardless of what a symbol typically does means a
# meaningful share of directionally-correct trades can never reach it before
# mean reverting. TARGET_1_MIN_RR_MULTIPLE keeps a floor so a quiet symbol
# never gets a target so close it is immediately eaten by noise/fees.
TARGET_1_MIN_RR_MULTIPLE = 1.2


def _realistic_target_1_multiple(
    mechanical_multiple: float,
    percentile_move_pct: float,
    entry_reference: float,
    risk_per_unit: float,
) -> float:
    """Cap (never raise) Target 1 to a historically plausible excursion."""
    values = (mechanical_multiple, percentile_move_pct, entry_reference, risk_per_unit)
    if not all(math.isfinite(float(value)) for value in values):
        return mechanical_multiple
    if risk_per_unit <= 0 or entry_reference <= 0 or percentile_move_pct <= 0:
        return mechanical_multiple
    percentile_price_move = entry_reference * (percentile_move_pct / 100)
    percentile_multiple = percentile_price_move / risk_per_unit
    return max(
        TARGET_1_MIN_RR_MULTIPLE,
        min(mechanical_multiple, percentile_multiple),
    )


@dataclass
class EntryExitPlan:
    symbol: str
    valid_now: bool
    entry_style: str

    entry_low: float
    entry_high: float
    chase_limit: float

    stop_price: float
    target_1: float
    target_2: float

    reward_to_risk_1: float
    reward_to_risk_2: float

    risk_level: str
    reason: str
    direction: str = "LONG"


def _invalid_plan(
    snapshot: MarketSnapshot,
    risk_level: str,
    direction: str,
    reason: str,
) -> EntryExitPlan:
    price = float(snapshot.last_price) if math.isfinite(float(snapshot.last_price)) else 0.0
    price = max(0.0, price)
    return EntryExitPlan(
        symbol=snapshot.symbol,
        valid_now=False,
        entry_style="wait",
        entry_low=price,
        entry_high=price,
        chase_limit=price,
        # Zero-valued risk geometry is an explicit invalid sentinel. Downstream
        # economic/target gates reject it; it can never be an ENTER-NOW plan.
        stop_price=0.0,
        target_1=0.0,
        target_2=0.0,
        reward_to_risk_1=0.0,
        reward_to_risk_2=0.0,
        risk_level=risk_level,
        reason=reason,
        direction=direction,
    )


def _geometry_is_valid(
    *,
    direction: str,
    entry_low: float,
    entry_high: float,
    chase_limit: float,
    entry_reference: float,
    stop_price: float,
    target_1: float,
    target_2: float,
    risk_per_unit: float,
) -> bool:
    values = (
        entry_low,
        entry_high,
        chase_limit,
        entry_reference,
        stop_price,
        target_1,
        target_2,
        risk_per_unit,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    if any(float(value) <= 0 for value in values):
        return False
    if entry_low > entry_high or risk_per_unit <= 0:
        return False
    if direction == "LONG":
        return (
            stop_price < entry_reference
            and target_1 > entry_reference
            and target_2 > target_1
            and chase_limit >= entry_high
        )
    return (
        stop_price > entry_reference
        and 0 < target_2 < target_1 < entry_reference
        and 0 < chase_limit <= entry_low
    )


def build_entry_exit_plan(
    snapshot: MarketSnapshot,
    risk_level: str,
    direction: str | None = None,
) -> EntryExitPlan:
    price = float(snapshot.last_price)
    atr_value = float(snapshot.atr)
    ema20_value = float(snapshot.ema20)
    direction = (direction or snapshot.trade_direction or "LONG").upper()

    if risk_level not in {"low", "medium"}:
        raise ValueError("Entry/exit plans are only supported for low or medium risk")
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

    if (
        not math.isfinite(price)
        or not math.isfinite(atr_value)
        or not math.isfinite(ema20_value)
        or price <= 0
        or ema20_value <= 0
        or atr_value <= 0
    ):
        return _invalid_plan(
            snapshot,
            risk_level,
            direction,
            "Invalid or non-positive market geometry; no entry is authorized.",
        )

    if risk_level == "low":
        stop_distance = atr_value * 1.5
        target_1_multiple = 2.0
        target_2_multiple = 3.0
        chase_atr = 0.5
        pullback_atr = 0.0
    else:
        stop_distance = atr_value * 1.75
        target_1_multiple = 2.0
        target_2_multiple = 3.0
        chase_atr = 0.75
        pullback_atr = 0.25

    if direction == "LONG":
        entry_low = min(ema20_value, price - (atr_value * pullback_atr))
        entry_high = price
        chase_limit = price + (atr_value * chase_atr)
        entry_reference = (entry_low + entry_high) / 2
        stop_price = entry_reference - stop_distance
        risk_per_unit = entry_reference - stop_price
        target_1_multiple_effective = _realistic_target_1_multiple(
            target_1_multiple,
            snapshot.rolling_24h_upside_p75_pct,
            entry_reference,
            risk_per_unit,
        )
        target_1 = entry_reference + risk_per_unit * target_1_multiple_effective
        target_2 = entry_reference + risk_per_unit * target_2_multiple
        too_extended = price > ema20_value + (atr_value * 1.25)
        if too_extended:
            entry_style = "wait_for_pullback"
            valid_now = False
            reason = (
                "Price is extended above EMA20 relative to ATR. "
                "Wait for a pullback instead of chasing."
            )
        elif price >= ema20_value:
            entry_style = "pullback_or_retest"
            valid_now = True
            reason = (
                "Trend structure supports a controlled pullback or retest entry "
                "with ATR-based risk."
            )
        else:
            entry_style = "wait"
            valid_now = False
            reason = "Price is below EMA20; wait for trend recovery."
    else:
        # Shorts seek a controlled rebound/retest rather than chasing a dump.
        entry_low = price
        entry_high = max(ema20_value, price + (atr_value * pullback_atr))
        chase_limit = price - (atr_value * chase_atr)
        entry_reference = (entry_low + entry_high) / 2
        stop_price = entry_reference + stop_distance
        risk_per_unit = stop_price - entry_reference
        target_1_multiple_effective = _realistic_target_1_multiple(
            target_1_multiple,
            snapshot.rolling_24h_downside_p75_pct,
            entry_reference,
            risk_per_unit,
        )
        target_1 = entry_reference - risk_per_unit * target_1_multiple_effective
        target_2 = entry_reference - risk_per_unit * target_2_multiple
        too_extended = price < ema20_value - (atr_value * 1.25)
        if too_extended:
            entry_style = "wait_for_rebound"
            valid_now = False
            reason = (
                "Price is extended below EMA20 relative to ATR. "
                "Wait for a rebound/retest instead of chasing the breakdown."
            )
        elif price <= ema20_value:
            entry_style = "rebound_or_retest"
            valid_now = True
            reason = (
                "Bearish structure supports a controlled rebound/retest short "
                "with ATR-based risk."
            )
        else:
            entry_style = "wait"
            valid_now = False
            reason = "Price is above EMA20; wait for bearish structure to recover."

    geometry_valid = _geometry_is_valid(
        direction=direction,
        entry_low=entry_low,
        entry_high=entry_high,
        chase_limit=chase_limit,
        entry_reference=entry_reference,
        stop_price=stop_price,
        target_1=target_1,
        target_2=target_2,
        risk_per_unit=risk_per_unit,
    )
    if not geometry_valid:
        valid_now = False
        entry_style = "wait"
        reason = "ATR-derived plan geometry is invalid; no entry is authorized."

    rr1 = abs(target_1 - entry_reference) / risk_per_unit if risk_per_unit > 0 else 0
    rr2 = abs(target_2 - entry_reference) / risk_per_unit if risk_per_unit > 0 else 0

    return EntryExitPlan(
        symbol=snapshot.symbol,
        valid_now=valid_now,
        entry_style=entry_style,
        entry_low=round(min(entry_low, entry_high), 8),
        entry_high=round(max(entry_low, entry_high), 8),
        chase_limit=round(chase_limit, 8),
        stop_price=round(stop_price, 8),
        target_1=round(target_1, 8),
        target_2=round(target_2, 8),
        reward_to_risk_1=round(rr1, 2),
        reward_to_risk_2=round(rr2, 2),
        risk_level=risk_level,
        reason=reason,
        direction=direction,
    )
