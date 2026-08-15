import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.active_trade_registry import get_active_trades
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.kraken_reconciliation import (
    reconciliation_enabled,
    reconciliation_mode,
)
from app.services.notification_policy import record_emitted, should_emit
from app.services.order_intent_registry import (
    OrderIntent,
    get_order_intent,
    register_order_intent,
)
from app.services.pending_setup_registry import (
    PendingSetup,
    add_pending_setup,
    get_pending_setup_by_trade_id,
    get_pending_setups,
    terminalize_pending_setup,
)
from app.services.telegram_notifier import send_telegram_message
from app.services.trade_decision_intelligence import evaluate_trade_decision
from app.services.trade_outcome_registry import record_recommendation


STATE_FILE = Path("/app/data/alert_state.json")


def _pretty_entry_style(value: str) -> str:
    return value.replace("_", " ").title()


def _load_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _action_type(plan: EntryExitPlan) -> str | None:
    if plan.valid_now:
        return "ENTER_NOW"
    if plan.entry_style in {"wait_for_pullback", "wait_for_rebound"}:
        return "PLACE_LIMIT"
    return None


def _round_price(value: float) -> str:
    return f"{float(value):.6g}"


def _alert_state_key(candidate: dict[str, Any], plan: EntryExitPlan) -> str:
    action = _action_type(plan) or "SILENT"
    direction = str(candidate.get("direction") or plan.direction or "LONG").upper()
    return ":".join(
        [
            direction,
            action,
            plan.risk_level,
            _round_price(plan.entry_low),
            _round_price(plan.entry_high),
            _round_price(plan.stop_price),
            _round_price(plan.target_1),
            _round_price(plan.target_2),
        ]
    )


def format_trade_plan(candidate: dict[str, Any], plan: EntryExitPlan, summary: str) -> str:
    action = _action_type(plan)
    direction = str(candidate.get("direction") or plan.direction or "LONG").upper()
    if action == "ENTER_NOW":
        headline = f"🔥 OHM CHIEF — {direction} — ENTER NOW"
        action_text = "Action: ENTRY CONDITIONS VALID\nUse the approved entry zone and respect the chase boundary."
    elif action == "PLACE_LIMIT":
        headline = f"🎯 OHM CHIEF — {direction} — PLACE LIMIT"
        action_text = (
            "Action: SET LIMIT ENTRY\n"
            + (
                "Setup is qualified, but price is extended downward. Wait for the approved rebound/retest."
                if direction == "SHORT"
                else "Setup is qualified, but price is extended. Wait for the approved pullback."
            )
        )
    else:
        raise ValueError("Cannot format non-actionable OHM trade plan")

    quote_note = ""
    if candidate.get("primary_quote_currency") == "USDT" and not candidate.get("secondary_pair"):
        quote_note = "Quote: USDT (USDT availability required; no automatic USD conversion)\n"

    ranking_note = ""
    if candidate.get("profit_rank") is not None:
        ranking_note += f"OHM Opportunity Rank: #{candidate['profit_rank']}\n"
    if candidate.get("profit_rank_score") is not None:
        ranking_note += f"Profit Rank Score: {float(candidate['profit_rank_score']):.2f}/100\n"

    intelligence_note = ""
    if candidate.get("recommended_capital") is not None:
        intelligence_note += f"Recommended Capital: ${float(candidate['recommended_capital']):,.2f}\n"
    if candidate.get("recommended_risk_dollars") is not None:
        intelligence_note += f"Estimated Stop Risk: ${float(candidate['recommended_risk_dollars']):,.2f}\n"
    if candidate.get("projected_net_edge_pct") is not None:
        intelligence_note += f"Projected Net Edge: {float(candidate['projected_net_edge_pct']):.2f}%\n"
    if candidate.get("calibration_status") is not None:
        intelligence_note += (
            f"Calibration: {candidate['calibration_status']} "
            f"({float(candidate.get('calibration_multiplier') or 1.0):.2f}x sizing only)\n"
        )

    margin_note = ""
    if direction == "SHORT":
        margin_note = (
            f"Margin: Kraken US retail eligible | Validation leverage: {float(candidate.get('margin_leverage') or 2.0):.1f}x\n"
            "Execution: MANUAL ONLY — verify live Kraken margin/opening/rollover fees before entry\n"
        )

    fill_tracking_note = ""
    if candidate.get("economic_qualified") is True:
        fill_tracking_note = (
            "Fill Tracking: Automatic via read-only Kraken reconciliation; "
            "no Telegram confirmation required\n"
        )

    chase_label = "Do Not Chase Below" if direction == "SHORT" else "Do Not Chase Above"
    return (
        f"{headline}\n\n{action_text}\n\n"
        f"Asset: {candidate.get('underlying_asset', plan.symbol)}\n"
        f"Market: {candidate.get('primary_pair', plan.symbol)}\n"
        f"Direction: {direction}\n"
        f"{quote_note}{margin_note}{ranking_note}{intelligence_note}{fill_tracking_note}"
        f"Risk: {plan.risk_level.upper()}\n"
        f"AI Confidence: {candidate.get('confidence', 0)}%\n"
        f"Technical Decision: {str(candidate.get('decision', '')).upper()}\n\n"
        f"📍 ENTRY PLAN\nEntry Zone: {plan.entry_low} - {plan.entry_high}\n"
        f"{chase_label}: {plan.chase_limit}\nStop: {plan.stop_price}\n\n"
        f"🎯 TARGETS\nTarget 1: {plan.target_1} ({plan.reward_to_risk_1}:1 R/R)\n"
        f"Target 2: {plan.target_2} ({plan.reward_to_risk_2}:1 R/R)\n\n"
        f"Entry Style: {_pretty_entry_style(plan.entry_style)}\n\n"
        f"Reason:\n{plan.reason}\n\nChief Analyst:\n{candidate.get('reason', '')}\n\n"
        f"Market Summary:\n{summary}"
    )


def should_send_trade_plan(candidate: dict[str, Any], plan: EntryExitPlan) -> bool:
    action = _action_type(plan)
    if action is None:
        return False
    current_key = _alert_state_key(candidate, plan)
    state = _load_state()
    if state.get(plan.symbol) == current_key:
        return False
    direction = str(candidate.get("direction") or plan.direction or "LONG").upper()
    return should_emit(
        identity=f"{direction}:{plan.symbol}",
        event_type="OPPORTUNITY",
        fingerprint=current_key,
    )


def _existing_setup_trade_id(symbol: str) -> str | None:
    for pending in get_pending_setups():
        if pending.symbol == symbol:
            return pending.trade_id or None
    return None


def _reconciliation_limit_price(
    plan: EntryExitPlan,
    *,
    action: str,
    direction: str,
) -> float:
    if action == "PLACE_LIMIT":
        return plan.entry_high if direction == "SHORT" else plan.entry_low
    return plan.entry_low if direction == "SHORT" else plan.entry_high


def _register_reconciliation_intent(
    *,
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    action: str,
    direction: str,
    leverage: float,
    trade_id: str,
) -> None:
    """Create the exchange-observation identity for a production alert."""
    if candidate.get("economic_qualified") is not True:
        return
    if not reconciliation_enabled() or reconciliation_mode() != "apply":
        raise RuntimeError("Kraken reconciliation apply mode is required")

    capital = candidate.get("recommended_capital")
    if not isinstance(capital, (int, float)) or float(capital) <= 0:
        raise ValueError("recommended capital is required for Kraken fill tracking")

    limit_price = _reconciliation_limit_price(
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
        raise ValueError(f"reconciliation intent {trade_id} does not match this alert")

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


def _apply_intelligence(candidate: dict[str, Any], plan: EntryExitPlan) -> bool:
    # Only deterministic survivors from scan_opportunities carry this flag.
    # Legacy/direct unit callers retain the old behavior and do not require a
    # fully populated production Settings object.
    if candidate.get("economic_qualified") is not True:
        return True

    settings = get_settings()
    intelligence = evaluate_trade_decision(
        candidate=candidate,
        plan=plan,
        account_capital=settings.account_equity,
        active_trades=get_active_trades(),
    )
    candidate["recommended_capital"] = intelligence.allocation.recommended_capital
    candidate["recommended_risk_dollars"] = intelligence.allocation.risk_dollars
    candidate["projected_net_edge_pct"] = intelligence.projected_net_edge_pct
    candidate["calibration_status"] = intelligence.calibration_status
    candidate["calibration_multiplier"] = intelligence.calibration_multiplier
    candidate["portfolio_risk_allowed"] = intelligence.portfolio_risk.allowed
    candidate["portfolio_risk_reason"] = intelligence.portfolio_risk.reason

    if intelligence.allowed:
        return True
    existing_trade_id = _existing_setup_trade_id(plan.symbol)
    if existing_trade_id:
        terminalize_pending_setup(existing_trade_id, "portfolio_risk_rejected")
    return False


def send_trade_plan(
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    summary: str,
    bot_token: str,
    chat_id: str,
) -> bool:
    action = _action_type(plan)
    if action is None or not should_send_trade_plan(candidate, plan):
        return False
    if not _apply_intelligence(candidate, plan):
        return False

    direction = str(candidate.get("direction") or plan.direction or "LONG").upper()
    leverage = float(candidate.get("margin_leverage") or (2.0 if direction == "SHORT" else 1.0))
    message = format_trade_plan(candidate=candidate, plan=plan, summary=summary)
    trade_id = str(candidate.get("trade_id") or "") or _existing_setup_trade_id(plan.symbol)
    setup = get_pending_setup_by_trade_id(trade_id) if trade_id else None
    if setup is None:
        setup = add_pending_setup(
            PendingSetup(
                symbol=plan.symbol,
                entry_low=plan.entry_low,
                entry_high=plan.entry_high,
                chase_limit=plan.chase_limit,
                stop_price=plan.stop_price,
                target_1=plan.target_1,
                target_2=plan.target_2,
                risk_level=plan.risk_level,
                confidence=int(candidate.get("confidence", 0)),
                confirmation_price=(plan.entry_low if direction == "SHORT" else plan.entry_high),
                direction=direction,
                margin_leverage=leverage,
                trade_id=trade_id,
            )
        )
        trade_id = setup.trade_id
    if trade_id:
        candidate["trade_id"] = trade_id

    if candidate.get("economic_qualified") is True:
        if not trade_id:
            return False
        try:
            _register_reconciliation_intent(
                candidate=candidate,
                plan=plan,
                action=action,
                direction=direction,
                leverage=leverage,
                trade_id=trade_id,
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            terminalize_pending_setup(trade_id, "tracking_failed")
            return False

    record_recommendation(trade_id=trade_id, candidate=candidate, plan=plan, action=action)
    sent = send_telegram_message(bot_token, chat_id, message)
    if sent:
        key = _alert_state_key(candidate, plan)
        state = _load_state()
        state[plan.symbol] = key
        _save_state(state)
        record_emitted(
            identity=f"{direction}:{plan.symbol}",
            event_type="OPPORTUNITY",
            fingerprint=key,
        )
    elif trade_id:
        terminalize_pending_setup(trade_id, "send_failed")
    return sent
