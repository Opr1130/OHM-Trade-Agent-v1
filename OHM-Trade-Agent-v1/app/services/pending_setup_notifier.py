import json
from pathlib import Path

from app.services.pending_setup_monitor import PendingSetupMonitorResult
from app.services.pending_setup_registry import PendingSetup
from app.services.telegram_notifier import send_telegram_message


STATE_FILE = Path("/app/data/pending_setup_alert_state.json")


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


def format_pending_setup_message(
    setup: PendingSetup,
    result: PendingSetupMonitorResult,
) -> str:
    if result.status == "ENTRY_ZONE_REACHED":
        header = "🟢 OHM AI — ENTRY ZONE REACHED"
        action = "ENTRY CONDITIONS ARE NOW IN RANGE"
    elif result.status == "INVALIDATED":
        header = "🔴 OHM AI — SETUP INVALIDATED"
        action = "DO NOT ENTER"
    elif result.status == "TOO_EXTENDED":
        header = "⚠️ OHM AI — DO NOT CHASE"
        action = "WAIT FOR A NEW SETUP"
    else:
        header = "ℹ️ OHM AI — SETUP UPDATE"
        action = "CONTINUE WAITING"

    return (
        f"{header}\n\n"
        f"Symbol: {setup.symbol}\n"
        f"Action: {action}\n"
        f"Risk: {setup.risk_level.upper()}\n"
        f"AI Confidence: {setup.confidence}%\n\n"
        f"Current Price: {result.current_price}\n\n"
        f"📍 ENTRY PLAN\n"
        f"Entry Zone: {setup.entry_low} - {setup.entry_high}\n"
        f"Do Not Chase Above: {setup.chase_limit}\n"
        f"Stop: {setup.stop_price}\n\n"
        f"🎯 TARGETS\n"
        f"Target 1: {setup.target_1}\n"
        f"Target 2: {setup.target_2}\n\n"
        f"Reason:\n{result.reason}\n\n"
        f"Confirm your actual fill before OHM AI begins active-trade monitoring."
    )


def send_pending_setup_update(
    setup: PendingSetup,
    result: PendingSetupMonitorResult,
    bot_token: str,
    chat_id: str,
) -> bool:
    # WAITING and NEAR_ENTRY stay silent.
    if result.status in {"WAITING", "NEAR_ENTRY"}:
        return False

    state = _load_state()
    previous_status = state.get(setup.symbol)

    if previous_status == result.status:
        return False

    sent = send_telegram_message(
        bot_token,
        chat_id,
        format_pending_setup_message(setup, result),
    )

    if sent:
        state[setup.symbol] = result.status
        _save_state(state)

    return sent
