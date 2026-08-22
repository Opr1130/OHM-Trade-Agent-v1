from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EntryTimingDecision:
    action: str
    score: int
    reason: str
    max_chase_pct: float

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate_entry_timing(*, direction: str, current_price: float, entry_low: float, entry_high: float, chase_limit: float, spread_bps: float | None = None, recent_trade_age_seconds: float | None = None) -> EntryTimingDecision:
    direction = direction.upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

    prices = (current_price, entry_low, entry_high, chase_limit)
    if not all(math.isfinite(float(value)) for value in prices):
        raise ValueError("prices must be finite")
    if min(prices) <= 0:
        raise ValueError("prices must be positive")
    if spread_bps is not None and not math.isfinite(float(spread_bps)):
        raise ValueError("spread_bps must be finite")
    if recent_trade_age_seconds is not None and not math.isfinite(float(recent_trade_age_seconds)):
        raise ValueError("recent_trade_age_seconds must be finite")

    lo, hi = sorted((entry_low, entry_high))
    score = 100
    reasons: list[str] = []
    if spread_bps is not None and spread_bps > 35:
        score -= 30
        reasons.append("spread elevated")
    if recent_trade_age_seconds is not None and recent_trade_age_seconds > 300:
        score -= 25
        reasons.append("trade evidence stale")
    if lo <= current_price <= hi:
        action = "ENTER_NOW" if score >= 70 else "WAIT"
        reasons.append("price inside planned entry zone")
    elif direction == "LONG" and current_price > hi:
        chase_pct = (current_price / hi - 1) * 100
        if current_price > chase_limit:
            return EntryTimingDecision("DO_NOT_CHASE", 0, "price beyond chase limit", round(chase_pct, 4))
        score -= min(40, int(chase_pct * 20))
        action = "ENTER_NOW" if score >= 75 else "WAIT"
        reasons.append("price above entry zone but within chase limit")
    elif direction == "SHORT" and current_price < lo:
        chase_pct = (lo / current_price - 1) * 100
        if current_price < chase_limit:
            return EntryTimingDecision("DO_NOT_CHASE", 0, "price beyond chase limit", round(chase_pct, 4))
        score -= min(40, int(chase_pct * 20))
        action = "ENTER_NOW" if score >= 75 else "WAIT"
        reasons.append("price below entry zone but within chase limit")
    else:
        action = "WAIT"
        reasons.append("price has not reached planned entry zone")
    return EntryTimingDecision(action, max(0, score), "; ".join(reasons), 0.0)
