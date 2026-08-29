"""Durable bounded storage for O'Pip Sequence 3 assessments and T0 evidence.

Append-only history is authoritative. A small atomic index avoids reparsing the
entire HOT/WARM/COLD history on each fresh unified-cycle process. If the index
is stale after a crash, HOT is scanned to recover recently appended ids; an
archive-wide rebuild is needed only when no trustworthy index exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
from typing import Any, Iterator

from app.opip.events.contract import require_utc
from app.opip.risk.attribution import T0Attribution
from app.opip.risk.contract import ExposureFamily, RiskAssessment
from app.opip.storage.bounded_jsonl import BoundedJsonlArchive, encode_row, parse_json_object_line
from app.services.registry_io import load_json, registry_lock, save_json_atomic

logger = logging.getLogger(__name__)
RISK_DIR = Path("/app/data/opip/risk")
ASSESSMENTS_MAX_BYTES = 8 * 1024 * 1024
ASSESSMENTS_KEEP_LINES = 20_000
T0_MAX_BYTES = 8 * 1024 * 1024
T0_KEEP_LINES = 20_000
MAX_CACHED_IDS = 50_000


def family_root(family: ExposureFamily, *, root: Path = RISK_DIR) -> Path:
    return root / family.value.lower()


def _parse_assessment_line(line: bytes) -> RiskAssessment:
    return RiskAssessment.from_dict(parse_json_object_line(line))


def _parse_t0_line(line: bytes) -> T0Attribution:
    return T0Attribution.from_dict(parse_json_object_line(line))


def _signature_payload(signature: tuple[int, int] | None) -> list[int] | None:
    return list(signature) if signature is not None else None


def _signature_from_payload(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("invalid HOT signature")
    return int(value[0]), int(value[1])


@dataclass(frozen=True)
class AssessmentAppendResult:
    stored: bool
    assessment: RiskAssessment
    reason: str


class _DurableIdIndex:
    """Small ordered recent-id index backed by an atomic JSON state file."""
    def __init__(self, *, path: Path, archive: BoundedJsonlArchive, id_of) -> None:
        self.path = path
        self.archive = archive
        self.id_of = id_of
        self.ordered_ids: list[str] = []
        self.known: set[str] = set()
        self.loaded = False
        self.signature: tuple[int, int] | None = None

    def _remember(self, value: str) -> None:
        if value in self.known:
            return
        self.known.add(value)
        self.ordered_ids.append(value)
        if len(self.ordered_ids) > MAX_CACHED_IDS:
            dropped = self.ordered_ids[:-MAX_CACHED_IDS]
            self.ordered_ids = self.ordered_ids[-MAX_CACHED_IDS:]
            self.known.difference_update(dropped)

    def _scan_all(self) -> None:
        self.ordered_ids = []
        self.known = set()
        for row in self.archive.iter_archive_rows():
            self._remember(str(self.id_of(row)))
        for row in self.archive.iter_hot_rows():
            self._remember(str(self.id_of(row)))

    def _scan_hot(self) -> None:
        for row in self.archive.iter_hot_rows():
            self._remember(str(self.id_of(row)))

    def load_locked(self) -> None:
        current = self.archive.hot_signature()
        state: dict[str, Any] = {}
        try:
            state = load_json(self.path)
        except Exception:
            state = {}
        valid = False
        try:
            ids = state.get("recent_ids") if isinstance(state, dict) else None
            saved_sig = _signature_from_payload(state.get("hot_signature"))
            valid = (
                isinstance(ids, list)
                and int(state.get("schema_version") or 0) == 1
                and str(state.get("family") or "")
                and str(state.get("kind") or "")
            )
            if valid:
                self.ordered_ids = []
                self.known = set()
                for item in ids[-MAX_CACHED_IDS:]:
                    self._remember(str(item))
                self.signature = saved_sig
                # A mismatch means a crash/external append may have happened.
                # Newest rows remain in HOT after compaction, so scanning HOT
                # repairs the recent-id cache without replaying all archives.
                if saved_sig != current:
                    self._scan_hot()
            else:
                self._scan_all()
        except Exception:
            logger.exception("O'Pip risk id index invalid; rebuilding from durable history")
            self._scan_all()
        self.signature = current
        self.loaded = True

    def contains(self, value: str) -> bool:
        return value in self.known

    def remember(self, value: str) -> None:
        self._remember(value)

    def save_locked(self, *, family: ExposureFamily, kind: str) -> None:
        self.signature = self.archive.hot_signature()
        save_json_atomic(
            self.path,
            {
                "schema_version": 1,
                "family": family.value,
                "kind": kind,
                "hot_signature": _signature_payload(self.signature),
                "recent_ids": list(self.ordered_ids),
            },
        )


class RiskAssessmentStore:
    def __init__(self, *, family: ExposureFamily, root: Path = RISK_DIR,
                 max_bytes: int = ASSESSMENTS_MAX_BYTES, keep_lines: int = ASSESSMENTS_KEEP_LINES) -> None:
        self.family = family
        self.root = family_root(family, root=root)
        self.data_file = self.root / "assessments.jsonl"
        self.lock_file = self.root / ".assessments.lock"
        self.index_file = self.root / "assessment_index.json"
        self._archive = BoundedJsonlArchive(
            data_file=self.data_file, archive_dir=self.root / "archive",
            max_bytes=max_bytes, keep_lines=keep_lines, archive_prefix="assessments",
            parse_line=_parse_assessment_line, visible_at=lambda row: row.decision_at_utc,
        )
        self._ids = _DurableIdIndex(path=self.index_file, archive=self._archive,
                                    id_of=lambda row: row.assessment_id)

    def _ensure_index_locked(self) -> None:
        if not self._ids.loaded or self._ids.signature != self._archive.hot_signature():
            self._ids.load_locked()

    def append(self, assessment: RiskAssessment) -> AssessmentAppendResult:
        if assessment.exposure_family is not self.family:
            raise ValueError("assessment exposure_family does not match this store's family")
        self.root.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.lock_file):
            self._archive.repair_tail()
            self._ensure_index_locked()
            if self._ids.contains(assessment.assessment_id):
                return AssessmentAppendResult(False, assessment, "DUPLICATE_UNCHANGED_INPUTS")
            self._archive.append_encoded_locked(encode_row(assessment.to_dict()))
            self._ids.remember(assessment.assessment_id)
            try:
                self._archive.compact_locked()
            except Exception:
                logger.exception("O'Pip risk assessment archive/compaction failed; HOT evidence preserved")
            # If this write fails, next process sees a signature mismatch and
            # repairs from HOT. History remains authoritative.
            try:
                self._ids.save_locked(family=self.family, kind="ASSESSMENT")
            except Exception:
                logger.exception("O'Pip risk assessment id index update failed; durable log preserved")
            return AssessmentAppendResult(True, assessment, "STORED")

    def iter_assessments(self, *, include_archive: bool = True) -> Iterator[RiskAssessment]:
        seen: set[str] = set()
        if include_archive:
            for row in self._archive.iter_archive_rows():
                if row.assessment_id not in seen:
                    seen.add(row.assessment_id); yield row
        for row in self._archive.iter_hot_rows():
            if row.assessment_id not in seen:
                seen.add(row.assessment_id); yield row

    def latest_by_exposure(self) -> dict[str, RiskAssessment]:
        """Audit helper only. BUILD 3.3 must use exposure-level aggregation."""
        latest: dict[str, RiskAssessment] = {}
        for assessment in self.iter_assessments(include_archive=True):
            latest[assessment.exposure_id] = assessment
        return latest

    def replay(self, *, through: datetime) -> tuple[RiskAssessment, ...]:
        cutoff = require_utc(through, field_name="through")
        return tuple(a for a in self.iter_assessments(include_archive=True) if a.decision_at_utc <= cutoff)

    def stats(self) -> dict[str, Any]:
        stats = self._archive.stats()
        return {"family": self.family.value, "hot_bytes": stats.hot_bytes, "hot_lines": stats.hot_lines,
                "warm_archive_segments": stats.warm_archive_segments,
                "cold_archive_segments": stats.cold_archive_segments,
                "manifest_segments": stats.manifest_segments}


class T0AttributionStore:
    def __init__(self, *, family: ExposureFamily, root: Path = RISK_DIR,
                 max_bytes: int = T0_MAX_BYTES, keep_lines: int = T0_KEEP_LINES) -> None:
        self.family = family
        self.root = family_root(family, root=root)
        self.data_file = self.root / "t0_attribution.jsonl"
        self.lock_file = self.root / ".t0_attribution.lock"
        self.index_file = self.root / "t0_index.json"
        self._archive = BoundedJsonlArchive(
            data_file=self.data_file, archive_dir=self.root / "archive_t0",
            max_bytes=max_bytes, keep_lines=keep_lines, archive_prefix="t0",
            parse_line=_parse_t0_line, visible_at=lambda row: row.decision_at_utc,
        )
        self._ids = _DurableIdIndex(path=self.index_file, archive=self._archive,
                                    id_of=lambda row: row.attribution_id)

    def _ensure_index_locked(self) -> None:
        if not self._ids.loaded or self._ids.signature != self._archive.hot_signature():
            self._ids.load_locked()

    def append(self, record: T0Attribution) -> bool:
        if record.exposure_family is not self.family:
            raise ValueError("T0 exposure_family does not match this store's family")
        self.root.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.lock_file):
            self._archive.repair_tail(); self._ensure_index_locked()
            if self._ids.contains(record.attribution_id):
                return False
            self._archive.append_encoded_locked(encode_row(record.to_dict()))
            self._ids.remember(record.attribution_id)
            try:
                self._archive.compact_locked()
            except Exception:
                logger.exception("O'Pip T0 archive/compaction failed; HOT evidence preserved")
            try:
                self._ids.save_locked(family=self.family, kind="T0")
            except Exception:
                logger.exception("O'Pip T0 id index update failed; durable log preserved")
            return True

    def iter_records(self, *, include_archive: bool = True) -> Iterator[T0Attribution]:
        seen: set[str] = set()
        if include_archive:
            for row in self._archive.iter_archive_rows():
                if row.attribution_id not in seen:
                    seen.add(row.attribution_id); yield row
        for row in self._archive.iter_hot_rows():
            if row.attribution_id not in seen:
                seen.add(row.attribution_id); yield row

    def records_at_or_before(self, *, through: datetime) -> tuple[T0Attribution, ...]:
        cutoff = require_utc(through, field_name="through")
        return tuple(r for r in self.iter_records() if r.decision_at_utc <= cutoff)
