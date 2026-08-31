from __future__ import annotations

from typing import Any

from app.services.entry_exit_advisor import EntryExitPlan
from app.services.kraken_reconciliation import (
    reconciliation_enabled,
    reconciliation_mode,
)
from app.services.order_intent_registry import (
    OrderIntent,
    get_order_intent,
    register_order_intent,
)


class ReconciliationTrackingDisabled(RuntimeError):
    """Qualified trade tracking is intentionally unavailable by configuration."""


def reconciliation_limit_price(
    plan: EntryExitPlan,
    *,
    action: str,
    direction: str,
) -> float:
    if action == "PLACE_LIMIT":
        return plan.entry_high if direction == "SHORT" else plan.entry_low
    return plan.entry_low if direction == "SHORT" else plan.entry_high


def register_reconciliation_intent(
    *,
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    action: str,
    direction: str,
    leverage: float,
    trade_id: str,
    reconciliation_is_enabled: bool | None = None,
    reconciliation_mode_value: str | None = None,
) -> None:
    """Create the read-only reconciliation identity for a qualified action."""
    if candidate.get("economic_qualified") is not True:
        return
    enabled = (
        reconciliation_enabled()
        if reconciliation_is_enabled is None
        else bool(reconciliation_is_enabled)
    )
    mode = (
        reconciliation_mode()
        if reconciliation_mode_value is None
        else str(reconciliation_mode_value).lower()
    )
    if not enabled or mode != "apply":
        raise ReconciliationTrackingDisabled(
            "Kraken reconciliation must be enabled in apply mode"
        )

    capital = candidate.get("recommended_capital")
    if not isinstance(capital, (int, float)) or float(capital) <= 0:
        raise ValueError("recommended capital is required for Kraken fill tracking")

    limit_price = reconciliation_limit_price(
        plan,
        action=action,
        direction=direction,
    )
    existing = get_order_intent(trade_id)
    if existing is not None:
        same_identity = (
            existing.status == "LIMIT_PLACED"
            and existing.symbol == plan.symbol.upper()
            and existing.direction == direction
            and existing.source == "ohm_actionable_signal"
            and abs(existing.limit_price - limit_price) <= 1e-9
            and abs(existing.capital - float(capital)) <= 1e-9
        )
        if same_identity:
            return
        raise ValueError(
            f"reconciliation intent {trade_id} does not match this alert"
        )

    register_order_intent(
        OrderIntent(
            symbol=plan.symbol,
            direction=direction,
            limit_price=limit_price,
            capital=float(capital),
            stop_price=plan.stop_price,
            target_1=plan.target_1,
            target_2=plan.target_2,
            margin_leverage=leverage,
            risk_level=plan.risk_level,
            entry_action=action,
            source="ohm_actionable_signal",
            trade_id=trade_id,
        )
    )