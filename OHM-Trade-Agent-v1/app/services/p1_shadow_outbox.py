"""Durable local outbox and evidence ledger for P1 shadow intelligence.

The producer performs local append-only I/O only. It never performs network
calls or P1 evaluation. The outbox is dark by default and is enabled only by
P1_SHADOW_OUTBOX_ENABLED=true.

The consumer is a separate process/job and therefore cannot delay Telegram or
the live Phase 1 decision path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable, Mapping

from app.services.p1_intelligence_contracts import build_live_scan_snapshot
from app.services.registry_io import registry_lock, save_json_atomic


DEFAULT_OUTBOX_FILE = Path("/app/data/p1_shadow_outbox.jsonl")
DEFAULT_EVIDENCE_LEDGER = Path("/app/data/p1_evidence_ledger.jsonl")
DEFAULT_CHECKPOINT_FILE = Path("/app/data/p1_shadow_outbox_checkpoint.json")
DEFAULT_DEAD_LETTER_FILE = Path("/app/data/p1_shadow_outbox_dead_letter.jsonl")


def p1_shadow_outbox_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get("P1_SHADOW_OUTBOX_ENABLED", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.parent / f".{path.name}.lock"
    with registry_lock(lock):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass


def _repair_truncated_tail_before_append(target: Path) -> bool:
    """Isolate a crashed partial tail before a later producer appends.

    JSONL requires record boundaries. If a prior process died after writing only
    part of its final row, appending the next valid snapshot directly would
    concatenate the two payloads into one malformed line and destroy the good
    later snapshot with the bad tail. Under the producer/consumer file lock,
    terminate that orphan tail with a newline first. The consumer can then
    dead-letter only the damaged row while preserving every later valid row.

    Returns True when a repair newline was written.
    """
    if not target.exists() or target.stat().st_size <= 0:
        return False
    with target.open("rb") as reader:
        reader.seek(-1, os.SEEK_END)
        if reader.read(1) == b"\n":
            return False
    with target.open("ab") as handle:
        handle.write(b"\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    return True


def append_live_scan_snapshots(
    candidates: Iterable[Any],
    *,
    decision_at: datetime,
    reference_prices: Mapping[str, float] | None = None,
    path: Path | None = None,
    dead_letter_path: Path | None = None,
    enabled: bool | None = None,
) -> int:
    """Append candidate snapshots in existing ranked order, fail-soft.

    Suppressed candidates are intentionally retained. ``candidate_rank`` is
    the one-based index in the incoming ranked stream.

    Candidate build/serialization failures are isolated per row and written to
    the research dead-letter stream; one malformed candidate cannot truncate
    the remainder of the ranked cohort. The return value is the number of
    snapshots actually appended, not the number attempted.
    """
    active = p1_shadow_outbox_enabled() if enabled is None else bool(enabled)
    if not active:
        return 0

    rows = list(candidates)
    if not rows:
        return 0

    target = path or DEFAULT_OUTBOX_FILE
    dead_letter = dead_letter_path or DEFAULT_DEAD_LETTER_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / f".{target.name}.lock"
    written = 0

    try:
        with registry_lock(lock):
            _repair_truncated_tail_before_append(target)
            with target.open("a", encoding="utf-8") as handle:
                for index, candidate in enumerate(rows, start=1):
                    try:
                        snapshot = build_live_scan_snapshot(
                            candidate,
                            decision_at=decision_at,
                            candidate_rank=index,
                            reference_prices=reference_prices,
                        )
                        encoded = json.dumps(
                            snapshot.as_dict(), sort_keys=True, allow_nan=False
                        )
                    except Exception as exc:
                        _append_jsonl(
                            dead_letter,
                            {
                                "dead_letter_source": "P1_OUTBOX_PRODUCER",
                                "candidate_rank": index,
                                "symbol": str(
                                    getattr(candidate, "symbol", "") or ""
                                ).upper(),
                                "decision_at_utc": (
                                    decision_at.isoformat()
                                    if isinstance(decision_at, datetime)
                                    else str(decision_at)
                                ),
                                "error_type": type(exc).__name__,
                                "measurement_only": True,
                                "affects_live_decisions": False,
                            },
                        )
                        continue
                    handle.write(encoded + "\n")
                    written += 1
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        return written
    except Exception:
        # The live caller is intentionally fail-soft. A producer-level I/O or
        # lock failure never suppresses alerts or Phase 3B measurement.
        return 0


@dataclass(frozen=True)
class DrainResult:
    processed: int
    duplicates: int
    malformed: int
    remaining_from_line: int
    stopped_on_error: bool
    error_type: str | None = None


OUTBOX_CHECKPOINT_SCHEMA_VERSION = 2
CHECKPOINT_ANCHOR_BYTES = 4096


@dataclass(frozen=True)
class _OutboxCheckpoint:
    next_line: int = 0
    byte_offset: int = 0
    anchor_start: int = 0
    anchor_size: int = 0
    anchor_sha256: str | None = None
    source_size: int = 0
    source_tail_start: int = 0
    source_tail_size: int = 0
    source_tail_sha256: str | None = None


@dataclass(frozen=True)
class _OutboxLine:
    line_number: int
    raw: bytes
    end_offset: int


class _CheckpointInvariantError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _checkpoint_anchor(source: Path, byte_offset: int) -> tuple[int, int, str | None]:
    if byte_offset <= 0:
        return 0, 0, None
    start = max(0, byte_offset - CHECKPOINT_ANCHOR_BYTES)
    size = byte_offset - start
    with source.open("rb") as handle:
        handle.seek(start)
        payload = handle.read(size)
    if len(payload) != size:
        raise _CheckpointInvariantError("CHECKPOINT_AHEAD_OF_OUTBOX")
    return start, size, hashlib.sha256(payload).hexdigest()


def _seek_legacy_line_offset(source: Path, next_line: int) -> int:
    """Migrate a legacy line cursor without materializing the whole source."""
    if next_line <= 0:
        return 0
    if not source.exists():
        raise _CheckpointInvariantError("CHECKPOINT_AHEAD_OF_OUTBOX")
    with source.open("rb") as handle:
        for _ in range(next_line):
            raw = handle.readline()
            if not raw or not raw.endswith(b"\n"):
                raise _CheckpointInvariantError("CHECKPOINT_AHEAD_OF_OUTBOX")
        return handle.tell()


def _load_checkpoint_state(path: Path, source: Path) -> _OutboxCheckpoint:
    if not path.exists():
        return _OutboxCheckpoint()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        next_line = int(payload.get("next_line", 0))
        if next_line < 0:
            raise ValueError("negative checkpoint")

        schema = int(payload.get("schema_version", 1) or 1)
        if schema >= OUTBOX_CHECKPOINT_SCHEMA_VERSION and "byte_offset" in payload:
            byte_offset = int(payload.get("byte_offset", 0))
            anchor_start = int(payload.get("anchor_start", 0))
            anchor_size = int(payload.get("anchor_size", 0))
            anchor_sha256 = str(payload.get("anchor_sha256") or "") or None

            # Early schema-2 development checkpoints did not yet preserve the
            # prior source generation tail. Upgrade them in-memory once; every
            # subsequent advancement persists the full continuity contract.
            if "source_size" in payload:
                source_size = int(payload.get("source_size", 0))
                source_tail_start = int(payload.get("source_tail_start", 0))
                source_tail_size = int(payload.get("source_tail_size", 0))
                source_tail_sha256 = (
                    str(payload.get("source_tail_sha256") or "") or None
                )
            else:
                source_size = source.stat().st_size if source.exists() else 0
                (
                    source_tail_start,
                    source_tail_size,
                    source_tail_sha256,
                ) = _checkpoint_anchor(source, source_size)

            if min(
                byte_offset,
                anchor_start,
                anchor_size,
                source_size,
                source_tail_start,
                source_tail_size,
            ) < 0:
                raise ValueError("negative checkpoint component")
            return _OutboxCheckpoint(
                next_line=next_line,
                byte_offset=byte_offset,
                anchor_start=anchor_start,
                anchor_size=anchor_size,
                anchor_sha256=anchor_sha256,
                source_size=source_size,
                source_tail_start=source_tail_start,
                source_tail_size=source_tail_size,
                source_tail_sha256=source_tail_sha256,
            )

        # Legacy checkpoints stored only a line index. Resolve that index once
        # with a streaming scan, then persist schema 2 on the next advancement.
        byte_offset = _seek_legacy_line_offset(source, next_line)
        anchor_start, anchor_size, anchor_sha256 = _checkpoint_anchor(
            source, byte_offset
        )
        source_size = source.stat().st_size if source.exists() else 0
        (
            source_tail_start,
            source_tail_size,
            source_tail_sha256,
        ) = _checkpoint_anchor(source, source_size)
        return _OutboxCheckpoint(
            next_line=next_line,
            byte_offset=byte_offset,
            anchor_start=anchor_start,
            anchor_size=anchor_size,
            anchor_sha256=anchor_sha256,
            source_size=source_size,
            source_tail_start=source_tail_start,
            source_tail_size=source_tail_size,
            source_tail_sha256=source_tail_sha256,
        )
    except _CheckpointInvariantError:
        raise
    except Exception as exc:
        raise _CheckpointInvariantError("CHECKPOINT_UNREADABLE") from exc


def _verify_checkpoint_continuity(source: Path, checkpoint: _OutboxCheckpoint) -> None:
    if not source.exists():
        if checkpoint.byte_offset:
            raise _CheckpointInvariantError("CHECKPOINT_AHEAD_OF_OUTBOX")
        return

    size = source.stat().st_size
    if checkpoint.byte_offset > size:
        raise _CheckpointInvariantError("CHECKPOINT_AHEAD_OF_OUTBOX")
    if checkpoint.source_size > size:
        raise _CheckpointInvariantError("CHECKPOINT_SOURCE_DIVERGED")

    if checkpoint.byte_offset > 0 and not checkpoint.anchor_sha256:
        raise _CheckpointInvariantError("CHECKPOINT_UNREADABLE")
    if checkpoint.source_size > 0 and not checkpoint.source_tail_sha256:
        raise _CheckpointInvariantError("CHECKPOINT_UNREADABLE")

    if checkpoint.anchor_sha256:
        if (
            checkpoint.anchor_start < 0
            or checkpoint.anchor_size < 0
            or checkpoint.anchor_start + checkpoint.anchor_size
            != checkpoint.byte_offset
        ):
            raise _CheckpointInvariantError("CHECKPOINT_UNREADABLE")

        with source.open("rb") as handle:
            handle.seek(checkpoint.anchor_start)
            payload = handle.read(checkpoint.anchor_size)
        if (
            len(payload) != checkpoint.anchor_size
            or hashlib.sha256(payload).hexdigest() != checkpoint.anchor_sha256
        ):
            raise _CheckpointInvariantError("CHECKPOINT_SOURCE_DIVERGED")

    # The processed-prefix anchor prevents replaying changed history before the
    # cursor. The prior-generation tail anchor additionally proves that every
    # byte that existed after the cursor on the previous sync is still present.
    # This catches truncate/rotate-and-regrow cases that a cursor-only anchor
    # cannot distinguish from a legitimate append-only replacement.
    if checkpoint.source_tail_sha256:
        if (
            checkpoint.source_tail_start < 0
            or checkpoint.source_tail_size < 0
            or checkpoint.source_tail_start + checkpoint.source_tail_size
            != checkpoint.source_size
        ):
            raise _CheckpointInvariantError("CHECKPOINT_UNREADABLE")
        with source.open("rb") as handle:
            handle.seek(checkpoint.source_tail_start)
            payload = handle.read(checkpoint.source_tail_size)
        if (
            len(payload) != checkpoint.source_tail_size
            or hashlib.sha256(payload).hexdigest()
            != checkpoint.source_tail_sha256
        ):
            raise _CheckpointInvariantError("CHECKPOINT_SOURCE_DIVERGED")


def _save_checkpoint_state(
    path: Path,
    source: Path,
    *,
    next_line: int,
    byte_offset: int,
) -> None:
    anchor_start, anchor_size, anchor_sha256 = _checkpoint_anchor(
        source, byte_offset
    )
    source_size = source.stat().st_size if source.exists() else 0
    (
        source_tail_start,
        source_tail_size,
        source_tail_sha256,
    ) = _checkpoint_anchor(source, source_size)
    save_json_atomic(
        path,
        {
            "schema_version": OUTBOX_CHECKPOINT_SCHEMA_VERSION,
            "next_line": next_line,
            "byte_offset": byte_offset,
            "anchor_start": anchor_start,
            "anchor_size": anchor_size,
            "anchor_sha256": anchor_sha256,
            "source_size": source_size,
            "source_tail_start": source_tail_start,
            "source_tail_size": source_tail_size,
            "source_tail_sha256": source_tail_sha256,
        },
    )


def _read_complete_outbox_batch(
    source: Path,
    *,
    checkpoint: _OutboxCheckpoint,
    batch_limit: int,
) -> list[_OutboxLine]:
    """Read only the next complete bounded JSONL batch from the byte cursor."""
    if not source.exists():
        return []

    source_lock = source.parent / f".{source.name}.lock"
    rows: list[_OutboxLine] = []
    with registry_lock(source_lock):
        _verify_checkpoint_continuity(source, checkpoint)
        with source.open("rb") as handle:
            handle.seek(checkpoint.byte_offset)
            line_number = checkpoint.next_line
            while len(rows) < batch_limit:
                raw = handle.readline()
                if not raw:
                    break
                end = handle.tell()
                if not raw.endswith(b"\n"):
                    # Writer has not completed the final JSONL record yet.
                    break
                rows.append(
                    _OutboxLine(
                        line_number=line_number,
                        raw=raw,
                        end_offset=end,
                    )
                )
                line_number += 1
    return rows


def _ledger_index_path(ledger: Path) -> Path:
    return ledger.parent / f".{ledger.name}.dedup.sqlite3"


def _open_ledger_index(path: Path) -> sqlite3.Connection:
    """Open the disk-backed dedup index; no historical id set lives in RAM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshot_ids (
            snapshot_id TEXT PRIMARY KEY
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def _metadata_int(connection: sqlite3.Connection, key: str, default: int = 0) -> int:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return default
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return default


def _set_metadata_int(connection: sqlite3.Connection, key: str, value: int) -> None:
    connection.execute(
        """
        INSERT INTO metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(int(value))),
    )


def _reconcile_ledger_index(
    connection: sqlite3.Connection,
    ledger: Path,
) -> None:
    """Catch the SQLite index up to the append-only ledger in constant memory."""
    indexed_offset = _metadata_int(connection, "indexed_offset", 0)
    if not ledger.exists():
        if indexed_offset:
            connection.execute("DELETE FROM snapshot_ids")
            _set_metadata_int(connection, "indexed_offset", 0)
            connection.commit()
        return

    size = ledger.stat().st_size
    if indexed_offset > size:
        # The ledger is contractually append-only. Rebuild if an operator
        # replaced/truncated it rather than trusting stale dedup state.
        connection.execute("DELETE FROM snapshot_ids")
        indexed_offset = 0

    last_complete = indexed_offset
    with ledger.open("rb") as handle:
        handle.seek(indexed_offset)
        while True:
            raw = handle.readline()
            if not raw:
                break
            end = handle.tell()
            if not raw.endswith(b"\n"):
                break
            last_complete = end
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            snapshot_id = str(row.get("snapshot_id", "") or "")
            if snapshot_id:
                connection.execute(
                    "INSERT OR IGNORE INTO snapshot_ids(snapshot_id) VALUES (?)",
                    (snapshot_id,),
                )

    _set_metadata_int(connection, "indexed_offset", last_complete)
    connection.commit()


def _ledger_has_snapshot(
    connection: sqlite3.Connection,
    snapshot_id: str,
) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM snapshot_ids WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        is not None
    )


def _append_ledger_payload(
    connection: sqlite3.Connection,
    ledger: Path,
    payload: Mapping[str, Any],
) -> None:
    snapshot_id = str(payload.get("snapshot_id", "") or "")
    if not snapshot_id:
        raise ValueError("snapshot_id missing")

    ledger.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(dict(payload), sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    with ledger.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        end_offset = handle.tell()

    # Ledger durability happens before index commit. If the process dies
    # between them, the next reconciliation recovers the missing index row.
    connection.execute(
        "INSERT OR IGNORE INTO snapshot_ids(snapshot_id) VALUES (?)",
        (snapshot_id,),
    )
    _set_metadata_int(connection, "indexed_offset", end_offset)
    connection.commit()


def _default_ledger_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "evidence_schema_version": 1,
        "evidence_status": "SNAPSHOT_ACCEPTED",
        "measurement_only": True,
        "advisory_only": True,
        "affects_ranking": False,
        "affects_telegram": False,
        "affects_pending_setup": False,
        "trade_authority_changed": False,
        "production_execution_gate_changed": False,
    }


def _drain_outbox_locked(
    *,
    source: Path,
    ledger: Path,
    checkpoint: Path,
    dead_letter: Path,
    batch_limit: int,
    processor: Callable[[dict[str, Any]], None] | None,
) -> DrainResult:
    try:
        state = _load_checkpoint_state(checkpoint, source)
        batch = _read_complete_outbox_batch(
            source,
            checkpoint=state,
            batch_limit=batch_limit,
        )
    except _CheckpointInvariantError as exc:
        return DrainResult(0, 0, 0, 0, True, exc.code)

    processed = duplicates = malformed = 0
    next_line = state.next_line
    next_offset = state.byte_offset
    stopped = False
    error_type: str | None = None

    connection: sqlite3.Connection | None = None
    ledger_lock = ledger.parent / f".{ledger.name}.lock"

    try:
        if processor is None:
            connection = _open_ledger_index(_ledger_index_path(ledger))
            ledger_context = registry_lock(ledger_lock)
        else:
            ledger_context = _NullContext()

        with ledger_context:
            if connection is not None:
                _reconcile_ledger_index(connection, ledger)

            for item in batch:
                try:
                    row = json.loads(item.raw.decode("utf-8"))
                    if not isinstance(row, dict):
                        raise ValueError("outbox row must be a JSON object")
                except Exception as exc:
                    malformed += 1
                    _append_jsonl(
                        dead_letter,
                        {
                            "dead_letter_source": "P1_OUTBOX_CONSUMER",
                            "line_number": item.line_number,
                            "error_type": type(exc).__name__,
                            "raw": item.raw.decode(
                                "utf-8", errors="replace"
                            ).rstrip("\n"),
                            "measurement_only": True,
                        },
                    )
                    next_line = item.line_number + 1
                    next_offset = item.end_offset
                    _save_checkpoint_state(
                        checkpoint,
                        source,
                        next_line=next_line,
                        byte_offset=next_offset,
                    )
                    continue

                try:
                    if processor is not None:
                        processor(row)
                        wrote = True
                    else:
                        assert connection is not None
                        snapshot_id = str(row.get("snapshot_id", "") or "")
                        if not snapshot_id:
                            raise ValueError("snapshot_id missing")
                        if _ledger_has_snapshot(connection, snapshot_id):
                            wrote = False
                        else:
                            _append_ledger_payload(
                                connection,
                                ledger,
                                _default_ledger_payload(row),
                            )
                            wrote = True

                    if wrote:
                        processed += 1
                    else:
                        duplicates += 1
                except Exception as exc:
                    stopped = True
                    error_type = type(exc).__name__
                    next_line = item.line_number
                    break

                next_line = item.line_number + 1
                next_offset = item.end_offset
                _save_checkpoint_state(
                    checkpoint,
                    source,
                    next_line=next_line,
                    byte_offset=next_offset,
                )
    finally:
        if connection is not None:
            connection.close()

    return DrainResult(
        processed=processed,
        duplicates=duplicates,
        malformed=malformed,
        remaining_from_line=next_line,
        stopped_on_error=stopped,
        error_type=error_type,
    )


def drain_outbox_to_evidence_ledger(
    *,
    outbox_path: Path | None = None,
    evidence_path: Path | None = None,
    checkpoint_path: Path | None = None,
    dead_letter_path: Path | None = None,
    batch_limit: int = 100,
    processor: Callable[[dict[str, Any]], None] | None = None,
) -> DrainResult:
    """Consume a bounded batch using a byte cursor and disk-backed dedup index.

    Total historical outbox size no longer determines resident memory. The
    schema-2 checkpoint stores a byte offset and continuity anchor so an
    atomically replaced export may grow append-only while truncation or
    rewritten history fails closed instead of silently skipping evidence.
    """
    source = outbox_path or DEFAULT_OUTBOX_FILE
    ledger = evidence_path or DEFAULT_EVIDENCE_LEDGER
    checkpoint = checkpoint_path or DEFAULT_CHECKPOINT_FILE
    dead_letter = dead_letter_path or DEFAULT_DEAD_LETTER_FILE
    if batch_limit < 1:
        raise ValueError("batch_limit must be >= 1")
    if not source.exists():
        return DrainResult(0, 0, 0, 0, False)

    consumer_lock = checkpoint.parent / f".{checkpoint.name}.consumer.lock"
    with registry_lock(consumer_lock):
        return _drain_outbox_locked(
            source=source,
            ledger=ledger,
            checkpoint=checkpoint,
            dead_letter=dead_letter,
            batch_limit=batch_limit,
            processor=processor,
        )


def _checkpoint_next_line(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(payload.get("next_line", 0)))
    except Exception:
        return 0


def _count_source_rows(source: Path) -> int:
    if not source.exists():
        return 0
    total = 0
    with source.open("rb") as handle:
        for _ in handle:
            total += 1
    return total


def outbox_health(
    *,
    outbox_path: Path | None = None,
    checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    source = outbox_path or DEFAULT_OUTBOX_FILE
    checkpoint = checkpoint_path or DEFAULT_CHECKPOINT_FILE
    next_line = _checkpoint_next_line(checkpoint)

    try:
        total = _count_source_rows(source)
    except OSError:
        total = 0

    checkpoint_ahead = next_line > total
    status = "CHECKPOINT_AHEAD_OF_OUTBOX" if checkpoint_ahead else "OK"
    if not checkpoint_ahead and checkpoint.exists() and source.exists():
        try:
            state = _load_checkpoint_state(checkpoint, source)
            _verify_checkpoint_continuity(source, state)
        except _CheckpointInvariantError as exc:
            status = exc.code

    return {
        "total_rows": total,
        "processed_through_line": next_line,
        "backlog_rows": max(0, total - next_line),
        "status": status,
        "checkpoint_ahead_of_outbox": checkpoint_ahead,
        "measurement_only": True,
        "affects_live_decisions": False,
    }
