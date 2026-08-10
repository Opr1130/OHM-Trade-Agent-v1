from dataclasses import dataclass

from app.scanner.models import MarketSnapshot


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


def build_entry_exit_plan(
    snapshot: MarketSnapshot,
    risk_level: str,
    direction: str | None = None,
) -> EntryExitPlan:
    price = snapshot.last_price
    atr_value = snapshot.atr
    direction = (direction or snapshot.trade_direction or "LONG").upper()

    if risk_level not in {"low", "medium"}:
        raise ValueError("Entry/exit plans are only supported for low or medium risk")
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

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
        entry_low = min(snapshot.ema20, price - (atr_value * pullback_atr))
        entry_high = price
        chase_limit = price + (atr_value * chase_atr)
        entry_reference = (entry_low + entry_high) / 2
        stop_price = entry_reference - stop_distance
        risk_per_unit = entry_reference - stop_price
        target_1 = entry_reference + risk_per_unit * target_1_multiple
        target_2 = entry_reference + risk_per_unit * target_2_multiple
        too_extended = price > snapshot.ema20 + (atr_value * 1.25)
        if too_extended:
            entry_style = "wait_for_pullback"
            valid_now = False
            reason = (
                "Price is extended above EMA20 relative to ATR. "
                "Wait for a pullback instead of chasing."
            )
        elif price >= snapshot.ema20:
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
        entry_high = max(snapshot.ema20, price + (atr_value * pullback_atr))
        chase_limit = price - (atr_value * chase_atr)
        entry_reference = (entry_low + entry_high) / 2
        stop_price = entry_reference + stop_distance
        risk_per_unit = stop_price - entry_reference
        target_1 = entry_reference - risk_per_unit * target_1_multiple
        target_2 = entry_reference - risk_per_unit * target_2_multiple
        too_extended = price < snapshot.ema20 - (atr_value * 1.25)
        if too_extended:
            entry_style = "wait_for_rebound"
            valid_now = False
            reason = (
                "Price is extended below EMA20 relative to ATR. "
                "Wait for a rebound/retest instead of chasing the breakdown."
            )
        elif price <= snapshot.ema20:
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

    if target_2 <= 0:
        valid_now = False
        entry_style = "wait"
        reason = "ATR-derived short target is non-positive; setup is invalid."

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
