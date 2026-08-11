import json
from pathlib import Path

from app.services.active_trade_registry import ActiveTrade
from app.services.notification_policy import record_emitted, should_emit
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


def format_monitor_message(trade: ActiveTrade, result: TradeMonitorResult) -> str:
    icon = {
        "HOLD": "✅",
        "WARNING": "⚠️",
        "TAKE_PROFIT": "🎯",
        "EXIT_NOW": "🛑",
    }.get(result.action, "ℹ️")
    reasons = "\n".join(f"• {reason}" for reason in result.reasons)
    pnl_note = ""
    if result.net_pnl is not None:
        pnl_note = (
            f"Gross P/L: ${result.gross_pnl:.2f}\n"
            f"Est. Trading Costs: ${result.estimated_total_costs:.2f}\n"
            f"NET P/L: ${result.net_pnl:.2f} ({result.net_pnl_pct:.2f}%)\n"
            f"Break-even Move: {result.break_even_move_pct:.3f}%\n"
            f"Fee Basis: {result.fee_source}\n\n"
        )
    return (
        f"{icon} OHM AI — TRADE MONITOR\n\n"
        f"Symbol: {trade.symbol}\n"
        f"Action: {result.action.replace('_', ' ')}\n"
        f"Risk: {trade.risk_level.upper()}\n\n"
        f"Entry: {trade.entry_price}\n"
        f"Current: {result.current_price}\n"
        f"Price Move P/L: {result.unrealized_pct}%\n"
        f"{pnl_note}"
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
    if trade.status != "active":
        return False

    state = _load_state()
    previous_action = state.get(trade.symbol)
    if previous_action == result.action:
        return False

    identity = trade.trade_id or trade.symbol
    fingerprint = f"{trade.direction}:{result.action}"
    if not should_emit(identity=identity, event_type=result.action, fingerprint=fingerprint):
        return False

    sent = send_telegram_message(bot_token, chat_id, format_monitor_message(trade, result))
    if sent:
        state[trade.symbol] = result.action
        _save_state(state)
        record_emitted(identity=identity, event_type=result.action, fingerprint=fingerprint)
    return sent
