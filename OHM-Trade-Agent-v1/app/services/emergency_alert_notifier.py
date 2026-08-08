import json
from datetime import datetime, timezone
from pathlib import Path

from app.services.active_trade_registry import ActiveTrade
from app.services.emergency_move_detector import EmergencyMoveResult
from app.services.telegram_notifier import send_telegram_message


STATE_FILE = Path("/app/data/emergency_alert_state.json")
CRITICAL_REPEAT_SECONDS = 300


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def format_emergency_message(
    trade: ActiveTrade,
    result: EmergencyMoveResult,
) -> str:
    icon = "🛑" if result.severity == "critical" else "⚠️"

    reasons = "\n".join(
        f"• {reason}"
        for reason in result.reasons
    )

    action = (
        "CLOSE / REDUCE POSITION NOW"
        if result.severity == "critical"
        else "REVIEW POSITION IMMEDIATELY"
    )

    return (
        f"{icon} OHM AI — EMERGENCY RISK ALERT\n\n"
        f"Symbol: {trade.symbol}\n"
        f"Severity: {result.severity.upper()}\n"
        f"Action: {action}\n\n"
        f"Entry: {trade.entry_price}\n"
        f"Current: {result.current_price}\n"
        f"Stop: {trade.stop_price}\n\n"
        f"5m Change: {result.change_5m_pct}%\n"
        f"15m Change: {result.change_15m_pct}%\n"
        f"Distance to Stop: {result.stop_distance_pct}%\n\n"
        f"Reasons:\n{reasons}"
    )


def should_send_emergency_alert(
    trade: ActiveTrade,
    result: EmergencyMoveResult,
) -> bool:
    if result.severity == "normal":
        return False

    state = _load_state()
    item = state.get(trade.symbol, {})

    previous_severity = item.get("severity")
    last_sent = float(item.get("last_sent", 0))

    if result.severity != previous_severity:
        return True

    if result.severity == "critical":
        return (_now_ts() - last_sent) >= CRITICAL_REPEAT_SECONDS

    return False


def send_emergency_alert(
    trade: ActiveTrade,
    result: EmergencyMoveResult,
    bot_token: str,
    chat_id: str,
) -> bool:
    if not should_send_emergency_alert(trade, result):
        return False

    sent = send_telegram_message(
        bot_token,
        chat_id,
        format_emergency_message(trade, result),
    )

    if sent:
        state = _load_state()
        state[trade.symbol] = {
            "severity": result.severity,
            "last_sent": _now_ts(),
        }
        _save_state(state)

    return sent
