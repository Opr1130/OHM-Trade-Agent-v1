"""Canonical writer transaction engine (write connection only inside writer process)."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.opip.canonical.models import PendingHandoff, WriterAck, WriterIntent
from app.opip.canonical.paths import (
    EVENT_SCHEMA_VERSION,
    SCHEMA_VERSION,
    STATE_FAMILY_EARLY_WATCH,
    STREAM_EARLY_WATCH,
)
from app.opip.canonical.schema import connect, initialize_schema

MAX_PAYLOAD_BYTES = 16 * 1024


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
            self._conn.close()

    def submit(self, intent: WriterIntent) -> WriterAck:
        with self._lock:
            try:
                self._validate_intent(intent)
            except ValueError as exc:
                return WriterAck(status="REJECTED", error_code="INVALID_INTENT", detail=str(exc))

            existing = self._lookup_idempotency(intent.idempotency_key)
            if existing is not None:
                return existing

            try:
                return self._commit_new(intent)
            except sqlite3.IntegrityError:
                existing = self._lookup_idempotency(intent.idempotency_key)
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
            self._conn.execute("BEGIN IMMEDIATE")
            meta = self._conn.execute(
                "SELECT history_epoch FROM meta WHERE id = 1"
            ).fetchone()
            assert meta is not None
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

    def _lookup_idempotency(self, key: str) -> WriterAck | None:
        existing = self._conn.execute(
            "SELECT event_id, history_epoch, local_sequence FROM events WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if existing is None:
            return None
        return WriterAck(
            status="DUPLICATE_OK",
            event_id=str(existing["event_id"]),
            history_epoch=int(existing["history_epoch"]),
            local_sequence=int(existing["local_sequence"]),
        )

    def _validate_intent(self, intent: WriterIntent) -> None:
        if intent.schema_version != SCHEMA_VERSION:
            raise ValueError("schema_version mismatch")
        if intent.priority not in {"HIGH", "NORMAL", "LOW"}:
            raise ValueError("invalid priority")
        if not intent.idempotency_key.strip():
            raise ValueError("idempotency_key required")
        if intent.event_type not in {
            "alert_governor.transition.recorded",
            "alert_governor.reservation.released",
            "alert_governor.capture_gap.recorded",
        }:
            raise ValueError("unsupported event_type")
        raw = json.dumps(intent.payload, separators=(",", ":"), sort_keys=True)
        if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload too large")
        if intent.event_type == "alert_governor.capture_gap.recorded":
            if intent.ops_handoff is not None:
                raise ValueError("capture_gap must not carry ops_handoff")
            return
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

    def _commit_new(self, intent: WriterIntent) -> WriterAck:
        now = _utc_now()
        event_id = _new_event_id()
        payload_json = json.dumps(intent.payload, separators=(",", ":"), sort_keys=True)
        handoff = intent.ops_handoff or {}
        action = "CREATE" if bool(handoff.get("created_new")) else "EDIT"
        identity = str(handoff.get("identity") or intent.payload.get("identity") or "")
        transition_key = str(handoff.get("transition_key") or "")
        message_id = handoff.get("message_id")
        reservation_token = handoff.get("reservation_token")
        # Persist bounded family id only — never an arbitrary filesystem path.
        state_file = STATE_FAMILY_EARLY_WATCH
        operation = str(handoff.get("operation") or "")
        is_gap = intent.event_type == "alert_governor.capture_gap.recorded"

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
            (STREAM_EARLY_WATCH, history_epoch, local_sequence, now),
        )
        if not is_gap:
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
