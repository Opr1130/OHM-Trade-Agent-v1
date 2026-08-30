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

# Existing holdings are monitored continuously, but routine states should not
# create periodic noise. Re-alert only when the state changes or the position
# materially changes. Actionable terminal/profit states may repeat on a bounded
# cadence until the holding disappears from Kraken.
ACTION_REPEAT_SECONDS = {
    "TAKE_PROFIT": 10 * 60,
    "EXIT_NOW": 5 * 60,
}
MATERIAL_PRICE_CHANGE_PCT = {
    "HOLD": 3.0,
    "WARNING": 2.0,
}
MATERIAL_PNL_CHANGE_POINTS = {
    "HOLD": 3.0,
    "WARNING": 2.0,
}


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


def _previous_float(value, key: str) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get(key)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _reason_signature(result: TradeMonitorResult) -> str:
    return "|".join(sorted(str(reason).strip() for reason in (result.reasons or []) if str(reason).strip()))


def _material_change(previous, result: TradeMonitorResult) -> bool:
    action = str(result.action).upper()
    if _previous_action(previous) != action:
        return True

    current_price = float(result.current_price)
    current_pnl = float(result.net_pnl_pct if result.net_pnl_pct is not None else result.unrealized_pct)
    previous_price = _previous_float(previous, "price")
    previous_pnl = _previous_float(previous, "pnl_pct")
    previous_reason = str(previous.get("reason_signature") or "") if isinstance(previous, dict) else ""

    if previous_reason and previous_reason != _reason_signature(result):
        return True

    price_threshold = MATERIAL_PRICE_CHANGE_PCT.get(action)
    if price_threshold is not None and previous_price and previous_price > 0:
        move_pct = abs(current_price / previous_price - 1.0) * 100.0
        if move_pct >= price_threshold:
            return True

    pnl_threshold = MATERIAL_PNL_CHANGE_POINTS.get(action)
    if pnl_threshold is not None and previous_pnl is not None:
        if abs(current_pnl - previous_pnl) >= pnl_threshold:
            return True

    return False


def _action_repeat_due(previous, action: str, *, now: datetime) -> bool:
    repeat_seconds = ACTION_REPEAT_SECONDS.get(str(action).upper())
    if repeat_seconds is None:
        return False
    updated_at = _previous_updated_at(previous)
    if updated_at is None:
        return True
    return (now - updated_at).total_seconds() >= repeat_seconds


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
    material = _material_change(previous, result)
    repeat_due = _action_repeat_due(previous, result.action, now=now)

    if not material and not repeat_due:
        record_telegram_suppression(
            identity=identity,
            alert_family="ACTIVE_TRADE",
            event_type=result.action,
            fingerprint=f"{trade.direction}:{result.action}",
            reason="NO_MATERIAL_CHANGE",
            symbol=trade.symbol,
            trade_id=trade.trade_id,
        )
        return False

    current_price = float(result.current_price)
    current_pnl = float(result.net_pnl_pct if result.net_pnl_pct is not None else result.unrealized_pct)
    reason_sig = _reason_signature(result)

    if repeat_due and not material:
        repeat_seconds = ACTION_REPEAT_SECONDS[str(result.action).upper()]
        bucket = int(now.timestamp()) // max(1, repeat_seconds)
        fingerprint = f"{trade.direction}:{result.action}:REPEAT:{bucket}"
        cooldown_seconds = repeat_seconds
    else:
        # Include rounded material state so the global policy permits meaningful
        # same-action updates while still deduplicating identical observations.
        fingerprint = (
            f"{trade.direction}:{result.action}:"
            f"{round(current_price, 8)}:{round(current_pnl, 2)}:{reason_sig}"
        )
        cooldown_seconds = 0

    if not should_emit(
        identity=identity,
        event_type=result.action,
        fingerprint=fingerprint,
        cooldown_seconds=cooldown_seconds,
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
        state[trade.symbol] = {
            "action": result.action,
            "message_id": delivery.message_id,
            "updated_at": now.isoformat(),
            "price": current_price,
            "pnl_pct": current_pnl,
            "reason_signature": reason_sig,
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
