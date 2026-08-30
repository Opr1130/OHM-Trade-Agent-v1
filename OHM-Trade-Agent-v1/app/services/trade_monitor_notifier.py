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

# Active-position monitoring is a safety workflow, not a one-shot state-change
# notifier. Re-emit the current state on a bounded heartbeat so the operator
# continues to receive protection even when the action remains unchanged.
HEARTBEAT_SECONDS = {
    "HOLD": 30 * 60,
    "WARNING": 10 * 60,
    "TAKE_PROFIT": 5 * 60,
    "EXIT_NOW": 2 * 60,
}
DEFAULT_HEARTBEAT_SECONDS = 15 * 60


def _load_state() -> dict:
    with registry_lock(STATE_FILE.parent / f".{STATE_FILE.name}.lock"):
        return load_json(STATE_FILE)


def _save_state(state: dict) -> None:
    with registry_lock(STATE_FILE.parent / f".{STATE_FILE.name}.lock"):
        save_json_atomic(STATE_FILE, state)


def _previous_action(value) -> str | None:
    if isinstance(value, dict):
        raw = value.get("action")
    else:
        raw = value
    return str(raw) if raw not in (None, "") else None


def _previous_updated_at(value) -> datetime | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("updated_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _heartbeat_seconds(action: str) -> int:
    return int(HEARTBEAT_SECONDS.get(str(action).upper(), DEFAULT_HEARTBEAT_SECONDS))


def _same_action_heartbeat_due(value, action: str, *, now: datetime) -> bool:
    if _previous_action(value) != action:
        return True
    updated_at = _previous_updated_at(value)
    if updated_at is None:
        return True
    return (now - updated_at).total_seconds() >= _heartbeat_seconds(action)


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
    now = datetime.now(timezone.utc)
    heartbeat_seconds = _heartbeat_seconds(result.action)
    try:
        state = _load_state()
    except (OSError, TimeoutError, RegistryIOError):
        record_telegram_suppression(
            identity=identity,
            alert_family="ACTIVE_TRADE",
            event_type=result.action,
            fingerprint=f"{trade.direction}:{result.action}",
            reason="STATE_UNAVAILABLE_FAIL_CLOSED",
            symbol=trade.symbol,
            trade_id=trade.trade_id,
        )
        return False

    previous = state.get(trade.symbol)
    if not _same_action_heartbeat_due(previous, result.action, now=now):
        record_telegram_suppression(
            identity=identity,
            alert_family="ACTIVE_TRADE",
            event_type=result.action,
            fingerprint=f"{trade.direction}:{result.action}",
            reason="SAME_ACTION_HEARTBEAT_NOT_DUE",
            symbol=trade.symbol,
            trade_id=trade.trade_id,
        )
        return False

    # Bucket the fingerprint by the action heartbeat. This preserves global
    # deduplication while allowing a healthy active position to keep producing
    # bounded reminders instead of being suppressed forever.
    heartbeat_bucket = int(now.timestamp()) // max(1, heartbeat_seconds)
    fingerprint = f"{trade.direction}:{result.action}:{heartbeat_bucket}"

    if not should_emit(
        identity=identity,
        event_type=result.action,
        fingerprint=fingerprint,
        cooldown_seconds=heartbeat_seconds,
        now=now,
    ):
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
        pnl_pct = result.net_pnl_pct if result.net_pnl_pct is not None else result.unrealized_pct
        state[trade.symbol] = {
            "action": result.action,
            "message_id": delivery.message_id,
            "updated_at": now.isoformat(),
            "price": float(result.current_price),
            "pnl_pct": float(pnl_pct),
        }
        try:
            _save_state(state)
        except (OSError, TimeoutError, RegistryIOError):
            pass
        record_emitted(
            identity=identity,
            event_type=result.action,
            fingerprint=fingerprint,
            now=now,
        )
    return delivery.delivered
