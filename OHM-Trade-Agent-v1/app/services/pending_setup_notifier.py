from datetime import datetime, timezone
from pathlib import Path

from app.services.asset_display_identity import display_market_label
from app.services.compact_alerts import one_line_reason
from app.services.notification_policy import record_emitted, should_emit
from app.services.pending_setup_monitor import PendingSetupMonitorResult
from app.services.pending_setup_registry import (
    PendingSetup,
    terminalize_pending_setup,
)
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic
from app.services.telegram_delivery import (
    record_telegram_not_eligible,
    record_telegram_suppression,
    send_tracked_telegram,
)


STATE_FILE = Path("/app/data/pending_setup_alert_state.json")


def _load_state() -> dict:
    with registry_lock(STATE_FILE.parent / f".{STATE_FILE.name}.lock"):
        return load_json(STATE_FILE)


def _save_state(state: dict) -> None:
    with registry_lock(STATE_FILE.parent / f".{STATE_FILE.name}.lock"):
        save_json_atomic(STATE_FILE, state)


def _previous_status(value) -> str | None:
    if isinstance(value, dict):
        raw = value.get("status")
    else:
        raw = value
    return str(raw) if raw not in (None, "") else None


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
        action = "DO NOT ENTER — Cancel any open Kraken order manually; OHM uses a read-only Kraken key"
    elif result.status == "TOO_EXTENDED":
        icon = "⚠️"
        title = "OHM DO NOT CHASE"
        action = "WAIT — Cancel any open Kraken order manually; OHM uses a read-only Kraken key"
    else:
        icon = "ℹ️"
        title = "OHM SETUP UPDATE"
        action = "WAIT"

    downside = _stop_downside_pct(setup, float(result.current_price))
    return (
        f"{icon} {title} — {display_market_label(setup.symbol)}\n"
        f"Price: {float(result.current_price):.8g}\n"
        f"Entry zone: {float(setup.entry_low):.8g} - {float(setup.entry_high):.8g}\n"
        f"Do not chase: {float(setup.chase_limit):.8g} | Stop: {float(setup.stop_price):.8g}\n"
        f"T1 / T2: {float(setup.target_1):.8g} / {float(setup.target_2):.8g}\n"
        f"Confidence*: {int(setup.confidence)}% | Risk: {setup.risk_level.upper()} | Downside: {downside:.1f}%\n"
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
    identity = f"PENDING_SETUP:{setup.trade_id or setup.symbol}"
    fingerprint = f"{setup.direction}:{result.status}"

    if result.status in {"WAITING", "NEAR_ENTRY"}:
        record_telegram_not_eligible(
            identity=identity,
            alert_family="PENDING_SETUP",
            event_type=result.status,
            fingerprint=fingerprint,
            reason="NON_MATERIAL_PENDING_STATE",
            symbol=setup.symbol,
            trade_id=setup.trade_id,
        )
        return False

    try:
        state = _load_state()
    except (OSError, TimeoutError, RegistryIOError):
        record_telegram_suppression(
            identity=identity,
            alert_family="PENDING_SETUP",
            event_type=result.status,
            fingerprint=fingerprint,
            reason="STATE_UNAVAILABLE_FAIL_CLOSED",
            symbol=setup.symbol,
            trade_id=setup.trade_id,
        )
        return False

    previous_status = _previous_status(state.get(setup.symbol))
    terminal_status = {
        "INVALIDATED": "invalidated",
        "TOO_EXTENDED": "too_extended",
    }.get(result.status)

    if previous_status == result.status:
        if terminal_status:
            terminalize_pending_setup(setup.trade_id, terminal_status)
        record_telegram_suppression(
            identity=identity,
            alert_family="PENDING_SETUP",
            event_type=result.status,
            fingerprint=fingerprint,
            reason="SAME_STATE",
            symbol=setup.symbol,
            trade_id=setup.trade_id,
        )
        return False

    if not should_emit(
        identity=identity,
        event_type=result.status,
        fingerprint=fingerprint,
    ):
        record_telegram_suppression(
            identity=identity,
            alert_family="PENDING_SETUP",
            event_type=result.status,
            fingerprint=fingerprint,
            reason="NOTIFICATION_POLICY",
            symbol=setup.symbol,
            trade_id=setup.trade_id,
        )
        return False

    delivery = send_tracked_telegram(
        bot_token=bot_token,
        chat_id=chat_id,
        message=format_pending_setup_message(setup, result),
        identity=identity,
        alert_family="PENDING_SETUP",
        event_type=result.status,
        fingerprint=fingerprint,
        symbol=setup.symbol,
        trade_id=setup.trade_id,
    )

    if delivery.delivered:
        state[setup.symbol] = {
            "status": result.status,
            "message_id": delivery.message_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            _save_state(state)
        except (OSError, TimeoutError, RegistryIOError):
            # Telegram accepted the alert. Delivery history is canonical even if
            # this convenience dedup registry cannot be advanced.
            pass
        record_emitted(
            identity=identity,
            event_type=result.status,
            fingerprint=fingerprint,
        )
        if terminal_status:
            terminalize_pending_setup(setup.trade_id, terminal_status)
    elif terminal_status:
        # Keep the setup non-terminal so the next monitor pass can retry the
        # lifecycle-critical invalidation/extended alert. No entry is authorized.
        return False

    return delivery.delivered
