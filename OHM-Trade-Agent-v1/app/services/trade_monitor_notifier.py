import json
from pathlib import Path

from app.services.active_trade_registry import ActiveTrade
from app.services.telegram_notifier import send_telegram_message
from app.services.trade_monitor import TradeMonitorResult


STATE_FILE = Path("/app/data/trade_monitor_state.json")


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


def format_monitor_message(
    trade: ActiveTrade,
    result: TradeMonitorResult,
) -> str:
    icon = {
        "HOLD": "✅",
        "WARNING": "⚠️",
        "TAKE_PROFIT": "🎯",
        "EXIT_NOW": "🛑",
    }.get(result.action, "ℹ️")

    reasons = "\n".join(
        f"• {reason}"
        for reason in result.reasons
    )

    return (
        f"{icon} OHM AI — TRADE MONITOR\n\n"
        f"Symbol: {trade.symbol}\n"
        f"Action: {result.action.replace('_', ' ')}\n"
        f"Risk: {trade.risk_level.upper()}\n\n"
        f"Entry: {trade.entry_price}\n"
        f"Current: {result.current_price}\n"
        f"Unrealized: {result.unrealized_pct}%\n\n"
        f"Stop: {trade.stop_price}\n"
        f"Target 1: {trade.target_1}\n"
        f"Target 2: {trade.target_2}\n\n"
        f"Reasons:\n{reasons}"
    )


def send_monitor_update(
    trade: ActiveTrade,
    result: TradeMonitorResult,
    bot_token: str,
    chat_id: str,
) -> bool:
    state = _load_state()
    previous_action = state.get(trade.symbol)

    # Only notify when the trade state changes.
    if previous_action == result.action:
        return False

    message = format_monitor_message(trade, result)

    sent = send_telegram_message(
        bot_token,
        chat_id,
        message,
    )

    if sent:
        state[trade.symbol] = result.action
        _save_state(state)

    return sent
