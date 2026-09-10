"""Bounded discovery-outcome maturation with immutable revisions.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

Mirrors Phase 3C semantics: partial labels stay eligible, later revisions are
appended, consumers read the latest revision per observation_id, and the
screening ledger is indexed by byte offset so unprocessed rows cannot fall
out of a tail read.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping

from app.opip.discovery.constants import (
    DISCOVERY_BOUNDED_CHECKPOINT_ANCHOR_BYTES,
    DISCOVERY_BOUNDED_MAX_ROWS,
    DISCOVERY_BOUNDED_RETRY_DELAY,
    DISCOVERY_FORWARD_READ_GRACE,
    DISCOVERY_MATURATION_MILESTONES,
    DISCOVERY_PRIMARY_HORIZON,
    PENDING_FINALIZATION,
)
from app.opip.discovery.attribution import attribution_record
from app.opip.discovery.earliness import earliness_metrics
from app.opip.discovery.outcomes import label_screening_observation
from app.opip.discovery.store import (
    append_discovery_attributions,
    append_discovery_forward_outcomes_locked,
)
from app.opip.early.point_in_time import parse_timestamp
from app.opip.storage.bounded_jsonl import repair_truncated_tail
from app.services.registry_io import registry_lock
from app.services.signal_quality_phase2 import build_timelines, read_observations


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_label_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key
        not in {
            "outcome_record_id",
            "outcome_revision",
            "labeled_at",
        }
    }


def discovery_outcome_record_id(row: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_label_payload(row),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "DOUT:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def discovery_window_complete(row: Mapping[str, Any]) -> bool:
    if bool(row.get("window_complete")):
        return True
    primary = (row.get("horizons") or {}).get(DISCOVERY_PRIMARY_HORIZON) or {}
    return bool(primary.get("window_complete"))


def next_discovery_due_at(
    row: Mapping[str, Any],
    *,
    evaluated_at: datetime,
) -> datetime | None:
    """Incomplete labels remain eligible. Completed windows leave the queue."""
    if discovery_window_complete(row):
        return None
    reference_at = parse_timestamp(
        row.get("reference_at") or row.get("observed_at")
    )
    evaluated = _utc(evaluated_at)
    if reference_at is None:
        return evaluated + DISCOVERY_BOUNDED_RETRY_DELAY
    for delta in DISCOVERY_MATURATION_MILESTONES:
        due = reference_at + delta
        if due > evaluated:
            return due
    return evaluated + DISCOVERY_BOUNDED_RETRY_DELAY


def _bounded_state_path(output_path: Path) -> Path:
    return output_path.parent / f".{output_path.name}.state.sqlite3"


def open_discovery_state(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS latest_outcomes (
            observation_id TEXT PRIMARY KEY,
            outcome_record_id TEXT NOT NULL,
            outcome_revision INTEGER NOT NULL,
            window_complete INTEGER NOT NULL,
            reference_at TEXT NOT NULL DEFAULT '',
            row_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS observation_queue (
            observation_id TEXT PRIMARY KEY,
            observed_at TEXT NOT NULL,
            next_due_at TEXT NOT NULL,
            venue_instrument_id TEXT NOT NULL DEFAULT '',
            row_json TEXT NOT NULL
        )
        """
    )
    queue_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(observation_queue)")
    }
    if "venue_instrument_id" not in queue_columns:
        connection.execute(
            "ALTER TABLE observation_queue "
            "ADD COLUMN venue_instrument_id TEXT NOT NULL DEFAULT ''"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_discovery_queue_due "
        "ON observation_queue(next_due_at, observed_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_discovery_queue_venue "
        "ON observation_queue(venue_instrument_id)"
    )
    connection.commit()
    return connection


def _state_int(connection: sqlite3.Connection, key: str, default: int = 0) -> int:
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


def _set_state_int(connection: sqlite3.Connection, key: str, value: int) -> None:
    connection.execute(
        """
        INSERT INTO metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(int(value))),
    )


def _state_text(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])


def _set_state_text(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )


def _file_anchor(path: Path, offset: int) -> tuple[int, int, str | None]:
    if offset <= 0:
        return 0, 0, None
    start = max(0, offset - DISCOVERY_BOUNDED_CHECKPOINT_ANCHOR_BYTES)
    size = offset - start
    with path.open("rb") as handle:
        handle.seek(start)
        payload = handle.read(size)
    if len(payload) != size:
        raise RuntimeError("DISCOVERY_SCREENING_CHECKPOINT_SHORT_READ")
    return start, size, hashlib.sha256(payload).hexdigest()


def _state_checkpoint_matches(
    connection: sqlite3.Connection,
    path: Path,
    prefix: str,
    offset: int,
) -> bool:
    if offset <= 0:
        return True
    expected = _state_text(connection, f"{prefix}_anchor_sha256")
    start = _state_int(connection, f"{prefix}_anchor_start", -1)
    size = _state_int(connection, f"{prefix}_anchor_size", -1)
    if not expected or start < 0 or size <= 0 or start + size != offset:
        return False
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            payload = handle.read(size)
    except OSError:
        return False
    return len(payload) == size and hashlib.sha256(payload).hexdigest() == expected


def _set_state_checkpoint(
    connection: sqlite3.Connection,
    path: Path,
    prefix: str,
    offset: int,
) -> None:
    _set_state_int(connection, f"{prefix}_indexed_offset", offset)
    if offset <= 0:
        _set_state_int(connection, f"{prefix}_anchor_start", 0)
        _set_state_int(connection, f"{prefix}_anchor_size", 0)
        _set_state_text(connection, f"{prefix}_anchor_sha256", "")
        return
    start, size, digest = _file_anchor(path, offset)
    _set_state_int(connection, f"{prefix}_anchor_start", start)
    _set_state_int(connection, f"{prefix}_anchor_size", size)
    _set_state_text(connection, f"{prefix}_anchor_sha256", digest or "")


def latest_discovery_outcome_row(
    connection: sqlite3.Connection,
    observation_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT row_json FROM latest_outcomes WHERE observation_id = ?",
        (observation_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def latest_discovery_outcomes_by_observation(
    outcomes: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Resolve the latest append-only revision per observation_id."""
    latest: dict[str, dict[str, Any]] = {}
    for row in outcomes:
        if not isinstance(row, Mapping):
            continue
        observation_id = str(row.get("observation_id") or "").strip()
        if not observation_id:
            continue
        try:
            revision = int(row.get("outcome_revision", 0) or 0)
        except (TypeError, ValueError):
            revision = 0
        current = latest.get(observation_id)
        if current is None:
            latest[observation_id] = dict(row)
            continue
        try:
            current_revision = int(current.get("outcome_revision", 0) or 0)
        except (TypeError, ValueError):
            current_revision = 0
        if revision >= current_revision:
            latest[observation_id] = dict(row)
    return latest


def _upsert_latest_outcome(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
) -> None:
    observation_id = str(row.get("observation_id") or "").strip()
    record_id = str(row.get("outcome_record_id") or "").strip()
    if not observation_id:
        return
    try:
        revision = int(row.get("outcome_revision", 0) or 0)
    except (TypeError, ValueError):
        return
    connection.execute(
        """
        INSERT INTO latest_outcomes(
            observation_id, outcome_record_id, outcome_revision,
            window_complete, reference_at, row_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(observation_id) DO UPDATE SET
            outcome_record_id = excluded.outcome_record_id,
            outcome_revision = excluded.outcome_revision,
            window_complete = excluded.window_complete,
            reference_at = excluded.reference_at,
            row_json = excluded.row_json
        WHERE excluded.outcome_revision >= latest_outcomes.outcome_revision
        """,
        (
            observation_id,
            record_id,
            revision,
            1 if discovery_window_complete(row) else 0,
            str(row.get("reference_at") or row.get("observed_at") or ""),
            json.dumps(dict(row), sort_keys=True, allow_nan=False),
        ),
    )


def _schedule_after_evaluation(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
    *,
    evaluated_at: datetime,
) -> None:
    observation_id = str(row.get("observation_id") or "").strip()
    if not observation_id:
        return
    next_due = next_discovery_due_at(row, evaluated_at=evaluated_at)
    if next_due is None:
        connection.execute(
            "DELETE FROM observation_queue WHERE observation_id = ?",
            (observation_id,),
        )
        return
    connection.execute(
        "UPDATE observation_queue SET next_due_at = ? WHERE observation_id = ?",
        (next_due.isoformat(), observation_id),
    )


def _observation_id_from_screening(row: Mapping[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    return str((metadata or {}).get("observation_id") or "").strip()


def reconcile_screening_queue(
    connection: sqlite3.Connection,
    screening_path: Path,
    *,
    now: datetime,
) -> None:
    indexed_offset = _state_int(connection, "screening_indexed_offset", 0)
    if not screening_path.exists():
        if indexed_offset:
            raise RuntimeError("DISCOVERY_SCREENING_LEDGER_TRUNCATED")
        return

    size = screening_path.stat().st_size
    if indexed_offset > size:
        raise RuntimeError("DISCOVERY_SCREENING_LEDGER_TRUNCATED")
    if not _state_checkpoint_matches(
        connection, screening_path, "screening", indexed_offset
    ):
        raise RuntimeError("DISCOVERY_SCREENING_LEDGER_DIVERGED")

    last_complete = indexed_offset
    with screening_path.open("rb") as handle:
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
                snapshot = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(snapshot, dict):
                continue
            if str(snapshot.get("scanner_type") or "") != "BROAD_SEARCH":
                continue
            if str(snapshot.get("outcome") or "") == PENDING_FINALIZATION:
                continue
            metadata = snapshot.get("metadata")
            if isinstance(metadata, Mapping) and str(
                metadata.get("production_admission_result") or ""
            ) == PENDING_FINALIZATION:
                continue
            observation_id = _observation_id_from_screening(snapshot)
            observed_at = parse_timestamp(snapshot.get("observed_at"))
            if not observation_id or observed_at is None:
                continue

            prior = latest_discovery_outcome_row(connection, observation_id)
            if prior is not None and discovery_window_complete(prior):
                connection.execute(
                    "DELETE FROM observation_queue WHERE observation_id = ?",
                    (observation_id,),
                )
                continue
            next_due = (
                next_discovery_due_at(prior, evaluated_at=now)
                if prior is not None
                else observed_at
            )
            if next_due is None:
                connection.execute(
                    "DELETE FROM observation_queue WHERE observation_id = ?",
                    (observation_id,),
                )
                continue
            connection.execute(
                """
                INSERT INTO observation_queue(
                    observation_id, observed_at, next_due_at,
                    venue_instrument_id, row_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    row_json = excluded.row_json,
                    observed_at = excluded.observed_at,
                    venue_instrument_id = excluded.venue_instrument_id
                """,
                (
                    observation_id,
                    observed_at.isoformat(),
                    next_due.isoformat(),
                    str(snapshot.get("venue_instrument_id") or ""),
                    json.dumps(snapshot, sort_keys=True, allow_nan=False),
                ),
            )

    _set_state_checkpoint(connection, screening_path, "screening", last_complete)
    connection.commit()


def due_observation_batch(
    connection: sqlite3.Connection,
    *,
    now: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT row_json
        FROM observation_queue
        WHERE next_due_at <= ?
        ORDER BY observed_at, observation_id
        LIMIT ?
        """,
        (_utc(now).isoformat(), int(limit)),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for (raw,) in rows:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def mature_discovery_outcomes_bounded(
    *,
    screening_path: Path,
    observation_path: Path,
    output_dir: Path,
    max_rows: int = DISCOVERY_BOUNDED_MAX_ROWS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Label a bounded due queue. Incomplete IDs remain eligible for revision."""
    labeled_at = _utc(now or datetime.now(timezone.utc))
    outcomes_path = output_dir / "forward_outcomes.jsonl"
    state_path = _bounded_state_path(outcomes_path)
    summary = {
        "measurement_only": True,
        "trade_authority_changed": False,
        "evaluated": 0,
        "written_outcomes": 0,
        "reused_current_revision": 0,
        "queued_due": 0,
        "completed": 0,
        "still_incomplete": 0,
        "written_attributions": 0,
    }
    if max_rows < 1:
        return summary

    output_dir.mkdir(parents=True, exist_ok=True)
    lock = output_dir / ".forward_outcomes.jsonl.lock"
    with registry_lock(lock):
        repair_truncated_tail(outcomes_path)
        connection = open_discovery_state(state_path)
        try:
            reconcile_screening_queue(
                connection, screening_path, now=labeled_at
            )
            pending = due_observation_batch(
                connection, now=labeled_at, limit=max_rows
            )
            summary["queued_due"] = len(pending)
            if not pending:
                connection.commit()
                return summary

            symbols = {
                str(row.get("venue_instrument_id") or "").upper()
                for row in pending
                if str(row.get("venue_instrument_id") or "").strip()
            }
            times = [
                parsed
                for parsed in (
                    parse_timestamp(row.get("observed_at")) for row in pending
                )
                if parsed is not None
            ]
            latest_decision = max(times) if times else labeled_at
            earliest = min(times) if times else labeled_at
            ingestion = read_observations(
                observation_path,
                symbols=symbols or None,
                start_at=earliest,
                end_at=latest_decision + DISCOVERY_FORWARD_READ_GRACE,
            )
            timelines = build_timelines(ingestion.observations)

            by_instrument: dict[str, list[dict[str, Any]]] = {}
            needed = tuple(
                venue
                for venue in {
                    str(row.get("venue_instrument_id") or "")
                    for row in pending
                }
                if venue
            )
            if needed:
                placeholders = ",".join("?" * len(needed))
                queued = connection.execute(
                    "SELECT row_json FROM observation_queue "
                    f"WHERE venue_instrument_id IN ({placeholders})",
                    needed,
                ).fetchall()
                for (raw,) in queued:
                    try:
                        item = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(item, dict):
                        continue
                    venue_id = str(item.get("venue_instrument_id") or "")
                    if venue_id:
                        by_instrument.setdefault(venue_id, []).append(item)

            new_rows: list[dict[str, Any]] = []
            for row in pending:
                observation_id = _observation_id_from_screening(row)
                venue_id = str(row.get("venue_instrument_id") or "").upper()
                timeline = timelines.get(venue_id)
                outcome = label_screening_observation(
                    row, timeline, labeled_at=labeled_at
                )
                primary = (outcome.get("horizons") or {}).get(
                    DISCOVERY_PRIMARY_HORIZON
                ) or {}
                favorable_at = primary.get("favorable_barrier_at") or primary.get(
                    "mfe_at"
                )
                favorable_price = None
                last_forward = primary.get("last_forward_price")
                if primary.get("mfe_pct") is not None and outcome.get(
                    "reference_price"
                ):
                    direction = str(
                        outcome.get("realized_opportunity_direction")
                        or outcome.get("production_preferred_direction")
                        or "LONG"
                    )
                    ref = float(outcome["reference_price"])
                    mfe = float(primary["mfe_pct"])
                    if direction == "SHORT":
                        favorable_price = ref * (1.0 - mfe / 100.0)
                    else:
                        favorable_price = ref * (1.0 + mfe / 100.0)
                outcome["earliness"] = earliness_metrics(
                    by_instrument.get(
                        str(row.get("venue_instrument_id") or ""), []
                    ),
                    direction=str(
                        outcome.get("realized_opportunity_direction")
                        or outcome.get("production_preferred_direction")
                        or "LONG"
                    ),
                    favorable_price=favorable_price or last_forward,
                    favorable_at=favorable_at,
                )
                outcome["window_complete"] = discovery_window_complete(outcome)
                record_id = discovery_outcome_record_id(outcome)
                prior = latest_discovery_outcome_row(connection, observation_id)
                if (
                    prior is not None
                    and str(prior.get("outcome_record_id") or "") == record_id
                ):
                    summary["reused_current_revision"] += 1
                    _schedule_after_evaluation(
                        connection, prior, evaluated_at=labeled_at
                    )
                    if discovery_window_complete(prior):
                        summary["completed"] += 1
                    else:
                        summary["still_incomplete"] += 1
                    continue

                revision = (
                    int(prior.get("outcome_revision", 0) or 0) + 1
                    if prior is not None
                    else 1
                )
                stamped = {
                    **outcome,
                    "outcome_record_id": record_id,
                    "outcome_revision": revision,
                    "append_only": True,
                }
                new_rows.append(stamped)
                _upsert_latest_outcome(connection, stamped)
                _schedule_after_evaluation(
                    connection, stamped, evaluated_at=labeled_at
                )
                if discovery_window_complete(stamped):
                    summary["completed"] += 1
                else:
                    summary["still_incomplete"] += 1

            written = 0
            written_attr = 0
            if new_rows:
                written = append_discovery_forward_outcomes_locked(
                    new_rows, path=outcomes_path
                )
                new_ids = {
                    str(item.get("observation_id") or "")
                    for item in new_rows
                    if item.get("observation_id")
                }
                attribution_rows = [
                    attribution_record(row, labeled_at=labeled_at)
                    for row in pending
                    if _observation_id_from_screening(row) in new_ids
                ]
                if attribution_rows:
                    written_attr = append_discovery_attributions(
                        attribution_rows,
                        path=output_dir / "attributions.jsonl",
                    )
            summary["evaluated"] = len(pending)
            summary["written_outcomes"] = written
            summary["written_attributions"] = written_attr
            connection.commit()
            return summary
        finally:
            connection.close()
