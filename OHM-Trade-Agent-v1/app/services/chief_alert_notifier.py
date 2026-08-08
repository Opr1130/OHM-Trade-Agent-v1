import json
from pathlib import Path
from typing import Any

from app.services.entry_exit_advisor import EntryExitPlan
from app.services.telegram_notifier import send_telegram_message


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


def _alert_state_key(
    candidate: dict[str, Any],
    plan: EntryExitPlan,
) -> str:
    status = "ENTER_NOW" if plan.valid_now else "WAIT_FOR_ENTRY"
    confidence = int(candidate.get("confidence", 0))
    return f"{status}:{plan.risk_level}:{confidence}"


def format_trade_plan(
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    summary: str,
) -> str:
    status = "ENTER NOW" if plan.valid_now else "WAIT FOR ENTRY"

    action_text = (
        "Action: ENTRY CONDITIONS VALID"
        if plan.valid_now
        else "Action: WAIT — DO NOT ENTER YET"
    )

    return (
        f"🚨 OHM AI — {status}\n\n"
        f"{action_text}\n\n"
        f"Symbol: {plan.symbol}\n"
        f"Risk: {plan.risk_level.upper()}\n"
        f"AI Confidence: {candidate.get('confidence', 0)}%\n"
        f"Technical Decision: {str(candidate.get('decision', '')).upper()}\n\n"
        f"📍 TRADE PLAN\n"
        f"Entry Zone: {plan.entry_low} - {plan.entry_high}\n"
        f"Do Not Chase Above: {plan.chase_limit}\n"
        f"Stop: {plan.stop_price}\n\n"
        f"🎯 TARGETS\n"
        f"Target 1: {plan.target_1} ({plan.reward_to_risk_1}:1 R/R)\n"
        f"Target 2: {plan.target_2} ({plan.reward_to_risk_2}:1 R/R)\n\n"
        f"Entry Style: {_pretty_entry_style(plan.entry_style)}\n\n"
        f"Reason:\n{plan.reason}\n\n"
        f"Chief Analyst:\n{candidate.get('reason', '')}\n\n"
        f"Market Summary:\n{summary}"
    )


def should_send_trade_plan(
    candidate: dict[str, Any],
    plan: EntryExitPlan,
) -> bool:
    state = _load_state()
    current_key = _alert_state_key(candidate, plan)
    previous_key = state.get(plan.symbol)

    return current_key != previous_key


def send_trade_plan(
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    summary: str,
    bot_token: str,
    chat_id: str,
) -> bool:
    if not should_send_trade_plan(candidate, plan):
        return False

    message = format_trade_plan(
        candidate=candidate,
        plan=plan,
        summary=summary,
    )

    sent = send_telegram_message(
        bot_token,
        chat_id,
        message,
    )

    if sent:
        state = _load_state()
        state[plan.symbol] = _alert_state_key(candidate, plan)
        _save_state(state)

    return sent
