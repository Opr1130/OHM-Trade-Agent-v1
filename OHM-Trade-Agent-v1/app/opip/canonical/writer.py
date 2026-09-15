"""Canonical writer transaction engine (write connection only inside writer process)."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from app.opip.canonical.models import PendingHandoff, WriterAck, WriterIntent
from app.opip.canonical.paths import (
    EVENT_SCHEMA_VERSION,
    SCHEMA_VERSION,
    STATE_FAMILY_EARLY_WATCH,
    STREAM_EARLY_WATCH,
)
from app.opip.canonical.schema import connect, initialize_schema
from app.opip.contracts.events import (
    FEATURE_BUS_EVENT_TYPES,
    FEATURE_BUS_PRIORITY,
    FEATURE_BUS_STREAM,
    FEATURE_CHECKPOINT_RECORDED,
    FEATURE_SNAPSHOT_RECORDED,
    MARKET_OBSERVATION_RECORDED,
)
from app.opip.decision_intelligence.events import (
    DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,
    DECISION_INTELLIGENCE_COMPARISON_RECORDED,
    DECISION_INTELLIGENCE_CONTEXT_RECORDED,
    DECISION_INTELLIGENCE_EVENT_TYPES,
    DECISION_INTELLIGENCE_INVOCATION_RECORDED,
    DECISION_INTELLIGENCE_REQUEST_RECORDED,
    DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
    DECISION_INTELLIGENCE_STREAM,
    DECISION_INTELLIGENCE_TRANSITION_RECORDED,
    canonical_di_idempotency_key,
    context_idempotency_key,
    request_idempotency_key,
    validate_di_payload,
)
from app.opip.decision_intelligence.serialization import canonical_serialize

MAX_PAYLOAD_BYTES = 16 * 1024

#: Same idempotency key + different semantic payload is integrity corruption.
#: Feature snapshots and rolling-state checkpoints are included after redacting
#: volatile clocks so a re-poll that only advances ``evaluated_at_utc`` /
#: ``created_at_utc`` / receipt stamps stays ``DUPLICATE_OK``, while divergent
#: values/coverage/retained state fail closed instead of promoting in-memory
#: state the WAL does not actually hold.
IDEMPOTENT_PAYLOAD_EVENT_TYPES = frozenset(
    {
        MARKET_OBSERVATION_RECORDED,
        FEATURE_CHECKPOINT_RECORDED,
        FEATURE_SNAPSHOT_RECORDED,
    }
    | DECISION_INTELLIGENCE_EVENT_TYPES
)

#: Wall-clock / hash fields that may move on an otherwise identical snapshot.
_SNAPSHOT_VOLATILE_KEYS = frozenset(
    {
        "evaluated_at_utc",
        "notes",
        "content_hash",
        "visible_at_utc",
    }
)
#: Receipt clocks inside availability; keep source_at_utc / source_version as
#: substantive content so adapter-version or source-time drift fails closed.
_SNAPSHOT_AVAILABILITY_VOLATILE_KEYS = frozenset(
    {
        "ingested_at_utc",
        "visible_at_utc",
    }
)
#: The only checkpoint field allowed to move between same-key retries is its
#: creation clock; retained rolling state is substantive evidence.
_CHECKPOINT_VOLATILE_KEYS = frozenset({"created_at_utc"})

def _idempotency_payload_json(event_type: str, payload: object) -> str:
    """Canonical JSON used for same-key semantic conflict detection."""
    if not isinstance(payload, dict):
        raise TypeError("idempotency payload must be a mapping")
    body: dict = dict(payload)
    if event_type in DECISION_INTELLIGENCE_EVENT_TYPES:
        provenance = body.get("provenance")
        if isinstance(provenance, Mapping):
            body["provenance"] = {
                key: value
                for key, value in provenance.items()
                if key not in {"artifact_or_build_id", "emitted_at", "process_instance_id", "host_identity", "history_epoch"}
            }
        return canonical_serialize(body)
    if event_type == FEATURE_SNAPSHOT_RECORDED:
        body = {
            key: value
            for key, value in body.items()
            if key not in _SNAPSHOT_VOLATILE_KEYS
        }
        availability = body.get("availability")
        if isinstance(availability, Mapping):
            body["availability"] = {
                key: value
                for key, value in availability.items()
                if key not in _SNAPSHOT_AVAILABILITY_VOLATILE_KEYS
            }
    elif event_type == FEATURE_CHECKPOINT_RECORDED:
        body = {
            key: value
            for key, value in body.items()
            if key not in _CHECKPOINT_VOLATILE_KEYS
        }
    return json.dumps(body, separators=(",", ":"), sort_keys=True)


ALERT_GOVERNOR_EVENT_TYPES = frozenset(
    {
        "alert_governor.transition.recorded",
        "alert_governor.reservation.released",
        "alert_governor.capture_gap.recorded",
    }
)

ACCEPTED_EVENT_TYPES = (
    ALERT_GOVERNOR_EVENT_TYPES | FEATURE_BUS_EVENT_TYPES | DECISION_INTELLIGENCE_EVENT_TYPES
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_event_id() -> str:
    return f"EVT:{uuid.uuid4().hex}"


class CanonicalWriter:
    """Single write authority for the operational SQLite WAL database."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn = connect(self.db_path, read_only=False)
        initialize_schema(self._conn, now_iso=_utc_now())

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._conn.close()

    def checkpoint_wal(self) -> None:
        """Flush WAL into the main DB file (required before atomic file cutover)."""
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.commit()

    def submit(self, intent: WriterIntent) -> WriterAck:
        with self._lock:
            normalized_payload = intent.payload
            try:
                normalized_payload = self._validate_intent(intent)
            except (TypeError, ValueError) as exc:
                return WriterAck(status="REJECTED", error_code="INVALID_INTENT", detail=str(exc))

            payload_json = None
            if intent.event_type in IDEMPOTENT_PAYLOAD_EVENT_TYPES:
                payload_json = _idempotency_payload_json(
                    intent.event_type, normalized_payload
                )
            existing = self._lookup_idempotency(
                intent.idempotency_key,
                payload_json=payload_json,
                event_type=intent.event_type,
            )
            if existing is not None:
                return existing

            try:
                return self._commit_new(intent, normalized_payload=normalized_payload)
            except sqlite3.IntegrityError:
                existing = self._lookup_idempotency(
                    intent.idempotency_key,
                    payload_json=payload_json,
                    event_type=intent.event_type,
                )
                if existing is not None:
                    return existing
                return WriterAck(status="RETRYABLE", error_code="INTEGRITY_CONFLICT")
            except sqlite3.Error as exc:
                self._conn.rollback()
                return WriterAck(status="RETRYABLE", error_code="SQLITE_ERROR", detail=str(exc))

    def confirm_ops_applied(self, event_id: str) -> WriterAck:
        now = _utc_now()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT status FROM alert_ops_handoffs WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if row is None:
                    self._conn.rollback()
                    return WriterAck(status="REJECTED", error_code="HANDOFF_NOT_FOUND")
                if str(row["status"]) == "APPLIED":
                    self._conn.commit()
                    return WriterAck(status="DUPLICATE_OK", event_id=event_id)
                self._conn.execute(
                    """
                    UPDATE alert_ops_handoffs
                    SET status = 'APPLIED', applied_at = ?
                    WHERE event_id = ?
                    """,
                    (now, event_id),
                )
                self._conn.commit()
                return WriterAck(status="OK", event_id=event_id)
            except sqlite3.Error as exc:
                self._conn.rollback()
                return WriterAck(status="RETRYABLE", error_code="SQLITE_ERROR", detail=str(exc))

    def mark_handoff_superseded(self, event_id: str) -> WriterAck:
        now = _utc_now()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT status FROM alert_ops_handoffs WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if row is None:
                    self._conn.rollback()
                    return WriterAck(status="REJECTED", error_code="HANDOFF_NOT_FOUND")
                self._conn.execute(
                    """
                    UPDATE alert_ops_handoffs
                    SET status = 'SUPERSEDED', applied_at = ?
                    WHERE event_id = ?
                    """,
                    (now, event_id),
                )
                self._conn.commit()
                return WriterAck(status="OK", event_id=event_id)
            except sqlite3.Error as exc:
                self._conn.rollback()
                return WriterAck(status="RETRYABLE", error_code="SQLITE_ERROR", detail=str(exc))

    def list_pending_handoffs(self) -> list[PendingHandoff]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT h.*, e.payload_json
                FROM alert_ops_handoffs h
                JOIN events e ON e.event_id = h.event_id
                WHERE h.status = 'PENDING'
                ORDER BY e.history_epoch ASC, e.local_sequence ASC
                """
            ).fetchall()
            out: list[PendingHandoff] = []
            for row in rows:
                payload = json.loads(str(row["payload_json"]))
                out.append(
                    PendingHandoff(
                        event_id=str(row["event_id"]),
                        operation=str(row["operation"]),  # type: ignore[arg-type]
                        identity=str(row["identity"]),
                        transition_key=str(row["transition_key"]),
                        message_id=(
                            int(row["message_id"]) if row["message_id"] is not None else None
                        ),
                        created_new=bool(row["created_new"]),
                        reservation_token=(
                            str(row["reservation_token"])
                            if row["reservation_token"] is not None
                            else None
                        ),
                        state_file=str(row["state_file"]),
                        status=str(row["status"]),  # type: ignore[arg-type]
                        created_at=str(row["created_at"]),
                        applied_at=(
                            str(row["applied_at"]) if row["applied_at"] is not None else None
                        ),
                        payload=payload,
                    )
                )
            return out

    def advance_history_epoch_for_restore(self) -> int:
        """Required before accepting writes after restoring an older snapshot."""
        now = _utc_now()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                meta = self._conn.execute(
                    "SELECT history_epoch FROM meta WHERE id = 1"
                ).fetchone()
                if meta is None:
                    self._conn.rollback()
                    raise RuntimeError("canonical meta row missing")
                new_epoch = int(meta["history_epoch"]) + 1
                self._conn.execute(
                    """
                    UPDATE meta
                    SET history_epoch = ?, next_local_sequence = 1, updated_at = ?
                    WHERE id = 1
                    """,
                    (new_epoch, now),
                )
                self._conn.commit()
                return new_epoch
            except sqlite3.Error:
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                raise
            except Exception:
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                raise

    def _load_di_payload(
        self, *, event_type: str, idempotency_key: str
    ) -> dict:
        row = self._conn.execute(
            """
            SELECT payload_json
            FROM events
            WHERE event_type = ? AND idempotency_key = ?
            """,
            (event_type, idempotency_key),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"required {event_type} record is missing for evidence validation"
            )
        try:
            raw_payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"persisted {event_type} payload is invalid JSON"
            ) from exc
        return validate_di_payload(event_type, raw_payload)

    def _validate_di_evidence_eligibility(
        self, event_type: str, payload: Mapping[str, object]
    ) -> None:
        if event_type not in {
            DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
            DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,
        }:
            return
        evidence_refs = payload.get("evidence_refs")
        if not evidence_refs:
            return
        if not isinstance(evidence_refs, list):
            raise ValueError("evidence_refs must be a canonical array")

        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id required for evidence validation")
        request_payload = self._load_di_payload(
            event_type=DECISION_INTELLIGENCE_REQUEST_RECORDED,
            idempotency_key=request_idempotency_key(request_id=request_id),
        )
        context_id = request_payload.get("context_id")
        if not isinstance(context_id, str) or not context_id:
            raise ValueError("persisted request has no valid context_id")
        context_payload = self._load_di_payload(
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            idempotency_key=context_idempotency_key(context_id=context_id),
        )
        if request_payload.get("frozen_snapshot_hash") != context_payload.get(
            "snapshot_hash"
        ):
            raise ValueError("persisted request/context snapshot mismatch")

        manifest = context_payload.get("evidence_eligibility_manifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("persisted context evidence manifest is invalid")
        cutoff_raw = context_payload.get("evidence_cutoff")
        if not isinstance(cutoff_raw, str):
            raise ValueError("persisted context evidence_cutoff is invalid")
        cutoff = datetime.fromisoformat(cutoff_raw.replace("Z", "+00:00"))
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("persisted context evidence_cutoff is not aware")

        for evidence_ref in evidence_refs:
            if evidence_ref not in manifest:
                raise ValueError(
                    f"evidence reference is not in frozen manifest: {evidence_ref}"
                )
            entry = manifest[evidence_ref]
            if not isinstance(entry, Mapping):
                raise ValueError("persisted evidence manifest entry is invalid")
            available_at_raw = entry.get("available_at")
            if available_at_raw is None:
                continue
            if not isinstance(available_at_raw, str):
                raise ValueError("persisted evidence availability is invalid")
            available_at = datetime.fromisoformat(
                available_at_raw.replace("Z", "+00:00")
            )
            if available_at.tzinfo is None or available_at.utcoffset() is None:
                raise ValueError("persisted evidence availability is not aware")
            if available_at > cutoff:
                raise ValueError(
                    "evidence reference is unavailable at evidence_cutoff"
                )

    def _lookup_idempotency(
        self, key: str, *, payload_json: str | None = None, event_type: str | None = None
    ) -> WriterAck | None:
        existing = self._conn.execute(
            "SELECT event_id, history_epoch, local_sequence, payload_json "
            "FROM events WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if existing is None:
            return None
        if payload_json is not None:
            existing_payload = str(existing["payload_json"])
            if event_type is not None:
                try:
                    existing_payload = _idempotency_payload_json(
                        event_type, json.loads(existing_payload)
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    existing_payload = str(existing["payload_json"])
            if existing_payload != payload_json:
                return WriterAck(
                    status="REJECTED",
                    error_code="IDEMPOTENCY_PAYLOAD_CONFLICT",
                    event_id=str(existing["event_id"]),
                    detail="idempotency_key already committed with a different payload",
                )
        return WriterAck(
            status="DUPLICATE_OK",
            event_id=str(existing["event_id"]),
            history_epoch=int(existing["history_epoch"]),
            local_sequence=int(existing["local_sequence"]),
        )

    def _validate_intent(self, intent: WriterIntent) -> dict:
        if intent.schema_version != SCHEMA_VERSION:
            raise ValueError("schema_version mismatch")
        if intent.priority not in {"HIGH", "NORMAL", "LOW"}:
            raise ValueError("invalid priority")
        if not intent.idempotency_key.strip():
            raise ValueError("idempotency_key required")
        if intent.event_type not in ACCEPTED_EVENT_TYPES:
            raise ValueError("unsupported event_type")
        raw = (
            canonical_serialize(intent.payload)
            if intent.event_type in DECISION_INTELLIGENCE_EVENT_TYPES
            else json.dumps(intent.payload, separators=(",", ":"), sort_keys=True)
        )
        if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload too large")
        if intent.event_type in FEATURE_BUS_EVENT_TYPES:
            # Feature-bus traffic is telemetry. It must not borrow protection or
            # execution priority, and it never drives an operational handoff.
            if intent.priority != FEATURE_BUS_PRIORITY:
                raise ValueError("feature bus events must use LOW priority")
            if intent.ops_handoff is not None:
                raise ValueError("feature bus events must not carry ops_handoff")
            return intent.payload
        if intent.event_type in DECISION_INTELLIGENCE_EVENT_TYPES:
            if intent.priority != "LOW":
                raise ValueError("decision intelligence events must use LOW priority")
            if intent.ops_handoff is not None:
                raise ValueError("decision intelligence events must not carry ops_handoff")
            normalized = validate_di_payload(intent.event_type, intent.payload)
            expected_key = canonical_di_idempotency_key(
                intent.event_type, normalized
            )
            if intent.idempotency_key != expected_key:
                raise ValueError(
                    "decision intelligence idempotency_key does not match "
                    "canonical record identity"
                )
            self._validate_di_evidence_eligibility(
                intent.event_type, normalized
            )
            return normalized
        if intent.event_type == "alert_governor.capture_gap.recorded":
            if intent.ops_handoff is not None:
                raise ValueError("capture_gap must not carry ops_handoff")
            return intent.payload
        if intent.ops_handoff is None:
            raise ValueError("ops_handoff required")
        op = str(intent.ops_handoff.get("operation") or "")
        if op not in {"RECORD", "RELEASE"}:
            raise ValueError("invalid ops_handoff.operation")
        if intent.event_type == "alert_governor.reservation.released" and op != "RELEASE":
            raise ValueError("released event requires RELEASE handoff")
        if intent.event_type == "alert_governor.transition.recorded" and op != "RECORD":
            raise ValueError("recorded event requires RECORD handoff")
        family = str(
            intent.ops_handoff.get("state_family")
            or intent.ops_handoff.get("state_file")
            or ""
        ).strip()
        if family not in {STATE_FAMILY_EARLY_WATCH}:
            raise ValueError("unsupported ops_handoff.state_family")

    def _commit_new(self, intent: WriterIntent, *, normalized_payload: dict | None = None) -> WriterAck:
        now = _utc_now()
        event_id = _new_event_id()
        payload = normalized_payload if normalized_payload is not None else intent.payload
        payload_json = (
            canonical_serialize(payload)
            if intent.event_type in DECISION_INTELLIGENCE_EVENT_TYPES
            else json.dumps(payload, separators=(",", ":"), sort_keys=True)
        )
        handoff = intent.ops_handoff or {}
        action = "CREATE" if bool(handoff.get("created_new")) else "EDIT"
        identity = str(handoff.get("identity") or payload.get("identity") or "")
        transition_key = str(handoff.get("transition_key") or "")
        message_id = handoff.get("message_id")
        reservation_token = handoff.get("reservation_token")
        # Persist bounded family id only — never an arbitrary filesystem path.
        state_file = STATE_FAMILY_EARLY_WATCH
        operation = str(handoff.get("operation") or "")
        is_gap = intent.event_type == "alert_governor.capture_gap.recorded"
        is_feature_bus = intent.event_type in FEATURE_BUS_EVENT_TYPES
        is_decision_intelligence = intent.event_type in DECISION_INTELLIGENCE_EVENT_TYPES
        # Separate watermark streams: feature-bus progress can never rewind or
        # advance Early Watch alert-control progress, or the reverse.
        stream = (
            DECISION_INTELLIGENCE_STREAM
            if is_decision_intelligence
            else FEATURE_BUS_STREAM if is_feature_bus else STREAM_EARLY_WATCH
        )

        self._conn.execute("BEGIN IMMEDIATE")
        meta = self._conn.execute(
            "SELECT history_epoch, next_local_sequence, schema_version FROM meta WHERE id = 1"
        ).fetchone()
        assert meta is not None
        if int(meta["schema_version"]) != SCHEMA_VERSION:
            self._conn.rollback()
            return WriterAck(status="REJECTED", error_code="DB_SCHEMA_MISMATCH")

        history_epoch = int(meta["history_epoch"])
        local_sequence = int(meta["next_local_sequence"])

        self._conn.execute(
            """
            INSERT INTO events (
                event_id, schema_version, event_type, history_epoch, local_sequence,
                recorded_at, event_time, causation_id, correlation_id,
                idempotency_key, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                EVENT_SCHEMA_VERSION,
                intent.event_type,
                history_epoch,
                local_sequence,
                now,
                intent.event_time,
                intent.causation_id,
                intent.correlation_id,
                intent.idempotency_key,
                payload_json,
            ),
        )
        self._conn.execute(
            """
            INSERT INTO idempotency_keys (idempotency_key, event_id, committed_at)
            VALUES (?, ?, ?)
            """,
            (intent.idempotency_key, event_id, now),
        )

        # RELEASE and capture_gap never upsert identity projection.
        if intent.event_type == "alert_governor.transition.recorded":
            self._conn.execute(
                """
                INSERT INTO alert_identity_projection (
                    state_family, identity, transition_key, last_action, message_id,
                    last_event_id, last_history_epoch, last_local_sequence, updated_at
                ) VALUES ('early_watch', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(state_family, identity) DO UPDATE SET
                    transition_key = excluded.transition_key,
                    last_action = excluded.last_action,
                    message_id = excluded.message_id,
                    last_event_id = excluded.last_event_id,
                    last_history_epoch = excluded.last_history_epoch,
                    last_local_sequence = excluded.last_local_sequence,
                    updated_at = excluded.updated_at
                """,
                (
                    identity,
                    transition_key,
                    action,
                    int(message_id) if message_id is not None else None,
                    event_id,
                    history_epoch,
                    local_sequence,
                    now,
                ),
            )

        self._conn.execute(
            """
            INSERT INTO watermarks (stream, history_epoch, local_sequence, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stream) DO UPDATE SET
                history_epoch = excluded.history_epoch,
                local_sequence = excluded.local_sequence,
                updated_at = excluded.updated_at
            """,
            (stream, history_epoch, local_sequence, now),
        )
        if not is_gap and not is_feature_bus and not is_decision_intelligence:
            self._conn.execute(
                """
                INSERT INTO alert_ops_handoffs (
                    event_id, operation, identity, transition_key, message_id,
                    created_new, reservation_token, state_file, status, created_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, NULL)
                """,
                (
                    event_id,
                    operation,
                    identity,
                    transition_key,
                    int(message_id) if message_id is not None else None,
                    1 if bool(handoff.get("created_new")) else 0,
                    str(reservation_token) if reservation_token else None,
                    state_file,
                    now,
                ),
            )
        self._conn.execute(
            """
            UPDATE meta
            SET next_local_sequence = ?, updated_at = ?
            WHERE id = 1
            """,
            (local_sequence + 1, now),
        )
        self._conn.commit()
        return WriterAck(
            status="OK",
            event_id=event_id,
            history_epoch=history_epoch,
            local_sequence=local_sequence,
        )
