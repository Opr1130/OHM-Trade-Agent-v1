from datetime import datetime, timezone
from pathlib import Path

from app.services.active_trade_registry import ActiveTrade
from app.services.asset_display_identity import display_market_label
from app.services.compact_alerts import one_line_reason
from app.services.notification_policy import record_emitted, should_emit
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic
from app.services.telegram_delivery import record_telegram_suppression, send_tracked_telegram
from app.services.trade_monitor import TradeMonitorResult


STATE_FILE = Path("/app/data/trade_monitor_state.json")
LOCK_FILE = STATE_FILE.parent / ".trade_monitor_state.lock"


def _load_state() -> dict:
    with registry_lock(LOCK_FILE):
        return load_json(STATE_FILE)


def _save_state(state: dict) -> None:
    with registry_lock(LOCK_FILE):
        save_json_atomic(STATE_FILE, state)


def _previous_action(value) -> str | None:
    if isinstance(value, dict):
        raw = value.get("action")
    else:
        raw = value
    return str(raw) if raw not in (None, "") else None


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
        f"{icon} OHM ACTIVE TRADE — {display_market_label(trade.symbol)}\n"
        f"Price: {float(result.current_price):.8g} | Entry: {float(trade.entry_price):.8g}\n"
        f"P/L: {float(pnl_pct):+.2f}% | Risk: {trade.risk_level.upper()}\n"
        f"Stop: {float(trade.stop_price):.8g} | Downside: {downside:.1f}%\n"
        f"T1 / T2: {float(trade.target_1):.8g} / {float(trade.target_2):.8g}\n"
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

    identity = f"ACTIVE_TRADE:{trade.trade_id or trade.symbol}"
    fingerprint = f"{trade.direction}:{result.action}"
    try:
        state = _load_state()
    except (OSError, TimeoutError, RegistryIOError):
        record_telegram_suppression(
            identity=identity,
            alert_family="ACTIVE_TRADE",
            event_type=result.action,
            fingerprint=fingerprint,
            reason="STATE_UNAVAILABLE_FAIL_CLOSED",
            symbol=trade.symbol,
            trade_id=trade.trade_id,
        )
        return False

    previous_action = _previous_action(state.get(trade.symbol))
    if previous_action == result.action:
        record_telegram_suppression(
            identity=identity,
            alert_family="ACTIVE_TRADE",
            event_type=result.action,
            fingerprint=fingerprint,
            reason="SAME_ACTION",
            symbol=trade.symbol,
            trade_id=trade.trade_id,
        )
        return False

    if not should_emit(identity=identity, event_type=result.action, fingerprint=fingerprint):
        record_telegram_suppression(
            identity=identity,
            alert_family="ACTIVE_TRADE",
            event_type=result.action,
            fingerprint=fingerprint,
            reason="NOTIFICATION_POLICY",
            symbol=trade.symbol,
            trade_id=trade.trade_id,
        )
        return False

    delivery = send_tracked_telegram(
        bot_token=bot_token,
        chat_id=chat_id,
        message=format_monitor_message(trade, result),
        identity=identity,
        alert_family="ACTIVE_TRADE",
        event_type=result.action,
        fingerprint=fingerprint,
        symbol=trade.symbol,
        trade_id=trade.trade_id,
    )
    if delivery.delivered:
        state[trade.symbol] = {
            "action": result.action,
            "message_id": delivery.message_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            _save_state(state)
        except (OSError, TimeoutError, RegistryIOError):
            pass
        record_emitted(identity=identity, event_type=result.action, fingerprint=fingerprint)
    return delivery.delivered
