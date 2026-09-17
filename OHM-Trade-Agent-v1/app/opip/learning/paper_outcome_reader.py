"""Read-only canonical terminal paper-outcome reader (PR-A).

The canonical SQLite evidence plane is the authority for terminal paper
economic truth. This module reads committed ``paper_outcome.terminal.recorded``
events and nothing else. It is deliberately the smallest reader that satisfies
that need.

Boundary guarantees:

* Read-only. The connection is opened ``mode=ro`` and no statement writes.
* Never constructs or imports ``CanonicalWriter``. Only the canonical
  ``connect`` helper is imported, lazily, so there is no writer coupling at
  import time.
* No new database, projection, JSON authority or queue. The canonical store is
  reused as-is.
* Fails closed. A missing database, an incompatible schema, an unreadable
  store, or an invalid payload raises rather than being reported as "no
  outcomes". Source-unavailable and a legitimately empty stream are different
  states and must never be conflated - reporting zero outcomes for an
  unavailable store would silently claim a complete, empty population.
* Deterministic. Events are returned in canonical commit order
  (``history_epoch``, ``local_sequence``), so reconstruction is stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.opip.canonical.paths import (
    EVENT_SCHEMA_VERSION,
    SCHEMA_VERSION,
    db_path as canonical_db_path,
)
from app.opip.contracts.paper_outcome import (
    PAPER_OUTCOME_STREAM,
    PAPER_OUTCOME_TERMINAL_RECORDED,
    validate_terminal_outcome_payload,
)


class PaperOutcomeSourceUnavailableError(RuntimeError):
    """The canonical store could not be opened for reading.

    Distinct from an empty stream: callers must not treat this as "no outcomes".
    """


class PaperOutcomeIntegrityError(RuntimeError):
    """Stored canonical evidence is unreadable, incompatible, or invalid."""


class PaperOutcomeSchemaError(PaperOutcomeIntegrityError):
    """The canonical store's schema version is not one this build understands."""


@dataclass(frozen=True)
class CanonicalPaperOutcome:
    """One validated committed terminal paper outcome."""

    outcome_id: str
    event_id: str
    history_epoch: int
    local_sequence: int
    recorded_at: str
    payload: dict[str, Any]

    @property
    def paper_trade_id(self) -> str:
        return str(self.payload.get("paper_trade_id") or "")

    @property
    def episode_id(self) -> str:
        return str(self.payload.get("episode_id") or "")

    @property
    def engine(self) -> str:
        return str(self.payload.get("engine") or "")

    @property
    def final_revision(self) -> int:
        return int(self.payload.get("final_revision") or 0)

    @property
    def terminal_status(self) -> str:
        return str(self.payload.get("terminal_status") or "")

    def as_dict(self) -> dict[str, Any]:
        """Return the validated contract payload (not a lifecycle row)."""
        return dict(self.payload)


@dataclass(frozen=True)
class CanonicalPaperOutcomeRead:
    """Result of one canonical read, including whether the source was read."""

    outcomes: tuple[CanonicalPaperOutcome, ...]
    #: True when the stream was read successfully, including when legitimately
    #: empty. False is never returned today - unreadable sources raise - but it
    #: keeps "read and empty" explicit for callers.
    stream_present: bool

    def by_paper_trade_id(self) -> dict[str, list[CanonicalPaperOutcome]]:
        grouped: dict[str, list[CanonicalPaperOutcome]] = {}
        for outcome in self.outcomes:
            grouped.setdefault(outcome.paper_trade_id, []).append(outcome)
        return grouped

    def by_episode_id(self) -> dict[str, list[CanonicalPaperOutcome]]:
        grouped: dict[str, list[CanonicalPaperOutcome]] = {}
        for outcome in self.outcomes:
            grouped.setdefault(outcome.episode_id, []).append(outcome)
        return grouped


def _assert_interpretable_schema(connection) -> None:
    meta = connection.execute("SELECT schema_version FROM meta WHERE id = 1").fetchone()
    if meta is None:
        raise PaperOutcomeIntegrityError("canonical meta row is missing")
    version = meta["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise PaperOutcomeSchemaError(
            f"canonical schema_version={version!r} incompatible with code "
            f"SCHEMA_VERSION={SCHEMA_VERSION}"
        )


def _read_outcome_events(connection) -> list[Any]:
    """Read committed paper-outcome events in canonical commit order."""
    return list(
        connection.execute(
            """
            SELECT event_id, schema_version, history_epoch, local_sequence,
                   recorded_at, idempotency_key, payload_json
            FROM events
            WHERE event_type = ?
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (PAPER_OUTCOME_TERMINAL_RECORDED,),
        )
    )


def _stream_present(connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM watermarks WHERE stream = ?", (PAPER_OUTCOME_STREAM,)
    ).fetchone()
    return row is not None


def read_canonical_paper_outcomes(
    db_path: Path | None = None,
) -> CanonicalPaperOutcomeRead:
    """Read and validate every committed terminal paper outcome.

    Raises:
        PaperOutcomeSourceUnavailableError: the store cannot be opened.
        PaperOutcomeSchemaError: the store's schema version is unsupported.
        PaperOutcomeIntegrityError: the store is unreadable, or any committed
            event fails authoritative contract validation.
    """
    import json
    import sqlite3

    target = Path(db_path) if db_path is not None else canonical_db_path()
    if not target.exists():
        raise PaperOutcomeSourceUnavailableError(
            f"canonical evidence store is unavailable: {target}"
        )

    # Imported lazily so this module has no canonical-writer coupling at import
    # time, and so the read-only connection mechanism stays the canonical one.
    from app.opip.canonical.schema import connect

    try:
        connection = connect(target, read_only=True)
    except sqlite3.Error as exc:
        raise PaperOutcomeSourceUnavailableError(
            f"canonical evidence store could not be opened: {exc}"
        ) from exc

    try:
        _assert_interpretable_schema(connection)
        rows = _read_outcome_events(connection)
        stream_present = _stream_present(connection)
    except PaperOutcomeIntegrityError:
        raise
    except sqlite3.Error as exc:
        raise PaperOutcomeIntegrityError(
            f"canonical paper outcome read failed: {exc}"
        ) from exc
    finally:
        # Closing a read-only connection rolls back the read transaction; no
        # canonical storage is written either way.
        connection.close()

    outcomes: list[CanonicalPaperOutcome] = []
    for row in rows:
        envelope_version = row["schema_version"]
        if type(envelope_version) is not int or envelope_version != EVENT_SCHEMA_VERSION:
            raise PaperOutcomeIntegrityError(
                f"paper outcome event {row['event_id']} has envelope "
                f"schema_version={envelope_version!r}, unsupported by this build "
                f"(EVENT_SCHEMA_VERSION={EVENT_SCHEMA_VERSION})"
            )
        try:
            raw = json.loads(row["payload_json"])
        except (TypeError, ValueError) as exc:
            raise PaperOutcomeIntegrityError(
                f"paper outcome event {row['event_id']} has unreadable payload: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise PaperOutcomeIntegrityError(
                f"paper outcome event {row['event_id']} payload is not an object"
            )
        # Stored JSON is not trusted merely because it was stored.
        try:
            payload = validate_terminal_outcome_payload(raw)
        except ValueError as exc:
            raise PaperOutcomeIntegrityError(
                f"paper outcome event {row['event_id']} failed contract "
                f"validation: {exc}"
            ) from exc

        expected_key = f"{PAPER_OUTCOME_TERMINAL_RECORDED}:{payload['outcome_id']}"
        if str(row["idempotency_key"]) != expected_key:
            raise PaperOutcomeIntegrityError(
                f"paper outcome event {row['event_id']} idempotency key does not "
                "match its outcome identity"
            )

        outcomes.append(
            CanonicalPaperOutcome(
                outcome_id=str(payload["outcome_id"]),
                event_id=str(row["event_id"]),
                history_epoch=int(row["history_epoch"]),
                local_sequence=int(row["local_sequence"]),
                recorded_at=str(row["recorded_at"]),
                payload=payload,
            )
        )

    return CanonicalPaperOutcomeRead(outcomes=tuple(outcomes), stream_present=stream_present)


def detect_identity_conflicts(
    outcomes: tuple[CanonicalPaperOutcome, ...],
) -> dict[str, list[CanonicalPaperOutcome]]:
    """Group outcomes that share a paper trade identity.

    Two issues are possible and both must fail closed rather than let commit
    order decide a winner:

    * the same ``outcome_id`` appearing twice, which the writer's uniqueness
      constraint should make impossible and therefore signals tampering or a
      hand-edited store;
    * one paper trade carrying outcomes for more than one execution engine.
    """
    grouped: dict[str, list[CanonicalPaperOutcome]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.paper_trade_id, []).append(outcome)
    return {key: rows for key, rows in grouped.items() if len(rows) > 1}


__all__ = [
    "CanonicalPaperOutcome",
    "CanonicalPaperOutcomeRead",
    "PaperOutcomeIntegrityError",
    "PaperOutcomeSchemaError",
    "PaperOutcomeSourceUnavailableError",
    "detect_identity_conflicts",
    "read_canonical_paper_outcomes",
]
