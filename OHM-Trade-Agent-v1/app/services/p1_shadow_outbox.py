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
import json
import os
from pathlib import Path
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


def _load_checkpoint(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(payload.get("next_line", 0)))
    except Exception:
        return 0


def _ledger_snapshot_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            snapshot_id = str(row.get("snapshot_id", "") or "")
            if snapshot_id:
                ids.add(snapshot_id)
    except OSError:
        return set()
    return ids


def _default_ledger_processor(
    row: dict[str, Any],
    *,
    evidence_path: Path,
    known_ids: set[str],
) -> bool:
    snapshot_id = str(row.get("snapshot_id", "") or "")
    if not snapshot_id:
        raise ValueError("snapshot_id missing")
    if snapshot_id in known_ids:
        return False

    payload = {
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
    _append_jsonl(evidence_path, payload)
    known_ids.add(snapshot_id)
    return True


def _read_complete_outbox_lines(source: Path) -> list[str]:
    """Read a stable outbox snapshot and never consume a partial tail record.

    The producer and reader share the same file lock, so a concurrent append
    cannot be observed mid-write. If a prior process crashed with a truncated
    final line, the incomplete tail is deliberately left pending instead of
    being dead-lettered and checkpointed past. A later producer first inserts a
    newline boundary before appending, turning the orphan tail into its own
    dead-letterable row without corrupting the new snapshot.
    """
    source_lock = source.parent / f".{source.name}.lock"
    with registry_lock(source_lock):
        text = source.read_text(encoding="utf-8")
    if not text:
        return []
    lines = text.splitlines()
    if not text.endswith("\n") and lines:
        lines = lines[:-1]
    return lines


def _drain_outbox_locked(
    *,
    source: Path,
    ledger: Path,
    checkpoint: Path,
    dead_letter: Path,
    batch_limit: int,
    processor: Callable[[dict[str, Any]], None] | None,
) -> DrainResult:
    start_line = _load_checkpoint(checkpoint)
    try:
        lines = _read_complete_outbox_lines(source)
    except OSError as exc:
        return DrainResult(0, 0, 0, start_line, True, type(exc).__name__)

    # A line-index checkpoint cannot safely follow an outbox that has been
    # externally truncated or rotated. Do not silently report "caught up" and
    # skip the new generation; surface the invariant violation explicitly.
    if start_line > len(lines):
        return DrainResult(
            0,
            0,
            0,
            start_line,
            True,
            "CHECKPOINT_AHEAD_OF_OUTBOX",
        )

    known_ids = _ledger_snapshot_ids(ledger)
    processed = duplicates = malformed = 0
    next_line = start_line
    stopped = False
    error_type: str | None = None

    for index in range(start_line, min(len(lines), start_line + batch_limit)):
        raw = lines[index]
        try:
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError("outbox row must be a JSON object")
        except Exception as exc:
            malformed += 1
            _append_jsonl(
                dead_letter,
                {
                    "dead_letter_source": "P1_OUTBOX_CONSUMER",
                    "line_number": index,
                    "error_type": type(exc).__name__,
                    "raw": raw,
                    "measurement_only": True,
                },
            )
            next_line = index + 1
            save_json_atomic(checkpoint, {"next_line": next_line})
            continue

        try:
            if processor is not None:
                processor(row)
                wrote = True
            else:
                wrote = _default_ledger_processor(
                    row, evidence_path=ledger, known_ids=known_ids
                )
            if wrote:
                processed += 1
            else:
                duplicates += 1
        except Exception as exc:
            stopped = True
            error_type = type(exc).__name__
            next_line = index
            break

        next_line = index + 1
        save_json_atomic(checkpoint, {"next_line": next_line})

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
    """Consume snapshots after the live path and advance a durable line cursor.

    A processor failure does not advance the failing line, so it can be retried.
    Malformed complete JSON is moved to a dead-letter stream and the cursor
    advances. A partial trailing line is never treated as malformed. A dedicated
    consumer lock prevents two workers from racing the same checkpoint/ledger.
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


def outbox_health(
    *,
    outbox_path: Path | None = None,
    checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    source = outbox_path or DEFAULT_OUTBOX_FILE
    checkpoint = checkpoint_path or DEFAULT_CHECKPOINT_FILE
    next_line = _load_checkpoint(checkpoint)
    try:
        total = len(source.read_text(encoding="utf-8").splitlines()) if source.exists() else 0
    except OSError:
        total = 0
    checkpoint_ahead = next_line > total
    return {
        "total_rows": total,
        "processed_through_line": next_line,
        "backlog_rows": max(0, total - next_line),
        "status": (
            "CHECKPOINT_AHEAD_OF_OUTBOX" if checkpoint_ahead else "OK"
        ),
        "checkpoint_ahead_of_outbox": checkpoint_ahead,
        "measurement_only": True,
        "affects_live_decisions": False,
    }
