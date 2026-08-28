from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.services.asset_display_identity import display_market_label
from app.services.compact_alerts import one_line_reason
from app.services.notification_policy import record_emitted, should_emit
from app.services.pending_setup_monitor import PendingSetupMonitorResult
from app.services.pending_setup_registry import (
    PendingSetup,
    get_pending_setup_record,
    terminalize_pending_setup,
)
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic
from app.services.telegram_delivery import (
    accepted_delivery_message_id,
    record_telegram_not_eligible,
    record_telegram_suppression,
    send_tracked_telegram,
)


STATE_FILE = Path("/app/data/pending_setup_alert_state.json")
RETRY_FILE = Path("/app/data/pending_setup_terminal_alert_outbox.json")


def _locked_load(path: Path) -> dict:
    with registry_lock(path.parent / f".{path.name}.lock"):
        return load_json(path)


def _locked_save(path: Path, payload: dict) -> None:
    with registry_lock(path.parent / f".{path.name}.lock"):
        save_json_atomic(path, payload)


def _load_state() -> dict:
    return _locked_load(STATE_FILE)


def _save_state(state: dict) -> None:
    _locked_save(STATE_FILE, state)


def _load_retries() -> dict:
    return _locked_load(RETRY_FILE)


def _save_retries(retries: dict) -> None:
    _locked_save(RETRY_FILE, retries)


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


def _terminal_status(result_status: str) -> str | None:
    return {
        "INVALIDATED": "invalidated",
        "TOO_EXTENDED": "too_extended",
    }.get(str(result_status or "").upper())


def _queue_terminal_notification(
    setup: PendingSetup,
    result: PendingSetupMonitorResult,
) -> None:
    trade_id = str(setup.trade_id or "").strip()
    if not trade_id:
        raise ValueError("terminal pending alert requires trade_id")
    with registry_lock(RETRY_FILE.parent / f".{RETRY_FILE.name}.lock"):
        retries = load_json(RETRY_FILE)
        retries[trade_id] = {
            "schema_version": 1,
            "trade_id": trade_id,
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "setup": asdict(setup),
            "result": asdict(result),
        }
        save_json_atomic(RETRY_FILE, retries)


def _remove_terminal_retry(trade_id: str) -> None:
    with registry_lock(RETRY_FILE.parent / f".{RETRY_FILE.name}.lock"):
        retries = load_json(RETRY_FILE)
        if trade_id in retries:
            del retries[trade_id]
            save_json_atomic(RETRY_FILE, retries)


def _persist_delivered_state(
    *,
    setup: PendingSetup,
    result: PendingSetupMonitorResult,
    message_id: int | None,
) -> None:
    key = str(setup.trade_id or setup.symbol)
    try:
        state = _load_state()
        state[key] = {
            "status": result.status,
            "message_id": message_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_state(state)
    except (OSError, TimeoutError, RegistryIOError):
        # Telegram delivery history is canonical for transport outcome.
        pass


def _deliver_terminal_retry(
    *,
    setup: PendingSetup,
    result: PendingSetupMonitorResult,
    bot_token: str,
    chat_id: str,
) -> bool:
    terminal_status = _terminal_status(result.status)
    if terminal_status is None:
        return False

    lifecycle = get_pending_setup_record(setup.trade_id)
    persisted_status = str((lifecycle or {}).get("status") or "")
    identity = f"PENDING_SETUP:{setup.trade_id or setup.symbol}"
    fingerprint = f"{setup.direction}:{result.status}"

    if persisted_status != terminal_status:
        if persisted_status and persisted_status != "waiting":
            # Exchange/lifecycle truth superseded the queued market-terminal
            # notification (for example a fill won the race). Retire the stale
            # outbox row rather than retrying an invalid alert forever.
            _remove_terminal_retry(setup.trade_id)
            record_telegram_not_eligible(
                identity=identity,
                alert_family="PENDING_SETUP",
                event_type=result.status,
                fingerprint=fingerprint,
                reason=f"TERMINAL_ALERT_SUPERSEDED_BY_{persisted_status.upper()}",
                symbol=setup.symbol,
                trade_id=setup.trade_id,
            )
            return False
        record_telegram_suppression(
            identity=identity,
            alert_family="PENDING_SETUP",
            event_type=result.status,
            fingerprint=fingerprint,
            reason="TERMINAL_MARKET_STATE_NOT_PERSISTED",
            symbol=setup.symbol,
            trade_id=setup.trade_id,
        )
        return False

    accepted_message_id = accepted_delivery_message_id(
        identity=identity,
        event_type=result.status,
        fingerprint=fingerprint,
    )
    if accepted_message_id is not None:
        _persist_delivered_state(
            setup=setup,
            result=result,
            message_id=accepted_message_id,
        )
        record_emitted(
            identity=identity,
            event_type=result.status,
            fingerprint=fingerprint,
        )
        _remove_terminal_retry(setup.trade_id)
        return True

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
    if not delivery.delivered:
        return False

    _persist_delivered_state(
        setup=setup,
        result=result,
        message_id=delivery.message_id,
    )
    record_emitted(
        identity=identity,
        event_type=result.status,
        fingerprint=fingerprint,
    )
    _remove_terminal_retry(setup.trade_id)
    return True


def retry_terminal_pending_notifications(
    *,
    bot_token: str,
    chat_id: str,
) -> tuple[int, int]:
    """Retry terminal alerts from an outbox independent of live setup state."""
    try:
        retries = _load_retries()
    except (OSError, TimeoutError, RegistryIOError):
        return 0, 1

    sent = 0
    failed = 0
    for trade_id, row in list(retries.items()):
        if not isinstance(row, dict):
            failed += 1
            continue
        try:
            setup = PendingSetup(**dict(row.get("setup") or {}))
            result = PendingSetupMonitorResult(**dict(row.get("result") or {}))
            if str(setup.trade_id or "") != str(trade_id):
                raise ValueError("terminal alert outbox trade_id mismatch")
            if _deliver_terminal_retry(
                setup=setup,
                result=result,
                bot_token=bot_token,
                chat_id=chat_id,
            ):
                sent += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return sent, failed


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

    terminal_status = _terminal_status(result.status)
    if terminal_status is not None:
        # Transactional-outbox ordering:
        # 1) queue notification first;
        # 2) persist terminal market truth;
        # 3) deliver only after terminal truth is confirmed.
        # If the process dies between 1 and 2, the retry worker refuses to send
        # until terminal state exists. If it dies after 2, the outbox preserves
        # the notification retry without resurrecting the setup.
        try:
            _queue_terminal_notification(setup, result)
        except (OSError, TimeoutError, RegistryIOError, ValueError):
            record_telegram_suppression(
                identity=identity,
                alert_family="PENDING_SETUP",
                event_type=result.status,
                fingerprint=fingerprint,
                reason="TERMINAL_ALERT_OUTBOX_UNAVAILABLE_FAIL_CLOSED",
                symbol=setup.symbol,
                trade_id=setup.trade_id,
            )
            return False

        terminalized = terminalize_pending_setup(setup.trade_id, terminal_status)
        lifecycle = get_pending_setup_record(setup.trade_id)
        persisted_status = str((lifecycle or {}).get("status") or "")
        if not terminalized and persisted_status != terminal_status:
            if persisted_status and persisted_status != "waiting":
                _remove_terminal_retry(setup.trade_id)
                record_telegram_not_eligible(
                    identity=identity,
                    alert_family="PENDING_SETUP",
                    event_type=result.status,
                    fingerprint=fingerprint,
                    reason=f"TERMINAL_ALERT_SUPERSEDED_BY_{persisted_status.upper()}",
                    symbol=setup.symbol,
                    trade_id=setup.trade_id,
                )
                return False
            record_telegram_suppression(
                identity=identity,
                alert_family="PENDING_SETUP",
                event_type=result.status,
                fingerprint=fingerprint,
                reason="TERMINALIZATION_PENDING",
                symbol=setup.symbol,
                trade_id=setup.trade_id,
            )
            return False

        return _deliver_terminal_retry(
            setup=setup,
            result=result,
            bot_token=bot_token,
            chat_id=chat_id,
        )

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

    state_key = str(setup.trade_id or setup.symbol)
    previous_status = _previous_status(state.get(state_key))
    if previous_status == result.status:
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
        _persist_delivered_state(
            setup=setup,
            result=result,
            message_id=delivery.message_id,
        )
        record_emitted(
            identity=identity,
            event_type=result.status,
            fingerprint=fingerprint,
        )
    return delivery.delivered
