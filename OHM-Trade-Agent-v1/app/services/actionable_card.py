"""OPIP-ALERT-ACTIONABLE-ONLY-001 card contract.

The single renderer for the mandatory actionable-plan block, and the checker that proves a
rendered card is complete. Both actionable producers (the native qualified path and the
Tradevisor plane) append this block, so the operator cannot receive an "actionable" BUY/SELL
without entry zone, stop-loss, targets, invalidation, expiry, **net** reward-to-risk, the setup
identity and the manual-execution statement.

Presentation only: no eligibility, no delivery, no authority. The net reward-to-risk shown here is
the value the admission gate computed and admitted with -- it is never recomputed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.actionable_admission import (
    ENTRY_TRIGGER_LIMIT,
    ENTRY_TRIGGER_MARKET,
    ActionableAdmission,
)

MANUAL_ONLY_LINE = "Execution: MANUAL ONLY — O'Pip placed no order."

#: Labels every emitted actionable card must contain. Kept as prefixes so the checker does not
#: depend on formatting of the values.
REQUIRED_CARD_ELEMENTS: tuple[str, ...] = (
    "Setup ID",
    "Entry zone",
    "Stop-loss",
    "Target 1",
    "Invalidation",
    "Expiry",
    "Net R/R",
    "MANUAL ONLY",
)


def _price(value: Any) -> str:
    try:
        return f"{float(value):.8f}".rstrip("0").rstrip(".") or "0"
    except (TypeError, ValueError):
        return "N/A"


def _utc_label(value: datetime | None) -> str:
    if value is None:
        return "N/A"
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _invalidation_text(plan: Any, direction: str) -> str:
    stop = _price(getattr(plan, "stop_price", None))
    if direction == "SHORT":
        return f"close above {stop} (stop)"
    return f"close below {stop} (stop)"


def _trigger_text(admission: ActionableAdmission, plan: Any) -> str:
    zone_low = _price(getattr(plan, "entry_low", None))
    zone_high = _price(getattr(plan, "entry_high", None))
    quote = _price(admission.current_price)
    if admission.entry_trigger_kind == ENTRY_TRIGGER_MARKET:
        return f"ENTER NOW — quote {quote} inside the approved zone"
    if admission.entry_trigger_kind == ENTRY_TRIGGER_LIMIT:
        return f"SET LIMIT — executable now around {zone_low}-{zone_high} (quote {quote})"
    return f"NOT ACTIONABLE — quote {quote}"


def render_actionable_block(
    *,
    admission: ActionableAdmission,
    plan: Any,
    setup_id: str,
    direction: str,
    venue: str | None,
    instrument: str | None,
    observed_at: datetime | None = None,
) -> str:
    """Render the mandatory block for one admitted actionable card."""
    if not admission.admitted:
        raise ValueError("only an admitted plan may be rendered as actionable")
    trade_direction = str(direction or "LONG").upper()
    expiry_note = (
        "explicit"
        if admission.expiry_source == "explicit"
        else "plan validity window"
    )
    cost = admission.effective_cost_pct
    net_rr = admission.net_reward_to_risk
    net_rr_text = "N/A" if net_rr is None else f"{float(net_rr):.2f}:1"
    cost_text = "N/A" if cost is None else f"{float(cost):.2f}%"
    target_2 = getattr(plan, "target_2", None)
    lines = [
        f"— ACTIONABLE PLAN ({admission.policy_version}) —",
        f"Setup ID: {setup_id or 'N/A'}",
        f"Evidence: {_utc_label(observed_at)} · {venue or 'N/A'} · {instrument or 'N/A'}",
        f"Trigger: {_trigger_text(admission, plan)}",
        f"Entry zone: {_price(getattr(plan, 'entry_low', None))} - "
        f"{_price(getattr(plan, 'entry_high', None))}",
        f"Stop-loss: {_price(getattr(plan, 'stop_price', None))}",
        f"Target 1: {_price(getattr(plan, 'target_1', None))}"
        + (f" · Target 2: {_price(target_2)}" if target_2 else ""),
        f"Invalidation: {_invalidation_text(plan, trade_direction)}",
        f"Expiry: {_utc_label(admission.expires_at)} ({expiry_note})",
        f"Net R/R after {cost_text} fees + measured impact: {net_rr_text}",
        MANUAL_ONLY_LINE,
    ]
    return "\n".join(lines)


def missing_card_elements(message: str) -> tuple[str, ...]:
    """Return the mandatory labels absent from a rendered actionable card.

    Empty means the card satisfies the notification contract. Producers use this as a
    fail-closed guard and the regression suite asserts every emitted card passes it.
    """
    text = str(message or "")
    return tuple(label for label in REQUIRED_CARD_ELEMENTS if label not in text)


__all__ = [
    "MANUAL_ONLY_LINE",
    "REQUIRED_CARD_ELEMENTS",
    "missing_card_elements",
    "render_actionable_block",
]
