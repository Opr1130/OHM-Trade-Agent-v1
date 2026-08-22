import json
from pathlib import Path

from app.services.compact_alerts import one_line_reason
from app.services.notification_policy import record_emitted, should_emit
from app.services.pending_setup_monitor import PendingSetupMonitorResult
from app.services.pending_setup_registry import (
    PendingSetup,
    terminalize_pending_setup,
)
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


def _stop_downside_pct(setup: PendingSetup, current_price: float) -> float:
    if current_price <= 0:
        return 0.0
    direction = str(setup.direction or "LONG").upper()
    if direction == "SHORT":
        return max(0.0, (float(setup.stop_price) / current_price - 1.0) * 100.0)
    return max(0.0, (1.0 - float(setup.stop_price) / current_price) * 100.0)


def format_pending_setup_message(
    setup: PendingSetup,
    result: PendingSetupMonitorResult,
) -> str:
    if result.status == "ENTRY_ZONE_REACHED":
        icon = "🟢"
        title = "OHM ENTRY READY"
        action = "REVIEW ENTRY"
    elif result.status == "INVALIDATED":
        icon = "🔴"
        title = "OHM SETUP INVALID"
        action = "DO NOT ENTER — Cancel any open Kraken order manually"
    elif result.status == "TOO_EXTENDED":
        icon = "⚠️"
        title = "OHM DO NOT CHASE"
        action = "WAIT FOR NEW SETUP — Cancel any open Kraken order manually"
    else:
        icon = "ℹ️"
        title = "OHM SETUP UPDATE"
        action = "WAIT"

    downside = _stop_downside_pct(setup, float(result.current_price))
    return (
        f"{icon} {title} — {setup.symbol}\n"
        f"Confidence*: {int(setup.confidence)}%\n"
        f"Risk: {setup.risk_level.upper()}\n"
        f"Downside to stop: {downside:.1f}%\n"
        f"Reason: {one_line_reason(result.reason)}\n"
        f"Action: {action}\n"
        "*Heuristic confidence, not probability."
    )


def send_pending_setup_update(
    setup: PendingSetup,
    result: PendingSetupMonitorResult,
    bot_token: str,
    chat_id: str,
) -> bool:
    if result.status in {"WAITING", "NEAR_ENTRY"}:
        return False

    state = _load_state()
    previous_status = state.get(setup.symbol)

    terminal_status = {
        "INVALIDATED": "invalidated",
        "TOO_EXTENDED": "too_extended",
    }.get(result.status)

    if previous_status == result.status:
        if terminal_status:
            terminalize_pending_setup(setup.trade_id, terminal_status)
        return False

    fingerprint = f"{setup.direction}:{result.status}"
    if not should_emit(
        identity=f"PENDING_SETUP:{setup.trade_id or setup.symbol}",
        event_type=result.status,
        fingerprint=fingerprint,
    ):
        return False

    sent = send_telegram_message(
        bot_token,
        chat_id,
        format_pending_setup_message(setup, result),
    )

    if sent:
        state[setup.symbol] = result.status
        _save_state(state)
        record_emitted(
            identity=f"PENDING_SETUP:{setup.trade_id or setup.symbol}",
            event_type=result.status,
            fingerprint=fingerprint,
        )

    if terminal_status:
        terminalize_pending_setup(setup.trade_id, terminal_status)

    return sent
