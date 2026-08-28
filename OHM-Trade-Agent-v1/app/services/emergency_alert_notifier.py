from datetime import datetime, timezone
from pathlib import Path

from app.services.active_trade_registry import ActiveTrade
from app.services.asset_display_identity import display_market_label
from app.services.compact_alerts import one_line_reason
from app.services.emergency_move_detector import EmergencyMoveResult
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic
from app.services.telegram_delivery import record_telegram_suppression, send_tracked_telegram


STATE_FILE = Path("/app/data/emergency_alert_state.json")
LOCK_FILE = STATE_FILE.parent / ".emergency_alert_state.lock"
CRITICAL_REPEAT_SECONDS = 300


def _load_state() -> dict:
    with registry_lock(LOCK_FILE):
        return load_json(STATE_FILE)


def _save_state(state: dict) -> None:
    with registry_lock(LOCK_FILE):
        save_json_atomic(STATE_FILE, state)


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def format_emergency_message(
    trade: ActiveTrade,
    result: EmergencyMoveResult,
) -> str:
    icon = "🛑" if result.severity == "critical" else "⚠️"
    action = (
        "CLOSE / REDUCE NOW"
        if result.severity == "critical"
        else "REVIEW POSITION NOW"
    )
    reason = one_line_reason(*(result.reasons or []))
    return (
        f"{icon} OHM RISK — {display_market_label(trade.symbol)}\n"
        f"Price: {float(result.current_price):.8g} | Entry: {float(trade.entry_price):.8g}\n"
        f"Risk: {result.severity.upper()} | Stop: {float(trade.stop_price):.8g}\n"
        f"Distance to stop: {float(result.stop_distance_pct):.1f}%\n"
        f"5m / 15m: {float(result.change_5m_pct):+.1f}% / {float(result.change_15m_pct):+.1f}%\n"
        f"T1 / T2: {float(trade.target_1):.8g} / {float(trade.target_2):.8g}\n"
        f"Reason: {reason}\n"
        f"Action: {action}"
    )


def _emergency_decision(
    trade: ActiveTrade,
    result: EmergencyMoveResult,
) -> tuple[bool, str]:
    if result.severity == "normal":
        return False, "NORMAL_SEVERITY"

    try:
        state = _load_state()
    except (OSError, TimeoutError, RegistryIOError):
        # Risk alerts are lifecycle-critical. Fail open on dedup-state loss;
        # the delivery ledger will record the attempt.
        return True, "STATE_UNAVAILABLE_FAIL_OPEN"

    item = state.get(trade.symbol, {})
    if not isinstance(item, dict):
        item = {}
    previous_severity = item.get("severity")
    last_sent = float(item.get("last_sent", 0) or 0)

    if result.severity != previous_severity:
        return True, "SEVERITY_TRANSITION"

    if result.severity == "critical":
        if (_now_ts() - last_sent) >= CRITICAL_REPEAT_SECONDS:
            return True, "CRITICAL_REPEAT_DUE"
        return False, "CRITICAL_REPEAT_COOLDOWN"

    return False, "SAME_WARNING_STATE"


def should_send_emergency_alert(
    trade: ActiveTrade,
    result: EmergencyMoveResult,
) -> bool:
    allowed, _ = _emergency_decision(trade, result)
    return allowed


def send_emergency_alert(
    trade: ActiveTrade,
    result: EmergencyMoveResult,
    bot_token: str,
    chat_id: str,
) -> bool:
    identity = f"EMERGENCY:{trade.trade_id or trade.symbol}"
    fingerprint = (
        f"{trade.direction}:{result.severity}:"
        f"{round(float(result.change_5m_pct), 1)}:"
        f"{round(float(result.change_15m_pct), 1)}"
    )
    allowed, reason = _emergency_decision(trade, result)
    if not allowed:
        record_telegram_suppression(
            identity=identity,
            alert_family="EMERGENCY_RISK",
            event_type=result.severity,
            fingerprint=fingerprint,
            reason=reason,
            symbol=trade.symbol,
            trade_id=trade.trade_id,
        )
        return False

    delivery = send_tracked_telegram(
        bot_token=bot_token,
        chat_id=chat_id,
        message=format_emergency_message(trade, result),
        identity=identity,
        alert_family="EMERGENCY_RISK",
        event_type=result.severity,
        fingerprint=fingerprint,
        symbol=trade.symbol,
        trade_id=trade.trade_id,
        success_status="TRANSITION_PUSHED" if reason == "SEVERITY_TRANSITION" else "DELIVERED",
    )

    if delivery.delivered:
        try:
            state = _load_state()
            state[trade.symbol] = {
                "severity": result.severity,
                "last_sent": _now_ts(),
                "message_id": delivery.message_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_state(state)
        except (OSError, TimeoutError, RegistryIOError):
            pass

    return delivery.delivered
