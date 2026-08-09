import json
from pathlib import Path
from typing import Any

from app.services.entry_exit_advisor import EntryExitPlan
from app.services.pending_setup_registry import (
    PendingSetup,
    add_pending_setup,
    terminalize_pending_setup,
)
from app.services.telegram_notifier import (
    build_trade_confirmation_buttons,
    send_telegram_message,
)


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
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATE_FILE.write_text(
        json.dumps(
            state,
            indent=2,
        )
    )


def _action_type(plan: EntryExitPlan) -> str | None:
    """
    Return a Telegram-worthy action.

    ENTER_NOW:
        Setup is economically qualified and currently actionable.

    PLACE_LIMIT:
        Setup is economically qualified, but OHM wants a controlled
        pullback entry instead of chasing.

    None:
        Ordinary WAIT conditions stay silent.
    """

    if plan.valid_now:
        return "ENTER_NOW"

    if plan.entry_style == "wait_for_pullback":
        return "PLACE_LIMIT"

    return None


def _alert_state_key(
    candidate: dict[str, Any],
    plan: EntryExitPlan,
) -> str:
    action = _action_type(plan) or "SILENT"

    confidence = int(
        candidate.get(
            "confidence",
            0,
        )
    )

    return (
        f"{action}:"
        f"{plan.risk_level}:"
        f"{confidence}:"
        f"{plan.entry_low}:"
        f"{plan.entry_high}:"
        f"{plan.stop_price}:"
        f"{plan.target_2}"
    )


def format_trade_plan(
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    summary: str,
) -> str:
    action = _action_type(plan)

    if action == "ENTER_NOW":
        headline = "🔥 OHM CHIEF — ENTER NOW"
        action_text = (
            "Action: ENTRY CONDITIONS VALID\n"
            "Use the approved entry zone. "
            "Do not chase above the limit."
        )

    elif action == "PLACE_LIMIT":
        headline = "🎯 OHM CHIEF — PLACE LIMIT"
        action_text = (
            "Action: SET LIMIT ENTRY\n"
            "Setup is qualified, but current price is extended. "
            "Wait for the approved pullback."
        )

    else:
        raise ValueError(
            "Cannot format non-actionable OHM trade plan"
        )

    quote_note = ""
    if (
        candidate.get("primary_quote_currency") == "USDT"
        and not candidate.get("secondary_pair")
    ):
        quote_note = (
            "Quote: USDT (USDT availability required; no automatic USD "
            "conversion)\n"
        )

    return (
        f"{headline}\n\n"
        f"{action_text}\n\n"
        f"Asset: {candidate.get('underlying_asset', plan.symbol)}\n"
        f"Market: {candidate.get('primary_pair', plan.symbol)}\n"
        f"{quote_note}"
        f"Risk: {plan.risk_level.upper()}\n"
        f"AI Confidence: "
        f"{candidate.get('confidence', 0)}%\n"
        f"Technical Decision: "
        f"{str(candidate.get('decision', '')).upper()}\n\n"

        f"📍 ENTRY PLAN\n"
        f"Entry Zone: "
        f"{plan.entry_low} - {plan.entry_high}\n"
        f"Do Not Chase Above: "
        f"{plan.chase_limit}\n"
        f"Stop: "
        f"{plan.stop_price}\n\n"

        f"🎯 TARGETS\n"
        f"Target 1: "
        f"{plan.target_1} "
        f"({plan.reward_to_risk_1}:1 R/R)\n"
        f"Target 2: "
        f"{plan.target_2} "
        f"({plan.reward_to_risk_2}:1 R/R)\n\n"

        f"Entry Style: "
        f"{_pretty_entry_style(plan.entry_style)}\n\n"

        f"Reason:\n"
        f"{plan.reason}\n\n"

        f"Chief Analyst:\n"
        f"{candidate.get('reason', '')}\n\n"

        f"Market Summary:\n"
        f"{summary}"
    )


def should_send_trade_plan(
    candidate: dict[str, Any],
    plan: EntryExitPlan,
) -> bool:
    action = _action_type(plan)

    #
    # REJECT / WATCH / ordinary WAIT conditions
    # never reach Telegram.
    #
    if action is None:
        return False

    state = _load_state()

    current_key = _alert_state_key(
        candidate,
        plan,
    )

    previous_key = state.get(
        plan.symbol
    )

    return current_key != previous_key


def send_trade_plan(
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    summary: str,
    bot_token: str,
    chat_id: str,
) -> bool:

    action = _action_type(plan)

    #
    # Silent means silent.
    #
    if action is None:
        return False

    if not should_send_trade_plan(
        candidate,
        plan,
    ):
        return False

    message = format_trade_plan(
        candidate=candidate,
        plan=plan,
        summary=summary,
    )

    #
    # ENTER NOW uses the already-tested Telegram confirmation
    # buttons.
    #
    # PLACE LIMIT will get its own "LIMIT PLACED" button in
    # the next upgrade to the callback listener.
    #
    reply_markup = None

    if action == "ENTER_NOW":
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
                confirmation_price=plan.entry_high,
            )
        )
        reply_markup = (
            build_trade_confirmation_buttons(
                setup.trade_id
            )
        )

    sent = send_telegram_message(
        bot_token,
        chat_id,
        message,
        reply_markup=reply_markup,
    )

    if sent:
        state = _load_state()

        state[plan.symbol] = (
            _alert_state_key(
                candidate,
                plan,
            )
        )

        _save_state(state)

    elif action == "ENTER_NOW":
        terminalize_pending_setup(setup.trade_id, "send_failed")

    return sent
