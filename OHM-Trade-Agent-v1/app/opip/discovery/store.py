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


def append_discovery_forward_outcomes_locked(
    rows: Iterable[Mapping[str, Any]],
    *,
    path: Path,
) -> int:
    """Append while the caller already holds the outcomes JSONL lock.

    Phase 3C writes under the same lock that owns maturation state. Nested
    ``registry_lock`` on the same file would timeout.
    """
    pending = [dict(row) for row in rows]
    if not pending:
        return 0
    encoded: list[bytes] = []
    for row in pending:
        encoded.append(encode_row(row))
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


def read_discovery_attributions(
    *,
    path: Path | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return read_jsonl(path or ATTRIBUTIONS_FILE, limit=limit)


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
