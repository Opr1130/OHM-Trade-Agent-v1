"""Bounded JSONL persistence for Discovery V2-01 offline labels.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

These streams live on the learning/offline plane. They are append-only,
joinable by observation_id, and never imported by the production selector.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.opip.decision.store import (
    _append_dead_letter,
    _append_rows,
    read_jsonl,
)
from app.opip.storage.bounded_jsonl import BoundedJsonlArchive, encode_row, parse_json_object_line


logger = logging.getLogger(__name__)

DISCOVERY_DIR = Path("/app/data/opip/discovery")
FORWARD_OUTCOMES_FILE = DISCOVERY_DIR / "forward_outcomes.jsonl"
ATTRIBUTIONS_FILE = DISCOVERY_DIR / "attributions.jsonl"
DEAD_LETTER_FILE = DISCOVERY_DIR / "discovery_dead_letter.jsonl"

FORWARD_OUTCOMES_MAX_BYTES = 64 * 1024 * 1024
FORWARD_OUTCOMES_KEEP_LINES = 100_000
ATTRIBUTIONS_MAX_BYTES = 32 * 1024 * 1024
ATTRIBUTIONS_KEEP_LINES = 100_000


def append_discovery_forward_outcomes(
    rows: Iterable[Mapping[str, Any]],
    *,
    path: Path | None = None,
) -> int:
    return _append_rows(
        path or FORWARD_OUTCOMES_FILE,
        rows,
        max_bytes=FORWARD_OUTCOMES_MAX_BYTES,
        keep_lines=FORWARD_OUTCOMES_KEEP_LINES,
        dead_letter_path=DEAD_LETTER_FILE,
    )


def dead_letter_discovery_row(
    row: Mapping[str, Any],
    *,
    path: Path,
    reason: str,
) -> None:
    """Best-effort dead-letter for one invalid offline discovery record."""
    _append_dead_letter(
        path.parent / DEAD_LETTER_FILE.name,
        reason,
        dict(row),
    )


def append_discovery_forward_outcomes_locked(
    rows: Iterable[Mapping[str, Any]],
    *,
    path: Path,
    rejected_observation_ids: set[str] | None = None,
) -> int:
    """Append valid rows while the caller already holds the outcomes JSONL lock.

    Per-row serialization defects are dead-lettered so one malformed outcome
    cannot block unrelated valid observations in the same bounded batch.
    """
    pending = [dict(row) for row in rows]
    if not pending:
        return 0
    encoded: list[bytes] = []
    for row in pending:
        try:
            encoded.append(encode_row(row))
        except (TypeError, ValueError) as exc:
            observation_id = str(row.get("observation_id") or "").strip()
            if rejected_observation_ids is not None and observation_id:
                rejected_observation_ids.add(observation_id)
            dead_letter_discovery_row(
                row,
                path=path,
                reason=f"{type(exc).__name__}: {exc}",
            )
            continue
    if not encoded:
        return 0
    archive = discovery_outcomes_archive(path)
    archive.repair_tail()
    written = archive.append_encoded_many_locked(encoded)
    try:
        archive.compact_locked()
    except Exception as exc:
        logger.error(
            "O'Pip discovery outcome archive-before-delete failed open for %s; "
            "retaining unarchived HOT evidence: %s",
            path,
            type(exc).__name__,
        )
    return written


def append_discovery_attributions(
    rows: Iterable[Mapping[str, Any]],
    *,
    path: Path | None = None,
) -> int:
    return _append_rows(
        path or ATTRIBUTIONS_FILE,
        rows,
        max_bytes=ATTRIBUTIONS_MAX_BYTES,
        keep_lines=ATTRIBUTIONS_KEEP_LINES,
        dead_letter_path=DEAD_LETTER_FILE,
    )


def read_discovery_forward_outcomes(
    *,
    path: Path | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return read_jsonl(path or FORWARD_OUTCOMES_FILE, limit=limit)


def _attribution_record_key(row: Mapping[str, Any]) -> str | None:
    explicit = str(row.get("attribution_record_id") or "").strip()
    if explicit:
        return explicit
    observation_id = str(row.get("observation_id") or "").strip()
    category = str(row.get("stage0_attribution") or "").strip()
    taxonomy = str(row.get("taxonomy_version") or "").strip()
    if observation_id and category:
        return f"LEGACY:{taxonomy}|{observation_id}|{category}"
    return None


def read_discovery_attributions(
    *,
    path: Path | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read logical attributions, deduplicated by stable idempotency key."""
    rows = read_jsonl(path or ATTRIBUTIONS_FILE, limit=limit)
    result: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for row in rows:
        key = _attribution_record_key(row)
        if key is None:
            result.append(row)
            continue
        prior = positions.get(key)
        if prior is None:
            positions[key] = len(result)
            result.append(row)
        else:
            result[prior] = row
    return result


def persist_discovery_attributions(
    rows: Iterable[Mapping[str, Any]],
    *,
    path: Path | None = None,
) -> tuple[set[str], int]:
    """Idempotently persist attribution rows and report success per record id.

    The post-write readback handles partial I/O: rows that reached disk are
    recognized individually, while only missing record ids remain retryable.
    """
    target = path or ATTRIBUTIONS_FILE
    pending: list[dict[str, Any]] = []
    requested_ids: set[str] = set()
    for raw in rows:
        row = dict(raw)
        record_id = str(row.get("attribution_record_id") or "").strip()
        if not record_id or record_id in requested_ids:
            continue
        requested_ids.add(record_id)
        pending.append(row)
    if not pending:
        return set(), 0

    existing_rows = read_jsonl(target)
    existing_ids = {
        key
        for item in existing_rows
        if (key := _attribution_record_key(item)) is not None
    }
    to_write = [
        row
        for row in pending
        if str(row.get("attribution_record_id") or "").strip() not in existing_ids
    ]
    if to_write:
        append_discovery_attributions(to_write, path=target)

    # Re-read after the append. If the underlying batch suffered a partial I/O
    # failure, complete lines that actually reached disk are still recognized.
    after_rows = read_jsonl(target)
    persisted_ids = {
        key
        for item in after_rows
        if (key := _attribution_record_key(item)) is not None
    }
    successful = requested_ids & persisted_ids
    newly_written = len(successful - existing_ids)
    return successful, newly_written


def discovery_outcomes_archive(path: Path | None = None) -> BoundedJsonlArchive:
    target = path or FORWARD_OUTCOMES_FILE
    return BoundedJsonlArchive(
        data_file=target,
        archive_dir=target.parent / f"{target.stem}_archive",
        max_bytes=FORWARD_OUTCOMES_MAX_BYTES,
        keep_lines=FORWARD_OUTCOMES_KEEP_LINES,
        archive_prefix=target.stem,
        parse_line=parse_json_object_line,
    )
