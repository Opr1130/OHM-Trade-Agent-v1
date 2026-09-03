"""Append-only JSONL store for O'Pip qualification evidence.

Three required streams live under ``/app/data/opip/qualification/``:

``funnel_events.jsonl``
    one row per candidate per scan, carrying the full ordered gate history.

``scan_summaries.jsonl``
    one row per completed scan, carrying the funnel counters, the terminal
    attribution, the AI stage evidence and the shadow-comparison telemetry.

``screening_evaluations.jsonl``
    one row per venue instrument evaluated by one scanner in one scan,
    including non-advancing outcomes.

Both follow the durability conventions already used by the P1 shadow outbox:
an exclusive writer lock, a truncated-tail repair before appending, fsync on
close and a dead-letter stream for rows that cannot be serialised. Required
rows removed from the bounded HOT files are first preserved in immutable,
checksummed, verified archive segments. Archive failure retains HOT evidence.

The store is dark by default. ``OPIP_FUNNEL_TELEMETRY_ENABLED`` must be set
explicitly, exactly like ``P1_SHADOW_OUTBOX_ENABLED``, so merging this build
cannot begin writing to a production volume that nobody sized for it. The
in-memory funnel and the printed scan summary do not depend on the flag.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.opip.storage.bounded_jsonl import (
    BoundedJsonlArchive,
    encode_row,
    parse_json_object_line,
)
from app.services.jsonl_retention import compact_jsonl_recent
from app.services.registry_io import registry_lock


logger = logging.getLogger(__name__)


QUALIFICATION_DIR = Path("/app/data/opip/qualification")
FUNNEL_EVENTS_FILE = QUALIFICATION_DIR / "funnel_events.jsonl"
SCAN_SUMMARIES_FILE = QUALIFICATION_DIR / "scan_summaries.jsonl"
SCREENING_EVALUATIONS_FILE = QUALIFICATION_DIR / "screening_evaluations.jsonl"
DEAD_LETTER_FILE = QUALIFICATION_DIR / "funnel_dead_letter.jsonl"

# Retention. A funnel row carries the candidate's full ordered gate history, so
# it is roughly 5.5 KB - the per-gate timestamp, threshold, measurement and
# metadata are the point of the record and are not trimmed to flatter a number.
#
# Worst-case funnel persistence now includes recovery states: the initial
# directional shortlist can register 8 candidates and up to 5 rejected SHORT
# terminal rows can coexist with 5 recovered LONG rows. The deterministic
# capacity projection below therefore uses 13 funnel rows per SEARCH scan.
#
# The scan-summary stream is ~1.9 KB per scan and is the long-horizon record:
# 10,000 rows is about five weeks of continuous 5-minute scanning, which is
# what the "why zero trades?" read model actually needs.
FUNNEL_EVENTS_MAX_BYTES = 64 * 1024 * 1024
FUNNEL_EVENTS_KEEP_LINES = 50_000
SCAN_SUMMARIES_MAX_BYTES = 8 * 1024 * 1024
SCAN_SUMMARIES_KEEP_LINES = 10_000
SCREENING_EVALUATIONS_MAX_BYTES = 64 * 1024 * 1024
SCREENING_EVALUATIONS_KEEP_LINES = 100_000
DEAD_LETTER_MAX_BYTES = 4 * 1024 * 1024
DEAD_LETTER_KEEP_LINES = 2_000

# Deterministic capacity model. Values are conservative p95 encoded-row sizes
# measured from the Stage 0 records; the 1.5 factor covers burstiness.
STAGE0_REQUIRED_RECOVERY_DAYS = 14
STAGE0_CAPACITY_SAFETY_FACTOR = 1.5
STAGE0_MINIMUM_FREE_RESERVE_FRACTION = 0.30
BROAD_SEARCH_MAX_INSTRUMENTS = 200
BROAD_SEARCH_SCANS_PER_DAY = 288
EARLY_WATCH_DEEP_MAX_INSTRUMENTS = 40
EARLY_WATCH_SCANS_PER_DAY = 144
# A production scan initially registers at most 8 directional candidates.
# Up to 5 rejected SHORT terminal states can remain in the forensic funnel
# while the newly opened slots are refilled with independently qualified LONG
# recovery candidates. Capacity must retain both states: 8 + 5 = 13 rows.
MAX_FUNNEL_CANDIDATES_PER_SCAN = 13
SCREENING_P95_ROW_BYTES = 750
FUNNEL_P95_ROW_BYTES = 9_700
SUMMARY_P95_ROW_BYTES = 1_900


@dataclass(frozen=True)
class Stage0RetentionCapacityHealth:
    """Measured storage headroom for the required Stage 0 recovery window."""

    observed_early_watch_universe: int | None
    active_bytes: int
    archive_bytes: int
    projected_14d_bytes: int | None
    current_free_bytes: int
    minimum_free_reserve_bytes: int
    recoverable_days_estimate: float | None
    recovery_window_proven: bool
    capacity_status: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _latest_observed_universe(path: Path) -> int | None:
    for row in reversed(read_jsonl(path)):
        metadata = row.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        try:
            value = int(metadata.get("universe_count"))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def retention_capacity_health(
    *,
    qualification_dir: Path | None = None,
    observed_early_watch_universe: int | None = None,
) -> Stage0RetentionCapacityHealth:
    """Measure whether local storage can preserve 14 days without pruning.

    The projection uses the observed Early Watch venue universe rather than an
    invented constant.  UNKNOWN is intentional until one instrument census is
    captured; no required evidence is automatically deleted in any state.
    """
    root = qualification_dir or QUALIFICATION_DIR
    funnel = funnel_events_archive(root / FUNNEL_EVENTS_FILE.name).stats()
    summaries = scan_summaries_archive(root / SCAN_SUMMARIES_FILE.name).stats()
    screening = screening_evaluations_archive(
        root / SCREENING_EVALUATIONS_FILE.name
    ).stats()
    stats = (funnel, summaries, screening)
    active_bytes = sum(item.hot_bytes for item in stats)
    archive_bytes = sum(
        item.warm_archive_bytes + item.cold_archive_bytes for item in stats
    )

    universe = observed_early_watch_universe
    if universe is None:
        universe = _latest_observed_universe(root / SCREENING_EVALUATIONS_FILE.name)
    if universe is not None:
        universe = max(0, int(universe))

    root.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(root)
    reserve = int(disk.total * STAGE0_MINIMUM_FREE_RESERVE_FRACTION)
    projected: int | None = None
    recoverable_days: float | None = None
    proven = False
    if universe is not None:
        screening_rows_per_day = (
            BROAD_SEARCH_MAX_INSTRUMENTS * BROAD_SEARCH_SCANS_PER_DAY
            + EARLY_WATCH_DEEP_MAX_INSTRUMENTS * EARLY_WATCH_SCANS_PER_DAY
            + universe * EARLY_WATCH_SCANS_PER_DAY
        )
        daily_bytes = STAGE0_CAPACITY_SAFETY_FACTOR * (
            screening_rows_per_day * SCREENING_P95_ROW_BYTES
            + MAX_FUNNEL_CANDIDATES_PER_SCAN
            * BROAD_SEARCH_SCANS_PER_DAY
            * FUNNEL_P95_ROW_BYTES
            + BROAD_SEARCH_SCANS_PER_DAY * SUMMARY_P95_ROW_BYTES
        )
        projected = int(daily_bytes * STAGE0_REQUIRED_RECOVERY_DAYS)
        evidence_capacity = active_bytes + archive_bytes + max(0, disk.free - reserve)
        recoverable_days = round(evidence_capacity / daily_bytes, 2)
        proven = recoverable_days >= STAGE0_REQUIRED_RECOVERY_DAYS

    if disk.free <= reserve:
        status = "CRITICAL"
    elif universe is None:
        status = "UNKNOWN"
    elif proven:
        status = "HEALTHY"
    else:
        status = "DEGRADED"
    return Stage0RetentionCapacityHealth(
        observed_early_watch_universe=universe,
        active_bytes=active_bytes,
        archive_bytes=archive_bytes,
        projected_14d_bytes=projected,
        current_free_bytes=disk.free,
        minimum_free_reserve_bytes=reserve,
        recoverable_days_estimate=recoverable_days,
        recovery_window_proven=proven,
        capacity_status=status,
    )


def _visible_at(payload: Any) -> datetime | None:
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("observed_at") or payload.get("decision_at_utc")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _archive_for(
    path: Path,
    *,
    max_bytes: int,
    keep_lines: int,
) -> BoundedJsonlArchive:
    return BoundedJsonlArchive(
        data_file=path,
        archive_dir=path.parent / f"{path.stem}_archive",
        max_bytes=max_bytes,
        keep_lines=keep_lines,
        archive_prefix=path.stem,
        parse_line=parse_json_object_line,
        visible_at=_visible_at,
    )


def funnel_events_archive(path: Path | None = None) -> BoundedJsonlArchive:
    return _archive_for(
        path or FUNNEL_EVENTS_FILE,
        max_bytes=FUNNEL_EVENTS_MAX_BYTES,
        keep_lines=FUNNEL_EVENTS_KEEP_LINES,
    )


def scan_summaries_archive(path: Path | None = None) -> BoundedJsonlArchive:
    return _archive_for(
        path or SCAN_SUMMARIES_FILE,
        max_bytes=SCAN_SUMMARIES_MAX_BYTES,
        keep_lines=SCAN_SUMMARIES_KEEP_LINES,
    )


def screening_evaluations_archive(path: Path | None = None) -> BoundedJsonlArchive:
    return _archive_for(
        path or SCREENING_EVALUATIONS_FILE,
        max_bytes=SCREENING_EVALUATIONS_MAX_BYTES,
        keep_lines=SCREENING_EVALUATIONS_KEEP_LINES,
    )


def opip_funnel_telemetry_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether O'Pip funnel persistence is switched on.

    Measurement-only: this flag gates writing evidence to disk. It cannot
    change a ranking, an alert, a paper admission, or any trading authority.
    """
    env = environ if environ is not None else os.environ
    return str(env.get("OPIP_FUNNEL_TELEMETRY_ENABLED", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _repair_truncated_tail(target: Path) -> bool:
    """Terminate a crashed partial final line before appending after it.

    Without this, a process that died mid-write leaves a fragment that the next
    append concatenates with, destroying a good row along with the bad one.
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


def _serialize(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, allow_nan=False)


def _append_dead_letter(path: Path, reason: str, payload: Any) -> None:
    """Record a row that could not be serialised, never raising."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.parent / f".{path.name}.lock"
        with registry_lock(lock):
            _repair_truncated_tail(path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "recorded_at": datetime.now(timezone.utc).isoformat(),
                            "reason": reason,
                            "payload_repr": repr(payload)[:2000],
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                handle.flush()
            compact_jsonl_recent(
                path,
                max_bytes=DEAD_LETTER_MAX_BYTES,
                keep_lines=DEAD_LETTER_KEEP_LINES,
            )
    except (OSError, TimeoutError, ValueError):
        logger.warning("O'Pip funnel dead-letter write failed open")


def _append_rows(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    max_bytes: int,
    keep_lines: int,
    dead_letter_path: Path,
) -> int:
    """Append rows under one lock, isolating per-row serialisation failures.

    Returns the number of rows actually written, not the number attempted.
    """
    pending = list(rows)
    if not pending:
        return 0

    written = 0
    failures: list[tuple[str, Any]] = []
    encoded_rows: list[bytes] = []
    for row in pending:
        try:
            encoded_rows.append(encode_row(dict(row)))
        except (TypeError, ValueError) as exc:
            failures.append((f"{type(exc).__name__}: {exc}", row))

    if not encoded_rows:
        for reason, row in failures:
            _append_dead_letter(dead_letter_path, reason, row)
        return 0

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.parent / f".{path.name}.lock"
        archive = _archive_for(path, max_bytes=max_bytes, keep_lines=keep_lines)
        with registry_lock(lock):
            # One-time legacy backfill; steady state is an O(1) manifest-stat
            # check. Learning window selection never scans the lifetime archive.
            try:
                archive.ensure_window_index_locked()
            except Exception as exc:
                logger.error(
                    "O'Pip archive window-index maintenance failed open for %s: %s",
                    path,
                    type(exc).__name__,
                )
            archive.repair_tail()
            written = archive.append_encoded_many_locked(encoded_rows)
            try:
                archive.compact_locked()
            except Exception as exc:
                # Archive/retention failure must preserve HOT and must not
                # reach scanner, risk, alert, paper, or execution behavior.
                logger.error(
                    "O'Pip archive-before-delete failed open for %s; "
                    "retaining unarchived HOT evidence: %s",
                    path,
                    type(exc).__name__,
                )
    except (OSError, TimeoutError) as exc:
        logger.error(
            "O'Pip qualification append failed open; possible disk pressure "
            "path=%s error=%s",
            path,
            type(exc).__name__,
        )
        return 0

    for reason, row in failures:
        _append_dead_letter(dead_letter_path, reason, row)
    return written


def append_funnel_events(
    events: Iterable[Mapping[str, Any]],
    *,
    path: Path | None = None,
    dead_letter_path: Path | None = None,
    enabled: bool | None = None,
) -> int:
    """Persist funnel rows. Fail-soft; returns the number written."""
    active = opip_funnel_telemetry_enabled() if enabled is None else bool(enabled)
    if not active:
        return 0
    return _append_rows(
        path or FUNNEL_EVENTS_FILE,
        events,
        max_bytes=FUNNEL_EVENTS_MAX_BYTES,
        keep_lines=FUNNEL_EVENTS_KEEP_LINES,
        dead_letter_path=dead_letter_path or DEAD_LETTER_FILE,
    )


def append_scan_summary(
    summary: Mapping[str, Any],
    *,
    path: Path | None = None,
    dead_letter_path: Path | None = None,
    enabled: bool | None = None,
) -> int:
    """Persist one scan summary row. Fail-soft; returns 1 when written."""
    active = opip_funnel_telemetry_enabled() if enabled is None else bool(enabled)
    if not active:
        return 0
    return _append_rows(
        path or SCAN_SUMMARIES_FILE,
        [summary],
        max_bytes=SCAN_SUMMARIES_MAX_BYTES,
        keep_lines=SCAN_SUMMARIES_KEEP_LINES,
        dead_letter_path=dead_letter_path or DEAD_LETTER_FILE,
    )


def append_screening_evaluations(
    evaluations: Iterable[Mapping[str, Any]],
    *,
    path: Path | None = None,
    dead_letter_path: Path | None = None,
    enabled: bool | None = None,
) -> int:
    """Persist screening rows. Fail-soft; returns the number written."""
    active = opip_funnel_telemetry_enabled() if enabled is None else bool(enabled)
    if not active:
        return 0
    return _append_rows(
        path or SCREENING_EVALUATIONS_FILE,
        evaluations,
        max_bytes=SCREENING_EVALUATIONS_MAX_BYTES,
        keep_lines=SCREENING_EVALUATIONS_KEEP_LINES,
        dead_letter_path=dead_letter_path or DEAD_LETTER_FILE,
    )


def read_jsonl(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Return parsed rows, skipping malformed lines rather than raising.

    A partially written or corrupted line must not make the whole read model
    unavailable: the operator asking "why zero trades?" needs the answer the
    healthy rows can still give.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
    except OSError as exc:
        logger.warning("O'Pip qualification read failed open: %s", type(exc).__name__)
        return []
    if limit is not None and limit >= 0:
        return rows[-limit:]
    return rows


def read_latest_scan_summary(
    *,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Return the most recent persisted scan summary, or None."""
    rows = read_jsonl(path or SCAN_SUMMARIES_FILE)
    return rows[-1] if rows else None


def read_funnel_events_for_scan(
    scan_id: str,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return every funnel row belonging to one scan."""
    target = str(scan_id or "")
    if not target:
        return []
    return [
        row
        for row in read_jsonl(path or FUNNEL_EVENTS_FILE)
        if str(row.get("scan_id") or "") == target
    ]


def read_screening_evaluations_for_scan(
    scan_id: str,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return screening evaluations from the active HOT file for one scan."""
    target = str(scan_id or "")
    if not target:
        return []
    return [
        row
        for row in read_jsonl(path or SCREENING_EVALUATIONS_FILE)
        if str(row.get("scan_id") or "") == target
    ]
