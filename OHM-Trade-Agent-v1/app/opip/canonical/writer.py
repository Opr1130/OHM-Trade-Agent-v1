"""Canonical writer transaction engine (write connection only inside writer process)."""

from __future__ import annotations

import contextlib
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
from app.opip.canonical.schema import (
    CanonicalStoreLock,
    canonical_store_path,
    checkpoint_wal_strict,
    connect,
    initialize_schema,
)
from app.opip.contracts.events import (
    FEATURE_BUS_EVENT_TYPES,
    FEATURE_BUS_PRIORITY,
    FEATURE_BUS_STREAM,
    FEATURE_CHECKPOINT_RECORDED,
    FEATURE_SNAPSHOT_RECORDED,
    MARKET_OBSERVATION_RECORDED,
)
from app.opip.contracts.paper_execution import ExecutionState
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_EXECUTION_PRIORITY,
    PAPER_EXECUTION_STREAM,
    PAPER_FILL_RECORDED,
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    event_contract as paper_event_contract,
    paper_evidence_idempotency_key,
    validate_paper_evidence_payload,
)
from app.opip.contracts.paper_execution_runtime import (
    PAPER_ADMISSION_REQUEST_RECORDED,
    PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES,
    PAPER_QUOTE_EVIDENCE_RECORDED,
    PaperAdmissionAck,
    PaperAdmissionRequest,
    admission_request_idempotency_key,
    admission_result_identities,
    quote_evidence_idempotency_key,
    validate_admission_request,
    validate_admission_request_record_payload,
    validate_quote_evidence_payload,
)
from app.opip.contracts.paper_outcome import (
    PAPER_OUTCOME_EVENT_TYPES,
    PAPER_OUTCOME_PRIORITY,
    PAPER_OUTCOME_STREAM,
    PAPER_OUTCOME_TERMINAL_RECORDED,
    assert_supersession_consistent,
    terminal_outcome_idempotency_key,
    validate_terminal_outcome_payload,
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
    assessment_idempotency_key,
    canonical_di_idempotency_key,
    comparison_idempotency_key,
    context_idempotency_key,
    invocation_idempotency_key,
    request_idempotency_key,
    transition_idempotency_key,
    validate_di_payload,
)
from app.opip.decision_intelligence.serialization import canonical_serialize

MAX_PAYLOAD_BYTES = 16 * 1024
_UTC_OFFSET = "+00:00"
_UTC_Z = "Z"
_ALERT_CAPTURE_GAP_RECORDED = "alert_governor.capture_gap.recorded"
_ALERT_RESERVATION_RELEASED = "alert_governor.reservation.released"
_ALERT_TRANSITION_RECORDED = "alert_governor.transition.recorded"

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
    # Paper outcomes join the full-payload conflict set deliberately. They must
    # never be routed into any volatile-stripping branch below: economic truth
    # (realised P/L, costs, quantity, prices, quote currency, terminal reason,
    # strategy/execution provenance) is exactly what makes two otherwise
    # identical submissions different facts rather than duplicates.
    | PAPER_OUTCOME_EVENT_TYPES
    | PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES
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
    ALERT_GOVERNOR_EVENT_TYPES
    | FEATURE_BUS_EVENT_TYPES
    | DECISION_INTELLIGENCE_EVENT_TYPES
    | PAPER_OUTCOME_EVENT_TYPES
    | PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES
)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace(_UTC_OFFSET, _UTC_Z)
    )


def _parse_aware_iso_timestamp(value: str, *, field_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace(_UTC_Z, _UTC_OFFSET))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} is not aware")
    return parsed


def _new_event_id() -> str:
    return f"EVT:{uuid.uuid4().hex}"


class CanonicalWriter:
    """Single write authority for the operational SQLite WAL database."""

    def __init__(self, db_path: Path) -> None:
        # `db_path` is the canonical normalised live-store path, not the
        # caller's alias, so lock identity, SQLite access, sidecar handling and
        # restore cutover all agree on one store.
        self.db_path = canonical_store_path(db_path)
        self._lock = threading.Lock()
        # Exclusivity is acquired before the writable connection is opened, and
        # held for this writer's entire lifetime, so no second writer and no
        # restore can own the same canonical store concurrently.
        self._store_lock = CanonicalStoreLock(self.db_path)
        self._store_lock.acquire()
        # Everything after lock acquisition sits inside one deterministic cleanup
        # boundary. Schema initialization *and* projection hydration can fail on
        # persisted canonical/DI evidence, and a partially constructed writer must
        # never keep store ownership or leak a connection. Cleanup runs for any
        # failure, and the original error always propagates unmasked.
        try:
            self._conn = connect(self.db_path, read_only=False)
            initialize_schema(self._conn, now_iso=_utc_now())
            self._request_lifecycle_projection: dict[str, str] = {}
            self._request_lifecycle_projection_watermark: tuple[int, int] = (0, -1)
            self._role_result_idempotency_by_id: dict[str, str] = {}
            self._role_result_projection_watermark: tuple[int, int] = (0, -1)
            self._hydrate_request_lifecycle_projection()
            self._hydrate_role_result_identity_projection()
        except BaseException:
            # Cleanup is itself protected: a secondary failure while closing the
            # connection or releasing ownership must never replace the original
            # error, which is the only actionable signal for the operator.
            with contextlib.suppress(Exception):
                self._close_connection_best_effort()
            with contextlib.suppress(Exception):
                self._release_store_lock_best_effort()
            raise

    def _close_connection_best_effort(self) -> None:
        connection = getattr(self, "_conn", None)
        if connection is None:
            return
        # Construction-time cleanup only: the original error must surface, not
        # be masked by a secondary failure while closing a partial connection.
        with contextlib.suppress(Exception):
            connection.close()

    def _release_store_lock_best_effort(self) -> None:
        """Return store ownership on a construction failure, without masking it."""
        with contextlib.suppress(Exception):
            self._store_lock.release()

    def _apply_persisted_request_transition(
        self,
        payload: Mapping[str, object],
    ) -> None:
        if payload.get("supersedes_id") is not None:
            return
        request_id = str(payload["request_id"])
        current = self._request_lifecycle_projection.get(
            request_id, "ELIGIBLE"
        )
        if payload["from_state"] != current:
            raise ValueError("persisted transition history is invalid")
        self._request_lifecycle_projection[request_id] = str(
            payload["to_state"]
        )

    def _hydrate_request_lifecycle_projection(self) -> None:
        rows = self._conn.execute(
            """
            SELECT history_epoch, local_sequence, payload_json
            FROM events
            WHERE event_type = ?
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (DECISION_INTELLIGENCE_TRANSITION_RECORDED,),
        ).fetchall()
        for row in rows:
            payload = validate_di_payload(
                DECISION_INTELLIGENCE_TRANSITION_RECORDED,
                json.loads(str(row["payload_json"])),
            )
            self._apply_persisted_request_transition(payload)
            self._request_lifecycle_projection_watermark = (
                int(row["history_epoch"]),
                int(row["local_sequence"]),
            )

    def _refresh_request_lifecycle_projection(self) -> None:
        history_epoch, local_sequence = (
            self._request_lifecycle_projection_watermark
        )
        rows = self._conn.execute(
            """
            SELECT history_epoch, local_sequence, payload_json
            FROM events
            WHERE event_type = ?
              AND (
                    history_epoch > ?
                    OR (
                        history_epoch = ?
                        AND local_sequence > ?
                    )
                  )
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (
                DECISION_INTELLIGENCE_TRANSITION_RECORDED,
                history_epoch,
                history_epoch,
                local_sequence,
            ),
        ).fetchall()
        for row in rows:
            payload = validate_di_payload(
                DECISION_INTELLIGENCE_TRANSITION_RECORDED,
                json.loads(str(row["payload_json"])),
            )
            self._apply_persisted_request_transition(payload)
            self._request_lifecycle_projection_watermark = (
                int(row["history_epoch"]),
                int(row["local_sequence"]),
            )

    def _apply_persisted_role_result(
        self,
        *,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> None:
        result_id = payload.get("result_id")
        if not isinstance(result_id, str) or not result_id:
            raise ValueError("persisted role result has invalid result_id")
        existing = self._role_result_idempotency_by_id.get(result_id)
        if existing is not None and existing != idempotency_key:
            raise ValueError("persisted role result identity is not unique")
        self._role_result_idempotency_by_id[result_id] = idempotency_key

    def _hydrate_role_result_identity_projection(self) -> None:
        rows = self._conn.execute(
            """
            SELECT history_epoch, local_sequence, idempotency_key, payload_json
            FROM events
            WHERE event_type = ?
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,),
        ).fetchall()
        for row in rows:
            payload = validate_di_payload(
                DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
                json.loads(str(row["payload_json"])),
            )
            self._apply_persisted_role_result(
                idempotency_key=str(row["idempotency_key"]),
                payload=payload,
            )
            self._role_result_projection_watermark = (
                int(row["history_epoch"]),
                int(row["local_sequence"]),
            )

    def _refresh_role_result_identity_projection(self) -> None:
        history_epoch, local_sequence = self._role_result_projection_watermark
        rows = self._conn.execute(
            """
            SELECT history_epoch, local_sequence, idempotency_key, payload_json
            FROM events
            WHERE event_type = ?
              AND (
                    history_epoch > ?
                    OR (
                        history_epoch = ?
                        AND local_sequence > ?
                    )
                  )
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (
                DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
                history_epoch,
                history_epoch,
                local_sequence,
            ),
        ).fetchall()
        for row in rows:
            payload = validate_di_payload(
                DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
                json.loads(str(row["payload_json"])),
            )
            self._apply_persisted_role_result(
                idempotency_key=str(row["idempotency_key"]),
                payload=payload,
            )
            self._role_result_projection_watermark = (
                int(row["history_epoch"]),
                int(row["local_sequence"]),
            )

    def close(self) -> None:
        with self._lock:
            try:
                # Best-effort only: shutdown must not fail on a busy checkpoint.
                # Callers that need proof of WAL durability use checkpoint_wal().
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self._conn.close()
            finally:
                self._store_lock.release()

    def checkpoint_wal(self) -> None:
        """Flush the WAL into the main DB file and prove the flush completed.

        Required before an atomic file cutover: a plain ``PRAGMA`` can return
        successfully while frames remain only in the WAL. Raises
        ``CanonicalCheckpointError`` when the checkpoint is busy, incomplete,
        unreadable or errored, so the database file alone is never assumed to be
        a complete snapshot without evidence.
        """
        with self._lock:
            checkpoint_wal_strict(self._conn)
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
            except (TypeError, ValueError) as exc:
                self._conn.rollback()
                return WriterAck(
                    status="REJECTED",
                    error_code="INVALID_INTENT",
                    detail=str(exc),
                )
            except sqlite3.IntegrityError:
                self._conn.rollback()
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

    def _insert_event_row_in_transaction(
        self,
        *,
        event_type: str,
        idempotency_key: str,
        payload: Mapping[str, object],
        history_epoch: int,
        local_sequence: int,
        now: str,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        event_id = _new_event_id()
        payload_json = json.dumps(
            dict(payload),
            separators=(",", ":"),
            sort_keys=True,
        )
        self._conn.execute(
            """
            INSERT INTO events (
                event_id, schema_version, event_type, history_epoch, local_sequence,
                recorded_at, event_time, causation_id, correlation_id,
                idempotency_key, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                event_id,
                EVENT_SCHEMA_VERSION,
                event_type,
                history_epoch,
                local_sequence,
                now,
                causation_id,
                correlation_id,
                idempotency_key,
                payload_json,
            ),
        )
        self._conn.execute(
            """
            INSERT INTO idempotency_keys (idempotency_key, event_id, committed_at)
            VALUES (?, ?, ?)
            """,
            (idempotency_key, event_id, now),
        )
        return event_id

    def _existing_admission_result(
        self,
        request: PaperAdmissionRequest,
        *,
        request_payload: Mapping[str, object],
    ) -> PaperAdmissionAck | None:
        request_key = admission_request_idempotency_key(request.disposition_id)
        row = self._conn.execute(
            """
            SELECT event_id, history_epoch, local_sequence, payload_json
            FROM events
            WHERE event_type = ? AND idempotency_key = ?
            """,
            (PAPER_ADMISSION_REQUEST_RECORDED, request_key),
        ).fetchone()
        if row is None:
            return None
        try:
            stored = validate_admission_request_record_payload(
                json.loads(str(row["payload_json"]))
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return PaperAdmissionAck(
                status="REJECTED",
                request_event_id=str(row["event_id"]),
                error_code="ADMISSION_REQUEST_CORRUPT",
            )

        stored_request = {key: stored.get(key) for key in request_payload}
        expected_json = json.dumps(
            dict(request_payload),
            separators=(",", ":"),
            sort_keys=True,
        )
        stored_json = json.dumps(
            stored_request,
            separators=(",", ":"),
            sort_keys=True,
        )
        if stored_json != expected_json:
            return PaperAdmissionAck(
                status="REJECTED",
                request_event_id=str(row["event_id"]),
                error_code="IDEMPOTENCY_PAYLOAD_CONFLICT",
                detail=(
                    "disposition identity already committed with a different "
                    "admission request payload"
                ),
            )

        observed_version = stored.get("observed_portfolio_version")
        if type(observed_version) is not int or observed_version < 0:
            return PaperAdmissionAck(
                status="REJECTED",
                request_event_id=str(row["event_id"]),
                error_code="ADMISSION_REQUEST_CORRUPT",
            )
        guard_result = stored.get("guard_result")
        if guard_result == "STALE_PORTFOLIO_VERSION":
            return PaperAdmissionAck(
                status="REJECTED",
                request_event_id=str(row["event_id"]),
                history_epoch=int(row["history_epoch"]),
                local_sequence=int(row["local_sequence"]),
                portfolio_version=observed_version,
                error_code="STALE_PORTFOLIO_VERSION",
                detail=(
                    f"expected portfolio version {request.expected_portfolio_version}; "
                    f"observed {observed_version}"
                ),
            )
        if guard_result != "ELIGIBLE":
            return PaperAdmissionAck(
                status="REJECTED",
                request_event_id=str(row["event_id"]),
                error_code="ADMISSION_REQUEST_CORRUPT",
            )

        disposition_key = paper_evidence_idempotency_key(
            PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
            {"disposition_id": request.disposition_id},
        )
        result_row = self._conn.execute(
            """
            SELECT event_id, history_epoch, local_sequence, payload_json
            FROM events
            WHERE event_type = ? AND idempotency_key = ?
            """,
            (PAPER_OPPORTUNITY_DISPOSITION_RECORDED, disposition_key),
        ).fetchone()
        if result_row is None:
            return PaperAdmissionAck(
                status="REJECTED",
                request_event_id=str(row["event_id"]),
                error_code="ADMISSION_TRANSACTION_INCOMPLETE",
            )
        try:
            result = validate_paper_evidence_payload(
                PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
                json.loads(str(result_row["payload_json"])),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return PaperAdmissionAck(
                status="REJECTED",
                request_event_id=str(row["event_id"]),
                disposition_event_id=str(result_row["event_id"]),
                error_code="ADMISSION_RESULT_CORRUPT",
            )
        disposition = str(result["disposition"])
        portfolio_version = observed_version + (1 if disposition == "ADMITTED" else 0)
        return PaperAdmissionAck(
            status="DUPLICATE_OK",
            disposition=disposition,
            request_event_id=str(row["event_id"]),
            disposition_event_id=str(result_row["event_id"]),
            history_epoch=int(result_row["history_epoch"]),
            local_sequence=int(result_row["local_sequence"]),
            portfolio_version=portfolio_version,
            paper_trade_id=(
                str(result["paper_trade_id"])
                if result.get("paper_trade_id") is not None
                else None
            ),
            reservation_id=(
                str(result["reservation_id"])
                if result.get("reservation_id") is not None
                else None
            ),
        )

    def admit_paper_opportunity(
        self,
        request: PaperAdmissionRequest,
    ) -> PaperAdmissionAck:
        """Atomically decide Paper v2 capital/capacity and record the disposition."""

        with self._lock:
            try:
                request_payload = validate_admission_request(request)
            except (TypeError, ValueError) as exc:
                return PaperAdmissionAck(
                    status="REJECTED",
                    error_code="INVALID_ADMISSION_REQUEST",
                    detail=str(exc),
                )

            existing = self._existing_admission_result(
                request,
                request_payload=request_payload,
            )
            if existing is not None:
                return existing

            now = _utc_now()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                meta = self._conn.execute(
                    "SELECT history_epoch, next_local_sequence, schema_version "
                    "FROM meta WHERE id = 1"
                ).fetchone()
                if meta is None:
                    raise ValueError("canonical meta row missing")
                if int(meta["schema_version"]) != SCHEMA_VERSION:
                    self._conn.rollback()
                    return PaperAdmissionAck(
                        status="REJECTED",
                        error_code="DB_SCHEMA_MISMATCH",
                    )

                # The decision context must already be canonical. Admission never
                # invents or reconstructs upstream decision ancestry.
                self._load_context_by_id(request.decision_context_id)

                history_epoch = int(meta["history_epoch"])
                local_sequence = int(meta["next_local_sequence"])
                current_version, reserved_capital, active_reservations = (
                    self._portfolio_state(request.quote_currency)
                )

                guard_result = (
                    "ELIGIBLE"
                    if request.expected_portfolio_version == current_version
                    else "STALE_PORTFOLIO_VERSION"
                )
                request_record = validate_admission_request_record_payload(
                    {
                        **request_payload,
                        "guard_result": guard_result,
                        "observed_portfolio_version": current_version,
                    }
                )
                request_event_id = self._insert_event_row_in_transaction(
                    event_type=PAPER_ADMISSION_REQUEST_RECORDED,
                    idempotency_key=admission_request_idempotency_key(
                        request.disposition_id
                    ),
                    payload=request_record,
                    history_epoch=history_epoch,
                    local_sequence=local_sequence,
                    now=now,
                    correlation_id=request.decision_context_id,
                )

                if guard_result == "STALE_PORTFOLIO_VERSION":
                    self._conn.execute(
                        """
                        INSERT INTO watermarks (
                            stream, history_epoch, local_sequence, updated_at
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(stream) DO UPDATE SET
                            history_epoch = excluded.history_epoch,
                            local_sequence = excluded.local_sequence,
                            updated_at = excluded.updated_at
                        """,
                        (
                            PAPER_EXECUTION_STREAM,
                            history_epoch,
                            local_sequence,
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
                    return PaperAdmissionAck(
                        status="REJECTED",
                        request_event_id=request_event_id,
                        history_epoch=history_epoch,
                        local_sequence=local_sequence,
                        portfolio_version=current_version,
                        error_code="STALE_PORTFOLIO_VERSION",
                        detail=(
                            f"expected portfolio version "
                            f"{request.expected_portfolio_version}; "
                            f"observed {current_version}"
                        ),
                    )

                if active_reservations >= request.portfolio_position_limit:
                    disposition = "CAPACITY_REJECTED"
                    reason_code = "PORTFOLIO_POSITION_LIMIT"
                elif (
                    reserved_capital + request.requested_reservation_amount
                    > request.portfolio_equity_limit + 1e-9
                ):
                    disposition = "CAPITAL_REJECTED"
                    reason_code = "PORTFOLIO_CAPITAL_LIMIT"
                else:
                    disposition = "ADMITTED"
                    reason_code = "CAPITAL_AND_CAPACITY_ADMITTED"

                disposition_payload = {
                    **request_payload,
                    "disposition": disposition,
                    "reason_code": reason_code,
                }
                paper_trade_id: str | None = None
                reservation_id: str | None = None
                if disposition == "ADMITTED":
                    paper_trade_id, reservation_id = admission_result_identities(
                        request.disposition_id
                    )
                    disposition_payload["paper_trade_id"] = paper_trade_id
                    disposition_payload["reservation_id"] = reservation_id

                normalized = validate_paper_evidence_payload(
                    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
                    disposition_payload,
                )
                disposition_sequence = local_sequence + 1
                disposition_event_id = self._insert_event_row_in_transaction(
                    event_type=PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
                    idempotency_key=paper_evidence_idempotency_key(
                        PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
                        normalized,
                    ),
                    payload=normalized,
                    history_epoch=history_epoch,
                    local_sequence=disposition_sequence,
                    now=now,
                    causation_id=request_event_id,
                    correlation_id=request.decision_context_id,
                )

                self._conn.execute(
                    """
                    INSERT INTO watermarks (
                        stream, history_epoch, local_sequence, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(stream) DO UPDATE SET
                        history_epoch = excluded.history_epoch,
                        local_sequence = excluded.local_sequence,
                        updated_at = excluded.updated_at
                    """,
                    (
                        PAPER_EXECUTION_STREAM,
                        history_epoch,
                        disposition_sequence,
                        now,
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE meta
                    SET next_local_sequence = ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (disposition_sequence + 1, now),
                )
                self._conn.commit()
                resulting_version = current_version + (
                    1 if disposition == "ADMITTED" else 0
                )
                return PaperAdmissionAck(
                    status="OK",
                    disposition=disposition,
                    request_event_id=request_event_id,
                    disposition_event_id=disposition_event_id,
                    history_epoch=history_epoch,
                    local_sequence=disposition_sequence,
                    portfolio_version=resulting_version,
                    paper_trade_id=paper_trade_id,
                    reservation_id=reservation_id,
                )
            except (TypeError, ValueError) as exc:
                self._conn.rollback()
                return PaperAdmissionAck(
                    status="REJECTED",
                    error_code="INVALID_ADMISSION_REQUEST",
                    detail=str(exc),
                )
            except sqlite3.IntegrityError:
                self._conn.rollback()
                existing = self._existing_admission_result(
                    request,
                    request_payload=request_payload,
                )
                if existing is not None:
                    return existing
                return PaperAdmissionAck(
                    status="RETRYABLE",
                    error_code="INTEGRITY_CONFLICT",
                )
            except sqlite3.Error as exc:
                self._conn.rollback()
                return PaperAdmissionAck(
                    status="RETRYABLE",
                    error_code="SQLITE_ERROR",
                    detail=str(exc),
                )

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

    def advance_history_epoch_for_restore(self, minimum_epoch: int | None = None) -> int:
        """Required before accepting writes after restoring an older snapshot.

        Canonical commit order is ``(history_epoch, local_sequence)`` and must
        never regress. A live restore therefore passes the epoch the replaced
        live store had already reached, and the new epoch is
        ``max(this_snapshot_epoch, minimum_epoch) + 1`` so restored coordinates
        always dominate everything already published. Omitting ``minimum_epoch``
        keeps the historical single-snapshot behaviour
        (``this_snapshot_epoch + 1``).

        Existing event coordinates and stream watermarks are never rewritten;
        only ``meta`` advances, so restored history stays historically accurate
        until new events are written under the new epoch.
        """
        if minimum_epoch is not None and int(minimum_epoch) < 0:
            raise ValueError("minimum_epoch must be non-negative")
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
                current_epoch = int(meta["history_epoch"])
                floor = (
                    current_epoch
                    if minimum_epoch is None
                    else max(current_epoch, int(minimum_epoch))
                )
                new_epoch = floor + 1
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

    def _di_evidence_refs(
        self, event_type: str, payload: Mapping[str, object]
    ) -> list[str] | None:
        if event_type not in {
            DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
            DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,
        }:
            return None
        evidence_refs = payload.get("evidence_refs")
        if not evidence_refs:
            return []
        if not isinstance(evidence_refs, list):
            raise ValueError("evidence_refs must be a canonical array")
        return evidence_refs

    def _load_di_context_for_evidence(
        self, payload: Mapping[str, object]
    ) -> dict:
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
        return context_payload

    def _validate_request_context_ancestry(
        self, payload: Mapping[str, object]
    ) -> None:
        context_id = payload.get("context_id")
        if not isinstance(context_id, str) or not context_id:
            raise ValueError("request context_id is required")
        context_payload = self._load_di_payload(
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            idempotency_key=context_idempotency_key(context_id=context_id),
        )
        if payload.get("frozen_snapshot_hash") != context_payload.get(
            "snapshot_hash"
        ):
            raise ValueError("request/context snapshot mismatch")

    def _load_context_by_id(self, context_id: str) -> dict:
        return self._load_di_payload(
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            idempotency_key=context_idempotency_key(context_id=context_id),
        )

    def _load_request_context_for_id(
        self, request_id: str
    ) -> tuple[dict, dict]:
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
        return request_payload, context_payload

    def _load_invocation_by_id(self, invocation_id: str) -> dict:
        return self._load_di_payload(
            event_type=DECISION_INTELLIGENCE_INVOCATION_RECORDED,
            idempotency_key=invocation_idempotency_key(
                invocation_id=invocation_id
            ),
        )

    def _load_assessment_by_id(self, assessment_id: str) -> dict:
        return self._load_di_payload(
            event_type=DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,
            idempotency_key=assessment_idempotency_key(
                assessment_id=assessment_id
            ),
        )

    def _load_comparison_by_id(self, comparison_id: str) -> dict:
        return self._load_di_payload(
            event_type=DECISION_INTELLIGENCE_COMPARISON_RECORDED,
            idempotency_key=comparison_idempotency_key(
                comparison_id=comparison_id
            ),
        )

    def _load_role_result_by_id(self, result_id: str) -> dict:
        idempotency_key = self._role_result_idempotency_by_id.get(result_id)
        if idempotency_key is None:
            self._refresh_role_result_identity_projection()
            idempotency_key = self._role_result_idempotency_by_id.get(result_id)
        if idempotency_key is None:
            raise ValueError(
                "required decision_intelligence.role_result.recorded "
                "record is missing for ancestry validation"
            )
        return self._load_di_payload(
            event_type=DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _require_string_ref(payload: Mapping[str, object], field_name: str) -> str:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field_name} is required for ancestry validation")
        return value

    @staticmethod
    def _require_string_ref_list(
        payload: Mapping[str, object], field_name: str
    ) -> list[str]:
        value = payload.get(field_name)
        if not isinstance(value, list):
            raise ValueError(f"{field_name} must be a canonical array")
        if any(not isinstance(item, str) or not item for item in value):
            raise ValueError(f"{field_name} must contain string references")
        return value

    def _validate_di_downstream_ancestry(
        self, event_type: str, payload: Mapping[str, object]
    ) -> None:
        if event_type == DECISION_INTELLIGENCE_INVOCATION_RECORDED:
            request_id = self._require_string_ref(payload, "request_id")
            self._load_request_context_for_id(request_id)
            return

        if event_type == DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED:
            request_id = self._require_string_ref(payload, "request_id")
            self._load_request_context_for_id(request_id)
            invocation_ref = payload.get("invocation_ref")
            if invocation_ref is None:
                return
            if not isinstance(invocation_ref, str) or not invocation_ref:
                raise ValueError("invocation_ref is invalid")
            invocation = self._load_invocation_by_id(invocation_ref)
            if invocation.get("request_id") != request_id:
                raise ValueError("role result invocation request mismatch")
            return

        if event_type == DECISION_INTELLIGENCE_ASSESSMENT_RECORDED:
            request_id = self._require_string_ref(payload, "request_id")
            self._load_request_context_for_id(request_id)
            for result_id in self._require_string_ref_list(
                payload, "referenced_role_result_ids"
            ):
                role_result = self._load_role_result_by_id(result_id)
                if role_result.get("request_id") != request_id:
                    raise ValueError("assessment role result request mismatch")
            for invocation_id in self._require_string_ref_list(
                payload, "invocation_references"
            ):
                invocation = self._load_invocation_by_id(invocation_id)
                if invocation.get("request_id") != request_id:
                    raise ValueError("assessment invocation request mismatch")
            return

        if event_type != DECISION_INTELLIGENCE_COMPARISON_RECORDED:
            return

        request_id = self._require_string_ref(payload, "committee_request_id")
        context_id = self._require_string_ref(payload, "decision_context_id")
        assessment_id = self._require_string_ref(
            payload, "committee_assessment_id"
        )
        request_payload, context_payload = self._load_request_context_for_id(
            request_id
        )
        if request_payload.get("context_id") != context_id:
            raise ValueError("comparison request/context mismatch")
        if context_payload.get("context_id") != context_id:
            raise ValueError("comparison context mismatch")
        if payload.get("experiment_id") != request_payload.get("experiment_id"):
            raise ValueError("comparison experiment/request mismatch")

        assessment = self._load_assessment_by_id(assessment_id)
        if assessment.get("request_id") != request_id:
            raise ValueError("comparison assessment request mismatch")
        for invocation_id in self._require_string_ref_list(
            payload, "invocation_refs"
        ):
            invocation = self._load_invocation_by_id(invocation_id)
            if invocation.get("request_id") != request_id:
                raise ValueError("comparison invocation request mismatch")

    def _validate_di_record_supersession(
        self, event_type: str, payload: Mapping[str, object]
    ) -> None:
        supersedes_id = payload.get("supersedes_id")
        if supersedes_id is None:
            return
        if not isinstance(supersedes_id, str) or not supersedes_id:
            raise ValueError("supersedes_id is invalid")

        if event_type == DECISION_INTELLIGENCE_CONTEXT_RECORDED:
            self._load_context_by_id(supersedes_id)
            return

        if event_type == DECISION_INTELLIGENCE_REQUEST_RECORDED:
            superseded, _ = self._load_request_context_for_id(supersedes_id)
            context_id = self._require_string_ref(payload, "context_id")
            if superseded.get("context_id") != context_id:
                raise ValueError("request supersession context mismatch")
            self._load_context_by_id(context_id)
            return

        if event_type == DECISION_INTELLIGENCE_INVOCATION_RECORDED:
            superseded = self._load_invocation_by_id(supersedes_id)
            request_id = self._require_string_ref(payload, "request_id")
            if superseded.get("request_id") != request_id:
                raise ValueError("invocation supersession request mismatch")
            self._load_request_context_for_id(request_id)
            return

        if event_type == DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED:
            superseded = self._load_role_result_by_id(supersedes_id)
            request_id = self._require_string_ref(payload, "request_id")
            if superseded.get("request_id") != request_id:
                raise ValueError("role result supersession request mismatch")
            self._load_request_context_for_id(request_id)
            return

        if event_type == DECISION_INTELLIGENCE_ASSESSMENT_RECORDED:
            superseded = self._load_assessment_by_id(supersedes_id)
            request_id = self._require_string_ref(payload, "request_id")
            if superseded.get("request_id") != request_id:
                raise ValueError("assessment supersession request mismatch")
            self._load_request_context_for_id(request_id)
            return

        if event_type == DECISION_INTELLIGENCE_COMPARISON_RECORDED:
            superseded = self._load_comparison_by_id(supersedes_id)
            request_id = self._require_string_ref(
                payload, "committee_request_id"
            )
            context_id = self._require_string_ref(
                payload, "decision_context_id"
            )
            if superseded.get("committee_request_id") != request_id:
                raise ValueError("comparison supersession request mismatch")
            if superseded.get("decision_context_id") != context_id:
                raise ValueError("comparison supersession context mismatch")
            request_payload, context_payload = (
                self._load_request_context_for_id(request_id)
            )
            if request_payload.get("context_id") != context_id:
                raise ValueError(
                    "comparison supersession request/context mismatch"
                )
            if context_payload.get("context_id") != context_id:
                raise ValueError("comparison supersession context mismatch")

    @staticmethod
    def _evidence_manifest_and_cutoff(
        context_payload: Mapping[str, object],
    ) -> tuple[Mapping[str, object], datetime]:
        manifest = context_payload.get("evidence_eligibility_manifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("persisted context evidence manifest is invalid")
        cutoff_raw = context_payload.get("evidence_cutoff")
        if not isinstance(cutoff_raw, str):
            raise ValueError("persisted context evidence_cutoff is invalid")
        cutoff = _parse_aware_iso_timestamp(
            cutoff_raw,
            field_name="persisted context evidence_cutoff",
        )
        return manifest, cutoff

    @staticmethod
    def _validate_evidence_ref(
        evidence_ref: str,
        *,
        manifest: Mapping[str, object],
        cutoff: datetime,
    ) -> None:
        if evidence_ref not in manifest:
            raise ValueError(
                f"evidence reference is not in frozen manifest: {evidence_ref}"
            )
        entry = manifest[evidence_ref]
        if not isinstance(entry, Mapping):
            raise ValueError("persisted evidence manifest entry is invalid")
        available_at_raw = entry.get("available_at")
        if available_at_raw is None:
            return
        if not isinstance(available_at_raw, str):
            raise ValueError("persisted evidence availability is invalid")
        available_at = _parse_aware_iso_timestamp(
            available_at_raw,
            field_name="persisted evidence availability",
        )
        if available_at > cutoff:
            raise ValueError(
                "evidence reference is unavailable at evidence_cutoff"
            )

    def _validate_di_evidence_eligibility(
        self, event_type: str, payload: Mapping[str, object]
    ) -> None:
        evidence_refs = self._di_evidence_refs(event_type, payload)
        if evidence_refs is None:
            return
        context_payload = self._load_di_context_for_evidence(payload)
        manifest, cutoff = self._evidence_manifest_and_cutoff(context_payload)
        for evidence_ref in evidence_refs:
            self._validate_evidence_ref(
                evidence_ref,
                manifest=manifest,
                cutoff=cutoff,
            )

    def _load_transition_by_id(self, transition_id: str) -> dict:
        return self._load_di_payload(
            event_type=DECISION_INTELLIGENCE_TRANSITION_RECORDED,
            idempotency_key=transition_idempotency_key(
                transition_id=transition_id
            ),
        )

    def _validate_transition_supersession(
        self,
        payload: Mapping[str, object],
        supersedes_id: str,
    ) -> None:
        superseded = self._load_transition_by_id(supersedes_id)
        for field_name in (
            "request_id",
            "from_state",
            "to_state",
            "transition_time",
        ):
            if payload.get(field_name) != superseded.get(field_name):
                raise ValueError(
                    "transition supersession must preserve lifecycle "
                    f"coordinate {field_name}"
                )

    def _validate_di_transition_for_commit(
        self,
        event_type: str,
        payload: Mapping[str, object],
    ) -> None:
        if event_type != DECISION_INTELLIGENCE_TRANSITION_RECORDED:
            return

        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("transition request_id is required")
        self._load_di_payload(
            event_type=DECISION_INTELLIGENCE_REQUEST_RECORDED,
            idempotency_key=request_idempotency_key(request_id=request_id),
        )

        supersedes_id = payload.get("supersedes_id")
        if supersedes_id is not None:
            if not isinstance(supersedes_id, str) or not supersedes_id:
                raise ValueError("transition supersedes_id is invalid")
            self._validate_transition_supersession(
                payload,
                supersedes_id,
            )
            return

        self._refresh_request_lifecycle_projection()
        current_state = self._request_lifecycle_projection.get(
            request_id, "ELIGIBLE"
        )
        from_state = payload.get("from_state")
        if from_state != current_state:
            raise ValueError(
                "transition from_state does not match persisted request state"
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

    @staticmethod
    def _serialized_intent_payload(intent: WriterIntent) -> str:
        if intent.event_type in DECISION_INTELLIGENCE_EVENT_TYPES:
            return canonical_serialize(intent.payload)
        return json.dumps(
            intent.payload,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _validate_feature_bus_intent(intent: WriterIntent) -> dict:
        if intent.priority != FEATURE_BUS_PRIORITY:
            raise ValueError("feature bus events must use LOW priority")
        if intent.ops_handoff is not None:
            raise ValueError("feature bus events must not carry ops_handoff")
        return intent.payload

    def _load_paper_event_by_identity(
        self,
        event_type: str,
        identity: str,
    ) -> dict:
        contract = paper_event_contract(event_type)
        key = paper_evidence_idempotency_key(
            event_type,
            {contract.identity_field: identity},
        )
        row = self._conn.execute(
            """
            SELECT payload_json FROM events
            WHERE event_type = ? AND idempotency_key = ?
            """,
            (event_type, key),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"required {event_type} record is missing for ancestry validation"
            )
        try:
            raw = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise ValueError(f"persisted {event_type} payload is invalid JSON") from exc
        return validate_paper_evidence_payload(event_type, raw)

    def _load_quote_evidence_by_id(self, quote_evidence_id: str) -> dict:
        key = f"{PAPER_QUOTE_EVIDENCE_RECORDED}:{quote_evidence_id}"
        row = self._conn.execute(
            """
            SELECT payload_json FROM events
            WHERE event_type = ? AND idempotency_key = ?
            """,
            (PAPER_QUOTE_EVIDENCE_RECORDED, key),
        ).fetchone()
        if row is None:
            raise ValueError(
                "required Level-1 quote evidence is missing for execution validation"
            )
        try:
            raw = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise ValueError("persisted quote evidence payload is invalid JSON") from exc
        return validate_quote_evidence_payload(raw)

    def _admitted_dispositions(self, quote_currency: str | None = None) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT payload_json
            FROM events
            WHERE event_type = ?
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (PAPER_OPPORTUNITY_DISPOSITION_RECORDED,),
        ).fetchall()
        admitted: list[dict] = []
        for row in rows:
            try:
                raw = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError as exc:
                raise ValueError("persisted Paper v2 disposition is invalid JSON") from exc
            payload = validate_paper_evidence_payload(
                PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
                raw,
            )
            if payload.get("disposition") != "ADMITTED":
                continue
            if quote_currency is not None and payload.get("quote_currency") != quote_currency:
                continue
            admitted.append(payload)
        return admitted

    def _portfolio_state(self, quote_currency: str) -> tuple[int, float, int]:
        """Return version, reserved capital, active reservation count.

        In B/C-1 a committed ADMITTED disposition is the reservation authority.
        Reservation release is deliberately deferred to B/C-2, so the version is
        exactly the number of admissions for the quote-currency portfolio.
        """
        admitted = self._admitted_dispositions(quote_currency)
        reserved = sum(
            float(item["requested_reservation_amount"])
            for item in admitted
        )
        return len(admitted), reserved, len(admitted)

    def _admission_for_reservation(self, reservation_id: str) -> dict:
        matches = [
            item
            for item in self._admitted_dispositions()
            if item.get("reservation_id") == reservation_id
        ]
        if len(matches) != 1:
            if not matches:
                raise ValueError("reservation ancestry is missing")
            raise ValueError("reservation ancestry is ambiguous")
        return matches[0]

    def _validate_quote_matches_lineage(
        self,
        quote: Mapping[str, object],
        *,
        context: Mapping[str, object],
        admission: Mapping[str, object],
    ) -> None:
        if quote.get("instrument_version") != context.get("instrument_version"):
            raise ValueError("quote evidence instrument_version does not match decision context")
        if quote.get("quote_currency") != admission.get("quote_currency"):
            raise ValueError("quote evidence quote_currency does not match reservation")

    def _validate_paper_execution_ancestry(
        self,
        event_type: str,
        payload: Mapping[str, object],
    ) -> None:
        if event_type == PAPER_QUOTE_EVIDENCE_RECORDED:
            return

        if event_type == PAPER_ORDER_INTENT_RECORDED:
            context_id = self._require_string_ref(payload, "decision_context_id")
            context = self._load_context_by_id(context_id)
            reservation_id = self._require_string_ref(payload, "reservation_id")
            admission = self._admission_for_reservation(reservation_id)
            if admission.get("decision_context_id") != context_id:
                raise ValueError("order intent decision context does not match reservation")
            if admission.get("paper_trade_id") != payload.get("paper_trade_id"):
                raise ValueError("order intent paper_trade_id does not match reservation")
            if context.get("context_id") != context_id:
                raise ValueError("order intent decision context ancestry is invalid")
            if payload.get("intent_role") == "ENTRY" and (
                float(payload["requested_notional"])
                > float(admission["requested_reservation_amount"]) + 1e-9
            ):
                raise ValueError("entry requested_notional exceeds reserved capital")
            return

        if event_type == PAPER_EXECUTION_ATTEMPT_RECORDED:
            order_id = self._require_string_ref(payload, "order_intent_id")
            order = self._load_paper_event_by_identity(
                PAPER_ORDER_INTENT_RECORDED,
                order_id,
            )
            if order.get("paper_trade_id") != payload.get("paper_trade_id"):
                raise ValueError("execution attempt paper_trade_id does not match order intent")
            admission = self._admission_for_reservation(str(order["reservation_id"]))
            context = self._load_context_by_id(str(order["decision_context_id"]))
            state = ExecutionState(str(payload["execution_state"]))
            quote_ref = payload.get("market_evidence_ref")
            quote_required = state in {
                ExecutionState.ACCEPTED,
                ExecutionState.WORKING,
                ExecutionState.PARTIALLY_FILLED,
                ExecutionState.FILLED,
            }
            if quote_required and not isinstance(quote_ref, str):
                raise ValueError(
                    "execution attempt state requires exact Level-1 market_evidence_ref"
                )
            if quote_ref is not None:
                if not isinstance(quote_ref, str) or not quote_ref:
                    raise ValueError("market_evidence_ref is invalid")
                quote = self._load_quote_evidence_by_id(quote_ref)
                self._validate_quote_matches_lineage(
                    quote,
                    context=context,
                    admission=admission,
                )
            return

        if event_type != PAPER_FILL_RECORDED:
            return

        attempt_id = self._require_string_ref(payload, "execution_attempt_id")
        order_id = self._require_string_ref(payload, "order_intent_id")
        attempt = self._load_paper_event_by_identity(
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            attempt_id,
        )
        order = self._load_paper_event_by_identity(
            PAPER_ORDER_INTENT_RECORDED,
            order_id,
        )
        if attempt.get("order_intent_id") != order_id:
            raise ValueError("fill execution attempt does not belong to order intent")
        if attempt.get("paper_trade_id") != payload.get("paper_trade_id"):
            raise ValueError("fill paper_trade_id does not match execution attempt")
        if order.get("paper_trade_id") != payload.get("paper_trade_id"):
            raise ValueError("fill paper_trade_id does not match order intent")
        if order.get("side") != payload.get("side"):
            raise ValueError("fill side does not match order intent")

        quote_ref = payload.get("market_evidence_ref")
        if not isinstance(quote_ref, str) or not quote_ref:
            raise ValueError("fill requires exact Level-1 market_evidence_ref")
        admission = self._admission_for_reservation(str(order["reservation_id"]))
        context = self._load_context_by_id(str(order["decision_context_id"]))
        quote = self._load_quote_evidence_by_id(quote_ref)
        self._validate_quote_matches_lineage(
            quote,
            context=context,
            admission=admission,
        )

        prior_rows = self._conn.execute(
            """
            SELECT payload_json
            FROM events
            WHERE event_type = ?
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (PAPER_FILL_RECORDED,),
        ).fetchall()
        prior_quantity = 0.0
        for row in prior_rows:
            raw = json.loads(str(row["payload_json"]))
            prior = validate_paper_evidence_payload(PAPER_FILL_RECORDED, raw)
            if prior.get("order_intent_id") == order_id:
                prior_quantity += float(prior["quantity"])
        if prior_quantity + float(payload["quantity"]) > float(order["requested_quantity"]) + 1e-9:
            raise ValueError("aggregate fill quantity exceeds requested order quantity")

    def _validate_paper_execution_intent(self, intent: WriterIntent) -> dict:
        """Validate B/C-1 producer-submitted Paper v2 evidence.

        Opportunity disposition is intentionally absent here: admission must go
        through admit_paper_opportunity so the portfolio version check,
        capital/capacity decision, reservation identity and disposition commit
        share one canonical transaction.
        """
        if intent.priority != PAPER_EXECUTION_PRIORITY:
            raise ValueError("Paper v2 execution events must use LOW priority")
        if intent.ops_handoff is not None:
            raise ValueError("Paper v2 execution events must not carry ops_handoff")

        if intent.event_type == PAPER_QUOTE_EVIDENCE_RECORDED:
            normalized = validate_quote_evidence_payload(intent.payload)
            expected_key = quote_evidence_idempotency_key(normalized)
        else:
            if intent.event_type not in PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES:
                raise ValueError("Paper v2 event is not registered in B/C-1")
            normalized = validate_paper_evidence_payload(
                intent.event_type,
                intent.payload,
            )
            expected_key = paper_evidence_idempotency_key(
                intent.event_type,
                normalized,
            )
        if intent.idempotency_key != expected_key:
            raise ValueError(
                "Paper v2 idempotency_key does not match canonical record identity"
            )
        return normalized

    def _validate_paper_outcome_intent(self, intent: WriterIntent) -> dict:
        """Validate terminal paper economic evidence.

        Mirrors the feature-bus boundary: telemetry-class priority and no
        ops handoff. The payload itself is validated by the contract module so
        the writer never has to know what makes paper economics well formed.
        A correction additionally has its supersession relationship proven here,
        so an unverifiable claim can never be committed.
        """
        if intent.priority != PAPER_OUTCOME_PRIORITY:
            raise ValueError("paper outcome events must use LOW priority")
        if intent.ops_handoff is not None:
            raise ValueError("paper outcome events must not carry ops_handoff")
        normalized = validate_terminal_outcome_payload(intent.payload)
        self._validate_paper_outcome_supersession(normalized)
        return normalized

    def _load_paper_outcome_by_id(self, outcome_id: str) -> dict:
        """Load one committed terminal paper outcome by its canonical identity."""
        row = self._conn.execute(
            """
            SELECT payload_json FROM events
            WHERE event_type = ? AND idempotency_key = ?
            """,
            (
                PAPER_OUTCOME_TERMINAL_RECORDED,
                terminal_outcome_idempotency_key(outcome_id),
            ),
        ).fetchone()
        if row is None:
            raise ValueError(f"supersession target not found: {outcome_id}")
        try:
            raw = json.loads(row["payload_json"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"superseded paper outcome is unreadable: {outcome_id}"
            ) from exc
        return validate_terminal_outcome_payload(raw)

    def _validate_paper_outcome_supersession(self, payload: Mapping[str, object]) -> None:
        """Prove a correction legitimately supersedes what it names.

        The correction is append-only and keeps the superseded record intact, so
        the only thing that makes it safe is that the relationship is real. A
        correction that supersedes another trade's outcome would silently remove
        that trade's evidence from the effective set.
        """
        supersedes_id = str(payload.get("supersedes_id") or "").strip()
        if not supersedes_id:
            return
        superseded = self._load_paper_outcome_by_id(supersedes_id)
        assert_supersession_consistent(correction=payload, superseded=superseded)

    def _validate_decision_intelligence_intent(
        self, intent: WriterIntent
    ) -> dict:
        if intent.priority != "LOW":
            raise ValueError(
                "decision intelligence events must use LOW priority"
            )
        if intent.ops_handoff is not None:
            raise ValueError(
                "decision intelligence events must not carry ops_handoff"
            )
        normalized = validate_di_payload(intent.event_type, intent.payload)
        expected_key = canonical_di_idempotency_key(
            intent.event_type,
            normalized,
        )
        if intent.idempotency_key != expected_key:
            raise ValueError(
                "decision intelligence idempotency_key does not match "
                "canonical record identity"
            )
        self._validate_di_evidence_eligibility(
            intent.event_type,
            normalized,
        )
        if intent.event_type == DECISION_INTELLIGENCE_REQUEST_RECORDED:
            self._validate_request_context_ancestry(normalized)
        self._validate_di_downstream_ancestry(
            intent.event_type,
            normalized,
        )
        self._validate_di_record_supersession(
            intent.event_type,
            normalized,
        )
        return normalized

    @staticmethod
    def _validate_alert_ops_intent(intent: WriterIntent) -> None:
        if intent.event_type == _ALERT_CAPTURE_GAP_RECORDED:
            if intent.ops_handoff is not None:
                raise ValueError("capture_gap must not carry ops_handoff")
            return
        if intent.ops_handoff is None:
            raise ValueError("ops_handoff required")

        op = str(intent.ops_handoff.get("operation") or "")
        if op not in {"RECORD", "RELEASE"}:
            raise ValueError("invalid ops_handoff.operation")
        if intent.event_type == _ALERT_RESERVATION_RELEASED and op != "RELEASE":
            raise ValueError("released event requires RELEASE handoff")
        if intent.event_type == _ALERT_TRANSITION_RECORDED and op != "RECORD":
            raise ValueError("recorded event requires RECORD handoff")

        family = str(
            intent.ops_handoff.get("state_family")
            or intent.ops_handoff.get("state_file")
            or ""
        ).strip()
        if family not in {STATE_FAMILY_EARLY_WATCH}:
            raise ValueError("unsupported ops_handoff.state_family")

    def _validate_intent(self, intent: WriterIntent) -> dict:
        if intent.schema_version != SCHEMA_VERSION:
            raise ValueError("schema_version mismatch")
        if intent.priority not in {"HIGH", "NORMAL", "LOW"}:
            raise ValueError("invalid priority")
        if not intent.idempotency_key.strip():
            raise ValueError("idempotency_key required")
        if intent.event_type not in ACCEPTED_EVENT_TYPES:
            raise ValueError("unsupported event_type")

        raw = self._serialized_intent_payload(intent)
        if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload too large")

        if intent.event_type in FEATURE_BUS_EVENT_TYPES:
            return self._validate_feature_bus_intent(intent)
        if intent.event_type in DECISION_INTELLIGENCE_EVENT_TYPES:
            return self._validate_decision_intelligence_intent(intent)
        if intent.event_type in PAPER_OUTCOME_EVENT_TYPES:
            return self._validate_paper_outcome_intent(intent)
        if intent.event_type in PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES:
            return self._validate_paper_execution_intent(intent)

        self._validate_alert_ops_intent(intent)
        return intent.payload

    @staticmethod
    def _serialized_committed_payload(
        event_type: str, payload: dict
    ) -> str:
        if event_type in DECISION_INTELLIGENCE_EVENT_TYPES:
            return canonical_serialize(payload)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _stream_for_event(event_type: str) -> str:
        if event_type in DECISION_INTELLIGENCE_EVENT_TYPES:
            return DECISION_INTELLIGENCE_STREAM
        if event_type in FEATURE_BUS_EVENT_TYPES:
            return FEATURE_BUS_STREAM
        if event_type in PAPER_OUTCOME_EVENT_TYPES:
            return PAPER_OUTCOME_STREAM
        if event_type in PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES:
            return PAPER_EXECUTION_STREAM
        return STREAM_EARLY_WATCH

    @staticmethod
    def _requires_ops_handoff(event_type: str) -> bool:
        return (
            event_type != _ALERT_CAPTURE_GAP_RECORDED
            and event_type not in FEATURE_BUS_EVENT_TYPES
            and event_type not in DECISION_INTELLIGENCE_EVENT_TYPES
            and event_type not in PAPER_OUTCOME_EVENT_TYPES
            and event_type not in PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES
        )

    def _upsert_alert_identity_projection(
        self,
        *,
        intent: WriterIntent,
        event_id: str,
        history_epoch: int,
        local_sequence: int,
        now: str,
        identity: str,
        transition_key: str,
        action: str,
        message_id: object,
    ) -> None:
        if intent.event_type != _ALERT_TRANSITION_RECORDED:
            return
        normalized_message_id = (
            int(message_id) if message_id is not None else None
        )
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
                normalized_message_id,
                event_id,
                history_epoch,
                local_sequence,
                now,
            ),
        )

    def _insert_ops_handoff(
        self,
        *,
        intent: WriterIntent,
        event_id: str,
        now: str,
        handoff: Mapping[str, object],
        identity: str,
        transition_key: str,
        message_id: object,
        reservation_token: object,
        operation: str,
    ) -> None:
        if not self._requires_ops_handoff(intent.event_type):
            return
        normalized_message_id = (
            int(message_id) if message_id is not None else None
        )
        normalized_reservation_token = (
            str(reservation_token) if reservation_token else None
        )
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
                normalized_message_id,
                1 if bool(handoff.get("created_new")) else 0,
                normalized_reservation_token,
                STATE_FAMILY_EARLY_WATCH,
                now,
            ),
        )

    def _commit_new(
        self,
        intent: WriterIntent,
        *,
        normalized_payload: dict | None = None,
    ) -> WriterAck:
        now = _utc_now()
        event_id = _new_event_id()
        payload = (
            normalized_payload
            if normalized_payload is not None
            else intent.payload
        )
        payload_json = self._serialized_committed_payload(
            intent.event_type,
            payload,
        )
        handoff = intent.ops_handoff or {}
        action = "CREATE" if bool(handoff.get("created_new")) else "EDIT"
        identity = str(
            handoff.get("identity") or payload.get("identity") or ""
        )
        transition_key = str(handoff.get("transition_key") or "")
        message_id = handoff.get("message_id")
        reservation_token = handoff.get("reservation_token")
        operation = str(handoff.get("operation") or "")
        stream = self._stream_for_event(intent.event_type)

        self._conn.execute("BEGIN IMMEDIATE")
        meta = self._conn.execute(
            "SELECT history_epoch, next_local_sequence, schema_version "
            "FROM meta WHERE id = 1"
        ).fetchone()
        assert meta is not None
        if int(meta["schema_version"]) != SCHEMA_VERSION:
            self._conn.rollback()
            return WriterAck(
                status="REJECTED",
                error_code="DB_SCHEMA_MISMATCH",
            )

        history_epoch = int(meta["history_epoch"])
        local_sequence = int(meta["next_local_sequence"])

        self._validate_di_transition_for_commit(
            intent.event_type,
            payload,
        )
        if intent.event_type in PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES:
            self._validate_paper_execution_ancestry(
                intent.event_type,
                payload,
            )
        if intent.event_type in {
            DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
            DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,
        }:
            self._refresh_role_result_identity_projection()
            self._validate_di_downstream_ancestry(
                intent.event_type,
                payload,
            )
            self._validate_di_record_supersession(
                intent.event_type,
                payload,
            )

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

        self._upsert_alert_identity_projection(
            intent=intent,
            event_id=event_id,
            history_epoch=history_epoch,
            local_sequence=local_sequence,
            now=now,
            identity=identity,
            transition_key=transition_key,
            action=action,
            message_id=message_id,
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
        self._insert_ops_handoff(
            intent=intent,
            event_id=event_id,
            now=now,
            handoff=handoff,
            identity=identity,
            transition_key=transition_key,
            message_id=message_id,
            reservation_token=reservation_token,
            operation=operation,
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
        if intent.event_type == DECISION_INTELLIGENCE_TRANSITION_RECORDED:
            self._request_lifecycle_projection_watermark = (
                history_epoch,
                local_sequence,
            )
            if payload.get("supersedes_id") is None:
                self._request_lifecycle_projection[payload["request_id"]] = payload[
                    "to_state"
                ]
        if intent.event_type == DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED:
            self._role_result_idempotency_by_id[payload["result_id"]] = (
                intent.idempotency_key
            )
            self._role_result_projection_watermark = (
                history_epoch,
                local_sequence,
            )
        return WriterAck(
            status="OK",
            event_id=event_id,
            history_epoch=history_epoch,
            local_sequence=local_sequence,
        )
