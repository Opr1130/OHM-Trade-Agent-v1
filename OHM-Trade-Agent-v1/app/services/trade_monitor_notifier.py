import json
from pathlib import Path

from app.services.active_trade_registry import ActiveTrade
from app.services.asset_display_identity import display_market_label
from app.services.compact_alerts import one_line_reason
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


def _stop_downside_pct(trade: ActiveTrade, current_price: float) -> float:
    if current_price <= 0:
        return 0.0
    direction = str(trade.direction or "LONG").upper()
    if direction == "SHORT":
        return max(0.0, (float(trade.stop_price) / current_price - 1.0) * 100.0)
    return max(0.0, (1.0 - float(trade.stop_price) / current_price) * 100.0)


def format_monitor_message(trade: ActiveTrade, result: TradeMonitorResult) -> str:
    icon = {
        "HOLD": "✅",
        "WARNING": "⚠️",
        "TAKE_PROFIT": "🎯",
        "EXIT_NOW": "🛑",
    }.get(result.action, "ℹ️")
    pnl_pct = result.net_pnl_pct if result.net_pnl_pct is not None else result.unrealized_pct
    downside = _stop_downside_pct(trade, float(result.current_price))
    reason = one_line_reason(*(result.reasons or []))
    return (
        f"{icon} OHM TRADE — {display_market_label(trade.symbol)}\n"
        f"P/L: {float(pnl_pct):+.2f}%\n"
        f"Risk: {trade.risk_level.upper()}\n"
        f"Downside to stop: {downside:.1f}%\n"
        f"Reason: {reason}\n"
        f"Action: {result.action.replace('_', ' ')}"
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
