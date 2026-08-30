from __future__ import annotations

from dataclasses import replace
import math

from app.services.active_trade_registry import ActiveTrade
from app.services.trade_monitor import TradeMonitorResult


MFE_MINIMUM_R = 1.0
MFE_GIVEBACK_TRIGGER_R = 0.50
ADVERSE_RISK_TRIGGER_R = 0.75


def _risk_unit_pct(trade: ActiveTrade) -> float:
    entry = float(trade.entry_price)
    stop = float(trade.stop_price)
    if entry <= 0:
        return 0.0
    value = abs(entry - stop) / entry * 100.0
    return value if math.isfinite(value) else 0.0


def _adverse_progress_r(trade: ActiveTrade, current_price: float) -> float:
    entry = float(trade.entry_price)
    stop = float(trade.stop_price)
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    if str(trade.direction or "LONG").upper() == "SHORT":
        return (current_price - entry) / risk
    return (entry - current_price) / risk


def refine_protection_action(
    trade: ActiveTrade,
    result: TradeMonitorResult,
    observation: dict | None,
) -> TradeMonitorResult:
    """Promote a silent HOLD only when protection evidence becomes actionable.

    Rules are expressed in R (entry-to-stop risk units) rather than fixed
    percentage moves so the same logic scales across volatility regimes.
    Existing EXIT_NOW/TAKE_PROFIT/WARNING decisions remain authoritative.
    """
    if result.action != "HOLD":
        return result

    reasons = list(result.reasons or [])
    current_price = float(result.current_price)
    risk_progress = _adverse_progress_r(trade, current_price)
    if math.isfinite(risk_progress) and risk_progress >= ADVERSE_RISK_TRIGGER_R:
        return replace(
            result,
            action="WARNING",
            reasons=[
                f"Price has consumed {risk_progress:.2f}R of the original stop-risk budget",
                *reasons,
            ],
        )

    if not observation:
        return result
    try:
        mfe_pct = float(observation.get("mfe_pct") or 0.0)
        current_pct = float(
            result.net_pnl_pct if result.net_pnl_pct is not None else result.unrealized_pct
        )
    except (TypeError, ValueError):
        return result

    risk_pct = _risk_unit_pct(trade)
    if risk_pct <= 0 or not all(
        math.isfinite(value) for value in (mfe_pct, current_pct, risk_pct)
    ):
        return result

    mfe_r = mfe_pct / risk_pct
    giveback_r = max(0.0, mfe_pct - max(0.0, current_pct)) / risk_pct
    if mfe_r >= MFE_MINIMUM_R and giveback_r >= MFE_GIVEBACK_TRIGGER_R:
        return replace(
            result,
            action="WARNING",
            reasons=[
                f"Profit protection: trade gave back {giveback_r:.2f}R after reaching {mfe_r:.2f}R MFE",
                *reasons,
            ],
        )
    return result