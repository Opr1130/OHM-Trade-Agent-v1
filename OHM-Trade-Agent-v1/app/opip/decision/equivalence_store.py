"""Bounded durable equivalence ledger for O'Pip BUILD 5.2A."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable, Mapping

from app.opip.decision.equivalence import EquivalenceObservation
from app.opip.storage.bounded_jsonl import (
    BoundedJsonlArchive,
    encode_row,
    parse_json_object_line,
)
from app.services.registry_io import registry_lock


EQUIVALENCE_DIR = Path("/app/data/opip/decision/equivalence")
EQUIVALENCE_LEDGER_FILE = EQUIVALENCE_DIR / "equivalence_ledger.jsonl"
EQUIVALENCE_ARCHIVE_DIR = EQUIVALENCE_DIR / "archive"
EQUIVALENCE_LEDGER_MAX_BYTES = 64 * 1024 * 1024
EQUIVALENCE_LEDGER_KEEP_LINES = 50_000


@dataclass(frozen=True)
class LedgerReadResult:
    observations: tuple[EquivalenceObservation, ...]
    complete: bool
    warnings: tuple[str, ...] = ()


def opip_equivalence_ledger_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Persistence-only feature flag. Dark by default."""
    env = environ if environ is not None else os.environ
    return str(
        env.get("OPIP_EQUIVALENCE_LEDGER_ENABLED", "false")
    ).strip().lower() in {"1", "true", "yes", "on"}


def _parse_line(line: bytes) -> EquivalenceObservation:
    return EquivalenceObservation.from_dict(parse_json_object_line(line))


def _archive(path: Path) -> BoundedJsonlArchive:
    archive_dir = (
        EQUIVALENCE_ARCHIVE_DIR
        if path == EQUIVALENCE_LEDGER_FILE
        else path.parent / f"{path.stem}_archive"
    )
    return BoundedJsonlArchive(
        data_file=path,
        archive_dir=archive_dir,
        max_bytes=EQUIVALENCE_LEDGER_MAX_BYTES,
        keep_lines=EQUIVALENCE_LEDGER_KEEP_LINES,
        archive_prefix="equivalence",
        parse_line=_parse_line,
        visible_at=lambda row: row.observed_at_utc,
    )


def append_equivalence_observations(
    observations: Iterable[EquivalenceObservation],
    *,
    path: Path | None = None,
    enabled: bool | None = None,
) -> int:
    """Append immutable observations. Storage failure is not a promotion pass."""
    active = opip_equivalence_ledger_enabled() if enabled is None else bool(enabled)
    if not active:
        return 0
    rows = tuple(observations)
    if not rows:
        return 0

    target = path or EQUIVALENCE_LEDGER_FILE
    archive = _archive(target)
    lock = target.parent / ".equivalence_ledger.lock"
    with registry_lock(lock):
        archive.repair_tail()
        for row in rows:
            archive.append_encoded_locked(encode_row(row.as_dict()))
        archive.compact_locked()
    return len(rows)


def read_equivalence_ledger(
    *,
    path: Path | None = None,
) -> LedgerReadResult:
    """Read HOT + verified archives; surface any coverage uncertainty."""
    target = path or EQUIVALENCE_LEDGER_FILE
    archive = _archive(target)
    observations: list[EquivalenceObservation] = []
    warnings: list[str] = []
    complete = True

    archive_paths = (
        tuple(sorted(archive.archive_dir.rglob(archive.archive_glob)))
        if archive.archive_dir.exists()
        else ()
    )
    if archive_paths:
        try:
            observations.extend(
                archive.iter_archive_rows_from_paths(archive_paths, strict=True)
            )
        except Exception as exc:
            complete = False
            warnings.append(f"ARCHIVE_READ_FAILED:{type(exc).__name__}")

    try:
        observations.extend(archive.iter_hot_rows(skip_malformed=False))
    except Exception as exc:
        complete = False
        warnings.append(f"HOT_READ_FAILED:{type(exc).__name__}")

    by_id: dict[str, EquivalenceObservation] = {}
    for row in observations:
        prior = by_id.get(row.observation_id)
        if prior is not None and prior.as_dict() != row.as_dict():
            complete = False
            warnings.append("OBSERVATION_ID_COLLISION")
            continue
        by_id[row.observation_id] = row

    ordered = tuple(
        sorted(
            by_id.values(),
            key=lambda item: (
                item.observed_at_utc,
                item.scan_id,
                item.candidate_id,
                item.observation_id,
            ),
        )
    )
    return LedgerReadResult(
        observations=ordered,
        complete=complete,
        warnings=tuple(dict.fromkeys(warnings)),
    )
