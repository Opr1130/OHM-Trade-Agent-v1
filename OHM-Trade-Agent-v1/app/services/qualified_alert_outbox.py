from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.entry_exit_advisor import EntryExitPlan
from app.services.notification_policy import (
    confirm_emit,
    is_confirmed_emission,
    release_emit,
    reserve_emit,
)
from app.services.pending_setup_registry import (
    get_pending_setup_record,
    terminalize_pending_setup,
)
from app.services.qualified_trade_tracking import (
    ReconciliationIdentityMismatch,
    ReconciliationTrackingDisabled,
    register_reconciliation_intent,
)
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


def _record_malformed_outbox(
    *,
    trade_id: str,
    row: dict | None,
    reason: str,
) -> bool:
    """Audit malformed delivery state and retire its waiting lifecycle.

    The outbox row is safe to delete only after the pending lifecycle is known
    to be non-waiting. Audit failure is observable but does not block the
    lifecycle transition; lifecycle/registry failure keeps the row retryable.
    """
    payload = row if isinstance(row, dict) else {}
    try:
        record_telegram_not_eligible(
            identity=str(
                payload.get("identity")
                or f"QUALIFIED_OPPORTUNITY:{trade_id}"
            ),
            alert_family="QUALIFIED_OPPORTUNITY",
            event_type=str(payload.get("action") or "ACTION"),
            fingerprint=str(payload.get("fingerprint") or ""),
            reason=reason,
            symbol=str(
                payload.get("symbol")
                or (
                    payload.get("plan", {}).get("symbol")
                    if isinstance(payload.get("plan"), dict)
                    else ""
                )
            ),
            journey_id=payload.get("journey_id"),
            signal_id=payload.get("signal_id"),
            trade_id=trade_id,
        )
    except Exception as exc:
        print(
            "O'Pip malformed-outbox audit failed:",
            f"trade_id={trade_id}",
            f"{type(exc).__name__}: {exc}",
        )

    try:
        lifecycle = get_pending_setup_record(trade_id)
        lifecycle_status = str((lifecycle or {}).get("status") or "")
        if lifecycle is None or lifecycle_status != "waiting":
            return True
        if terminalize_pending_setup(trade_id, "delivery_malformed"):
            return True
        refreshed = get_pending_setup_record(trade_id)
        return str((refreshed or {}).get("status") or "") != "waiting"
    except Exception as exc:
        print(
            "O'Pip malformed-outbox lifecycle transition failed:",
            f"trade_id={trade_id}",
            f"{type(exc).__name__}: {exc}",
        )
        return False


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
        direction = str(
            row.get("direction") or plan.direction or "LONG"
        ).upper()
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("qualified alert outbox direction is invalid")
        action = str(row.get("action") or "")
        candidate = dict(row.get("tracking_candidate") or {})
        leverage = float(
            row.get("leverage")
            or (2.0 if direction == "SHORT" else 1.0)
        )
        if not math.isfinite(leverage) or leverage <= 0:
            raise ValueError("qualified alert outbox leverage is invalid")
    except Exception as exc:
        retired = _record_malformed_outbox(
            trade_id=trade_id,
            row=row,
            reason=f"OUTBOX_MALFORMED:{type(exc).__name__}",
        )
        if retired:
            _remove(trade_id, token=lease_token)
            return "MALFORMED"
        _release(trade_id, lease_token)
        return "MALFORMED_PENDING"

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
        except ReconciliationTrackingDisabled:
            terminalize_pending_setup(trade_id, "tracking_disabled")
            _remove(trade_id, token=lease_token)
            record_telegram_suppression(
                identity=str(row.get("identity") or f"QUALIFIED_OPPORTUNITY:{trade_id}"),
                alert_family="QUALIFIED_OPPORTUNITY",
                event_type=action or "ACTION",
                fingerprint=str(row.get("fingerprint") or ""),
                reason="RECONCILIATION_NOT_APPLY_TERMINAL",
                symbol=plan.symbol,
                journey_id=row.get("journey_id"),
                signal_id=row.get("signal_id"),
                trade_id=trade_id,
            )
            return "SUPPRESSED"
        except ReconciliationIdentityMismatch as exc:
            try:
                transitioned = terminalize_pending_setup(
                    trade_id,
                    "tracking_failed",
                )
                lifecycle_after = get_pending_setup_record(trade_id)
                lifecycle_after_status = str(
                    (lifecycle_after or {}).get("status") or ""
                )
            except Exception as transition_exc:
                transitioned = False
                lifecycle_after_status = "waiting"
                print(
                    "O'Pip reconciliation-mismatch terminalization failed:",
                    f"trade_id={trade_id}",
                    f"{type(transition_exc).__name__}: {transition_exc}",
                )

            if transitioned or lifecycle_after_status != "waiting":
                _remove(trade_id, token=lease_token)
                record_telegram_suppression(
                    identity=str(
                        row.get("identity")
                        or f"QUALIFIED_OPPORTUNITY:{trade_id}"
                    ),
                    alert_family="QUALIFIED_OPPORTUNITY",
                    event_type=action or "ACTION",
                    fingerprint=str(row.get("fingerprint") or ""),
                    reason="TRACKING_IDENTITY_MISMATCH_TERMINAL",
                    symbol=plan.symbol,
                    journey_id=row.get("journey_id"),
                    signal_id=row.get("signal_id"),
                    trade_id=trade_id,
                )
                return "SUPPRESSED"

            _release(trade_id, lease_token)
            record_telegram_suppression(
                identity=str(
                    row.get("identity")
                    or f"QUALIFIED_OPPORTUNITY:{trade_id}"
                ),
                alert_family="QUALIFIED_OPPORTUNITY",
                event_type=action or "ACTION",
                fingerprint=str(row.get("fingerprint") or ""),
                reason=(
                    "TRACKING_IDENTITY_MISMATCH_TERMINALIZATION_PENDING:"
                    f"{type(exc).__name__}"
                ),
                symbol=plan.symbol,
                journey_id=row.get("journey_id"),
                signal_id=row.get("signal_id"),
                trade_id=trade_id,
            )
            return "TRACKING_PENDING"
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
        if is_confirmed_emission(
            identity=policy_identity,
            event_type="ACTIONABLE_TRADE",
            fingerprint=fingerprint,
        ):
            _remove(trade_id, token=lease_token)
            return "DELIVERED"
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
            retired = _record_malformed_outbox(
                trade_id=str(trade_id),
                row=None,
                reason="OUTBOX_MALFORMED:NON_DICT_ROW",
            )
            if not retired:
                pending += 1
                continue
            try:
                with registry_lock(_lock_file()):
                    current = load_json(OUTBOX_FILE)
                    current.pop(str(trade_id), None)
                    save_json_atomic(OUTBOX_FILE, current)
            except (OSError, TimeoutError, RegistryIOError):
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
        elif status not in {
            "SUPERSEDED",
            "SUPPRESSED",
            "MALFORMED",
            "BUSY_OR_MISSING",
        }:
            pending += 1
    return delivered, pending