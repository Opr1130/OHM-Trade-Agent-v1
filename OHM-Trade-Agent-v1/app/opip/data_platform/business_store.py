"""Authoritative PostgreSQL business-record store (Phase 1, disabled by default).

Nothing imports this module in production yet. It is the data-access layer the approved cutover
will wire in, and it is deliberately independent of the safety-critical paths:

* **Disabled by default.** ``OPIP_BUSINESS_STORE_AUTHORITY`` defaults to ``FILES``; while it is
  ``FILES`` the admission gate makes no database call at all and the database itself refuses
  business writes (``ops.business_write_guard``), so a half-enabled deployment cannot create a
  second authoritative store.
* **Idempotent, ordered, atomic.** Every write is one transaction: the ledger event
  (``ops.append_business_event``) and its typed projection commit together or not at all. A replay
  of the same key and payload is a no-op, the same key with a different payload is an error, and
  the per-aggregate sequence is gap-free under concurrency.
* **Fail closed.** Any failure to prove that PostgreSQL is reachable, authoritative and at the
  configured epoch means *no new admission*. The gate only ever answers for NEW admissions;
  F11, protective exits and emergency alerts never import this module (enforced by a test), so
  a database outage cannot delay or block them.

The driver is imported lazily by ``app.opip.data_platform.db`` so importing this module costs
nothing in the deterministic scanner import graph.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

FILES = "FILES"
POSTGRES = "POSTGRES"

# SQLSTATEs raised by migration 007.
_SQLSTATE_AUTHORITY_NOT_POSTGRES = "P0002"
_SQLSTATE_EPOCH_MISMATCH = "P0003"
_SQLSTATE_IDEMPOTENCY_CONFLICT = "P0004"
_SQLSTATE_SEQUENCE_CONFLICT = "P0005"


class BusinessStoreError(RuntimeError):
    """Base class: every subclass means the write did NOT commit."""


class StoreUnavailable(BusinessStoreError):
    """PostgreSQL could not be reached, timed out or failed; the caller must fail closed."""


class AuthorityMismatch(BusinessStoreError):
    """PostgreSQL is not the authority, or this writer's epoch is stale (rolled back / split-brain)."""


class IdempotencyConflict(BusinessStoreError):
    """The same idempotency key was reused with a different payload."""


class SequenceConflict(BusinessStoreError):
    """The aggregate moved since the caller read it (optimistic concurrency)."""


class StoreRejected(BusinessStoreError):
    """The database rejected the row (constraint or data error); not an availability problem."""


@dataclass(frozen=True)
class BusinessStoreConfig:
    authority: str = FILES
    database_url: str | None = None
    expected_epoch: int = 0
    connect_timeout_seconds: int = 2
    statement_timeout_ms: int = 1500
    config_error: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BusinessStoreConfig":
        source = os.environ if env is None else env
        raw = str(source.get("OPIP_BUSINESS_STORE_AUTHORITY") or FILES).strip().upper()
        error = None
        authority = FILES
        if raw == POSTGRES:
            authority = POSTGRES
        elif raw != FILES:
            # An unrecognised value never enables PostgreSQL and never silently selects FILES:
            # ``new_admissions_allowed`` refuses new admissions while this error is present.
            error = f"unrecognised OPIP_BUSINESS_STORE_AUTHORITY {raw!r}; new admissions refused"

        def _int(name: str, default: int) -> int:
            try:
                parsed = int(str(source.get(name) or default))
            except ValueError:
                return default
            return parsed if parsed >= 0 else default

        return cls(
            authority=authority,
            database_url=(source.get("OPIP_BUSINESS_STORE_DATABASE_URL") or None),
            expected_epoch=_int("OPIP_BUSINESS_STORE_EPOCH", 0),
            connect_timeout_seconds=max(1, _int("OPIP_BUSINESS_STORE_CONNECT_TIMEOUT_SECONDS", 2)),
            statement_timeout_ms=max(1, _int("OPIP_BUSINESS_STORE_STATEMENT_TIMEOUT_MS", 1500)),
            config_error=error,
        )


@dataclass(frozen=True)
class AppendResult:
    status: str  # "INSERTED" | "DUPLICATE"
    aggregate_seq: int

    @property
    def inserted(self) -> bool:
        return self.status == "INSERTED"


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason: str


def _json_default(value: Any) -> str:
    # Timestamps are the only non-JSON type accepted, and only when timezone-aware.
    if isinstance(value, datetime):
        return _aware(value, "timestamp").isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def canonical_json(payload: Mapping[str, Any]) -> tuple[str, str]:
    """Deterministic JSON text and its SHA-256. NaN/Infinity and unknown types are rejected."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_json_default)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _classify(exc: BaseException) -> BusinessStoreError:
    state = getattr(exc, "sqlstate", None) or getattr(getattr(exc, "diag", None), "sqlstate", None)
    if state in {_SQLSTATE_AUTHORITY_NOT_POSTGRES, _SQLSTATE_EPOCH_MISMATCH}:
        return AuthorityMismatch(str(exc))
    if state == _SQLSTATE_IDEMPOTENCY_CONFLICT:
        return IdempotencyConflict(str(exc))
    if state == _SQLSTATE_SEQUENCE_CONFLICT:
        return SequenceConflict(str(exc))
    class_name = type(exc).__name__
    # SQLSTATE class 22 (data exception) and 23 (integrity constraint violation) describe the row,
    # not the link, and so does any driver subclass of IntegrityError/DataError (for example
    # UniqueViolation, NumericValueOutOfRange, InvalidTextRepresentation). Retrying cannot help.
    if isinstance(state, str) and state[:2] in {"22", "23"}:
        return StoreRejected(str(exc))
    if any(base.__name__ in {"IntegrityError", "DataError"} for base in type(exc).__mro__):
        return StoreRejected(str(exc))
    return StoreUnavailable(f"{class_name}: {exc}")


class BusinessStore:
    """Thin, transactional access to ``ops.business_event`` and its typed projections."""

    def __init__(self, connection_factory: Callable[[], Any], *, authority_epoch: int) -> None:
        if int(authority_epoch) <= 0:
            raise ValueError("authority_epoch must be a positive integer")
        self._factory = connection_factory
        self.authority_epoch = int(authority_epoch)

    # ------------------------------------------------------------------ authority
    def read_authority(self) -> tuple[str, int]:
        try:
            with self._factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT authority, epoch FROM ops.cutover_authority WHERE singleton")
                    row = cursor.fetchone()
        except BusinessStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - every driver failure is "unavailable"
            raise StoreUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if row is None:
            raise StoreUnavailable("ops.cutover_authority has no row")
        return str(row[0]), int(row[1])

    # --------------------------------------------------------------------- writes
    def _append(
        self,
        *,
        idempotency_key: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        occurred_at: datetime,
        payload: Mapping[str, Any],
        expected_prev_seq: int | None,
        projection: Callable[[Any, str], None] | None,
    ) -> AppendResult:
        for name, value in (
            ("idempotency_key", idempotency_key),
            ("aggregate_type", aggregate_type),
            ("aggregate_id", aggregate_id),
            ("event_type", event_type),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        moment = _aware(occurred_at, "occurred_at")
        text, digest = canonical_json(payload)
        try:
            with self._factory() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT status, aggregate_seq FROM ops.append_business_event("
                            "%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)",
                            (
                                idempotency_key,
                                aggregate_type,
                                aggregate_id,
                                event_type,
                                moment,
                                text,
                                digest,
                                self.authority_epoch,
                                expected_prev_seq,
                            ),
                        )
                        status, seq = cursor.fetchone()
                        if status == "INSERTED" and projection is not None:
                            projection(cursor, idempotency_key)
        except BusinessStoreError:
            raise
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        return AppendResult(status=str(status), aggregate_seq=int(seq))

    def record_delivery_event(
        self, row: Mapping[str, Any], *, expected_prev_seq: int | None = None
    ) -> AppendResult:
        """Persist one Telegram delivery-ledger row (the ``TELEGRAM_DELIVERY_EVENT`` shape)."""
        alert_id = str(row["alert_id"])
        attempted_at = _aware(_parse_time(row["attempted_at"]), "attempted_at")
        key = f"delivery:{alert_id}:{attempted_at.isoformat()}:{row['status']}:{int(row.get('attempt') or 0)}"

        def project(cursor: Any, idempotency_key: str) -> None:
            cursor.execute(
                "INSERT INTO signal.delivery_event (idempotency_key, alert_id, alert_family, event_type,"
                " status, delivered, attempted_at, signal_id, journey_id, trade_id, symbol, attempt,"
                " retry_count, suppression_reason, failure_reason, suppressed_since_last_row, evidence)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (
                    idempotency_key,
                    alert_id,
                    row["alert_family"],
                    row["event_type"],
                    row["status"],
                    bool(row["delivered"]),
                    attempted_at,
                    row.get("signal_id"),
                    row.get("journey_id"),
                    row.get("trade_id"),
                    row.get("symbol"),
                    int(row.get("attempt") or 0),
                    int(row.get("retry_count") or 0),
                    row.get("suppression_reason"),
                    row.get("failure_reason"),
                    row.get("suppressed_since_last_row"),
                    None if row.get("evidence") is None else canonical_json(row["evidence"])[0],
                ),
            )

        return self._append(
            idempotency_key=key,
            aggregate_type="alert",
            aggregate_id=alert_id,
            event_type="TELEGRAM_DELIVERY_EVENT",
            occurred_at=attempted_at,
            payload=dict(row),
            expected_prev_seq=expected_prev_seq,
            projection=project,
        )

    def record_ai_call(self, call: Mapping[str, Any]) -> AppendResult:
        """Persist one AI call (tokens only; USD is derived elsewhere from priced metadata)."""
        observed_at = _aware(_parse_time(call["observed_at"]), "observed_at")
        key = f"ai_call:{call['provider']}:{call['model']}:{call['call_id']}"

        def project(cursor: Any, idempotency_key: str) -> None:
            cursor.execute(
                "INSERT INTO signal.ai_call (idempotency_key, observed_at, provider, model, purpose, status,"
                " error_type, input_tokens, output_tokens, total_tokens, signal_id, journey_id)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    idempotency_key,
                    observed_at,
                    call["provider"],
                    call["model"],
                    call.get("purpose"),
                    call["status"],
                    call.get("error_type"),
                    call.get("input_tokens"),
                    call.get("output_tokens"),
                    call["total_tokens"],
                    call.get("signal_id"),
                    call.get("journey_id"),
                ),
            )

        return self._append(
            idempotency_key=key,
            aggregate_type="ai_call",
            aggregate_id=str(call["call_id"]),
            event_type="AI_CALL_RECORDED",
            occurred_at=observed_at,
            payload=dict(call),
            expected_prev_seq=None,
            projection=project,
        )

    def record_signal_outcome(self, outcome: Mapping[str, Any]) -> AppendResult:
        """Persist one deterministic outcome; INSUFFICIENT_EVIDENCE rows carry no verdict or return."""
        evaluated_at = _aware(_parse_time(outcome["evaluated_at"]), "evaluated_at")
        key = f"outcome:{outcome['signal_id']}:{outcome['horizon_seconds']}:{outcome['outcome_class']}"

        def project(cursor: Any, idempotency_key: str) -> None:
            cursor.execute(
                "INSERT INTO learning.signal_outcome (idempotency_key, signal_id, horizon_seconds,"
                " evaluated_at, outcome_class, verdict, gross_return_pct, net_return_pct, assumed_fee_pct,"
                " assumed_slippage_pct, evidence) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (
                    idempotency_key,
                    outcome["signal_id"],
                    int(outcome["horizon_seconds"]),
                    evaluated_at,
                    outcome["outcome_class"],
                    outcome["verdict"],
                    outcome.get("gross_return_pct"),
                    outcome.get("net_return_pct"),
                    outcome.get("assumed_fee_pct"),
                    outcome.get("assumed_slippage_pct"),
                    canonical_json(outcome["evidence"])[0],
                ),
            )

        return self._append(
            idempotency_key=key,
            aggregate_type="signal_outcome",
            aggregate_id=str(outcome["signal_id"]),
            event_type="SIGNAL_OUTCOME_RECORDED",
            occurred_at=evaluated_at,
            payload=dict(outcome),
            expected_prev_seq=None,
            projection=project,
        )


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def new_admissions_allowed(
    config: BusinessStoreConfig,
    store: BusinessStore | None,
) -> AdmissionDecision:
    """May the pipeline admit a NEW opportunity? Fails closed once PostgreSQL is the authority.

    While the configured authority is ``FILES`` this makes no database call and cannot fail.
    An explicit invalid authority value is refused. Once it is ``POSTGRES`` an admission requires proof, on every call, that the database is
    reachable, reports ``POSTGRES`` and the configured epoch. This gate is for NEW admissions
    only: protective exits, F11 and emergency alerts must never call it.
    """
    if config.config_error:
        # An explicit but invalid authority (for example a typo for POSTGRES) must not fall back to
        # the file path: a misconfigured node would otherwise keep admitting while others have cut over.
        return AdmissionDecision(False, "INVALID_AUTHORITY_CONFIG")
    if config.authority != POSTGRES:
        return AdmissionDecision(True, "FILES_AUTHORITATIVE")
    if store is None or config.expected_epoch <= 0:
        return AdmissionDecision(False, "POSTGRES_STORE_NOT_CONFIGURED")
    try:
        authority, epoch = store.read_authority()
    except BusinessStoreError:
        return AdmissionDecision(False, "POSTGRES_UNAVAILABLE")
    if authority != POSTGRES:
        return AdmissionDecision(False, "AUTHORITY_NOT_POSTGRES")
    if epoch != config.expected_epoch or store.authority_epoch != epoch:
        return AdmissionDecision(False, "AUTHORITY_EPOCH_MISMATCH")
    return AdmissionDecision(True, "POSTGRES_AUTHORITATIVE")


def connection_factory_from_config(config: BusinessStoreConfig) -> Callable[[], Any]:
    """Short-lived connections with bounded connect and statement timeouts (lazy driver import)."""
    if not config.database_url:
        raise StoreUnavailable("OPIP_BUSINESS_STORE_DATABASE_URL is not set")

    def factory() -> Any:
        from app.opip.data_platform.db import connect

        connection = connect(
            config.database_url,
            connect_timeout_seconds=config.connect_timeout_seconds,
            application_name="opip-business-store",
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute(f"SET statement_timeout = {int(config.statement_timeout_ms)}")
        return connection

    return factory


def preflight(connection: Any) -> list[dict[str, str]]:
    """Read-only readiness report. Every check is PASS, FAIL or UNVERIFIED (never a guess)."""
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    with connection.cursor() as cursor:
        cursor.execute("SELECT version, sha256 FROM ops.schema_version ORDER BY version")
        applied = {int(row[0]): str(row[1]) for row in cursor.fetchall()}
        from app.opip.data_platform.migrations import discover_migrations

        expected = {item.version: item.sha256 for item in discover_migrations()}
        missing = sorted(set(expected) - set(applied))
        drifted = sorted(v for v in applied if v in expected and applied[v] != expected[v])
        add(
            "migrations",
            "PASS" if not missing and not drifted else "FAIL",
            f"missing={missing} checksum_drift={drifted}",
        )
        cursor.execute("SELECT authority, epoch FROM ops.cutover_authority WHERE singleton")
        row = cursor.fetchone()
        add("authority_row", "PASS" if row else "FAIL", f"authority={row[0]} epoch={row[1]}" if row else "missing")
        cursor.execute(
            "SELECT has_table_privilege('opip_app_writer', 'ops.cutover_authority', 'UPDATE'), "
            "has_table_privilege('opip_app_writer', 'ops.business_event', 'UPDATE'), "
            "has_table_privilege('opip_app_writer', 'ops.business_event', 'DELETE')"
        )
        can_flip, can_edit, can_delete = cursor.fetchone()
        add(
            "app_writer_least_privilege",
            "PASS" if not (can_flip or can_edit or can_delete) else "FAIL",
            f"update_authority={can_flip} update_events={can_edit} delete_events={can_delete}",
        )
        cursor.execute("SELECT pg_database_size(current_database())")
        size_bytes = int(cursor.fetchone()[0])
        cursor.execute("SELECT setting::int FROM pg_settings WHERE name = 'max_connections'")
        max_connections = int(cursor.fetchone()[0])
        cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        used = int(cursor.fetchone()[0])
        headroom = max_connections - used
        add(
            "connection_headroom",
            "PASS" if headroom >= max(5, max_connections // 5) else "FAIL",
            f"used={used} max={max_connections}",
        )
        add("database_size", "PASS", f"{size_bytes} bytes")
    add(
        "host_disk_and_memory",
        "UNVERIFIED",
        "requires the analytics host (disk free, RAM, backup target); not observable from SQL",
    )
    add(
        "backup_restore_drill",
        "UNVERIFIED",
        "owner-gated: backup timer activation and host restore drill are not performed by Phase 1",
    )
    return checks


__all__ = [
    "AdmissionDecision",
    "AppendResult",
    "AuthorityMismatch",
    "BusinessStore",
    "BusinessStoreConfig",
    "BusinessStoreError",
    "FILES",
    "IdempotencyConflict",
    "POSTGRES",
    "SequenceConflict",
    "StoreRejected",
    "StoreUnavailable",
    "canonical_json",
    "connection_factory_from_config",
    "new_admissions_allowed",
    "preflight",
]
