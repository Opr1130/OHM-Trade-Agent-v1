"""Read-only reader over canonically committed FeatureSnapshots.

This is the seam between the Feature Bus (which *publishes* snapshots through the
canonical writer) and the non-authoritative target spine (which *consumes* them).
It opens the canonical store through ``CanonicalWriter.for_reads`` - a read-only
connection with no store lock and no schema initialization - so it can never
mutate, quarantine or rewrite canonical evidence.

Properties this reader guarantees:

* reads only committed ``FEATURE_SNAPSHOT_RECORDED`` records, in canonical
  ``(history_epoch, local_sequence)`` order;
* reconstructs the canonical ``FeatureSnapshot`` contract and re-derives its
  ``snapshot_id`` and ``content_hash``, so a payload whose declared identity does
  not match its content is rejected rather than trusted;
* fails closed on malformed or corrupt records (they are rejected, never
  repaired or fabricated);
* exposes a bounded cursor so a consumer processes each committed snapshot once,
  deterministically, and resumes after a restart without reclassifying old
  snapshots as new;
* dedupes deterministically by ``snapshot_id``.

It holds no trading, admission, reservation or execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.enums import CoverageState, Missingness, RestartState
from app.opip.contracts.features import FEATURE_SNAPSHOT_RECORD_TYPE, FeatureSnapshot
from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.ml.temporal import AvailabilityStamp

#: A bounded read batch. Production caps the batch so one read cannot hold the
#: reader open for an unbounded time.
DEFAULT_BATCH_LIMIT = 200
MAX_BATCH_LIMIT = 1_000


class SnapshotRecordError(ValueError):
    """A committed snapshot payload could not be trusted as canonical evidence."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SnapshotRecordError(message)


def _parse_iso(value: object, *, field_name: str) -> datetime:
    _require(isinstance(value, str) and bool(value.strip()), f"{field_name} is required")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotRecordError(f"{field_name} is not an ISO timestamp") from exc


def feature_snapshot_from_payload(payload: Mapping[str, Any]) -> FeatureSnapshot:
    """Reconstruct and validate a canonical ``FeatureSnapshot`` from a payload.

    Fails closed: any missing field, wrong type, unknown enum value, malformed
    timestamp, or an identity (``snapshot_id``/``content_hash``) that does not match
    the content raises ``SnapshotRecordError``. This wrapper guarantees the parser
    never escapes a raw ``KeyError``/``ValueError``/``TypeError`` that could abort a
    whole read batch.
    """
    try:
        return _build_feature_snapshot(payload)
    except SnapshotRecordError:
        raise
    except (
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
        OverflowError,
        ArithmeticError,
    ) as exc:
        raise SnapshotRecordError(
            f"snapshot payload is not contract-valid: {exc}"
        ) from exc


def _build_feature_snapshot(payload: Mapping[str, Any]) -> FeatureSnapshot:
    """Parse a payload, raising ``SnapshotRecordError`` for malformed records."""
    _require(isinstance(payload, Mapping), "snapshot payload must be a mapping")
    _require(
        payload.get("record_type") == FEATURE_SNAPSHOT_RECORD_TYPE,
        "payload record_type is not a FeatureSnapshot",
    )

    watermark_raw = payload.get("consumed_input_watermark")
    _require(isinstance(watermark_raw, Mapping), "consumed_input_watermark is required")
    try:
        watermark = ConsumedInputWatermark.from_dict(watermark_raw)
    except (KeyError, ValueError, TypeError) as exc:
        # A watermark that is present but malformed (missing/non-int keys) must fail
        # closed like any other malformed record, never escape as a raw KeyError.
        raise SnapshotRecordError(
            f"consumed_input_watermark is malformed: {exc}"
        ) from exc

    availability_raw = payload.get("availability")
    _require(isinstance(availability_raw, Mapping), "availability is required")
    try:
        availability = AvailabilityStamp(
            source_at_utc=(
                _parse_iso(
                    availability_raw.get("source_at_utc"),
                    field_name="availability.source_at_utc",
                )
                if availability_raw.get("source_at_utc") is not None
                else None
            ),
            ingested_at_utc=_parse_iso(
                availability_raw.get("ingested_at_utc"),
                field_name="availability.ingested_at_utc",
            ),
            visible_at_utc=_parse_iso(
                availability_raw.get("visible_at_utc"),
                field_name="availability.visible_at_utc",
            ),
            source_version=str(availability_raw.get("source_version") or ""),
        )
    except (KeyError, ValueError, TypeError) as exc:
        # AvailabilityStamp raises TemporalIntegrityError (a ValueError) for a naive
        # timestamp, a visible-before-ingested ordering, or a missing source version.
        # Fail closed as SnapshotRecordError, never escape a raw error that aborts the
        # whole batch.
        raise SnapshotRecordError(f"availability is malformed: {exc}") from exc

    try:
        coverage = CoverageState(str(payload["coverage"]))
    except (KeyError, ValueError) as exc:
        raise SnapshotRecordError("coverage is missing or unknown") from exc
    try:
        restart_state = RestartState(str(payload["restart_state"]))
    except (KeyError, ValueError) as exc:
        raise SnapshotRecordError("restart_state is missing or unknown") from exc

    missingness_raw = payload.get("missingness") or {}
    _require(isinstance(missingness_raw, Mapping), "missingness must be a mapping")
    try:
        missingness = {
            str(key): Missingness(str(value)) for key, value in missingness_raw.items()
        }
    except ValueError as exc:
        raise SnapshotRecordError("missingness contains an unknown value") from exc

    values = payload.get("values")
    _require(isinstance(values, Mapping), "values must be a mapping")

    try:
        snapshot = FeatureSnapshot(
            instrument_version_id=str(payload["instrument_version_id"]),
            venue_instrument_id=str(payload["venue_instrument_id"]),
            feature_version=str(payload["feature_version"]),
            evaluation_cutoff=_parse_iso(
                payload.get("evaluation_cutoff"), field_name="evaluation_cutoff"
            ),
            evaluated_at_utc=_parse_iso(
                payload.get("evaluated_at_utc"), field_name="evaluated_at_utc"
            ),
            consumed_input_watermark=watermark,
            values=dict(values),
            availability=availability,
            missingness=missingness,
            coverage=coverage,
            restart_state=restart_state,
            evaluation_grid_seconds=int(payload["evaluation_grid_seconds"]),
            feature_schema_version=str(payload["feature_schema_version"]),
            feature_calc_version=str(payload["feature_calc_version"]),
            feature_dag_hash=str(payload.get("feature_dag_hash") or ""),
            notes=(str(payload["notes"]) if payload.get("notes") is not None else None),
            schema_version=int(payload["schema_version"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise SnapshotRecordError(f"snapshot payload is not contract-valid: {exc}") from exc

    declared_id = payload.get("snapshot_id")
    if declared_id is not None and str(declared_id) != snapshot.snapshot_id:
        raise SnapshotRecordError("snapshot_id does not match the payload content")
    declared_hash = payload.get("content_hash")
    if declared_hash is not None and str(declared_hash) != snapshot.content_hash():
        raise SnapshotRecordError("content_hash does not match the payload content")
    return snapshot


@dataclass(frozen=True)
class CommittedSnapshotBatch:
    """One bounded read: validated snapshots, the advanced cursor, and rejects."""

    snapshots: tuple[FeatureSnapshot, ...]
    cursor: tuple[int, int] | None
    rejected: int = 0
    reject_reasons: tuple[str, ...] = field(default_factory=tuple)


class CommittedSnapshotReader:
    """A bounded, read-only cursor over committed FeatureSnapshots.

    Cursor persistence across a process restart is the caller's responsibility: the
    reader surfaces each batch's cursor so a consumer can persist it and resume
    deterministically. The in-memory dedupe set is a within-process guard only.
    """

    def __init__(self, *, db_path: Path) -> None:
        self._reader = CanonicalWriter.for_reads(db_path)
        self._seen: set[str] = set()

    @property
    def is_read_only(self) -> bool:
        return self._reader.is_read_only

    def close(self) -> None:
        """Release the read-only connection."""
        self._reader.close()

    def __enter__(self) -> "CommittedSnapshotReader":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read_batch(
        self,
        *,
        after: tuple[int, int] | None = None,
        limit: int = DEFAULT_BATCH_LIMIT,
    ) -> CommittedSnapshotBatch:
        """Validate and return the next committed snapshots after ``after``."""
        bounded = min(max(1, int(limit)), MAX_BATCH_LIMIT)
        payloads, cursor = self._reader.read_feature_snapshots(after=after, limit=bounded)
        snapshots: list[FeatureSnapshot] = []
        rejected = 0
        reasons: list[str] = []
        for payload in payloads:
            try:
                snapshot = feature_snapshot_from_payload(payload)
            except SnapshotRecordError as exc:
                rejected += 1
                if len(reasons) < 8:
                    reasons.append(str(exc))
                continue
            # Deterministic dedupe across reads: a snapshot already surfaced is
            # never reclassified as new evidence.
            if snapshot.snapshot_id in self._seen:
                continue
            self._seen.add(snapshot.snapshot_id)
            snapshots.append(snapshot)
        # Canonical commit order (history_epoch, local_sequence) is the cursor's
        # guarantee and is preserved as returned. Each snapshot carries its own
        # evaluation cutoff; the reader never reorders relative to the cursor.
        return CommittedSnapshotBatch(
            snapshots=tuple(snapshots),
            cursor=cursor,
            rejected=rejected,
            reject_reasons=tuple(reasons),
        )


def read_all_committed_snapshots(db_path: Path) -> tuple[FeatureSnapshot, ...]:
    """Convenience: read every committed snapshot once, in canonical commit order.

    Intended for bounded tooling/tests, not for an unbounded production loop.
    """
    reader = CommittedSnapshotReader(db_path=db_path)
    collected: list[FeatureSnapshot] = []
    cursor: tuple[int, int] | None = None
    try:
        while True:
            batch = reader.read_batch(after=cursor)
            collected.extend(batch.snapshots)
            if batch.cursor == cursor or not batch.cursor:
                break
            cursor = batch.cursor
    finally:
        reader.close()
    return tuple(collected)


__all__ = [
    "CommittedSnapshotBatch",
    "CommittedSnapshotReader",
    "DEFAULT_BATCH_LIMIT",
    "MAX_BATCH_LIMIT",
    "SnapshotRecordError",
    "feature_snapshot_from_payload",
    "read_all_committed_snapshots",
]
