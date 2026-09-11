"""Early Watch bridge: Telegram outcome → canonical ACK → JSON ops.

JSON remains operational alert-control authority.
SQLite is canonical evidence authority for the named transition.
Default mode is off; shadow does not grant production influence.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.opip.canonical.client import CanonicalWriterClient, WriterClient
from app.opip.canonical.gap_spool import (
    GapSpoolError,
    append_capture_gap,
    bump_gap_retry,
    evidence_window_incomplete,
    load_gap_spool,
    resolve_gap,
)
from app.opip.canonical.models import WriterAck, WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION, STATE_FAMILY_EARLY_WATCH
from app.services.alert_governor import (
    STATE_FILE,
    record_opportunity_alert,
    release_opportunity_alert_reservation,
)
from app.services.registry_io import RegistryIOError, load_json

logger = logging.getLogger(__name__)

_client_override: WriterClient | None = None


def set_writer_client_for_tests(client: WriterClient | None) -> None:
    global _client_override
    _client_override = client


def resolve_writer_mode(settings: Any | None = None) -> str:
    if settings is not None:
        mode = str(getattr(settings, "opip_canonical_writer_mode", "off") or "off")
    else:
        try:
            from app.core.config import get_settings

            mode = str(get_settings().opip_canonical_writer_mode or "off")
        except Exception:
            mode = "off"
    mode = mode.strip().lower()
    return mode if mode in {"off", "shadow"} else "off"


def shadow_capture_enabled(settings: Any | None = None) -> bool:
    return resolve_writer_mode(settings) == "shadow"


def resolve_state_target(
    *,
    state_family: str | None = None,
    state_file: Path | None = None,
) -> Path:
    """Map a bounded family (or local override for tests) to a governor JSON path."""
    if state_file is not None:
        # Local/unit callers may pass an explicit temp state file. IPC recovery
        # never trusts arbitrary remote paths — only state_family.
        return Path(state_file)
    family = str(state_family or STATE_FAMILY_EARLY_WATCH).strip()
    if family == STATE_FAMILY_EARLY_WATCH:
        return STATE_FILE
    raise ValueError(f"unsupported state_family={family!r}")


def _client() -> WriterClient:
    if _client_override is not None:
        return _client_override
    return CanonicalWriterClient()


def idempotency_key_for_record(
    *,
    created_new: bool,
    reservation_token: str | None,
    scan_id: str,
    identity: str,
    transition_key: str,
) -> str:
    if created_new:
        token = str(reservation_token or "").strip()
        if not token:
            raise ValueError("reservation_token required for CREATE")
        return f"ag:v1:early_watch:CREATE:{token}"
    return f"ag:v1:early_watch:EDIT:{scan_id}:{identity}:{transition_key}"


def idempotency_key_for_release(reservation_token: str) -> str:
    return f"ag:v1:early_watch:RELEASE:{reservation_token}"


def _record_postcondition_met(
    target: Path,
    *,
    identity: str,
    transition_key: str,
    message_id: int,
) -> bool:
    try:
        state = load_json(target)
    except (OSError, TimeoutError, RegistryIOError):
        return False
    current = (state.get("identities") or {}).get(identity) or {}
    if str(current.get("transition_key") or "") != transition_key:
        return False
    try:
        return int(current.get("message_id")) == int(message_id)
    except (TypeError, ValueError):
        return False


def _release_postcondition_met(target: Path, *, reservation_token: str) -> bool:
    try:
        state = load_json(target)
    except (OSError, TimeoutError, RegistryIOError):
        return False
    reservations = state.get("new_card_reservations") or {}
    return str(reservation_token) not in reservations


def _confirm_if_verified(
    *,
    event_id: str | None,
    verified: bool,
) -> bool:
    if not event_id or not verified:
        return False
    try:
        ack = _client().confirm_ops_applied(event_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("confirm_ops_applied failed: %s", type(exc).__name__)
        return False
    if ack.status not in {"OK", "DUPLICATE_OK"}:
        logger.warning(
            "confirm_ops_applied rejected status=%s code=%s",
            ack.status,
            ack.error_code,
        )
        return False
    return True


def durable_record_opportunity_alert(
    *,
    identity: str,
    transition_key: str,
    message_id: int,
    created_new: bool,
    scan_id: str,
    reservation_token: str | None = None,
    state_file: Path | None = None,
    settings: Any | None = None,
) -> WriterAck | None:
    """Canonical ACK (when shadow) then JSON record. JSON always updates on ops path."""
    target = resolve_state_target(
        state_family=STATE_FAMILY_EARLY_WATCH,
        state_file=state_file,
    )
    if not shadow_capture_enabled(settings):
        record_opportunity_alert(
            identity=identity,
            transition_key=transition_key,
            message_id=message_id,
            created_new=created_new,
            reservation_token=reservation_token,
            state_file=target,
        )
        return None

    key = idempotency_key_for_record(
        created_new=created_new,
        reservation_token=reservation_token,
        scan_id=scan_id,
        identity=identity,
        transition_key=transition_key,
    )
    intent = WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority="NORMAL",
        idempotency_key=key,
        event_type="alert_governor.transition.recorded",
        payload={
            "identity": identity,
            "transition_key": transition_key,
            "message_id": int(message_id),
            "created_new": bool(created_new),
            "scan_id": scan_id,
            "reservation_token": reservation_token,
        },
        correlation_id=scan_id,
        ops_handoff={
            "operation": "RECORD",
            "identity": identity,
            "transition_key": transition_key,
            "message_id": int(message_id),
            "created_new": bool(created_new),
            "reservation_token": reservation_token,
            "state_family": STATE_FAMILY_EARLY_WATCH,
        },
    )

    ack: WriterAck | None = None
    writer_committed = False
    try:
        ack = _client().submit(intent)
    except Exception as exc:  # noqa: BLE001 — evidence fail-closed; ops continue
        append_capture_gap(
            idempotency_key=key,
            scan_id=scan_id,
            identity=identity,
            intended_event_type="alert_governor.transition.recorded",
            error_code=type(exc).__name__,
        )
        logger.warning("canonical writer submit failed: %s", type(exc).__name__)
        ack = WriterAck(status="RETRYABLE", error_code=type(exc).__name__)
    else:
        if ack.status in {"OK", "DUPLICATE_OK"}:
            writer_committed = True
        else:
            append_capture_gap(
                idempotency_key=key,
                scan_id=scan_id,
                identity=identity,
                intended_event_type="alert_governor.transition.recorded",
                error_code=str(ack.error_code or ack.status),
            )

    # JSON remains operational authority even when evidence capture fails.
    record_opportunity_alert(
        identity=identity,
        transition_key=transition_key,
        message_id=message_id,
        created_new=created_new,
        reservation_token=reservation_token,
        state_file=target,
    )

    if writer_committed:
        verified = _record_postcondition_met(
            target,
            identity=identity,
            transition_key=transition_key,
            message_id=message_id,
        )
        if not _confirm_if_verified(event_id=ack.event_id if ack else None, verified=verified):
            logger.warning(
                "leaving handoff PENDING after record verify_failed=%s event_id=%s",
                not verified,
                ack.event_id if ack else None,
            )
    return ack


def durable_release_opportunity_alert_reservation(
    reservation_token: str | None,
    *,
    scan_id: str,
    identity: str = "",
    transition_key: str = "",
    state_file: Path | None = None,
    settings: Any | None = None,
) -> WriterAck | None:
    target = resolve_state_target(
        state_family=STATE_FAMILY_EARLY_WATCH,
        state_file=state_file,
    )
    if not reservation_token:
        return None
    if not shadow_capture_enabled(settings):
        release_opportunity_alert_reservation(reservation_token, state_file=target)
        return None

    key = idempotency_key_for_release(str(reservation_token))
    intent = WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority="NORMAL",
        idempotency_key=key,
        event_type="alert_governor.reservation.released",
        payload={
            "identity": identity,
            "transition_key": transition_key,
            "reservation_token": reservation_token,
            "scan_id": scan_id,
            "created_new": False,
        },
        correlation_id=scan_id,
        ops_handoff={
            "operation": "RELEASE",
            "identity": identity,
            "transition_key": transition_key,
            "message_id": None,
            "created_new": False,
            "reservation_token": reservation_token,
            "state_family": STATE_FAMILY_EARLY_WATCH,
        },
    )

    ack: WriterAck | None = None
    writer_committed = False
    try:
        ack = _client().submit(intent)
    except Exception as exc:  # noqa: BLE001
        append_capture_gap(
            idempotency_key=key,
            scan_id=scan_id,
            identity=identity,
            intended_event_type="alert_governor.reservation.released",
            error_code=type(exc).__name__,
        )
        ack = WriterAck(status="RETRYABLE", error_code=type(exc).__name__)
    else:
        if ack.status in {"OK", "DUPLICATE_OK"}:
            writer_committed = True
        else:
            append_capture_gap(
                idempotency_key=key,
                scan_id=scan_id,
                identity=identity,
                intended_event_type="alert_governor.reservation.released",
                error_code=str(ack.error_code or ack.status),
            )

    release_opportunity_alert_reservation(reservation_token, state_file=target)

    if writer_committed:
        verified = _release_postcondition_met(
            target, reservation_token=str(reservation_token)
        )
        if not _confirm_if_verified(event_id=ack.event_id if ack else None, verified=verified):
            logger.warning(
                "leaving handoff PENDING after release verify_failed=%s event_id=%s",
                not verified,
                ack.event_id if ack else None,
            )
    return ack


def reconcile_pending_ops_handoffs(
    *,
    settings: Any | None = None,
    state_file: Path | None = None,
) -> dict[str, int]:
    """Reconcile PENDING handoffs before new Early Watch alert evaluation."""
    stats = {"applied": 0, "superseded": 0, "replayed": 0, "errors": 0, "pending": 0}
    if not shadow_capture_enabled(settings):
        return stats
    try:
        handoffs = _client().list_pending_handoffs()
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_pending_handoffs failed: %s", type(exc).__name__)
        stats["errors"] += 1
        return stats

    for handoff in handoffs:
        try:
            # Never trust arbitrary IPC paths; resolve only known families.
            # Tests may pass state_file to override the Early Watch target.
            target = resolve_state_target(
                state_family=str(handoff.state_file or STATE_FAMILY_EARLY_WATCH),
                state_file=state_file,
            )
            disposition = _reconcile_one(handoff, target=target)
            stats[disposition] = int(stats.get(disposition, 0)) + 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "handoff reconcile failed event_id=%s err=%s",
                handoff.event_id,
                type(exc).__name__,
            )
            stats["errors"] += 1
    return stats


def reconcile_capture_gap_spool(*, settings: Any | None = None) -> dict[str, int]:
    """Promote unresolved spool rows into canonical coverage-gap evidence."""
    stats = {"resolved": 0, "errors": 0, "incomplete": 0}
    if not shadow_capture_enabled(settings):
        return stats
    try:
        spool = load_gap_spool()
    except GapSpoolError as exc:
        logger.error("capture gap spool unreadable; fail closed: %s", exc)
        stats["errors"] += 1
        stats["incomplete"] = 1
        return stats
    if spool["unresolved"]:
        stats["incomplete"] = len(spool["unresolved"])
    for row in list(spool["unresolved"]):
        gap_id = str(row.get("gap_id") or "")
        key = f"ag:v1:early_watch:CAPTURE_GAP:{gap_id}"
        intent = WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="HIGH",
            idempotency_key=key,
            event_type="alert_governor.capture_gap.recorded",
            payload={
                "gap_id": gap_id,
                "idempotency_key": row.get("idempotency_key"),
                "scan_id": row.get("scan_id"),
                "identity": row.get("identity"),
                "intended_event_type": row.get("intended_event_type"),
                "observed_at": row.get("observed_at"),
                "error_code": row.get("error_code"),
                "retry_count": row.get("retry_count"),
                "evidence_window": "INCOMPLETE",
            },
            ops_handoff=None,
        )
        try:
            ack = _client().submit(intent)
        except Exception:
            try:
                bump_gap_retry(gap_id)
            except GapSpoolError:
                stats["errors"] += 1
                return stats
            stats["errors"] += 1
            continue
        if ack.status in {"OK", "DUPLICATE_OK"}:
            try:
                resolve_gap(gap_id)
            except GapSpoolError:
                stats["errors"] += 1
                return stats
            stats["resolved"] += 1
        else:
            try:
                bump_gap_retry(gap_id)
            except GapSpoolError:
                stats["errors"] += 1
                return stats
            stats["errors"] += 1
    try:
        if evidence_window_incomplete():
            stats["incomplete"] = len(load_gap_spool()["unresolved"])
    except GapSpoolError:
        stats["errors"] += 1
        stats["incomplete"] = max(int(stats["incomplete"]), 1)
    return stats


def _reconcile_one(handoff: Any, *, target: Path) -> str:
    client = _client()
    try:
        state = load_json(target)
    except (OSError, TimeoutError, RegistryIOError) as exc:
        raise RuntimeError("state unavailable") from exc

    identities = state.get("identities") or {}
    current = identities.get(handoff.identity) or {}
    reservations = state.get("new_card_reservations") or {}

    if handoff.operation == "RECORD":
        cur_key = str(current.get("transition_key") or "")
        cur_msg = current.get("message_id")
        if (
            cur_key == handoff.transition_key
            and cur_msg is not None
            and int(cur_msg) == int(handoff.message_id or -1)
        ):
            ack = client.confirm_ops_applied(handoff.event_id)
            if ack.status not in {"OK", "DUPLICATE_OK"}:
                return "pending"
            return "applied"
        if cur_key and cur_key != handoff.transition_key:
            ack = client.mark_handoff_superseded(handoff.event_id)
            if ack.status not in {"OK", "DUPLICATE_OK"}:
                return "pending"
            return "superseded"

    if handoff.operation == "RELEASE":
        token = str(handoff.reservation_token or "")
        if token and token not in reservations:
            ack = client.confirm_ops_applied(handoff.event_id)
            if ack.status not in {"OK", "DUPLICATE_OK"}:
                return "pending"
            return "applied"

    # Replay, then verify before confirm.
    if handoff.operation == "RECORD":
        if handoff.message_id is None:
            raise RuntimeError("RECORD handoff missing message_id")
        record_opportunity_alert(
            identity=handoff.identity,
            transition_key=handoff.transition_key,
            message_id=int(handoff.message_id),
            created_new=bool(handoff.created_new),
            reservation_token=handoff.reservation_token,
            state_file=target,
        )
        verified = _record_postcondition_met(
            target,
            identity=handoff.identity,
            transition_key=handoff.transition_key,
            message_id=int(handoff.message_id),
        )
    else:
        release_opportunity_alert_reservation(
            handoff.reservation_token,
            state_file=target,
        )
        verified = _release_postcondition_met(
            target, reservation_token=str(handoff.reservation_token or "")
        )

    if not verified:
        return "pending"
    ack = client.confirm_ops_applied(handoff.event_id)
    if ack.status not in {"OK", "DUPLICATE_OK"}:
        return "pending"
    return "replayed"
