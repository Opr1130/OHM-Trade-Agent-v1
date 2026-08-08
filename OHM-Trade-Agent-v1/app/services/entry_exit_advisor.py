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


def build_entry_exit_plan(
    snapshot: MarketSnapshot,
    risk_level: str,
) -> EntryExitPlan:
    price = snapshot.last_price
    atr_value = snapshot.atr

    if risk_level not in {"low", "medium"}:
        raise ValueError("Entry/exit plans are only supported for low or medium risk")

    if risk_level == "low":
        entry_low = min(snapshot.ema20, price)
        entry_high = price
        stop_distance = atr_value * 1.5
        chase_limit = price + (atr_value * 0.5)
        target_1_multiple = 2.0
        target_2_multiple = 3.0
    else:
        entry_low = min(snapshot.ema20, price - (atr_value * 0.25))
        entry_high = price
        stop_distance = atr_value * 1.75
        chase_limit = price + (atr_value * 0.75)
        target_1_multiple = 2.0
        target_2_multiple = 3.0

    entry_reference = (entry_low + entry_high) / 2
    stop_price = entry_reference - stop_distance

    risk_per_unit = entry_reference - stop_price

    target_1 = entry_reference + (risk_per_unit * target_1_multiple)
    target_2 = entry_reference + (risk_per_unit * target_2_multiple)

    rr1 = (target_1 - entry_reference) / risk_per_unit
    rr2 = (target_2 - entry_reference) / risk_per_unit

    too_extended = price > snapshot.ema20 + (atr_value * 1.25)
    valid_now = not too_extended

    if too_extended:
        entry_style = "wait_for_pullback"
        reason = (
            "Price is extended above EMA20 relative to ATR. "
            "Wait for a pullback instead of chasing."
        )
    elif snapshot.last_price >= snapshot.ema20:
        entry_style = "pullback_or_retest"
        reason = (
            "Trend structure supports a controlled pullback or retest entry "
            "with ATR-based risk."
        )
    else:
        entry_style = "wait"
        reason = "Price is below EMA20; wait for trend recovery."

    return EntryExitPlan(
        symbol=snapshot.symbol,
        valid_now=valid_now,
        entry_style=entry_style,
        entry_low=round(entry_low, 8),
        entry_high=round(entry_high, 8),
        chase_limit=round(chase_limit, 8),
        stop_price=round(stop_price, 8),
        target_1=round(target_1, 8),
        target_2=round(target_2, 8),
        reward_to_risk_1=round(rr1, 2),
        reward_to_risk_2=round(rr2, 2),
        risk_level=risk_level,
        reason=reason,
    )
