"""Tradevisor V2 durable advisory inbox.

TRANSPORT AND MEASUREMENT STATE ONLY - NO TRADING AUTHORITY.

What this module owns
---------------------
The durable receipt of a validated Tradevisor alert and its explicit
disposition. It is the only place a Tradevisor observation is written, so
there is no second evidence store, no competing writer, and no silent drop.

What it does NOT own
--------------------
Qualification, geometry, capital, risk, notification policy and learning
remain with their existing owners. This module never places, confirms,
cancels or sizes anything, and it never admits a symbol into native selection.

Delivery semantics
------------------
Receipt is at-least-once at the transport layer and effectively-once at the
logical layer: ``dedup_id`` is derived from source facts only, so a replayed
alert with a fresh receipt timestamp collapses onto the same row. A
successful webhook acknowledgement means "durably received", never
"authorized".
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import get_settings
from app.opip.learning.job_disposition import RELEASE_CURRENT
from app.services.registry_io import load_json, registry_lock, save_json_atomic
from app.services.tradevisor_ingress import (
    DISPOSITION_ACCEPTED,
    TradevisorSignal,
    signal_from_record,
)

logger = logging.getLogger(__name__)

INBOX_FILE = Path("/app/data/tradevisor_v1_inbox.json")
LOCK_FILE = INBOX_FILE.parent / ".tradevisor_v1_inbox.lock"

#: Terminal dispositions. Only these are eligible for retention pruning, so a
#: row that died mid-processing is reclaimed rather than silently aged out.
TERMINAL = {"PROCESSED", "REJECTED", "DUPLICATE", "POISONED"}
#: Rows a worker may claim.
ACTIVE = {"QUEUED", "RETRY"}

STATUS_QUEUED = "QUEUED"
STATUS_RETRY = "RETRY"
STATUS_PROCESSING = "PROCESSING"
STATUS_PROCESSED = "PROCESSED"
STATUS_REJECTED = "REJECTED"
STATUS_POISONED = "POISONED"

#: A durable per-record consumption acknowledgement. ``LEARNING_TERMINAL_NON_MEASURABLE``
#: records that the evidence can never become a measurable outcome; it is not a
#: measurement claim.
CONSUMPTION_LEARNING_CONSUMED = "LEARNING_CONSUMED"
CONSUMPTION_TERMINAL_NON_MEASURABLE = "LEARNING_TERMINAL_NON_MEASURABLE"
CONSUMPTION_DISPOSITIONS = frozenset(
    {CONSUMPTION_LEARNING_CONSUMED, CONSUMPTION_TERMINAL_NON_MEASURABLE}
)
#: Only a definitive measurement may authorize deletion of learning evidence.
DEFINITIVE_MEASUREMENT_STATUSES = frozenset({"MEASURED", "NO_ENTRY_TOUCH"})
#: Terminal states whose evidence can never contribute to learning: a poisoned
#: row never produced a frozen decision, so it has no measurable hypothesis.
NON_LEARNING_TERMINAL = {STATUS_POISONED}


class TradevisorInboxFullError(RuntimeError):
    """The bounded durable inbox has no capacity. The caller must back off."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _empty_registry() -> dict:
    return {"schema_version": 1, "events": {}, "metrics": {}}


def _parse(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _row_from_signal(signal: TradevisorSignal, now: datetime) -> dict:
    return {
        "schema_version": 1,
        "dedup_id": signal.dedup_id,
        "status": STATUS_QUEUED,
        "attempts": 0,
        "received_at": signal.received_at.isoformat(),
        "enqueued_at": now.isoformat(),
        "processing_started_at": None,
        #: Fencing token for the worker that currently owns settlement. A claim
        #: is only queued/retry until a drain mints a token for itself.
        "claim_token": None,  # nosec B105 - a claim fence, not a credential
        "processed_at": None,
        "final_disposition": None,
        "failure_reason": None,
        "decision": None,
        "decision_record": None,
        "notification": None,
        "outcome": None,
        #: Durable learning-consumption disposition. ``None`` means "not yet
        #: consumed", so the record is not eligible for retention pruning.
        "consumption": None,
        # The normalized signal is the durable evidence of what arrived. The
        # ingestion credential never reaches this point: it is stripped before
        # validation and the wire model forbids unknown fields.
        "signal": {
            "dedup_id": signal.dedup_id,
            "signal": signal.signal,
            "instrument": signal.instrument,
            "market": signal.market,
            "source_exchange": signal.source_exchange,
            "source_symbol": signal.source_symbol,
            "timeframe_minutes": signal.timeframe_minutes,
            "bar_opened_at": signal.bar_opened_at.isoformat(),
            "bar_closed_at": signal.bar_closed_at.isoformat(),
            "triggered_at": signal.triggered_at.isoformat(),
            "received_at": signal.received_at.isoformat(),
            "signal_price": signal.signal_price,
            "alert_config_version": signal.alert_config_version,
            "disposition": signal.disposition or DISPOSITION_ACCEPTED,
            # Observation provenance must survive the durable round trip: a row
            # reloaded for worker evaluation or reclamation would otherwise lose
            # what the external script claimed (and what O'Pip could not verify).
            "provenance": dict(getattr(signal, "provenance", {}) or {}),
        },
    }


def _consumption_ack_is_valid(row: dict) -> bool:
    """A consumption acknowledgement is durable evidence, so it fails closed.

    It must name the exact record, carry an allowed disposition, be backed by a
    *definitive* measurement, and have been recorded against an exact-SHA
    current release. Anything else is treated as "not consumed".
    """
    ack = row.get("consumption")
    if not isinstance(ack, dict):
        return False
    dedup = str(row.get("dedup_id") or "")
    if not dedup or str(ack.get("dedup_id") or "") != dedup:
        return False
    if str(ack.get("evidence_id") or "") == "":
        return False
    if str(ack.get("disposition") or "") not in CONSUMPTION_DISPOSITIONS:
        return False
    if str(ack.get("measurement_status") or "") not in DEFINITIVE_MEASUREMENT_STATUSES:
        return False
    if str(ack.get("release_compatibility") or "") != RELEASE_CURRENT:
        return False
    return _parse(ack.get("acknowledged_at")) is not None


def _is_learning_evidence(row: dict) -> bool:
    """True when this row can still become measurable learning evidence."""
    if row.get("status") not in TERMINAL:
        return True
    return row.get("status") not in NON_LEARNING_TERMINAL


def _retention_eligible(row: dict) -> bool:
    """Only a terminal non-learning row or an acknowledged row may be pruned."""
    if row.get("status") in NON_LEARNING_TERMINAL:
        return True
    return _consumption_ack_is_valid(row)


def _prune(events: dict, *, now: datetime, retention_seconds: int) -> int:
    """Age out rows whose evidence is provably no longer needed.

    Terminal *transport* status is not proof of learning consumption: a
    ``PROCESSED`` row can still owe a 24-hour prospective measurement. Retention
    therefore requires either an explicitly non-measurable disposition or a
    valid durable consumption acknowledgement. Unacknowledged evidence is kept
    even when it is older than the retention window, and capacity is enforced by
    existing backpressure instead of silent eviction.
    """
    cutoff = now - timedelta(seconds=retention_seconds)
    removed = 0
    for key, row in list(events.items()):
        if not isinstance(row, dict) or row.get("status") not in TERMINAL:
            continue
        if not _retention_eligible(row):
            continue
        stamp = _parse(row.get("processed_at")) or _parse(row.get("enqueued_at"))
        if stamp is not None and stamp < cutoff:
            events.pop(key, None)
            removed += 1
    return removed


def record_consumption_ack(
    dedup_id: str,
    *,
    evidence_id: str,
    measurement_status: str,
    release_compatibility: str,
    disposition: str = CONSUMPTION_LEARNING_CONSUMED,
    now: datetime | None = None,
) -> bool:
    """Record a durable per-record learning-consumption disposition.

    This is the only write path for consumption bookkeeping, and it touches only
    ``row["consumption"]``. It is idempotent: re-acknowledging the same evidence
    keeps the original timestamp, so a crash after the write and before cleanup
    cannot produce a duplicate logical consumption. Invalid input or a
    non-terminal row returns ``False`` without writing - consumption is never
    fabricated.
    """
    now = now or _now()
    dedup = str(dedup_id or "").strip()
    if not dedup or not str(evidence_id or "").strip():
        return False
    if str(disposition) not in CONSUMPTION_DISPOSITIONS:
        return False
    if str(measurement_status) not in DEFINITIVE_MEASUREMENT_STATUSES:
        return False
    if str(release_compatibility) != RELEASE_CURRENT:
        return False

    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or _empty_registry()
        events = registry.get("events", {})
        row = events.get(dedup)
        if not isinstance(row, dict) or row.get("status") not in TERMINAL:
            return False
        existing = row.get("consumption")
        if _consumption_ack_is_valid(row) and isinstance(existing, dict):
            # Idempotent: same evidence already acknowledged by the same release.
            if (
                str(existing.get("evidence_id")) == str(evidence_id)
                and str(existing.get("measurement_status")) == str(measurement_status)
                and str(existing.get("disposition")) == str(disposition)
            ):
                return True
        row["consumption"] = {
            "dedup_id": dedup,
            "evidence_id": str(evidence_id),
            "disposition": str(disposition),
            "measurement_status": str(measurement_status),
            "release_compatibility": str(release_compatibility),
            "acknowledged_at": now.isoformat(),
        }
        save_json_atomic(INBOX_FILE, registry)
        return True


def enqueue(signal: TradevisorSignal, *, now: datetime | None = None) -> tuple[dict, bool]:
    """Durably record one validated alert.

    Returns ``(row, is_new)``. A repeat of the same logical alert is not a new
    event: it returns the stored row with ``is_new`` False. Capacity pressure
    raises :class:`TradevisorInboxFullError` so the HTTP layer can back off
    instead of dropping evidence.
    """
    settings = get_settings()
    now = now or _now()
    key = str(signal.dedup_id or "").strip()
    if not key:
        raise ValueError("Tradevisor signal has no dedup identity")

    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or _empty_registry()
        events = registry.setdefault("events", {})
        metrics = registry.setdefault("metrics", {})
        existing = events.get(key)
        if isinstance(existing, dict):
            metrics["duplicate_receipts"] = int(metrics.get("duplicate_receipts", 0)) + 1
            metrics["last_received_at"] = now.isoformat()
            save_json_atomic(INBOX_FILE, registry)
            return dict(existing), False

        _prune(
            events,
            now=now,
            retention_seconds=int(settings.opip_tradevisor_retention_seconds),
        )
        if len(events) >= int(settings.opip_tradevisor_max_inbox_events):
            # Preserve explicit backpressure. Unconsumed evidence is never
            # evicted to make room, so the counter is the operational signal.
            metrics["capacity_backpressure_events"] = (
                int(metrics.get("capacity_backpressure_events", 0)) + 1
            )
            save_json_atomic(INBOX_FILE, registry)
            raise TradevisorInboxFullError("Tradevisor inbox is at capacity")

        row = _row_from_signal(signal, now)
        events[key] = row
        metrics["accepted"] = int(metrics.get("accepted", 0)) + 1
        metrics["last_received_at"] = now.isoformat()
        save_json_atomic(INBOX_FILE, registry)
        return dict(row), True


def _new_claim_token() -> str:
    """Mint a unique fencing token for one claim attempt."""
    return uuid.uuid4().hex


def _reclaim_stale(registry: dict, *, now: datetime, timeout_seconds: int) -> int:
    """Return crashed ``PROCESSING`` rows to the retry queue.

    A process kill between "claimed" and "settled" must not strand durable
    evidence forever (ATDD-36 / ATDD-40). Reclaiming also voids the previous
    owner's fencing token, so a worker that returns late cannot settle a claim
    it no longer holds.
    """
    reclaimed = 0
    for row in registry.get("events", {}).values():
        if not isinstance(row, dict) or row.get("status") != STATUS_PROCESSING:
            continue
        started = _parse(row.get("processing_started_at"))
        if started is None or (now - started).total_seconds() > timeout_seconds:
            row["status"] = STATUS_RETRY
            row["failure_reason"] = "PROCESSING_LEASE_EXPIRED"
            row["processing_started_at"] = None
            row["claim_token"] = None
            reclaimed += 1
    return reclaimed


def pending_count() -> int:
    registry = load_json(INBOX_FILE) or _empty_registry()
    return sum(
        1
        for row in registry.get("events", {}).values()
        if isinstance(row, dict) and row.get("status") in ACTIVE
    )


def inbox_status() -> dict:
    """Bounded, secret-free operational view of the inbox.

    Backlog metrics are aggregate counts only: no dedup identifiers, signal
    data or credentials are exposed.
    """
    registry = load_json(INBOX_FILE) or _empty_registry()
    events = registry.get("events", {})
    settings = get_settings()
    now = _now()
    cutoff = now - timedelta(
        seconds=int(settings.opip_tradevisor_retention_seconds)
    )
    counts: dict[str, int] = {}
    consumed = 0
    unconsumed = 0
    retention_blocked = 0
    oldest_unconsumed: datetime | None = None
    for row in events.values():
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
        if row.get("status") not in TERMINAL:
            continue
        if _retention_eligible(row):
            consumed += 1
            continue
        unconsumed += 1
        stamp = _parse(row.get("processed_at")) or _parse(row.get("enqueued_at"))
        if stamp is None:
            continue
        if oldest_unconsumed is None or stamp < oldest_unconsumed:
            oldest_unconsumed = stamp
        if stamp < cutoff:
            retention_blocked += 1
    return {
        "schema_version": 1,
        "configured": bool(settings.opip_tradevisor_enabled),
        "depth": len(events),
        "pending": sum(counts.get(key, 0) for key in ACTIVE),
        "by_status": counts,
        # Learning-evidence backlog: retained because no durable consumption
        # acknowledgement exists for these rows.
        "consumed": consumed,
        "unconsumed": unconsumed,
        "retention_blocked": retention_blocked,
        "oldest_unconsumed_age_seconds": (
            int((now - oldest_unconsumed).total_seconds())
            if oldest_unconsumed is not None
            else 0
        ),
        "capacity_backpressure_events": int(
            (registry.get("metrics") or {}).get("capacity_backpressure_events", 0)
        ),
        "metrics": dict(registry.get("metrics") or {}),
        # Explicit evidence classification: this store is advisory operational
        # evidence, NOT the canonical ledger. Naming it prevents any downstream
        # consumer from mistaking one for the other (G-03).
        "evidence_class": "ADVISORY_NON_CANONICAL",
        "canonical_handoff": "NOT_PERFORMED",
        "consumption": "app.jobs.report_tradevisor_learning",
        "authority": {
            "trading_authority_changed": False,
            "admission_authority": False,
            "execution_authority": False,
        },
    }


def _settle(
    key: str,
    *,
    status: str,
    disposition: str,
    reason: str,
    now: datetime,
    decision: object = None,
    record: object = None,
    notification: object = None,
    attempts: int | None = None,
    claim_token: str | None = None,
) -> bool:
    """Persist a terminal or retry disposition exactly once.

    The write applies only while this worker still owns the claim: the row must
    be ``PROCESSING`` *and* carry the fencing token minted for this worker. A
    row that was reclaimed and re-claimed by another worker carries a different
    token, so a late settle is a no-op and cannot overwrite the new owner's
    decision (ATDD-40).
    """
    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or _empty_registry()
        row = registry.setdefault("events", {}).get(key)
        if not isinstance(row, dict) or row.get("status") != STATUS_PROCESSING:
            return False
        if not claim_token or row.get("claim_token") != claim_token:
            # A status-only check is not ownership: after a lease expiry the row
            # returns to PROCESSING under a different token.
            return False
        row["status"] = status
        row["final_disposition"] = disposition
        row["failure_reason"] = reason or None
        row["processed_at"] = now.isoformat()
        row["processing_started_at"] = None
        row["claim_token"] = None
        if attempts is not None:
            row["attempts"] = attempts
        if decision is not None:
            row["decision"] = decision
        if record is not None:
            row["decision_record"] = record
        if notification is not None:
            row["notification"] = notification
        metrics = registry.setdefault("metrics", {})
        metrics["last_processed_at"] = now.isoformat()
        if status == STATUS_PROCESSED:
            metrics["processed"] = int(metrics.get("processed", 0)) + 1
        elif status == STATUS_REJECTED:
            metrics["rejected"] = int(metrics.get("rejected", 0)) + 1
        elif status == STATUS_POISONED:
            metrics["poisoned"] = int(metrics.get("poisoned", 0)) + 1
        save_json_atomic(INBOX_FILE, registry)
        return True


def process_queued_events(
    *,
    evaluator=None,
    now: datetime | None = None,
    limit: int | None = None,
) -> dict:
    """Drain one bounded batch through the evaluator.

    ``evaluator`` receives a :class:`TradevisorSignal` and returns a mapping
    with at least ``decision`` and ``reason``. An optional ``record`` mapping is
    frozen decision-time evidence persisted on the row; an optional
    ``notification`` mapping is the delivery outcome. ``disposition ==
    "REJECTED"`` records a terminal policy rejection.

    A missing evaluator is a recorded no-op disposition, never an implicit
    approval.
    """
    settings = get_settings()
    now = now or _now()
    batch_limit = int(limit or settings.opip_tradevisor_processing_batch_limit)
    summary = {
        "checked": 0,
        "processed": 0,
        "rejected": 0,
        "retried": 0,
        "poisoned": 0,
        "reclaimed": 0,
        "qualified": 0,
    }

    claimed: list[tuple[str, dict, str]] = []
    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or _empty_registry()
        summary["reclaimed"] = _reclaim_stale(
            registry,
            now=now,
            timeout_seconds=int(settings.opip_tradevisor_processing_timeout_seconds),
        )
        events = registry.setdefault("events", {})
        candidates = [
            (str(key), dict(row))
            for key, row in events.items()
            if isinstance(row, dict) and row.get("status") in ACTIVE
        ]
        candidates.sort(key=lambda item: str(item[1].get("enqueued_at") or ""))
        for key, row in candidates[:batch_limit]:
            token = _new_claim_token()
            row["status"] = STATUS_PROCESSING
            row["processing_started_at"] = now.isoformat()
            # Ownership is proven by this token, not by the status field.
            row["claim_token"] = token
            events[key] = row
            claimed.append((key, row, token))
        save_json_atomic(INBOX_FILE, registry)

    for key, row, claim_token in claimed:
        summary["checked"] += 1
        try:
            signal = signal_from_record(row.get("signal") or {})
        except Exception as exc:  # noqa: BLE001 - a malformed row is quarantined
            _settle(
                key,
                status=STATUS_POISONED,
                disposition="MALFORMED_RECORD",
                reason=f"{type(exc).__name__} while rebuilding the signal",
                now=now,
                claim_token=claim_token,
            )
            summary["poisoned"] += 1
            continue

        try:
            if evaluator is None:
                _settle(
                    key,
                    status=STATUS_PROCESSED,
                    disposition="EVALUATION_NOT_CONFIGURED",
                    reason="no evaluator configured",
                    now=now,
                    decision="NO_TRADE",
                    claim_token=claim_token,
                )
                summary["processed"] += 1
                continue

            result = dict(evaluator(signal) or {})
            decision = str(result.get("decision") or "NO_TRADE")
            reason = str(result.get("reason") or "")
            disposition = str(result.get("disposition") or "EVALUATED")
            terminal_status = (
                STATUS_REJECTED if disposition == "REJECTED" else STATUS_PROCESSED
            )
            _settle(
                key,
                status=terminal_status,
                disposition=disposition,
                reason=reason,
                now=now,
                decision=decision,
                record=result.get("record"),
                notification=result.get("notification"),
                claim_token=claim_token,
            )
            if terminal_status == STATUS_REJECTED:
                summary["rejected"] += 1
            else:
                summary["processed"] += 1
                if decision in {"ENTER_NOW", "SET_LIMIT_ENTRY"}:
                    summary["qualified"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad event cannot abort a batch
            # Only the exception type is persisted: a generic handler can catch
            # library errors whose text embeds request/response detail.
            attempts = int(row.get("attempts") or 0) + 1
            exhausted = attempts >= int(settings.opip_tradevisor_max_attempts)
            _settle(
                key,
                status=STATUS_POISONED if exhausted else STATUS_RETRY,
                disposition="POISONED" if exhausted else "RETRY",
                reason=f"{type(exc).__name__} during evaluation (see application logs)",
                now=now,
                attempts=attempts,
                claim_token=claim_token,
            )
            if exhausted:
                summary["poisoned"] += 1
            else:
                summary["retried"] += 1
            logger.exception("Tradevisor event %s failed evaluation", key)

    return summary


def iter_records() -> list[dict]:
    """Read-only snapshot of every stored row (learning and diagnostics)."""
    registry = load_json(INBOX_FILE) or _empty_registry()
    return [
        dict(row) for row in registry.get("events", {}).values() if isinstance(row, dict)
    ]


def undelivered_notification_rows(
    limit: int = 2, *, now: datetime | None = None
) -> list[dict]:
    """Rows whose actionable alert was never accepted by the transport.

    Read-only: claiming happens in ``record_notification_retry``. A row is a
    retry candidate only while its frozen plan is still unexpired, so a stale
    recommendation can never be resurrected by the retry path.
    """
    reference = now or _now()
    candidates: list[dict] = []
    for row in iter_records():
        notification = row.get("notification")
        if not isinstance(notification, dict):
            continue
        if notification.get("status") != "SEND_FAILED":
            continue
        frozen = row.get("decision_record")
        if not isinstance(frozen, dict):
            continue
        expires_at = _parse(frozen.get("expires_at"))
        if expires_at is None or reference > expires_at:
            # Expired recommendations are terminally not deliverable.
            continue
        candidates.append(row)
    candidates.sort(key=lambda row: str(row.get("processed_at") or ""))
    return candidates[: max(1, int(limit))]


def record_notification_retry(
    dedup_id: str,
    *,
    outcome: dict,
    now: datetime | None = None,
) -> bool:
    """Attach one retry attempt outcome to an already-settled row.

    Only the notification sub-record changes: the frozen decision record, the
    terminal status and the evidence are immutable, so retrying delivery can
    never rewrite what was decided or why.
    """
    key = str(dedup_id or "").strip()
    if not key:
        return False
    now = now or _now()
    with registry_lock(LOCK_FILE):
        registry = load_json(INBOX_FILE) or _empty_registry()
        row = registry.setdefault("events", {}).get(key)
        if not isinstance(row, dict) or row.get("status") not in TERMINAL:
            return False
        previous = row.get("notification")
        attempts = int((previous or {}).get("retry_count", 0)) if isinstance(previous, dict) else 0
        merged = dict(outcome or {})
        merged["retry_count"] = attempts + 1
        merged["last_retry_at"] = now.isoformat()
        row["notification"] = merged
        metrics = registry.setdefault("metrics", {})
        metrics["notification_retries"] = int(metrics.get("notification_retries", 0)) + 1
        save_json_atomic(INBOX_FILE, registry)
        return True


