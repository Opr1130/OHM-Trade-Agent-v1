from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.entry_exit_advisor import EntryExitPlan
from app.services.notification_policy import (
    confirm_emit,
    release_emit,
    reserve_emit,
)
from app.services.pending_setup_registry import get_pending_setup_record
from app.services.qualified_trade_tracking import register_reconciliation_intent
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic
from app.services.telegram_delivery import (
    accepted_delivery_message_id,
    record_telegram_not_eligible,
    record_telegram_suppression,
    send_tracked_telegram,
)


OUTBOX_FILE = Path("/app/data/qualified_alert_outbox.json")
LEASE_SECONDS = 120


def _lock_file() -> Path:
    return OUTBOX_FILE.parent / f".{OUTBOX_FILE.name}.lock"


def _parse_utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def queue_qualified_alert(
    *,
    trade_id: str,
    message: str,
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    action: str,
    direction: str,
    identity: str,
    fingerprint: str,
    reason: str,
) -> None:
    """Persist an actionable decision whose tracking/delivery is incomplete.

    This is transport/operational state only. It never changes the underlying
    trade qualification or terminalizes the pending setup.
    """
    key = str(trade_id or "").strip()
    if not key:
        raise ValueError("qualified alert outbox requires trade_id")
    now = datetime.now(timezone.utc).isoformat()
    with registry_lock(_lock_file()):
        rows = load_json(OUTBOX_FILE)
        existing = rows.get(key)
        row = dict(existing) if isinstance(existing, dict) else {}
        row["schema_version"] = 1
        row["trade_id"] = key
        row.setdefault("queued_at", now)
        row["updated_at"] = now
        row["reason"] = str(reason)
        row["message"] = str(message)
        row["action"] = str(action)
        row["direction"] = str(direction).upper()
        row["identity"] = str(identity)
        row["policy_identity"] = f"{str(direction).upper()}:{plan.symbol}"
        row["fingerprint"] = str(fingerprint)
        row["symbol"] = plan.symbol
        row["plan"] = asdict(plan)
        row["tracking_candidate"] = {
            "economic_qualified": candidate.get("economic_qualified") is True,
            "recommended_capital": candidate.get("recommended_capital"),
        }
        row["leverage"] = float(
            candidate.get("margin_leverage")
            or (2.0 if str(direction).upper() == "SHORT" else 1.0)
        )
        row["journey_id"] = candidate.get("journey_id")
        row["signal_id"] = candidate.get("signal_id")
        # Preserve an active lease if the same actionable decision is observed
        # while another worker is attempting recovery.
        rows[key] = row
        save_json_atomic(OUTBOX_FILE, rows)


def _claim(trade_id: str, *, now: datetime | None = None) -> tuple[str, dict] | None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with registry_lock(_lock_file()):
        rows = load_json(OUTBOX_FILE)
        row = rows.get(trade_id)
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
            current + timedelta(seconds=LEASE_SECONDS)
        ).isoformat()
        rows[trade_id] = claimed
        save_json_atomic(OUTBOX_FILE, rows)
        return token, claimed


def _release(trade_id: str, token: str) -> bool:
    with registry_lock(_lock_file()):
        rows = load_json(OUTBOX_FILE)
        row = rows.get(trade_id)
        if not isinstance(row, dict) or row.get("lease_token") != token:
            return False
        updated = dict(row)
        for key in ("lease_token", "claimed_at", "lease_until"):
            updated.pop(key, None)
        rows[trade_id] = updated
        save_json_atomic(OUTBOX_FILE, rows)
        return True


def _remove(trade_id: str, *, token: str | None = None) -> bool:
    with registry_lock(_lock_file()):
        rows = load_json(OUTBOX_FILE)
        row = rows.get(trade_id)
        if not isinstance(row, dict):
            return False
        if token is not None and row.get("lease_token") != token:
            return False
        del rows[trade_id]
        save_json_atomic(OUTBOX_FILE, rows)
        return True


def _retry_one(
    trade_id: str,
    *,
    bot_token: str,
    chat_id: str,
) -> str:
    claim = _claim(trade_id)
    if claim is None:
        return "BUSY_OR_MISSING"
    lease_token, row = claim

    try:
        plan = EntryExitPlan(**dict(row.get("plan") or {}))
        if str(row.get("trade_id") or "") != trade_id:
            raise ValueError("qualified alert outbox trade_id mismatch")
    except Exception:
        _remove(trade_id, token=lease_token)
        return "MALFORMED"

    lifecycle = get_pending_setup_record(trade_id)
    lifecycle_status = str((lifecycle or {}).get("status") or "")
    if lifecycle_status != "waiting":
        _remove(trade_id, token=lease_token)
        record_telegram_not_eligible(
            identity=str(row.get("identity") or f"QUALIFIED_OPPORTUNITY:{trade_id}"),
            alert_family="QUALIFIED_OPPORTUNITY",
            event_type=str(row.get("action") or "ACTION"),
            fingerprint=str(row.get("fingerprint") or ""),
            reason=(
                f"OUTBOX_SUPERSEDED_BY_{lifecycle_status.upper()}"
                if lifecycle_status
                else "OUTBOX_LIFECYCLE_MISSING"
            ),
            symbol=plan.symbol,
            journey_id=row.get("journey_id"),
            signal_id=row.get("signal_id"),
            trade_id=trade_id,
        )
        return "SUPERSEDED"

    direction = str(row.get("direction") or plan.direction or "LONG").upper()
    action = str(row.get("action") or "")
    candidate = dict(row.get("tracking_candidate") or {})
    leverage = float(row.get("leverage") or (2.0 if direction == "SHORT" else 1.0))

    if candidate.get("economic_qualified") is True:
        try:
            register_reconciliation_intent(
                candidate=candidate,
                plan=plan,
                action=action,
                direction=direction,
                leverage=leverage,
                trade_id=trade_id,
            )
        except Exception as exc:
            _release(trade_id, lease_token)
            record_telegram_suppression(
                identity=str(row.get("identity") or f"QUALIFIED_OPPORTUNITY:{trade_id}"),
                alert_family="QUALIFIED_OPPORTUNITY",
                event_type=action or "ACTION",
                fingerprint=str(row.get("fingerprint") or ""),
                reason=f"TRACKING_PENDING_RETRYABLE:{type(exc).__name__}",
                symbol=plan.symbol,
                journey_id=row.get("journey_id"),
                signal_id=row.get("signal_id"),
                trade_id=trade_id,
            )
            return "TRACKING_PENDING"

    identity = str(row.get("identity") or f"QUALIFIED_OPPORTUNITY:{trade_id}")
    fingerprint = str(row.get("fingerprint") or "")
    event_type = action or "ACTION"

    accepted = accepted_delivery_message_id(
        identity=identity,
        event_type=event_type,
        fingerprint=fingerprint,
    )
    if accepted is not None:
        _remove(trade_id, token=lease_token)
        return "DELIVERED"

    policy_identity = str(row.get("policy_identity") or f"{direction}:{plan.symbol}")
    reservation = reserve_emit(
        identity=policy_identity,
        event_type="ACTIONABLE_TRADE",
        fingerprint=fingerprint,
    )
    if reservation is None:
        _release(trade_id, lease_token)
        return "POLICY_PENDING"

    delivery = send_tracked_telegram(
        bot_token=bot_token,
        chat_id=chat_id,
        message=str(row.get("message") or ""),
        identity=identity,
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type=event_type,
        fingerprint=fingerprint,
        symbol=plan.symbol,
        journey_id=row.get("journey_id"),
        signal_id=row.get("signal_id"),
        trade_id=trade_id,
    )
    if not delivery.delivered:
        release_emit(
            identity=policy_identity,
            event_type="ACTIONABLE_TRADE",
            reservation_token=reservation,
        )
        _release(trade_id, lease_token)
        return "SEND_FAILED"

    confirm_emit(
        identity=policy_identity,
        event_type="ACTIONABLE_TRADE",
        fingerprint=fingerprint,
        reservation_token=reservation,
    )
    _remove(trade_id, token=lease_token)
    return "DELIVERED"


def retry_qualified_alerts(
    *,
    bot_token: str,
    chat_id: str,
) -> tuple[int, int]:
    """Retry operationally blocked qualified alerts without rescanning markets."""
    try:
        with registry_lock(_lock_file()):
            rows = load_json(OUTBOX_FILE)
    except (OSError, TimeoutError, RegistryIOError):
        return 0, 1

    delivered = 0
    pending = 0
    for trade_id, row in list(rows.items()):
        if not isinstance(row, dict):
            pending += 1
            continue
        try:
            status = _retry_one(
                str(trade_id),
                bot_token=bot_token,
                chat_id=chat_id,
            )
        except Exception:
            pending += 1
            continue
        if status == "DELIVERED":
            delivered += 1
        elif status not in {"SUPERSEDED", "BUSY_OR_MISSING"}:
            pending += 1
    return delivered, pending