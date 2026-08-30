from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

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
TERMINAL_RETRY_LEASE_SECONDS = 120


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


def terminal_notification_pending(trade_id: str) -> bool:
    """Treat a queued terminal event as a quarantine until lifecycle truth settles."""
    key = str(trade_id or "").strip()
    if not key:
        return False
    try:
        with registry_lock(RETRY_FILE.parent / f".{RETRY_FILE.name}.lock"):
            retries = load_json(RETRY_FILE)
        # Key presence itself is a quarantine signal. A malformed row is not
        # permission to resume entry monitoring; it is unresolved durable state.
        return key in retries
    except (OSError, TimeoutError, RegistryIOError):
        # Fail closed: inability to inspect terminal outbox must not authorize
        # a fresh entry-ready notification.
        return True


def _previous_status(value) -> str | None:
    if isinstance(value, dict):
        raw = value.get("status")
    else:
        raw = value
    return str(raw) if raw not in (None, "") else None


def _parse_utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


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
        f"Setup Score: {int(setup.confidence)}/100 (not probability) | Risk: {setup.risk_level.upper()} | Downside: {downside:.1f}%\n"
        f"Reason: {one_line_reason(result.reason)}\n"
        f"Action: {action}\n"
        "Score is deterministic setup quality, not a calibrated probability."
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
        existing = retries.get(trade_id)
        row = dict(existing) if isinstance(existing, dict) else {}
        row["schema_version"] = 2
        row["trade_id"] = trade_id
        row.setdefault("queued_at", datetime.now(timezone.utc).isoformat())
        row["setup"] = asdict(setup)
        row["result"] = asdict(result)
        # Preserve any active lease if the same terminal condition is observed
        # while another worker is draining the outbox.
        retries[trade_id] = row
        save_json_atomic(RETRY_FILE, retries)


def _claim_terminal_retry(
    trade_id: str,
    *,
    now: datetime | None = None,
) -> tuple[str, dict] | None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with registry_lock(RETRY_FILE.parent / f".{RETRY_FILE.name}.lock"):
        retries = load_json(RETRY_FILE)
        row = retries.get(trade_id)
        if not isinstance(row, dict):
            return None

        lease_until = _parse_utc(row.get("lease_until"))
        if lease_until is not None and lease_until > current:
            return None

        token = uuid4().hex
        claimed = dict(row)
        claimed["lease_token"] = token
        claimed["claimed_at"] = current.isoformat()
        claimed["lease_until"] = (
            current + timedelta(seconds=TERMINAL_RETRY_LEASE_SECONDS)
        ).isoformat()
        retries[trade_id] = claimed
        save_json_atomic(RETRY_FILE, retries)
        return token, dict(claimed)


def _release_terminal_retry(trade_id: str, lease_token: str) -> bool:
    with registry_lock(RETRY_FILE.parent / f".{RETRY_FILE.name}.lock"):
        retries = load_json(RETRY_FILE)
        row = retries.get(trade_id)
        if not isinstance(row, dict) or row.get("lease_token") != lease_token:
            return False
        updated = dict(row)
        updated.pop("lease_token", None)
        updated.pop("claimed_at", None)
        updated.pop("lease_until", None)
        retries[trade_id] = updated
        save_json_atomic(RETRY_FILE, retries)
        return True


def _remove_terminal_retry(
    trade_id: str,
    *,
    lease_token: str | None = None,
) -> bool:
    with registry_lock(RETRY_FILE.parent / f".{RETRY_FILE.name}.lock"):
        retries = load_json(RETRY_FILE)
        row = retries.get(trade_id)
        if not isinstance(row, dict):
            return False
        if lease_token is not None and row.get("lease_token") != lease_token:
            return False
        del retries[trade_id]
        save_json_atomic(RETRY_FILE, retries)
        return True


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


def _deliver_claimed_terminal_retry(
    *,
    setup: PendingSetup,
    result: PendingSetupMonitorResult,
    lease_token: str,
    bot_token: str,
    chat_id: str,
) -> str:
    terminal_status = _terminal_status(result.status)
    identity = f"PENDING_SETUP:{setup.trade_id or setup.symbol}"
    fingerprint = f"{setup.direction}:{result.status}"

    if terminal_status is None:
        _remove_terminal_retry(setup.trade_id, lease_token=lease_token)
        return "NOT_ELIGIBLE"

    lifecycle = get_pending_setup_record(setup.trade_id)
    persisted_status = str((lifecycle or {}).get("status") or "")

    if persisted_status != terminal_status:
        if persisted_status and persisted_status != "waiting":
            _remove_terminal_retry(setup.trade_id, lease_token=lease_token)
            record_telegram_not_eligible(
                identity=identity,
                alert_family="PENDING_SETUP",
                event_type=result.status,
                fingerprint=fingerprint,
                reason=f"TERMINAL_ALERT_SUPERSEDED_BY_{persisted_status.upper()}",
                symbol=setup.symbol,
                trade_id=setup.trade_id,
            )
            return "SUPERSEDED"

        # Crash recovery / transient failure path: the durable outbox is the
        # evidence that a terminal market transition had already been observed.
        # Re-apply that market truth before any Telegram delivery.
        terminalize_pending_setup(setup.trade_id, terminal_status)
        lifecycle = get_pending_setup_record(setup.trade_id)
        persisted_status = str((lifecycle or {}).get("status") or "")

        if persisted_status != terminal_status:
            if persisted_status and persisted_status != "waiting":
                _remove_terminal_retry(setup.trade_id, lease_token=lease_token)
                record_telegram_not_eligible(
                    identity=identity,
                    alert_family="PENDING_SETUP",
                    event_type=result.status,
                    fingerprint=fingerprint,
                    reason=f"TERMINAL_ALERT_SUPERSEDED_BY_{persisted_status.upper()}",
                    symbol=setup.symbol,
                    trade_id=setup.trade_id,
                )
                return "SUPERSEDED"

            _release_terminal_retry(setup.trade_id, lease_token)
            record_telegram_suppression(
                identity=identity,
                alert_family="PENDING_SETUP",
                event_type=result.status,
                fingerprint=fingerprint,
                reason="TERMINALIZATION_PENDING",
                symbol=setup.symbol,
                trade_id=setup.trade_id,
            )
            return "TERMINALIZATION_PENDING"

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
        _remove_terminal_retry(setup.trade_id, lease_token=lease_token)
        return "DELIVERED"

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
        _release_terminal_retry(setup.trade_id, lease_token)
        return "SEND_FAILED"

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
    _remove_terminal_retry(setup.trade_id, lease_token=lease_token)
    return "DELIVERED"


def _process_terminal_retry(
    trade_id: str,
    *,
    bot_token: str,
    chat_id: str,
) -> str:
    claim = _claim_terminal_retry(trade_id)
    if claim is None:
        return "BUSY_OR_MISSING"

    lease_token, row = claim
    try:
        setup = PendingSetup(**dict(row.get("setup") or {}))
        result = PendingSetupMonitorResult(**dict(row.get("result") or {}))
        if str(setup.trade_id or "") != str(trade_id):
            raise ValueError("terminal alert outbox trade_id mismatch")
        return _deliver_claimed_terminal_retry(
            setup=setup,
            result=result,
            lease_token=lease_token,
            bot_token=bot_token,
            chat_id=chat_id,
        )
    except Exception:
        _release_terminal_retry(trade_id, lease_token)
        return "FAILED"


def retry_terminal_pending_notifications(
    *,
    bot_token: str,
    chat_id: str,
) -> tuple[int, int]:
    """Drain terminal alerts with recoverable leases and market-truth recovery."""
    try:
        retries = _load_retries()
    except (OSError, TimeoutError, RegistryIOError):
        return 0, 1

    sent = 0
    failed = 0
    for trade_id, row in list(retries.items()):
        if not isinstance(row, dict):
            # Preserve malformed evidence for operator inspection, keep the
            # setup quarantined, and make the condition operationally visible.
            failed += 1
            continue
        status = _process_terminal_retry(
            str(trade_id),
            bot_token=bot_token,
            chat_id=chat_id,
        )
        if status == "DELIVERED":
            sent += 1
        elif status in {"FAILED", "SEND_FAILED", "TERMINALIZATION_PENDING"}:
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

    if _terminal_status(result.status) is None and terminal_notification_pending(setup.trade_id):
        record_telegram_not_eligible(
            identity=identity,
            alert_family="PENDING_SETUP",
            event_type=result.status,
            fingerprint=fingerprint,
            reason="TERMINAL_TRANSITION_PENDING",
            symbol=setup.symbol,
            trade_id=setup.trade_id,
        )
        return False

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
        # Durable outbox first. Any crash or transient terminalization failure
        # is recovered by the outbox drainer, which re-applies terminal market
        # truth before sending. Lease claiming prevents concurrent duplicate sends.
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

        status = _process_terminal_retry(
            setup.trade_id,
            bot_token=bot_token,
            chat_id=chat_id,
        )
        return status == "DELIVERED"

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