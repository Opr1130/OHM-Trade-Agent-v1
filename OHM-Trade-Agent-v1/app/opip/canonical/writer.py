"""Canonical writer transaction engine (write connection only inside writer process)."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from app.opip.canonical.models import (
    PaperPortfolioState,
    PaperV2ActiveExposure,
    PaperV2ActiveExposures,
    PaperV2ExecutionState,
    PaperV2RecoverableExecutions,
    PendingHandoff,
    WriterAck,
    WriterIntent,
)
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
    MARKET_INSTRUMENT_VERSION_RECORDED,
    MARKET_OBSERVATION_RECORDED,
)
from app.opip.contracts.paper_execution import (
    ExecutionState,
    PositionState,
    ProtectionState,
    TerminalReconciliationState,
)
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_EXECUTION_PRIORITY,
    PAPER_EXECUTION_STREAM,
    PAPER_FILL_RECORDED,
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_PROTECTION_STATE_RECORDED,
    PAPER_PROTECTION_TRIGGER_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
    event_contract as paper_event_contract,
    paper_evidence_idempotency_key,
    validate_paper_evidence_payload,
)
from app.opip.contracts.paper_v2_identity import (
    paper_v2_entry_attempt_id,
    paper_v2_entry_fill_id,
    paper_v2_entry_order_intent_id,
    paper_v2_no_fill_reconciliation_id,
    paper_v2_protection_plan_id,
)
from app.opip.contracts.paper_execution_runtime import (
    PAPER_ACTION_ARMED_STATES,
    PAPER_ACTION_EXIT_SIDE,
    PAPER_ADMISSION_REQUEST_RECORDED,
    PAPER_DECISION_SNAPSHOT_RECORDED,
    PAPER_EXECUTION_BC1_WRITER_EVENT_TYPES,
    PAPER_PROTECTION_BC2_WRITER_EVENT_TYPES,
    PAPER_QUOTE_EVIDENCE_RECORDED,
    PAPER_TRIGGER_TYPES_REQUIRING_QUOTE,
    PAPER_V2_ALL_EVENT_TYPES,
    PAPER_V2_ATOMIC_ONLY_EVENT_TYPES,
    PAPER_V2_WRITER_EVENT_TYPES,
    PaperAdmissionAck,
    PaperAdmissionRequest,
    PaperProtectionActionAck,
    PaperProtectionActionRequest,
    admission_request_idempotency_key,
    admission_result_identities,
    decision_snapshot_idempotency_key,
    protection_action_idempotency_key,
    protection_transition_allowed,
    protection_transition_requires_trigger,
    quote_evidence_idempotency_key,
    resolve_capital_policy,
    validate_admission_request,
    validate_admission_request_record_payload,
    validate_decision_snapshot_payload,
    validate_protection_action_request,
    validate_quote_evidence_payload,
)
from app.opip.contracts.paper_outcome import (
    PAPER_OUTCOME_EVENT_TYPES,
    PAPER_OUTCOME_PRIORITY,
    PAPER_OUTCOME_STREAM,
    QUOTE_CURRENCIES,
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
from app.opip.market.instrument_version_store import instrument_version_from_payload

MAX_PAYLOAD_BYTES = 16 * 1024
_UTC_OFFSET = "+00:00"
_UTC_Z = "Z"
_ALERT_CAPTURE_GAP_RECORDED = "alert_governor.capture_gap.recorded"
_ALERT_RESERVATION_RELEASED = "alert_governor.reservation.released"
_ALERT_TRANSITION_RECORDED = "alert_governor.transition.recorded"

#: Shared canonical-writer SQL and messages, defined once because several
#: transactional paths must read the same meta row for the same reason.
_META_SEQUENCE_SQL = (
    "SELECT history_epoch, next_local_sequence, schema_version FROM meta WHERE id = 1"
)
_META_ROW_MISSING = "canonical meta row missing"
_EVENT_PAYLOADS_BY_TYPE_SQL = (
    "SELECT payload_json FROM events WHERE event_type = ? "
    "ORDER BY history_epoch ASC, local_sequence ASC"
)

#: Execution states from which an attempt can still receive fills. This is the
#: single definition of "fill-capable": fill validation and the exit-capacity
#: projection both use it, so a live attempt cannot be forgotten by one while the
#: other still honours it.
_FILLABLE_EXECUTION_STATES = frozenset(
    {
        ExecutionState.ACCEPTED,
        ExecutionState.WORKING,
        ExecutionState.PARTIALLY_FILLED,
        ExecutionState.FILLED,
    }
)

#: Numeric tolerance for canonical Paper v2 quantity/price comparisons.
_QUANTITY_TOLERANCE = 1e-9

#: String values of the fill-capable execution states. The single definition of
#: "this attempt can still receive fills", used both by fill validation and by the
#: exit-capacity projection, so the two cannot drift apart.
_FILLABLE_STATE_VALUES = frozenset(state.value for state in _FILLABLE_EXECUTION_STATES)

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
    | PAPER_V2_ALL_EVENT_TYPES
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
    | PAPER_V2_WRITER_EVENT_TYPES
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

    def paper_portfolio_state(self, quote_currency: str) -> PaperPortfolioState:
        """Read-only projection of one paper portfolio's concurrency token.

        Exists so a Paper-v2 producer can obtain the authoritative
        ``expected_portfolio_version`` before constructing the already-frozen
        admission request. Nothing else is exposed: not the underlying event rows,
        not another portfolio's figures.

        This is a pure read. It holds the existing writer lock, validates the quote
        currency against the existing canonical rule, and delegates to the existing
         ``_portfolio_state`` projection - it does not reimplement it. It performs
        no write of any kind, so it cannot insert an event, consume a local
        sequence, change the history epoch, advance a watermark, create an
        idempotency row or reservation, or mutate canonical evidence.
        """
        # Canonical identity discipline: a padded or malformed currency is
        # rejected rather than silently normalized, so the caller cannot ask for
        # one portfolio and read another.
        if (
            not isinstance(quote_currency, str)
            or not quote_currency
            or quote_currency != quote_currency.strip()
        ):
            return PaperPortfolioState(
                status="REJECTED",
                error_code="MALFORMED_QUOTE_CURRENCY",
                detail="quote_currency must be a non-empty canonical string",
            )
        if quote_currency not in QUOTE_CURRENCIES:
            return PaperPortfolioState(
                status="REJECTED",
                error_code="UNSUPPORTED_QUOTE_CURRENCY",
                detail=f"unsupported quote_currency: {quote_currency!r}",
            )
        with self._lock:
            try:
                version, reserved_capital, active_reservations = (
                    self._portfolio_state(quote_currency)
                )
            except (TypeError, ValueError) as exc:
                return PaperPortfolioState(
                    status="REJECTED",
                    error_code="PORTFOLIO_STATE_UNAVAILABLE",
                    detail=str(exc),
                )
            except sqlite3.Error as exc:
                return PaperPortfolioState(
                    status="RETRYABLE",
                    error_code="SQLITE_ERROR",
                    detail=str(exc),
                )
        return PaperPortfolioState(
            status="OK",
            quote_currency=quote_currency,
            portfolio_version=int(version),
            reserved_capital=float(reserved_capital),
            active_reservations=int(active_reservations),
        )

    def paper_v2_active_exposures(self) -> PaperV2ActiveExposures:
        """Read-only projection of every canonically active Paper-v2 exposure.

        Pure read: holds the writer lock, inspects committed canonical evidence, and
        reports each trade whose fills leave a positive remaining quantity. It
        writes nothing. An admitted reservation with no fills has no exposure and is
        not listed; a fully reconciled trade is no longer active.

        This is the canonical source of truth for "what is O'Pip Paper v2 holding".
        It deliberately derives exposure from committed fills and reconciliations
        rather than from scanner-local or process state, so a later scan cannot
        believe the portfolio is empty while canonical exposure exists.
        """
        with self._lock:
            try:
                return self._paper_v2_active_exposures_unlocked()
            except (TypeError, ValueError) as exc:
                return PaperV2ActiveExposures(
                    status="REJECTED",
                    error_code="ACTIVE_EXPOSURE_UNAVAILABLE",
                    detail=str(exc),
                )
            except sqlite3.Error as exc:
                return PaperV2ActiveExposures(
                    status="RETRYABLE",
                    error_code="SQLITE_ERROR",
                    detail=str(exc),
                )

    def _load_decision_snapshot_by_id(self, snapshot_id: str) -> dict:
        """Load one committed decision-snapshot wrapper by its identity.

        The decision snapshot uses its own contract rather than the paper-evidence
        contract, so it is addressed and validated separately instead of being
        forced through the evidence loader.
        """
        row = self._conn.execute(
            "SELECT payload_json FROM events WHERE event_type = ? AND idempotency_key = ?",
            (
                PAPER_DECISION_SNAPSHOT_RECORDED,
                f"{PAPER_DECISION_SNAPSHOT_RECORDED}:{snapshot_id}",
            ),
        ).fetchone()
        if row is None:
            return {}
        raw = json.loads(str(row["payload_json"]))
        return validate_decision_snapshot_payload(raw)

    def _decision_symbol_for_trade(self, paper_trade_id: str) -> str:
        """The production decision symbol for one trade, from canonical evidence.

        The action gate compares production symbols (``SOLUSD``), not venue aliases
        (``SOL/USD``), so the symbol is read from the committed decision snapshot
        the trade's context cites. A trade whose symbol cannot be proven returns an
        empty string rather than a guessed alias.
        """
        order = self._optional_paper_event_by_identity(
            PAPER_ORDER_INTENT_RECORDED,
            paper_v2_entry_order_intent_id(paper_trade_id),
        )
        if order is None:
            return ""
        context_id = str(order.get("decision_context_id") or "")
        if not context_id:
            return ""
        context = self._load_context_by_id(context_id)
        snapshot_id = str(context.get("snapshot_id") or "")
        if not snapshot_id:
            return ""
        snapshot = self._load_decision_snapshot_by_id(snapshot_id)
        if not snapshot:
            return ""
        inner = snapshot.get("snapshot_payload")
        if not isinstance(inner, Mapping):
            return ""
        return str(inner.get("symbol") or "")

    def _paper_v2_active_exposures_unlocked(self) -> PaperV2ActiveExposures:
        """Derive active exposure from committed canonical fills.

        Candidate trades come from committed ADMITTED dispositions, then each is
        narrowed to its own committed fills through the per-trade index. A trade
        with no remaining quantity never appears, so reservation-only admissions and
        closed trades are both excluded by construction rather than by filtering
        convention.
        """
        exposures: list[PaperV2ActiveExposure] = []
        for disposition in self._admitted_dispositions():
            disposition_id = str(disposition.get("disposition_id") or "")
            if not disposition_id:
                continue
            paper_trade_id = str(disposition.get("paper_trade_id") or "")
            if not paper_trade_id:
                continue
            totals = self._canonical_totals_for_trade(
                paper_trade_id,
                paper_v2_entry_order_intent_id(paper_trade_id),
            )
            if float(totals["remaining_quantity"]) <= 0:
                # No exposure: either never filled, or fully closed.
                continue
            plan = self._optional_paper_event_by_identity(
                PAPER_PROTECTION_PLAN_RECORDED,
                paper_v2_protection_plan_id(paper_trade_id),
            )
            plan_id = (
                str(plan["protection_plan_id"]) if plan is not None else None
            )
            exposures.append(
                PaperV2ActiveExposure(
                    paper_trade_id=paper_trade_id,
                    disposition_id=disposition_id,
                    symbol=self._decision_symbol_for_trade(paper_trade_id),
                    quote_currency=str(disposition.get("quote_currency") or ""),
                    # This frozen engine is long-only; the direction is a property
                    # of the engine rather than of the disposition payload.
                    direction="LONG",
                    filled_quantity=float(totals["entry_quantity"]),
                    exited_quantity=float(totals["exit_quantity"]),
                    remaining_quantity=float(totals["remaining_quantity"]),
                    remaining_notional_basis=self._remaining_notional_basis(
                        paper_trade_id,
                        paper_v2_entry_order_intent_id(paper_trade_id),
                        remaining_quantity=float(totals["remaining_quantity"]),
                        entry_quantity=float(totals["entry_quantity"]),
                    ),
                    protection_plan_id=plan_id,
                    protection_state=(
                        self._effective_protection_state(plan_id).value
                        if plan_id
                        else None
                    ),
                )
            )
        exposures.sort(key=lambda item: item.paper_trade_id)
        return PaperV2ActiveExposures(status="OK", exposures=exposures)

    def _remaining_notional_basis(
        self,
        paper_trade_id: str,
        entry_order_id: str,
        *,
        remaining_quantity: float,
        entry_quantity: float,
    ) -> float:
        """Committed cost basis of the quantity still held, in quote currency.

        Derived from the trade's own committed entry fills - the average committed
        entry price applied to the remaining quantity - so it is frozen by evidence
        rather than by a later market read. No mark-to-market model is applied: Paper
        v2 has no frozen market-risk model, and inventing one here would silently
        change portfolio-risk semantics.
        """
        if entry_quantity <= 0:
            return 0.0
        committed_notional = 0.0
        for fill in self._paper_fills_for_trade(paper_trade_id):
            if str(fill["order_intent_id"]) != entry_order_id:
                continue
            committed_notional += float(fill["quantity"]) * float(fill["price"])
        average_entry_price = committed_notional / entry_quantity
        return remaining_quantity * average_entry_price

    def paper_v2_recoverable_executions(self) -> PaperV2RecoverableExecutions:
        """Read-only projection of committed trades needing lifecycle continuation.

        Pure read. Lists trades with a fill-capable ENTRY attempt and no fill, with
        the committed attempt and quote payloads needed to finish the fill. Recovery
        must work without the original opportunity qualifying again, so this is
        driven by canonical evidence rather than by a fresh scan.
        """
        with self._lock:
            try:
                return self._paper_v2_recoverable_executions_unlocked()
            except (TypeError, ValueError) as exc:
                return PaperV2RecoverableExecutions(
                    status="REJECTED",
                    error_code="RECOVERABLE_STATE_UNAVAILABLE",
                    detail=str(exc),
                )
            except sqlite3.Error as exc:
                return PaperV2RecoverableExecutions(
                    status="RETRYABLE",
                    error_code="SQLITE_ERROR",
                    detail=str(exc),
                )

    def _paper_v2_recoverable_executions_unlocked(
        self,
    ) -> PaperV2RecoverableExecutions:
        """Fill-capable attempts that have no committed fill yet.

        Scoped to the fill-capable states, so the candidate set is the number of live
        attempts rather than the size of canonical history.
        """
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for state_value in sorted(_FILLABLE_STATE_VALUES):
            rows = self._conn.execute(
                "SELECT payload_json FROM events "
                "WHERE event_type = ? "
                "AND json_extract(payload_json, '$.execution_state') = ? "
                "ORDER BY history_epoch ASC, local_sequence ASC",
                (PAPER_EXECUTION_ATTEMPT_RECORDED, state_value),
            ).fetchall()
            for row in rows:
                attempt = validate_paper_evidence_payload(
                    PAPER_EXECUTION_ATTEMPT_RECORDED,
                    json.loads(str(row["payload_json"])),
                )
                paper_trade_id = str(attempt.get("paper_trade_id") or "")
                if not paper_trade_id or paper_trade_id in seen:
                    continue
                order_id = str(attempt.get("order_intent_id") or "")
                if not order_id:
                    continue
                if (
                    self._optional_paper_event_by_identity(
                        PAPER_FILL_RECORDED, paper_v2_entry_fill_id(order_id)
                    )
                    is not None
                ):
                    # Already filled; nothing to continue.
                    continue
                quote_ref = attempt.get("market_evidence_ref")
                if not isinstance(quote_ref, str) or not quote_ref:
                    continue
                seen.add(paper_trade_id)
                entries.append(
                    {
                        "paper_trade_id": paper_trade_id,
                        "execution_attempt_id": str(
                            attempt["execution_attempt_id"]
                        ),
                        "execution_attempt": attempt,
                        "quote_evidence": self._load_quote_evidence_by_id(quote_ref),
                    }
                )
        entries.sort(key=lambda entry: entry["paper_trade_id"])
        return PaperV2RecoverableExecutions(status="OK", entries=entries)

    def paper_v2_execution_state(self, disposition_id: str) -> PaperV2ExecutionState:
        """Read-only projection of one Paper-v2 trade's canonical progress.

        Pure read: holds the writer lock, inspects already-committed canonical
        evidence, and reports which stages exist. It writes nothing - no event, no
        sequence, no watermark, no idempotency row, no reservation.

        The trade identity is derived from the disposition identity, so a restart
        can ask about a trade before admission has happened.
        """
        if (
            not isinstance(disposition_id, str)
            or not disposition_id
            or disposition_id != disposition_id.strip()
        ):
            return PaperV2ExecutionState(
                status="REJECTED",
                error_code="MALFORMED_DISPOSITION_ID",
                detail="disposition_id must be a non-empty canonical string",
            )
        with self._lock:
            try:
                return self._paper_v2_execution_state_unlocked(disposition_id)
            except (TypeError, ValueError) as exc:
                return PaperV2ExecutionState(
                    status="REJECTED",
                    disposition_id=disposition_id,
                    error_code="EXECUTION_STATE_UNAVAILABLE",
                    detail=str(exc),
                )
            except sqlite3.Error as exc:
                return PaperV2ExecutionState(
                    status="RETRYABLE",
                    disposition_id=disposition_id,
                    error_code="SQLITE_ERROR",
                    detail=str(exc),
                )

    def _optional_paper_event_by_identity(
        self, event_type: str, identity: str
    ) -> dict | None:
        """Load one committed Paper-v2 record by its deterministic identity.

        A targeted lookup on the UNIQUE ``idempotency_key`` index, not a scan of
        the event history. Returns ``None`` when the stage has not committed, so a
        caller can use absence to decide what still needs to happen. A committed
        record whose stored payload no longer validates is integrity corruption and
        raises rather than reading as absent.
        """
        contract = paper_event_contract(event_type)
        key = paper_evidence_idempotency_key(
            event_type, {contract.identity_field: identity}
        )
        row = self._conn.execute(
            "SELECT payload_json FROM events WHERE event_type = ? AND idempotency_key = ?",
            (event_type, key),
        ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"persisted {event_type} payload is invalid JSON"
            ) from exc
        return validate_paper_evidence_payload(event_type, raw)

    def _paper_fills_for_trade(self, paper_trade_id: str) -> list[dict]:
        """Committed Paper-v2 fills for one trade, in commit order.

        Uses the additive ``paper_trade_id`` expression index, so this is a
        per-trade lookup rather than a scan of all historical fills.
        """
        rows = self._conn.execute(
            "SELECT payload_json FROM events "
            "WHERE event_type = ? "
            "AND json_extract(payload_json, '$.paper_trade_id') = ? "
            "ORDER BY history_epoch ASC, local_sequence ASC",
            (PAPER_FILL_RECORDED, paper_trade_id),
        ).fetchall()
        return [validate_paper_evidence_payload(PAPER_FILL_RECORDED, json.loads(str(row["payload_json"]))) for row in rows]

    def _validate_zero_fill_release_is_safe(self, paper_trade_id: str) -> None:
        """Refuse a zero-fill terminalization while exposure could still appear.

        A terminal reconciliation releases reserved capital and a position slot, so
        it may only be written when canonical evidence proves the trade can never
        produce exposure: no committed fill exists, and no committed ENTRY attempt
        is still in a fill-capable state. A crashed-but-accepted attempt is
        therefore *not* releasable - it must be resumed and reconciled, because
        releasing it would free capacity against a trade that can still fill.
        """
        fills = self._paper_fills_for_trade(paper_trade_id)
        if fills:
            raise ValueError(
                "ZERO_FILL_RELEASE_UNSAFE: a committed fill exists for this trade, so a "
                "zero-fill terminal reconciliation cannot describe it"
            )
        for state_value in _FILLABLE_STATE_VALUES:
            rows = self._conn.execute(
                "SELECT payload_json FROM events "
                "WHERE event_type = ? "
                "AND json_extract(payload_json, '$.paper_trade_id') = ? "
                "AND json_extract(payload_json, '$.execution_state') = ? "
                "LIMIT 1",
                (
                    PAPER_EXECUTION_ATTEMPT_RECORDED,
                    paper_trade_id,
                    state_value,
                ),
            ).fetchall()
            if rows:
                raise ValueError(
                    "ZERO_FILL_RELEASE_UNSAFE: a fill-capable ENTRY attempt is "
                    f"outstanding (execution_state={state_value}); resume it rather "
                    "than releasing the reservation"
                )

    def _paper_v2_execution_state_unlocked(
        self, disposition_id: str
    ) -> PaperV2ExecutionState:
        """Committed Paper-v2 progress for one trade, read by deterministic identity.

        Every stage this producer can create has a deterministic identity derived
        from the trade, so each is fetched through the UNIQUE ``idempotency_key``
        index. Nothing here scans historical event families, so restart cost does
        not grow with total canonical history while the writer lock is held.

        Payloads - not just identities - are returned, because a restart must reuse
        the committed stage verbatim rather than rebuild it.
        """
        paper_trade_id, reservation_id = admission_result_identities(disposition_id)
        state: dict = {
            "status": "OK",
            "disposition_id": disposition_id,
            "paper_trade_id": paper_trade_id,
            "reservation_id": reservation_id,
        }

        # Admission request (writer-owned record of the frozen request payload).
        request_row = self._conn.execute(
            "SELECT payload_json FROM events WHERE event_type = ? AND idempotency_key = ?",
            (
                PAPER_ADMISSION_REQUEST_RECORDED,
                admission_request_idempotency_key(disposition_id),
            ),
        ).fetchone()
        if request_row is not None:
            stored = json.loads(str(request_row["payload_json"]))
            state.update(
                {
                    "expected_portfolio_version": stored.get(
                        "expected_portfolio_version"
                    ),
                    "quote_currency": stored.get("quote_currency"),
                    "decision_context_id": stored.get("decision_context_id"),
                    "disposition": stored.get("guard_result"),
                    "requested_reservation_amount": stored.get(
                        "requested_reservation_amount"
                    ),
                }
            )

        # The committed disposition is the reservation authority.
        disposition = self._optional_paper_event_by_identity(
            PAPER_OPPORTUNITY_DISPOSITION_RECORDED, disposition_id
        )
        if disposition is not None:
            state.update(
                {
                    "disposition": disposition.get("disposition"),
                    "quote_currency": disposition.get("quote_currency"),
                    "decision_context_id": disposition.get("decision_context_id"),
                }
            )
            if disposition.get("disposition") == "ADMITTED":
                state["admitted"] = True

        # Each stage below is addressed by the identity this producer derives.
        entry_order_id = paper_v2_entry_order_intent_id(paper_trade_id)
        attempt_id = paper_v2_entry_attempt_id(entry_order_id)
        fill_id = paper_v2_entry_fill_id(entry_order_id)

        entry = self._optional_paper_event_by_identity(
            PAPER_ORDER_INTENT_RECORDED, entry_order_id
        )
        if entry is not None:
            state["entry_order_intent_id"] = entry_order_id
            state["entry_order_intent"] = entry

        attempt = self._optional_paper_event_by_identity(
            PAPER_EXECUTION_ATTEMPT_RECORDED, attempt_id
        )
        if attempt is not None:
            state["execution_attempt_id"] = attempt_id
            state["execution_attempt"] = attempt
            state["entry_attempt_fill_capable"] = (
                str(attempt.get("execution_state")) in _FILLABLE_STATE_VALUES
            )
            quote_ref = attempt.get("market_evidence_ref")
            if isinstance(quote_ref, str) and quote_ref:
                state["quote_evidence"] = self._load_quote_evidence_by_id(quote_ref)

        fill = self._optional_paper_event_by_identity(
            PAPER_FILL_RECORDED, fill_id
        )
        if fill is not None:
            state["fill_id"] = fill_id
            state["fill"] = fill
            if "quote_evidence" not in state:
                quote_ref = fill.get("market_evidence_ref")
                if isinstance(quote_ref, str) and quote_ref:
                    state["quote_evidence"] = self._load_quote_evidence_by_id(
                        quote_ref
                    )

        # Totals from this trade's committed fills only: entry from the targeted
        # fill identity, exits from the per-trade index.
        totals = self._canonical_totals_for_trade(paper_trade_id, entry_order_id)
        state["filled_quantity"] = float(totals["entry_quantity"])
        state["remaining_quantity"] = float(totals["remaining_quantity"])

        plan = self._optional_paper_event_by_identity(
            PAPER_PROTECTION_PLAN_RECORDED,
            paper_v2_protection_plan_id(paper_trade_id),
        )
        if plan is not None:
            state["protection_plan"] = plan

        terminal = self._optional_paper_event_by_identity(
            PAPER_RECONCILIATION_RECORDED,
            paper_v2_no_fill_reconciliation_id(paper_trade_id),
        )
        if terminal is not None:
            state["terminal_reconciliation"] = terminal

        return PaperV2ExecutionState.from_dict(state)

    def _canonical_totals_for_trade(
        self, paper_trade_id: str, entry_order_id: str
    ) -> dict:
        """Exposure and economics for one trade from its committed fills.

        Same arithmetic as :meth:`_canonical_fill_totals`, but scoped to one trade
        through the per-trade index so a single trade's state never requires
        scanning the whole fill history. The entry order's role is known by
        construction from its identity rather than by reading every order intent.
        """
        entry_quantity = 0.0
        exit_quantity = 0.0
        gross_pnl = 0.0
        execution_costs = 0.0
        for fill in self._paper_fills_for_trade(paper_trade_id):
            order_id = str(fill["order_intent_id"])
            if order_id == entry_order_id:
                role = "ENTRY"
            else:
                role = self._order_roles_for_trade(paper_trade_id).get(order_id)
                if role is None:
                    raise ValueError(
                        "canonical fill has no committed parent order intent role"
                    )
            if role not in {"ENTRY", "EXIT"}:
                raise ValueError(
                    "canonical fill parent order has an unsupported intent_role"
                )
            quantity = float(fill["quantity"])
            notional = quantity * float(fill["price"])
            execution_costs += (
                float(fill["fee_cost"])
                + float(fill["spread_cost"])
                + float(fill["slippage_cost"])
                + float(fill["other_supported_cost"])
            )
            if role == "ENTRY":
                entry_quantity += quantity
                gross_pnl -= notional
            else:
                exit_quantity += quantity
                gross_pnl += notional
        return {
            "entry_quantity": entry_quantity,
            "exit_quantity": exit_quantity,
            "remaining_quantity": entry_quantity - exit_quantity,
            "gross_pnl": gross_pnl,
            "execution_costs": execution_costs,
        }

    def _order_roles_for_trade(self, paper_trade_id: str) -> dict[str, str]:
        """Committed order-intent roles for one trade, from the per-trade index."""
        rows = self._conn.execute(
            "SELECT payload_json FROM events "
            "WHERE event_type = ? "
            "AND json_extract(payload_json, '$.paper_trade_id') = ?",
            (PAPER_ORDER_INTENT_RECORDED, paper_trade_id),
        ).fetchall()
        roles: dict[str, str] = {}
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            roles[str(payload["order_intent_id"])] = str(payload["intent_role"])
        return roles

    def admit_paper_opportunity(
        self,
        request: PaperAdmissionRequest,
    ) -> PaperAdmissionAck:
        """Atomically decide Paper v2 capital/capacity and record the disposition."""

        with self._lock:
            # Resolve the authoritative policy first so an unsupported version is
            # reported precisely and can never fall through to a decision. The
            # effective limits below come from this policy, never from the request,
            # so a producer cannot widen the portfolio gate by supplying values.
            try:
                capital_policy = resolve_capital_policy(
                    request.capital_policy_version
                )
            except (TypeError, ValueError) as exc:
                return PaperAdmissionAck(
                    status="REJECTED",
                    error_code="UNSUPPORTED_CAPITAL_POLICY_VERSION",
                    detail=str(exc),
                )

            if (
                request.portfolio_equity_limit != capital_policy.portfolio_equity_limit
                or request.portfolio_position_limit
                != capital_policy.portfolio_position_limit
            ):
                return PaperAdmissionAck(
                    status="REJECTED",
                    error_code="CAPITAL_POLICY_MISMATCH",
                    detail=(
                        "admission limits do not match the authoritative capital "
                        f"policy {capital_policy.policy_version}: expected "
                        f"equity={capital_policy.portfolio_equity_limit} "
                        f"positions={capital_policy.portfolio_position_limit}"
                    ),
                )

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
                    _META_SEQUENCE_SQL
                ).fetchone()
                if meta is None:
                    raise ValueError(_META_ROW_MISSING)
                if int(meta["schema_version"]) != SCHEMA_VERSION:
                    self._conn.rollback()
                    return PaperAdmissionAck(
                        status="REJECTED",
                        error_code="DB_SCHEMA_MISMATCH",
                    )

                # The decision context must already be canonical and must itself
                # authorize Paper v2 evaluation. Admission never invents or
                # reconstructs upstream eligibility/environment ancestry.
                context = self._load_context_by_id(request.decision_context_id)
                if context.get("eligibility") is not True:
                    self._conn.rollback()
                    return PaperAdmissionAck(
                        status="REJECTED",
                        error_code="DECISION_CONTEXT_INELIGIBLE",
                    )
                if context.get("environment") != "paper":
                    self._conn.rollback()
                    return PaperAdmissionAck(
                        status="REJECTED",
                        error_code="DECISION_CONTEXT_NOT_PAPER",
                    )

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

                if active_reservations >= capital_policy.portfolio_position_limit:
                    disposition = "CAPACITY_REJECTED"
                    reason_code = "PORTFOLIO_POSITION_LIMIT"
                elif (
                    reserved_capital + request.requested_reservation_amount
                    > capital_policy.portfolio_equity_limit + 1e-9
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

    # ------------------------------------------------------------------
    # B/C-2 writer-owned atomic protection action
    # ------------------------------------------------------------------

    def _protection_action_bundle(
        self,
        payloads: Mapping[str, Mapping[str, Any]],
    ) -> list[tuple[str, str, str, Mapping[str, Any]]]:
        """Role-ordered ``(role, event_type, idempotency_key, payload)`` for the bundle."""
        trigger = payloads["trigger"]
        exit_order_intent = payloads["exit_order_intent"]
        protection_state = payloads["protection_state"]
        return [
            (
                "trigger",
                PAPER_PROTECTION_TRIGGER_RECORDED,
                protection_action_idempotency_key(trigger),
                trigger,
            ),
            (
                "exit_order_intent",
                PAPER_ORDER_INTENT_RECORDED,
                paper_evidence_idempotency_key(
                    PAPER_ORDER_INTENT_RECORDED, exit_order_intent
                ),
                exit_order_intent,
            ),
            (
                "protection_state",
                PAPER_PROTECTION_STATE_RECORDED,
                paper_evidence_idempotency_key(
                    PAPER_PROTECTION_STATE_RECORDED, protection_state
                ),
                protection_state,
            ),
        ]

    @staticmethod
    def _canonical_payload_json(payload: Mapping[str, object]) -> str:
        return json.dumps(dict(payload), separators=(",", ":"), sort_keys=True)

    def _existing_protection_action_result(
        self,
        payloads: Mapping[str, Mapping[str, Any]],
    ) -> PaperProtectionActionAck | None:
        """Deterministic retry resolution for the atomic protection bundle.

        A retry is ``DUPLICATE_OK`` only when all three facts already exist with
        byte-identical payloads. A partially committed bundle is an integrity
        contradiction and fails closed: missing artifacts are never manufactured
        from current mutable state.
        """
        roles = ("trigger", "exit_order_intent", "protection_state")
        found: dict[str, tuple[Any, Mapping[str, Any]]] = {}
        for role, event_type, key, payload in self._protection_action_bundle(payloads):
            row = self._conn.execute(
                """
                SELECT event_id, history_epoch, local_sequence, payload_json
                FROM events
                WHERE event_type = ? AND idempotency_key = ?
                """,
                (event_type, key),
            ).fetchone()
            if row is not None:
                found[role] = (row, payload)
        if not found:
            return None
        if len(found) != 3:
            present = ",".join(sorted(found))
            missing = ",".join(sorted(set(roles) - set(found)))
            return PaperProtectionActionAck(
                status="REJECTED",
                error_code="PROTECTION_ACTION_BUNDLE_INCOMPLETE",
                detail=(
                    "protection action bundle is only partially committed "
                    f"(present={present}; missing={missing}); refusing to "
                    "reconstruct missing evidence"
                ),
            )
        for role in roles:
            row, payload = found[role]
            try:
                stored = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                return PaperProtectionActionAck(
                    status="REJECTED",
                    error_code="PROTECTION_ACTION_BUNDLE_CORRUPT",
                    detail=f"persisted {role} payload is invalid JSON",
                )
            if self._canonical_payload_json(stored) != self._canonical_payload_json(
                payload
            ):
                return PaperProtectionActionAck(
                    status="REJECTED",
                    trigger_event_id=str(found["trigger"][0]["event_id"]),
                    error_code="IDEMPOTENCY_PAYLOAD_CONFLICT",
                    detail=(
                        f"{role} identity already committed with a different payload"
                    ),
                )
        state_row = found["protection_state"][0]
        return PaperProtectionActionAck(
            status="DUPLICATE_OK",
            trigger_event_id=str(found["trigger"][0]["event_id"]),
            exit_order_intent_event_id=str(found["exit_order_intent"][0]["event_id"]),
            protection_state_event_id=str(state_row["event_id"]),
            history_epoch=int(state_row["history_epoch"]),
            local_sequence=int(state_row["local_sequence"]),
        )

    def trigger_paper_protection_action(
        self,
        request: PaperProtectionActionRequest,
    ) -> PaperProtectionActionAck:
        """Atomically record a protection trigger, its EXIT intent and TRIGGERED state.

        The three facts either all commit or none do, so a trigger is never
        stranded without the EXIT order intent it caused and a producer cannot
        assert TRIGGERED independently. Nothing here changes exposure: position
        quantity and economics move only when canonical B/C-1 fills later commit.
        """
        with self._lock:
            try:
                payloads = validate_protection_action_request(request)
            except (TypeError, ValueError) as exc:
                return PaperProtectionActionAck(
                    status="REJECTED",
                    error_code="INVALID_PROTECTION_ACTION_REQUEST",
                    detail=str(exc),
                )

            existing = self._existing_protection_action_result(payloads)
            if existing is not None:
                return existing

            trigger = payloads["trigger"]
            exit_order_intent = payloads["exit_order_intent"]
            protection_state = payloads["protection_state"]
            paper_trade_id = str(trigger["paper_trade_id"])
            plan_id = str(trigger["protection_plan_id"])
            now = _utc_now()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                meta = self._conn.execute(
                    _META_SEQUENCE_SQL
                ).fetchone()
                if meta is None:
                    raise ValueError(_META_ROW_MISSING)
                if int(meta["schema_version"]) != SCHEMA_VERSION:
                    self._conn.rollback()
                    return PaperProtectionActionAck(
                        status="REJECTED",
                        error_code="DB_SCHEMA_MISMATCH",
                    )
                history_epoch = int(meta["history_epoch"])
                next_sequence = int(meta["next_local_sequence"])

                # All action validation happens before any row is written, so a
                # rejected action commits nothing.
                context_id = self._validate_protection_action(
                    trigger=trigger,
                    exit_order_intent=exit_order_intent,
                    protection_state=protection_state,
                    paper_trade_id=paper_trade_id,
                    plan_id=plan_id,
                )

                # Deterministic insertion order: trigger, EXIT intent, TRIGGERED
                # state. The EXIT intent and the state both name the trigger they
                # were caused by; the trigger payload itself is never mutated to
                # carry the exit identity.
                trigger_event_id = self._insert_event_row_in_transaction(
                    event_type=PAPER_PROTECTION_TRIGGER_RECORDED,
                    idempotency_key=protection_action_idempotency_key(trigger),
                    payload=trigger,
                    history_epoch=history_epoch,
                    local_sequence=next_sequence,
                    now=now,
                    correlation_id=context_id,
                )
                exit_event_id = self._insert_event_row_in_transaction(
                    event_type=PAPER_ORDER_INTENT_RECORDED,
                    idempotency_key=paper_evidence_idempotency_key(
                        PAPER_ORDER_INTENT_RECORDED, exit_order_intent
                    ),
                    payload=exit_order_intent,
                    history_epoch=history_epoch,
                    local_sequence=next_sequence + 1,
                    now=now,
                    causation_id=trigger_event_id,
                    correlation_id=context_id,
                )
                # Validated after the trigger row exists in this transaction, so
                # the TRIGGERED rule is satisfied by the fact committed with it.
                self._validate_protection_state(protection_state)
                state_event_id = self._insert_event_row_in_transaction(
                    event_type=PAPER_PROTECTION_STATE_RECORDED,
                    idempotency_key=paper_evidence_idempotency_key(
                        PAPER_PROTECTION_STATE_RECORDED, protection_state
                    ),
                    payload=protection_state,
                    history_epoch=history_epoch,
                    local_sequence=next_sequence + 2,
                    now=now,
                    causation_id=trigger_event_id,
                    correlation_id=context_id,
                )
                state_sequence = next_sequence + 2
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
                    (PAPER_EXECUTION_STREAM, history_epoch, state_sequence, now),
                )
                self._conn.execute(
                    """
                    UPDATE meta
                    SET next_local_sequence = ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (state_sequence + 1, now),
                )
                self._conn.commit()
                return PaperProtectionActionAck(
                    status="OK",
                    trigger_event_id=trigger_event_id,
                    exit_order_intent_event_id=exit_event_id,
                    protection_state_event_id=state_event_id,
                    history_epoch=history_epoch,
                    local_sequence=state_sequence,
                )
            except (TypeError, ValueError) as exc:
                self._conn.rollback()
                return PaperProtectionActionAck(
                    status="REJECTED",
                    error_code="INVALID_PROTECTION_ACTION",
                    detail=str(exc),
                )
            except sqlite3.IntegrityError:
                self._conn.rollback()
                existing = self._existing_protection_action_result(payloads)
                if existing is not None:
                    return existing
                return PaperProtectionActionAck(
                    status="RETRYABLE",
                    error_code="INTEGRITY_CONFLICT",
                )
            except sqlite3.Error as exc:
                self._conn.rollback()
                return PaperProtectionActionAck(
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
                    raise RuntimeError(_META_ROW_MISSING)
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
            superseded = self._load_context_by_id(supersedes_id)
            # Context schema versions are distinct semantic contracts: they require
            # different facts, use different identity domains, and reconstruct into
            # separate typed stores. A cross-version supersession would therefore be
            # writer-accepted evidence the reader cannot reconstruct safely, so it is
            # refused before commit. Same-version supersession is unchanged.
            superseded_version = superseded.get("schema_version")
            superseding_version = payload.get("schema_version")
            if superseded_version != superseding_version:
                raise ValueError(
                    "cross-version decision context supersession is not allowed "
                    f"(target schema_version={superseded_version!r}, "
                    f"superseding schema_version={superseding_version!r})"
                )
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

    def _load_instrument_version_by_id(self, instrument_version_id: str) -> dict:
        """Resolve a canonical instrument version, failing closed when unprovable.

        The authoritative mapping already exists: instrument versions are
        committed to this same canonical store as ``MARKET_INSTRUMENT_VERSION_``
        events. Reusing it avoids inventing a parallel instrument registry, and
        the payload is rebuilt through the canonical verifier so a committed row
        whose declared identity disagrees with its own reference data is rejected
        instead of being trusted.
        """
        wanted = str(instrument_version_id or "").strip()
        if not wanted:
            raise ValueError("decision context instrument_version is required")
        rows = self._conn.execute(
            _EVENT_PAYLOADS_BY_TYPE_SQL,
            (MARKET_INSTRUMENT_VERSION_RECORDED,),
        ).fetchall()
        resolved: dict | None = None
        for row in rows:
            try:
                raw = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "persisted instrument version payload is invalid JSON"
                ) from exc
            if str(raw.get("instrument_version_id") or "").strip() != wanted:
                continue
            try:
                version = instrument_version_from_payload(raw)
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(
                    "canonical instrument version payload is not verifiable"
                ) from exc
            candidate = {
                "instrument_version_id": version.instrument_version_id,
                "venue": version.venue,
                "base_asset": version.base_asset,
                "quote_currency": version.quote_currency,
                "venue_instrument_id": version.venue_instrument_id,
            }
            if resolved is not None and resolved != candidate:
                raise ValueError(
                    "canonical instrument version identity is ambiguous"
                )
            resolved = candidate
        if resolved is None:
            raise ValueError(
                "decision context instrument_version is not a registered "
                "canonical instrument version"
            )
        return resolved

    def _committed_sibling_sequences(
        self,
        *,
        event_type: str,
        parent_field: str,
        parent_value: str,
        sequence_field: str,
        identity_field: str,
        identity_value: str,
    ) -> list[int]:
        """Sequences already committed for one canonical sibling scope.

        The incoming event is excluded by identity so an idempotent retry of an
        already-committed record is never treated as a duplicate of itself. The
        caller runs inside the write transaction, so concurrent siblings cannot
        both observe the same predecessor set.
        """
        rows = self._conn.execute(
            _EVENT_PAYLOADS_BY_TYPE_SQL,
            (event_type,),
        ).fetchall()
        sequences: list[int] = []
        for row in rows:
            try:
                raw = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"persisted {event_type} payload is invalid JSON"
                ) from exc
            if str(raw.get(parent_field) or "") != parent_value:
                continue
            if str(raw.get(identity_field) or "") == identity_value:
                continue
            sequences.append(int(raw[sequence_field]))
        return sequences

    def _require_monotonic_sibling_sequence(
        self,
        *,
        event_type: str,
        payload: Mapping[str, object],
        parent_field: str,
        parent_value: str,
        sequence_field: str,
        identity_field: str,
        scope: str,
    ) -> None:
        """Enforce one unambiguous ordering for a sibling sequence.

        Sequences must strictly increase within their canonical parent scope, so
        neither a duplicate nor a regressing value can be committed and the field
        keeps a single deterministic interpretation for downstream consumers.
        """
        candidate = int(payload[sequence_field])  # type: ignore[arg-type]
        existing = self._committed_sibling_sequences(
            event_type=event_type,
            parent_field=parent_field,
            parent_value=parent_value,
            sequence_field=sequence_field,
            identity_field=identity_field,
            identity_value=str(payload.get(identity_field) or ""),
        )
        if candidate in existing:
            raise ValueError(
                f"{sequence_field} {candidate} is already committed for this {scope}"
            )
        if existing and candidate < max(existing):
            raise ValueError(
                f"{sequence_field} {candidate} regresses behind a committed "
                f"sibling sequence for this {scope}"
            )

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
        Reservation release is deferred to B/C-2 and is now driven by canonical
        terminal reconciliation: a reservation is released from the active
        projection only once its trade is FINAL_VERIFIED. Until then it remains
        active through plan creation, activation, trigger, exit intent, attempts,
        partial fills and unverified FLAT state.

        The portfolio version stays the cumulative admission count so it remains a
        monotone concurrency token; only the reservation projection is released.
        Quote currencies stay separate: only this currency's verified trades can
        release capacity here.
        """
        admitted = self._admitted_dispositions(quote_currency)
        released = self._verified_paper_trade_ids(quote_currency)
        active = [
            item
            for item in admitted
            if str(item.get("paper_trade_id")) not in released
        ]
        reserved = sum(
            float(item["requested_reservation_amount"])
            for item in active
        )
        return len(admitted), reserved, len(active)

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
        """Bind quote evidence to the canonical execution instrument identity.

        Comparing self-declared strings alone is insufficient: a producer could
        copy a compatible ``instrument_version`` while recording a different venue
        or native symbol. The authoritative identity is therefore resolved from
        the canonical instrument-version registry the decision context names, and
        the quote must match it on every identity dimension. If the instrument
        cannot be proven canonically, the evidence is rejected rather than trusted.
        """
        instrument_version_id = str(context.get("instrument_version") or "")
        if quote.get("instrument_version") != instrument_version_id:
            raise ValueError("quote evidence instrument_version does not match decision context")

        instrument = self._load_instrument_version_by_id(instrument_version_id)
        canonical_venue = str(instrument.get("venue") or "").strip().upper()
        if str(quote.get("venue") or "").strip().upper() != canonical_venue:
            raise ValueError(
                "quote evidence venue does not match the canonical instrument version"
            )
        canonical_native_symbol = str(
            instrument.get("venue_instrument_id") or ""
        ).strip()
        if str(quote.get("native_symbol") or "").strip() != canonical_native_symbol:
            raise ValueError(
                "quote evidence native_symbol does not match the canonical "
                "instrument version"
            )
        canonical_quote_currency = str(
            instrument.get("quote_currency") or ""
        ).strip().upper()
        if str(quote.get("quote_currency") or "").strip().upper() != canonical_quote_currency:
            raise ValueError(
                "quote evidence quote_currency does not match the canonical "
                "instrument version"
            )
        if quote.get("quote_currency") != admission.get("quote_currency"):
            raise ValueError("quote evidence quote_currency does not match reservation")

    @staticmethod
    def _temporal_bounds(
        evidence: object,
        *,
        field_name: str,
    ) -> tuple[datetime, datetime]:
        if not isinstance(evidence, Mapping):
            raise ValueError(f"{field_name} must be temporal evidence")
        precision = str(evidence.get("precision") or "")
        if precision == "EXACT":
            raw = evidence.get("occurred_at")
            if not isinstance(raw, str):
                raise ValueError(f"{field_name}.occurred_at is required")
            moment = _parse_aware_iso_timestamp(raw, field_name=field_name)
            return moment, moment
        if precision == "BOUNDED":
            raw_start = evidence.get("window_start")
            raw_end = evidence.get("window_end")
            if not isinstance(raw_start, str) or not isinstance(raw_end, str):
                raise ValueError(f"{field_name} bounded window is incomplete")
            start = _parse_aware_iso_timestamp(raw_start, field_name=field_name)
            end = _parse_aware_iso_timestamp(raw_end, field_name=field_name)
            if end < start:
                raise ValueError(f"{field_name} bounded window is inverted")
            return start, end
        raise ValueError(
            f"{field_name} must be EXACT or BOUNDED for execution evidence"
        )

    def _validate_quote_causality(
        self,
        quote: Mapping[str, object],
        *,
        execution_time: object,
        execution_time_field: str,
    ) -> None:
        # For bounded evidence, preserve uncertainty and fail closed unless the
        # entire quote interval is no later than the earliest possible execution
        # time. Overlapping intervals cannot prove the quote was already known.
        _, quote_end = self._temporal_bounds(
            quote.get("quote_time"),
            field_name="quote_time",
        )
        execution_start, _ = self._temporal_bounds(
            execution_time,
            field_name=execution_time_field,
        )
        if quote_end > execution_start:
            raise ValueError(
                "Level-1 quote evidence is not proven available before execution"
            )

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
            # Long-only Paper v2 role/side semantics are frozen here so an
            # unsupported pair cannot reach conservation, protection eligibility
            # or the reservation controls with the wrong economic meaning.
            role = str(payload.get("intent_role"))
            side = str(payload.get("side"))
            if role == "ENTRY" and side != "BUY":
                raise ValueError("ENTRY order intent must use side BUY")
            if role == "EXIT" and side != "SELL":
                raise ValueError("EXIT order intent must use side SELL")
            trade_id = self._require_string_ref(payload, "paper_trade_id")
            # Terminal FINAL_VERIFIED trade: no new economic mutation. Exact
            # replay never reaches here because idempotency resolves first.
            self._require_trade_not_final_verified(trade_id, "an order intent")
            if role == "ENTRY" and (
                float(payload["requested_notional"])
                > float(admission["requested_reservation_amount"]) + 1e-9
            ):
                raise ValueError("entry requested_notional exceeds reserved capital")
            if role == "EXIT":
                # Exit capacity is admitted against fills *and* already claimed,
                # still-unfilled exit intents, so competing exits cannot together
                # promise more than the trade actually holds.
                capacity = self._exit_capacity(trade_id)
                if (
                    float(payload["requested_quantity"])
                    > float(capacity["available_exit_quantity"]) + 1e-9
                ):
                    raise ValueError(
                        "exit order intent exceeds available canonical exit capacity"
                    )
            return

        if event_type == PAPER_EXECUTION_ATTEMPT_RECORDED:
            order_id = self._require_string_ref(payload, "order_intent_id")
            self._require_trade_not_final_verified(
                str(payload["paper_trade_id"]), "an execution attempt"
            )
            order = self._load_paper_event_by_identity(
                PAPER_ORDER_INTENT_RECORDED,
                order_id,
            )
            if order.get("paper_trade_id") != payload.get("paper_trade_id"):
                raise ValueError("execution attempt paper_trade_id does not match order intent")
            # Attempts are sequenced within their parent order intent, so the
            # order is the sibling scope that gives attempt_seq one meaning.
            self._require_monotonic_sibling_sequence(
                event_type=PAPER_EXECUTION_ATTEMPT_RECORDED,
                payload=payload,
                parent_field="order_intent_id",
                parent_value=order_id,
                sequence_field="attempt_seq",
                identity_field="execution_attempt_id",
                scope="order intent",
            )
            admission = self._admission_for_reservation(str(order["reservation_id"]))
            context = self._load_context_by_id(str(order["decision_context_id"]))
            state = ExecutionState(str(payload["execution_state"]))
            fillable_states = _FILLABLE_EXECUTION_STATES
            accepted_quantity = payload.get("accepted_quantity")
            if state in fillable_states:
                if not isinstance(accepted_quantity, (int, float)) or isinstance(
                    accepted_quantity, bool
                ):
                    raise ValueError(
                        "fillable execution attempt requires accepted_quantity"
                    )
                accepted = float(accepted_quantity)
                if accepted <= 0:
                    raise ValueError(
                        "fillable execution attempt requires positive accepted_quantity"
                    )
                if accepted > float(order["requested_quantity"]) + 1e-9:
                    raise ValueError(
                        "execution attempt accepted_quantity exceeds requested order quantity"
                    )

            quote_ref = payload.get("market_evidence_ref")
            quote_required = state in fillable_states
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
                self._validate_quote_causality(
                    quote,
                    execution_time=payload.get("attempt_time"),
                    execution_time_field="attempt_time",
                )
            return

        if event_type != PAPER_FILL_RECORDED:
            return

        attempt_id = self._require_string_ref(payload, "execution_attempt_id")
        order_id = self._require_string_ref(payload, "order_intent_id")
        self._require_trade_not_final_verified(
            str(payload["paper_trade_id"]), "a fill"
        )
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

        attempt_state = ExecutionState(str(attempt["execution_state"]))
        fillable_states = _FILLABLE_EXECUTION_STATES
        if attempt_state not in fillable_states:
            raise ValueError("fill cannot reference a non-fillable execution attempt")
        # Fills are sequenced within their parent execution attempt, which is the
        # scope the frozen fill contract already defines for fill_seq.
        self._require_monotonic_sibling_sequence(
            event_type=PAPER_FILL_RECORDED,
            payload=payload,
            parent_field="execution_attempt_id",
            parent_value=attempt_id,
            sequence_field="fill_seq",
            identity_field="fill_id",
            scope="execution attempt",
        )
        accepted_quantity = attempt.get("accepted_quantity")
        if not isinstance(accepted_quantity, (int, float)) or isinstance(
            accepted_quantity, bool
        ):
            raise ValueError("fill parent attempt is missing accepted_quantity")
        accepted_quantity = float(accepted_quantity)
        if accepted_quantity <= 0:
            raise ValueError("fill parent attempt accepted_quantity must be positive")

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
        self._validate_quote_causality(
            quote,
            execution_time=payload.get("fill_time"),
            execution_time_field="fill_time",
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
        prior_order_quantity = 0.0
        prior_attempt_quantity = 0.0
        for row in prior_rows:
            raw = json.loads(str(row["payload_json"]))
            prior = validate_paper_evidence_payload(PAPER_FILL_RECORDED, raw)
            quantity = float(prior["quantity"])
            if prior.get("order_intent_id") == order_id:
                prior_order_quantity += quantity
            if prior.get("execution_attempt_id") == attempt_id:
                prior_attempt_quantity += quantity

        fill_quantity = float(payload["quantity"])
        if prior_attempt_quantity + fill_quantity > accepted_quantity + 1e-9:
            raise ValueError(
                "aggregate fill quantity exceeds execution attempt accepted_quantity"
            )
        if (
            prior_order_quantity + fill_quantity
            > float(order["requested_quantity"]) + 1e-9
        ):
            raise ValueError("aggregate fill quantity exceeds requested order quantity")

        # Aggregate exit conservation at commit: however the exits are split
        # across orders and attempts, canonical exit quantity may never exceed the
        # trade's canonical entry exposure. Without this, two pending exits could
        # each honour their own order cap and still together over-close the trade.
        if str(order["intent_role"]) == "EXIT":
            committed = self._canonical_fill_totals(str(payload["paper_trade_id"]))
            if (
                float(committed["exit_quantity"]) + fill_quantity
                > float(committed["entry_quantity"]) + 1e-9
            ):
                raise ValueError(
                    "aggregate exit quantity exceeds canonical entry exposure"
                )

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
        elif intent.event_type == PAPER_DECISION_SNAPSHOT_RECORDED:
            # The decision snapshot verifies its own content binding, so a
            # fabricated or mismatched hash cannot reach the store. Its key is
            # anchored on snapshot identity, so an identical retry is
            # DUPLICATE_OK while different content under the same identity hits
            # the same-key conflict check below.
            normalized = validate_decision_snapshot_payload(intent.payload)
            expected_key = decision_snapshot_idempotency_key(normalized)
        else:
            if intent.event_type not in PAPER_V2_WRITER_EVENT_TYPES:
                raise ValueError("Paper v2 event is not registered for runtime")
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

    # ------------------------------------------------------------------
    # B/C-2 canonical protection and terminal reconciliation authority
    # ------------------------------------------------------------------

    def _committed_paper_rows(self, event_type: str) -> list[dict]:
        """All committed payloads for one Paper v2 event type, in commit order."""
        rows = self._conn.execute(
            _EVENT_PAYLOADS_BY_TYPE_SQL,
            (event_type,),
        ).fetchall()
        payloads: list[dict] = []
        for row in rows:
            try:
                raw = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"persisted {event_type} payload is invalid JSON"
                ) from exc
            payloads.append(
                validate_paper_evidence_payload(event_type, raw)
            )
        return payloads

    def _admitted_trade(self, paper_trade_id: str) -> dict:
        """The ADMITTED disposition that owns this trade, or fail closed.

        Protection and reconciliation evidence must attach to a trade the writer
        actually admitted; ancestry is never taken on the producer's word.
        """
        matches = [
            item
            for item in self._admitted_dispositions()
            if item.get("paper_trade_id") == paper_trade_id
        ]
        if len(matches) != 1:
            if not matches:
                raise ValueError("paper_trade_id is not an admitted Paper v2 trade")
            raise ValueError("paper_trade_id is ambiguous across admissions")
        return matches[0]

    def _order_role_by_id(self) -> dict[str, str]:
        """Map each committed order intent to its canonical ``intent_role``.

        Exposure and economics are classified by the parent order's role, never by
        a fill's own side: the role is what the trade actually intends, so a
        mislabelled fill cannot smuggle itself into the wrong side of the
        conservation identity.
        """
        roles: dict[str, str] = {}
        for order in self._committed_paper_rows(PAPER_ORDER_INTENT_RECORDED):
            roles[str(order["order_intent_id"])] = str(order["intent_role"])
        return roles

    def _canonical_fill_totals(self, paper_trade_id: str) -> dict:
        """Exposure and economics derived strictly from canonical fills.

        Position quantity is never taken from a trigger, a protection state, an
        exit intent or an attempted execution - only from fills that the canonical
        writer actually committed, classified by their parent order's canonical
        role.
        """
        roles = self._order_role_by_id()
        entry_quantity = 0.0
        exit_quantity = 0.0
        gross_pnl = 0.0
        execution_costs = 0.0
        for fill in self._committed_paper_rows(PAPER_FILL_RECORDED):
            if fill.get("paper_trade_id") != paper_trade_id:
                continue
            order_id = str(fill["order_intent_id"])
            role = roles.get(order_id)
            if role is None:
                raise ValueError(
                    "canonical fill has no committed parent order intent role"
                )
            if role not in {"ENTRY", "EXIT"}:
                raise ValueError(
                    "canonical fill parent order has an unsupported intent_role"
                )
            quantity = float(fill["quantity"])
            notional = quantity * float(fill["price"])
            execution_costs += (
                float(fill["fee_cost"])
                + float(fill["spread_cost"])
                + float(fill["slippage_cost"])
                + float(fill["other_supported_cost"])
            )
            if role == "ENTRY":
                entry_quantity += quantity
                gross_pnl -= notional
            else:
                exit_quantity += quantity
                gross_pnl += notional
        return {
            "entry_quantity": entry_quantity,
            "exit_quantity": exit_quantity,
            "remaining_quantity": entry_quantity - exit_quantity,
            "gross_pnl": gross_pnl,
            "execution_costs": execution_costs,
        }

    def _outstanding_exit_quantity(self, paper_trade_id: str) -> float:
        """Exit quantity already claimed by live, still-fillable EXIT intents.

        Partially filled orders count only for their unfilled remainder. These
        units are not yet economic exits, but they are already spoken for, so a new
        protection EXIT must not claim them again or two pending exits could later
        both fill and over-close the trade.

        An order is treated as dead only when it has at least one committed attempt
        and *none* of them can still receive a fill. A higher ``attempt_seq`` does
        not supersede an older attempt - B/C-1 freezes no supersession semantics,
        and fill validation accepts a fill against any fill-capable attempt - so an
        earlier WORKING or PARTIALLY_FILLED attempt keeps the unfilled remainder
        reserved even when a later attempt was rejected, cancelled or expired.
        Without that rule a live attempt could still fill while a replacement EXIT
        claimed the same exposure.

        Once every attempt for the order is non-fillable, the unfilled remainder is
        provably dead and stops counting, so a failed exit no longer permanently
        shrinks ``available_exit_quantity``. An order with no attempt yet is still a
        live claim: it was committed precisely to consume that capacity.
        Already-filled quantity is untouched either way: it is canonical history
        and still reduces exposure through the fill-derived totals.
        """
        roles = self._order_role_by_id()
        filled_by_order: dict[str, float] = {}
        for fill in self._committed_paper_rows(PAPER_FILL_RECORDED):
            if fill.get("paper_trade_id") != paper_trade_id:
                continue
            order_id = str(fill["order_intent_id"])
            if roles.get(order_id) != "EXIT":
                continue
            filled_by_order[order_id] = filled_by_order.get(order_id, 0.0) + float(
                fill["quantity"]
            )

        # Committed attempts per order. An order is dead only when it has at least
        # one attempt and *none* of them can still receive a fill. A higher
        # attempt_seq does not supersede an older one: B/C-1 freezes no such
        # semantics, and fill validation accepts fills against any fill-capable
        # attempt, so an earlier WORKING attempt is still live evidence even if a
        # later attempt was rejected.
        attempts_by_order: dict[str, list[str]] = {}
        for attempt in self._committed_paper_rows(PAPER_EXECUTION_ATTEMPT_RECORDED):
            if attempt.get("paper_trade_id") != paper_trade_id:
                continue
            attempts_by_order.setdefault(str(attempt["order_intent_id"]), []).append(
                str(attempt["execution_state"])
            )

        outstanding = 0.0
        for order in self._committed_paper_rows(PAPER_ORDER_INTENT_RECORDED):
            if order.get("paper_trade_id") != paper_trade_id:
                continue
            if str(order["intent_role"]) != "EXIT":
                continue
            order_id = str(order["order_intent_id"])
            states = attempts_by_order.get(order_id)
            # An order with no attempt yet is still a live claim against exit
            # capacity: it was committed precisely to consume that capacity.
            if states and not any(
                state in _FILLABLE_STATE_VALUES for state in states
            ):
                continue
            unfilled = float(order["requested_quantity"]) - filled_by_order.get(
                order_id, 0.0
            )
            if unfilled > 0:
                outstanding += unfilled
        return outstanding

    def _exit_capacity(self, paper_trade_id: str) -> dict:
        """Canonical exit capacity: fill-derived exposure minus claimed exits."""
        totals = self._canonical_fill_totals(paper_trade_id)
        outstanding = self._outstanding_exit_quantity(paper_trade_id)
        return {
            **totals,
            "outstanding_exit_quantity": outstanding,
            "available_exit_quantity": float(totals["remaining_quantity"]) - outstanding,
        }

    @staticmethod
    def _expected_position_state(totals: Mapping[str, object]) -> str:
        """Position state implied by canonical fill-derived exposure.

        Derived from fills alone so a reconciliation record cannot claim a
        position the trade's own canonical evidence contradicts.
        """
        entry = float(totals["entry_quantity"])
        exit_quantity = float(totals["exit_quantity"])
        remaining = float(totals["remaining_quantity"])
        if remaining <= 0:
            return (
                PositionState.NO_POSITION.value
                if entry <= 0
                else PositionState.FLAT.value
            )
        if exit_quantity <= 0:
            return PositionState.OPEN.value
        return PositionState.REDUCING.value

    def _protection_plan_record(self, protection_plan_id: str) -> dict:
        plan = self._load_paper_event_by_identity(
            PAPER_PROTECTION_PLAN_RECORDED,
            protection_plan_id,
        )
        if plan.get("protection_plan_id") != protection_plan_id:
            raise ValueError("protection plan ancestry is invalid")
        return plan

    def _protection_state_records(self, protection_plan_id: str) -> list[dict]:
        return [
            record
            for record in self._committed_paper_rows(PAPER_PROTECTION_STATE_RECORDED)
            if record.get("protection_plan_id") == protection_plan_id
        ]

    def _effective_protection_state(self, protection_plan_id: str) -> ProtectionState:
        """Reconstruct the current state from canonical evidence only.

        The latest committed transition wins, so no in-memory authority is
        required and a restart reproduces the same answer deterministically.
        """
        records = self._protection_state_records(protection_plan_id)
        if not records:
            return ProtectionState.PLANNED
        latest = max(records, key=lambda record: int(record["state_seq"]))
        return ProtectionState(str(latest["to_state"]))

    def _activated_plan_ids(self, paper_trade_id: str) -> list[tuple[int, str]]:
        """Plans for this trade that canonical state evidence proves activated.

        Activation is proven by a committed transition into ACTIVE or TRIGGERED,
        never by the plan payload alone.
        """
        activated: list[tuple[int, str]] = []
        for plan in self._committed_paper_rows(PAPER_PROTECTION_PLAN_RECORDED):
            if plan.get("paper_trade_id") != paper_trade_id:
                continue
            plan_id = str(plan["protection_plan_id"])
            if any(
                record["to_state"] in {"ACTIVE", "TRIGGERED"}
                for record in self._protection_state_records(plan_id)
            ):
                activated.append((int(plan["plan_seq"]), plan_id))
        return sorted(activated)

    def _effective_protection_plan(self, paper_trade_id: str) -> dict | None:
        """The effective plan is the highest activated plan_seq for the trade."""
        activated = self._activated_plan_ids(paper_trade_id)
        if not activated:
            return None
        return self._protection_plan_record(activated[-1][1])

    def _final_verified_trade_ids(self) -> set[str]:
        """Trades that have reached terminal FINAL_VERIFIED reconciliation.

        FINAL_VERIFIED is terminal for a Paper v2 trade's economic/execution
        lifecycle, so this set is the writer's authority for refusing later
        mutations. It is derived from canonical evidence only.
        """
        return {
            str(record["paper_trade_id"])
            for record in self._committed_paper_rows(PAPER_RECONCILIATION_RECORDED)
            if str(record.get("terminal_reconciliation_state"))
            == TerminalReconciliationState.FINAL_VERIFIED.value
        }

    def _require_trade_not_final_verified(
        self, paper_trade_id: str, what: str
    ) -> None:
        """Refuse a new mutation for a trade whose economics are final.

        Exact replay is unaffected: ``submit`` resolves idempotency before the
        transactional ancestry validation that calls this, so retrying
        already-committed evidence returns ``DUPLICATE_OK`` and never reaches
        here, while a genuinely new mutation fails closed.
        """
        if paper_trade_id in self._final_verified_trade_ids():
            raise ValueError(
                f"paper trade is already FINAL_VERIFIED; {what} cannot be added "
                "after terminal reconciliation"
            )

    def _verified_paper_trade_ids(self, quote_currency: str) -> set[str]:
        """Trades whose economics are FINAL_VERIFIED, so capacity may be released."""
        verified: set[str] = set()
        # A zero-fill terminal trade is truthfully NO_POSITION (it never held one),
        # while a trade that held exposure and closed is FLAT. Both release capacity,
        # so both states must be accepted here - requiring FLAT alone left a failed
        # pre-fill trade's reservation permanently active.
        releasable_states = {
            PositionState.FLAT.value,
            PositionState.NO_POSITION.value,
        }
        for record in self._committed_paper_rows(PAPER_RECONCILIATION_RECORDED):
            if (
                record.get("terminal_reconciliation_state") == "FINAL_VERIFIED"
                and record.get("position_state") in releasable_states
                # Tolerance rather than float equality: the frozen contract
                # already requires exactly zero remaining for this state, and an
                # equality check on floats is unreliable by construction.
                and abs(float(record["remaining_quantity"])) <= _QUANTITY_TOLERANCE
            ):
                verified.add(str(record["paper_trade_id"]))
        if not verified:
            return verified
        return {
            trade_id
            for trade_id in verified
            if any(
                item.get("paper_trade_id") == trade_id
                and item.get("quote_currency") == quote_currency
                for item in self._admitted_dispositions(quote_currency)
            )
        }

    def _validate_protection_plan(self, payload: Mapping[str, object]) -> None:
        """Immutable plans with strictly monotonic plan_seq within the trade."""
        paper_trade_id = self._require_string_ref(payload, "paper_trade_id")
        self._admitted_trade(paper_trade_id)
        self._require_trade_not_final_verified(paper_trade_id, "a protection plan")
        self._require_monotonic_sibling_sequence(
            event_type=PAPER_PROTECTION_PLAN_RECORDED,
            payload=payload,
            parent_field="paper_trade_id",
            parent_value=paper_trade_id,
            sequence_field="plan_seq",
            identity_field="protection_plan_id",
            scope="paper trade",
        )

    def _validate_protection_state(self, payload: Mapping[str, object]) -> None:
        """Only supported transitions, sequenced within their plan.

        ACTIVE additionally requires canonical fills to prove positive current
        exposure, and TRIGGERED requires a committed trigger for the same plan, so
        a producer cannot assert protection it has not earned.
        """
        plan_id = self._require_string_ref(payload, "protection_plan_id")
        plan = self._protection_plan_record(plan_id)
        if plan.get("paper_trade_id") != payload.get("paper_trade_id"):
            raise ValueError("protection state paper_trade_id does not match the plan")
        self._require_trade_not_final_verified(
            str(plan["paper_trade_id"]), "a protection state transition"
        )
        self._require_monotonic_sibling_sequence(
            event_type=PAPER_PROTECTION_STATE_RECORDED,
            payload=payload,
            parent_field="protection_plan_id",
            parent_value=plan_id,
            sequence_field="state_seq",
            identity_field="protection_event_id",
            scope="protection plan",
        )
        from_state = str(payload["from_state"])
        to_state = str(payload["to_state"])
        if not protection_transition_allowed(from_state, to_state):
            raise ValueError(
                f"unsupported protection state transition {from_state} -> {to_state}"
            )
        # The declared origin must be the state canonical evidence already proves,
        # so a producer cannot skip an intermediate transition.
        effective = self._effective_protection_state(plan_id)
        if effective.value != from_state:
            raise ValueError(
                "protection state from_state does not match the effective canonical state"
            )
        totals = self._canonical_fill_totals(str(plan["paper_trade_id"]))
        if to_state == ProtectionState.ACTIVE.value and totals["remaining_quantity"] <= 0:
            raise ValueError(
                "protection plan cannot become ACTIVE without positive canonical exposure"
            )
        if protection_transition_requires_trigger(from_state, to_state):
            committed = [
                trigger
                for trigger in self._committed_paper_rows(
                    PAPER_PROTECTION_TRIGGER_RECORDED
                )
                if trigger.get("protection_plan_id") == plan_id
            ]
            if not committed:
                raise ValueError(
                    "protection cannot become TRIGGERED without committed trigger evidence"
                )

    def _eligible_target(
        self,
        plan: Mapping[str, object],
        *,
        plan_id: str,
    ) -> dict:
        """Deterministically derive the uniquely eligible next target.

        The frozen trigger contract deliberately carries no target identity, so
        eligibility is derived from canonical evidence rather than invented: every
        committed TARGET trigger for this plan consumes one target in ascending
        price order, and the next target in that order is the only eligible one.
        Taking the lowest *untriggered* target is what stops one target's crossing
        from authorising an arbitrary or full-position exit.
        """
        targets = sorted(
            (dict(target) for target in plan["targets"]),
            key=lambda target: (float(target["price"]), str(target["target_id"])),
        )
        consumed = sum(
            1
            for trigger in self._committed_paper_rows(
                PAPER_PROTECTION_TRIGGER_RECORDED
            )
            if trigger.get("protection_plan_id") == plan_id
            and trigger.get("trigger_type") == "TARGET"
        )
        if consumed >= len(targets):
            raise ValueError(
                "TARGET action has no eligible configured target remaining for "
                "this protection plan"
            )
        return targets[consumed]

    def _validate_quote_backed_trigger(
        self,
        payload: Mapping[str, object],
        plan: Mapping[str, object],
        *,
        paper_trade_id: str,
    ) -> dict | None:
        """Prove a STOP/TARGET market claim against canonical Level-1 evidence.

        Returns the eligible target for TARGET actions so the caller can bound the
        exit quantity to that target's configured fraction, and ``None`` for STOP.
        """
        trigger_type = str(payload["trigger_type"])
        quote_ref = payload.get("market_evidence_ref")
        if not isinstance(quote_ref, str) or not quote_ref:
            raise ValueError(
                f"{trigger_type} trigger requires canonical Level-1 market evidence"
            )
        quote = self._load_quote_evidence_by_id(quote_ref)
        admission = self._admitted_trade(paper_trade_id)
        context = self._load_context_by_id(str(admission["decision_context_id"]))
        self._validate_quote_matches_lineage(
            quote,
            context=context,
            admission=admission,
        )
        self._validate_quote_causality(
            quote,
            execution_time=payload.get("trigger_time"),
            execution_time_field="trigger_time",
        )
        # A valid lineage quote is necessary but not sufficient: the quote must
        # itself prove the configured threshold was crossed. This trade is
        # long-only and exits by selling, so the authoritative executable price is
        # the quote's best bid, and reference_price must be exactly that price
        # rather than a caller-chosen level the market never traded.
        executable_price = float(quote["best_bid"])
        reference_price = float(payload["reference_price"])
        if abs(reference_price - executable_price) > _QUANTITY_TOLERANCE:
            raise ValueError(
                "protection trigger reference_price must equal the executable "
                "quote price for the cited evidence"
            )
        if trigger_type == "STOP":
            if executable_price > float(plan["stop_price"]) + _QUANTITY_TOLERANCE:
                raise ValueError(
                    "STOP trigger requires a quote at or below the configured "
                    "stop_price"
                )
            return None
        eligible = self._eligible_target(
            plan, plan_id=str(payload["protection_plan_id"])
        )
        if executable_price < float(eligible["price"]) - _QUANTITY_TOLERANCE:
            raise ValueError(
                "TARGET trigger requires a quote at or above the eligible "
                "target price"
            )
        return eligible

    def _validate_time_trigger(
        self,
        payload: Mapping[str, object],
        plan: Mapping[str, object],
    ) -> None:
        """Prove a TIME/EMERGENCY claim from exact temporal evidence."""
        trigger_type = str(payload["trigger_type"])
        temporal = payload.get("trigger_time")
        if not isinstance(temporal, Mapping) or str(temporal.get("precision")) != "EXACT":
            raise ValueError(
                f"{trigger_type} trigger requires exact temporal evidence"
            )
        if payload.get("market_evidence_ref") is not None:
            raise ValueError(
                f"{trigger_type} trigger cannot claim a market evidence reference"
            )
        if trigger_type != "TIME":
            return
        # The plan's holding period must actually have elapsed. Exact timestamps
        # are preserved: firing exactly at expiry is allowed, one second earlier
        # is not.
        plan_start, _plan_end = self._temporal_bounds(
            plan.get("plan_time"), field_name="plan_time"
        )
        trigger_start, _trigger_end = self._temporal_bounds(
            temporal, field_name="trigger_time"
        )
        expiry = plan_start + timedelta(seconds=int(plan["max_hold_seconds"]))
        if trigger_start < expiry:
            raise ValueError(
                "TIME trigger is before the plan's max_hold_seconds expiry"
            )

    def _validate_protection_trigger(
        self, payload: Mapping[str, object]
    ) -> dict | None:
        """Triggers require a proven armed plan and defensible evidence.

        A trigger never changes quantity, P/L or terminal state; this method only
        proves that the claimed market/time condition is canonically supported.
        Returns the eligible target for TARGET actions, else ``None``.
        """
        plan_id = self._require_string_ref(payload, "protection_plan_id")
        plan = self._protection_plan_record(plan_id)
        paper_trade_id = self._require_string_ref(payload, "paper_trade_id")
        if plan.get("paper_trade_id") != paper_trade_id:
            raise ValueError("protection trigger paper_trade_id does not match the plan")
        self._admitted_trade(paper_trade_id)
        self._require_trade_not_final_verified(paper_trade_id, "a protection trigger")
        self._require_monotonic_sibling_sequence(
            event_type=PAPER_PROTECTION_TRIGGER_RECORDED,
            payload=payload,
            parent_field="protection_plan_id",
            parent_value=plan_id,
            sequence_field="trigger_seq",
            identity_field="protection_trigger_id",
            scope="protection plan",
        )
        effective_state = self._effective_protection_state(plan_id)
        if effective_state not in PAPER_ACTION_ARMED_STATES:
            raise ValueError(
                "protection trigger requires an armed (ACTIVE or DEGRADED) plan"
            )
        if str(payload["trigger_type"]) in PAPER_TRIGGER_TYPES_REQUIRING_QUOTE:
            return self._validate_quote_backed_trigger(
                payload, plan, paper_trade_id=paper_trade_id
            )
        self._validate_time_trigger(payload, plan)
        return None

    def _validate_protection_action(
        self,
        *,
        trigger: Mapping[str, object],
        exit_order_intent: Mapping[str, object],
        protection_state: Mapping[str, object],
        paper_trade_id: str,
        plan_id: str,
    ) -> str:
        """Validate one atomic protection action and return its context id.

        Every step below is proven before any row is written, so a rejected action
        commits nothing. Steps are labelled as in the design: ancestry, effective
        plan, armed state, fill-derived exposure, exit sizing, trigger evidence.
        """
        # A. canonical trade, reservation and decision-context ancestry.
        admission = self._admitted_trade(paper_trade_id)
        context_id = str(admission["decision_context_id"])
        self._load_context_by_id(context_id)
        if str(admission.get("reservation_id")) != str(
            exit_order_intent.get("reservation_id")
        ):
            raise ValueError(
                "protection action EXIT intent reservation does not match "
                "the admission reservation"
            )
        if str(exit_order_intent.get("decision_context_id")) != context_id:
            raise ValueError(
                "protection action EXIT intent decision context does not "
                "match the admitted trade"
            )

        # B. only the effective (highest activated) plan may act.
        effective_plan = self._effective_protection_plan(paper_trade_id)
        if effective_plan is None:
            raise ValueError("protection action requires an activated protection plan")
        if str(effective_plan["protection_plan_id"]) != plan_id:
            raise ValueError(
                "protection action plan is not the effective activated plan"
            )

        # C. the plan must be armed, and the transition must start there.
        current_state = self._effective_protection_state(plan_id)
        if current_state not in PAPER_ACTION_ARMED_STATES:
            raise ValueError(
                "protection action requires an armed (ACTIVE or DEGRADED) plan"
            )
        if str(protection_state["from_state"]) != current_state.value:
            raise ValueError(
                "protection action state does not start from the effective "
                "canonical state"
            )

        # D. exposure is proven by canonical fills alone, and is reduced by exits
        # already claimed by live EXIT intents.
        capacity = self._exit_capacity(paper_trade_id)
        if float(capacity["remaining_quantity"]) <= 0:
            raise ValueError("protection action requires positive canonical exposure")

        # F. the EXIT intent must reduce exposure without over-closing it.
        self._validate_action_exit_intent(exit_order_intent, capacity)

        # E and G. frozen trigger evidence, B/C-1 order-intent ancestry, and
        # trigger sequence monotonicity. The trigger validation returns the
        # eligible TARGET when this is a target action, so the exit can be capped
        # to that target's configured fraction rather than the whole position.
        eligible_target = self._validate_protection_trigger(trigger)
        if eligible_target is not None:
            self._require_target_exit_within_fraction(
                eligible_target,
                requested_quantity=float(exit_order_intent["requested_quantity"]),
                entry_quantity=float(capacity["entry_quantity"]),
                available_exit_quantity=float(capacity["available_exit_quantity"]),
            )
        self._validate_paper_execution_ancestry(
            PAPER_ORDER_INTENT_RECORDED, exit_order_intent
        )
        return context_id

    def _validate_action_exit_intent(
        self,
        exit_order_intent: Mapping[str, object],
        capacity: Mapping[str, object],
    ) -> None:
        """An action exit must sell, be positive, and fit the available capacity."""
        if str(exit_order_intent["intent_role"]) != "EXIT":
            raise ValueError("protection action requires an EXIT order intent")
        if str(exit_order_intent["side"]) != PAPER_ACTION_EXIT_SIDE:
            raise ValueError(
                "protection action EXIT intent must use side "
                f"{PAPER_ACTION_EXIT_SIDE}"
            )
        requested_quantity = float(exit_order_intent["requested_quantity"])
        if requested_quantity <= 0:
            raise ValueError("protection action EXIT quantity must be positive")
        if (
            requested_quantity
            > float(capacity["available_exit_quantity"]) + _QUANTITY_TOLERANCE
        ):
            raise ValueError(
                "protection action EXIT quantity exceeds available canonical "
                "exit capacity"
            )

    @staticmethod
    def _require_target_exit_within_fraction(
        eligible_target: Mapping[str, object],
        *,
        requested_quantity: float,
        entry_quantity: float,
        available_exit_quantity: float,
    ) -> None:
        """A staged target may exit only its own configured fraction.

        Crossing a target authorises that target's fraction of the protected
        position, never the whole position, which is what keeps a staged plan
        staged when only its first target has been reached.
        """
        fraction_cap = float(eligible_target["fraction"]) * entry_quantity
        cap = min(fraction_cap, available_exit_quantity)
        if requested_quantity > cap + _QUANTITY_TOLERANCE:
            raise ValueError(
                "TARGET action EXIT quantity exceeds the eligible target's "
                f"configured fraction (cap {cap})"
            )

    def _validate_reconciliation(self, payload: Mapping[str, object]) -> None:
        """Terminal economics must be reproducible from canonical fills.

        Position and economics are never taken from protection evidence. A
        FINAL_VERIFIED claim that canonical fills do not support is refused, so
        unverifiable economics can only be recorded as UNRESOLVED_EVIDENCE -
        which never releases reserved capacity.
        """
        paper_trade_id = self._require_string_ref(payload, "paper_trade_id")
        admission = self._admitted_trade(paper_trade_id)
        # FINAL_VERIFIED is terminal: a later reconciliation (including a
        # higher-sequence non-final one, or UNRESOLVED_EVIDENCE) is refused. The
        # terminal record itself is unaffected because it is committed while the
        # trade is not yet final, and an exact replay resolves via idempotency.
        self._require_trade_not_final_verified(
            paper_trade_id, "a reconciliation record"
        )
        self._require_monotonic_sibling_sequence(
            event_type=PAPER_RECONCILIATION_RECORDED,
            payload=payload,
            parent_field="paper_trade_id",
            parent_value=paper_trade_id,
            sequence_field="reconciliation_seq",
            identity_field="reconciliation_id",
            scope="paper trade",
        )
        # The reserve is the admission's own reservation, which stays knowable
        # regardless of whether the economics could be proven, so it is bound for
        # every reconciliation record rather than only the final one.
        canonical_reserved = float(admission["requested_reservation_amount"])
        if abs(float(payload["reserved_capital"]) - canonical_reserved) > 1e-9:
            raise ValueError(
                "reserved_capital does not match the canonical admission reservation"
            )
        terminal = str(payload["terminal_reconciliation_state"])
        if terminal == TerminalReconciliationState.UNRESOLVED_EVIDENCE.value:
            # The contract already requires an explicit reason. Unverifiable
            # evidence is recorded rather than promoted, and the quantity claims
            # are deliberately not forced to match canonical fills: the point of
            # this state is that the economics could not be proven.
            return

        totals = self._canonical_fill_totals(paper_trade_id)
        for field_name, canonical in (
            ("filled_entry_quantity", totals["entry_quantity"]),
            ("filled_exit_quantity", totals["exit_quantity"]),
            ("remaining_quantity", totals["remaining_quantity"]),
        ):
            if abs(float(payload[field_name]) - canonical) > 1e-9:
                raise ValueError(
                    f"{field_name} is not reproducible from canonical fills"
                )
        expected_position_state = self._expected_position_state(totals)
        if str(payload["position_state"]) != expected_position_state:
            raise ValueError(
                "position_state does not agree with canonical fill-derived exposure"
            )
        if terminal != TerminalReconciliationState.FINAL_VERIFIED.value:
            return
        # A terminal reconciliation releases reserved capital and a position slot,
        # so it may only be written when canonical evidence proves the trade can no
        # longer produce exposure. Checked structurally here rather than trusted to
        # producer convention: a zero-fill terminalization while a fill-capable
        # ENTRY attempt is still outstanding would release capacity against a trade
        # that can still fill.
        if float(totals["entry_quantity"]) <= 0:
            self._validate_zero_fill_release_is_safe(paper_trade_id)
        for field_name, canonical in (
            ("realized_gross_pnl", totals["gross_pnl"]),
            ("recorded_execution_costs", totals["execution_costs"]),
        ):
            if abs(float(payload[field_name]) - canonical) > 1e-9:
                raise ValueError(
                    f"{field_name} is not reproducible from canonical fills"
                )

    def _validate_paper_protection_ancestry(
        self,
        event_type: str,
        payload: Mapping[str, object],
    ) -> None:
        if event_type == PAPER_PROTECTION_PLAN_RECORDED:
            self._validate_protection_plan(payload)
            return
        if event_type == PAPER_PROTECTION_STATE_RECORDED:
            # TRIGGERED is writer-owned: it is the third fact of the atomic
            # protection action, so a generic state submission must not assert it
            # even when trigger evidence happens to exist. The safe non-trigger
            # transitions below stay available on the generic path.
            if str(payload.get("to_state")) == ProtectionState.TRIGGERED.value:
                raise ValueError(
                    "ATOMIC_TRIGGER_ACTION_REQUIRED: a TRIGGERED protection state "
                    "commits only through the atomic protection action RPC"
                )
            self._validate_protection_state(payload)
            return
        if event_type == PAPER_PROTECTION_TRIGGER_RECORDED:
            raise ValueError(
                "ATOMIC_TRIGGER_ACTION_REQUIRED: a protection trigger commits only "
                "through the atomic protection action RPC"
            )
        if event_type == PAPER_RECONCILIATION_RECORDED:
            self._validate_reconciliation(payload)
            return
        raise ValueError("unsupported Paper v2 protection event type")

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
        # Checked before the registration gate so the refusal names the real
        # problem: an action trigger is not merely unregistered, it is only
        # meaningful inside the atomic protection action bundle.
        if intent.event_type in PAPER_V2_ATOMIC_ONLY_EVENT_TYPES:
            raise ValueError(
                "ATOMIC_TRIGGER_ACTION_REQUIRED: a protection trigger commits only "
                "through the atomic protection action RPC, never as a standalone "
                "WriterIntent"
            )
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
        if intent.event_type in PAPER_V2_WRITER_EVENT_TYPES:
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
        if event_type in PAPER_V2_ALL_EVENT_TYPES:
            return PAPER_EXECUTION_STREAM
        return STREAM_EARLY_WATCH

    @staticmethod
    def _requires_ops_handoff(event_type: str) -> bool:
        return (
            event_type != _ALERT_CAPTURE_GAP_RECORDED
            and event_type not in FEATURE_BUS_EVENT_TYPES
            and event_type not in DECISION_INTELLIGENCE_EVENT_TYPES
            and event_type not in PAPER_OUTCOME_EVENT_TYPES
            and event_type not in PAPER_V2_ALL_EVENT_TYPES
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
        meta = self._conn.execute(_META_SEQUENCE_SQL).fetchone()
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
        if intent.event_type in PAPER_PROTECTION_BC2_WRITER_EVENT_TYPES:
            self._validate_paper_protection_ancestry(
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
