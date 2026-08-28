"""Append-only JSONL store for O'Pip qualification evidence.

Two streams live under ``/app/data/opip/qualification/``:

``funnel_events.jsonl``
    one row per candidate per scan, carrying the full ordered gate history.

``scan_summaries.jsonl``
    one row per completed scan, carrying the funnel counters, the terminal
    attribution, the AI stage evidence and the shadow-comparison telemetry.

Both follow the durability conventions already used by the P1 shadow outbox:
an exclusive writer lock, a truncated-tail repair before appending, fsync on
close, a dead-letter stream for rows that cannot be serialised, and bounded
retention so an unattended deployment cannot fill its disk.

The store is dark by default. ``OPIP_FUNNEL_TELEMETRY_ENABLED`` must be set
explicitly, exactly like ``P1_SHADOW_OUTBOX_ENABLED``, so merging this build
cannot begin writing to a production volume that nobody sized for it. The
in-memory funnel and the printed scan summary do not depend on the flag.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.services.jsonl_retention import compact_jsonl_recent
from app.services.registry_io import registry_lock


logger = logging.getLogger(__name__)


QUALIFICATION_DIR = Path("/app/data/opip/qualification")
FUNNEL_EVENTS_FILE = QUALIFICATION_DIR / "funnel_events.jsonl"
SCAN_SUMMARIES_FILE = QUALIFICATION_DIR / "scan_summaries.jsonl"
DEAD_LETTER_FILE = QUALIFICATION_DIR / "funnel_dead_letter.jsonl"

# Retention. A funnel row carries the candidate's full ordered gate history, so
# it is roughly 5.5 KB - the per-gate timestamp, threshold, measurement and
# metadata are the point of the record and are not trimmed to flatter a number.
#
# Worst case is the directional cap of 8 candidates on the 5-minute SEARCH
# cadence: ~2,300 rows and ~12.5 MiB a day. The 64 MiB / 50,000-line caps
# therefore both land near a five-day full-fidelity forensic window at that
# worst case, and far longer at realistic shortlist sizes.
#
# The scan-summary stream is ~1.9 KB per scan and is the long-horizon record:
# 10,000 rows is about five weeks of continuous 5-minute scanning, which is
# what the "why zero trades?" read model actually needs.
FUNNEL_EVENTS_MAX_BYTES = 64 * 1024 * 1024
FUNNEL_EVENTS_KEEP_LINES = 50_000
SCAN_SUMMARIES_MAX_BYTES = 8 * 1024 * 1024
SCAN_SUMMARIES_KEEP_LINES = 10_000
DEAD_LETTER_MAX_BYTES = 4 * 1024 * 1024
DEAD_LETTER_KEEP_LINES = 2_000


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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.parent / f".{path.name}.lock"
        with registry_lock(lock):
            _repair_truncated_tail(path)
            with path.open("a", encoding="utf-8") as handle:
                for row in pending:
                    try:
                        line = _serialize(row)
                    except (TypeError, ValueError) as exc:
                        failures.append((f"{type(exc).__name__}: {exc}", row))
                        continue
                    handle.write(line + "\n")
                    written += 1
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            compact_jsonl_recent(path, max_bytes=max_bytes, keep_lines=keep_lines)
    except (OSError, TimeoutError) as exc:
        logger.warning(
            "O'Pip qualification append failed open: %s", type(exc).__name__
        )
        return written

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
