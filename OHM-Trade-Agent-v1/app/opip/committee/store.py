"""Durable committee evidence storage.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Committee evidence is written through the repository's shared bounded JSONL
archive, so it inherits the same durability semantics as every other O'Pip
evidence stream: fsynced HOT append, verified gzip archives before compaction,
and a quarantined truncated tail rather than a silently dropped row.

Nothing is ever overwritten. A committed observation is immutable, and a
logical re-execution is acknowledged as a duplicate instead of becoming a
second opinion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from app.opip.committee.contracts import (
    CommitteeCaseOutcome,
    ProviderCallOutcome,
)
from app.opip.committee.ledger import COMMITTED_STATUSES
from app.opip.committee.runtime import CommitteeReplayDivergenceError
from app.opip.committee.serialization import (
    CommitteeSerializationError,
    attribution_report_from_dict,
    attribution_report_to_dict,
    call_outcome_from_dict,
    call_outcome_to_dict,
    case_outcome_from_dict,
    case_outcome_to_dict,
    evaluation_report_from_dict,
    evaluation_report_to_dict,
    outcome_observation_to_dict,
    prospective_evaluation_to_dict,
    prospective_record_from_dict,
    sealed_prediction_to_dict,
)
from app.opip.storage.bounded_jsonl import (
    BoundedJsonlArchive,
    encode_row,
    parse_json_object_line,
)
from app.services.registry_io import load_json, registry_lock, save_json_atomic

logger = logging.getLogger(__name__)

COMMITTEE_DIR = Path("/app/data/opip/committee")
CALL_OUTCOMES_MAX_BYTES = 8 * 1024 * 1024
CALL_OUTCOMES_KEEP_LINES = 50_000
CASE_OUTCOMES_MAX_BYTES = 8 * 1024 * 1024
CASE_OUTCOMES_KEEP_LINES = 20_000
EVALUATIONS_MAX_BYTES = 8 * 1024 * 1024
EVALUATIONS_KEEP_LINES = 5_000
PROSPECTIVE_MAX_BYTES = 8 * 1024 * 1024
PROSPECTIVE_KEEP_LINES = 20_000
ATTRIBUTIONS_MAX_BYTES = 8 * 1024 * 1024
ATTRIBUTIONS_KEEP_LINES = 5_000

#: Reasons an append is acknowledged without writing a new row.
REASON_STORED = "STORED"
REASON_DUPLICATE = "DUPLICATE_UNCHANGED_INPUTS"
REASON_DIVERGENCE = "REPLAY_DIVERGENCE_PRESERVED_ORIGINAL"


@dataclass(frozen=True)
class StoreAppendResult:
    """Outcome of a durable append request."""

    stored: bool
    record_id: str
    reason: str


@dataclass(frozen=True)
class _CommittedIndexEntry:
    """What the durable index remembers about one committed logical seat."""

    outcome_id: str
    opinion_hash: str


def _parse_call_line(line: bytes) -> ProviderCallOutcome:
    return call_outcome_from_dict(parse_json_object_line(line))


def _parse_case_line(line: bytes) -> CommitteeCaseOutcome:
    return case_outcome_from_dict(parse_json_object_line(line))


def _parse_evaluation_line(line: bytes):
    return evaluation_report_from_dict(parse_json_object_line(line))


def _parse_prospective_line(line: bytes):
    return prospective_record_from_dict(parse_json_object_line(line))


def _parse_attribution_line(line: bytes):
    return attribution_report_from_dict(parse_json_object_line(line))


def _prospective_visible_at(row: Any) -> datetime:
    """The authoritative visibility timestamp for one prospective record.

    Dispatched by record type: a sealed prediction is visible when it was sealed,
    an outcome observation when it was observed, and a prospective evaluation when
    it was evaluated. An unknown record type raises rather than guessing at a
    temporal field, because a wrong timestamp would silently misplace the record
    in archive windows and hide it from verification.
    """
    from app.opip.committee.prospective import (
        OutcomeObservation,
        ProspectiveEvaluation,
        SealedPrediction,
    )

    if isinstance(row, SealedPrediction):
        return row.sealed_at
    if isinstance(row, OutcomeObservation):
        return row.observed_at
    if isinstance(row, ProspectiveEvaluation):
        return row.evaluated_at
    raise CommitteeSerializationError(
        "no prospective visibility timestamp is defined for "
        f"{type(row).__name__}"
    )


def _prospective_record_id(row: Any) -> str | None:
    """The durable id of one prospective record, chosen by its record type.

    A ``ProspectiveEvaluation`` carries both a ``prediction_id`` and an
    ``evaluation_id``, so an attribute-presence chain would always select the
    prediction id and the evaluation ids would never be indexed. Selecting by
    record type keeps every evaluation reachable for duplicate detection.
    """
    from app.opip.committee.prospective import (
        OutcomeObservation,
        ProspectiveEvaluation,
        SealedPrediction,
    )

    if isinstance(row, SealedPrediction):
        return row.prediction_id
    if isinstance(row, OutcomeObservation):
        return row.observation_id
    if isinstance(row, ProspectiveEvaluation):
        return row.evaluation_id
    return None


class CommitteeEvidenceStore:
    """Append-only durable store for committee observations and case outcomes."""

    def __init__(
        self,
        *,
        root: Path = COMMITTEE_DIR,
        call_max_bytes: int = CALL_OUTCOMES_MAX_BYTES,
        call_keep_lines: int = CALL_OUTCOMES_KEEP_LINES,
        case_max_bytes: int = CASE_OUTCOMES_MAX_BYTES,
        case_keep_lines: int = CASE_OUTCOMES_KEEP_LINES,
        evaluations_max_bytes: int = EVALUATIONS_MAX_BYTES,
        evaluations_keep_lines: int = EVALUATIONS_KEEP_LINES,
        prospective_max_bytes: int = PROSPECTIVE_MAX_BYTES,
        prospective_keep_lines: int = PROSPECTIVE_KEEP_LINES,
        attributions_max_bytes: int = ATTRIBUTIONS_MAX_BYTES,
        attributions_keep_lines: int = ATTRIBUTIONS_KEEP_LINES,
    ) -> None:
        self.root = Path(root)
        self.call_lock_file = self.root / ".call_outcomes.lock"
        self.case_lock_file = self.root / ".case_outcomes.lock"
        self.evaluation_lock_file = self.root / ".evaluations.lock"
        self.prospective_lock_file = self.root / ".prospective.lock"
        self.attribution_lock_file = self.root / ".attributions.lock"
        self.calls_index_file = self.root / "call_outcome_index.json"
        self.cases_index_file = self.root / "case_outcome_index.json"
        self.evaluations_index_file = self.root / "evaluation_index.json"
        self.prospective_index_file = self.root / "prospective_index.json"
        self.attribution_index_file = self.root / "attribution_index.json"
        self.call_charge_file = self.root / "call_charge_index.json"
        self._calls = BoundedJsonlArchive(
            data_file=self.root / "call_outcomes.jsonl",
            archive_dir=self.root / "archive_calls",
            max_bytes=call_max_bytes,
            keep_lines=call_keep_lines,
            archive_prefix="calls",
            parse_line=_parse_call_line,
            visible_at=lambda row: row.request_at,
        )
        self._cases = BoundedJsonlArchive(
            data_file=self.root / "case_outcomes.jsonl",
            archive_dir=self.root / "archive_cases",
            max_bytes=case_max_bytes,
            keep_lines=case_keep_lines,
            archive_prefix="cases",
            parse_line=_parse_case_line,
            visible_at=lambda row: row.started_at,
        )
        self._evaluations = BoundedJsonlArchive(
            data_file=self.root / "evaluations.jsonl",
            archive_dir=self.root / "archive_evaluations",
            max_bytes=evaluations_max_bytes,
            keep_lines=evaluations_keep_lines,
            archive_prefix="evaluations",
            parse_line=_parse_evaluation_line,
            visible_at=lambda row: row.generated_at,
        )
        self._prospective = BoundedJsonlArchive(
            data_file=self.root / "prospective.jsonl",
            archive_dir=self.root / "archive_prospective",
            max_bytes=prospective_max_bytes,
            keep_lines=prospective_keep_lines,
            archive_prefix="prospective",
            parse_line=_parse_prospective_line,
            # The stream holds several record types with different temporal
            # fields, so the visibility timestamp is dispatched by record type.
            visible_at=_prospective_visible_at,
        )
        self._attributions = BoundedJsonlArchive(
            data_file=self.root / "attributions.jsonl",
            archive_dir=self.root / "archive_attributions",
            max_bytes=attributions_max_bytes,
            keep_lines=attributions_keep_lines,
            archive_prefix="attributions",
            parse_line=_parse_attribution_line,
            visible_at=lambda row: row.generated_at,
        )

    # ---------------------------------------------------------------- calls

    def append_call_outcome(
        self,
        outcome: ProviderCallOutcome,
        *,
        charge_microunits: int | None = None,
    ) -> StoreAppendResult:
        """Append one call outcome, or acknowledge a duplicate without writing.

        A committed observation is immutable. When the same logical seat is
        re-delivered with materially different content, the original is kept and
        the divergence is reported instead of silently overwriting history.

        ``charge_microunits`` is the runtime's computed charge for this attempt,
        written under the same lock as the outcome so a reservation cannot be
        lost relative to the evidence it belongs to.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.call_lock_file):
            self._calls.repair_tail()
            index = self._load_call_index()
            previous = index.get(outcome.logical_observation_id)
            if previous is not None:
                if (
                    outcome.opinion is not None
                    and outcome.opinion.opinion_hash != previous.opinion_hash
                ):
                    return StoreAppendResult(False, previous.outcome_id, REASON_DIVERGENCE)
                return StoreAppendResult(False, previous.outcome_id, REASON_DUPLICATE)
            self._calls.append_encoded_locked(encode_row(call_outcome_to_dict(outcome)))
            if charge_microunits is not None:
                charges = self.load_call_charges()
                charges[outcome.outcome_id] = charge_microunits
                self.save_call_charges(charges)
            if outcome.status in COMMITTED_STATUSES and outcome.opinion is not None:
                index[outcome.logical_observation_id] = _CommittedIndexEntry(
                    outcome_id=outcome.outcome_id,
                    opinion_hash=outcome.opinion.opinion_hash,
                )
                self._save_call_index(index)
            self._compact(self._calls, "call outcome")
            return StoreAppendResult(True, outcome.outcome_id, REASON_STORED)

    def iter_call_outcomes(self, *, include_archive: bool = True) -> Iterator[ProviderCallOutcome]:
        seen: set[str] = set()
        if include_archive:
            for row in self._calls.iter_archive_rows():
                if row.outcome_id in seen:
                    continue
                seen.add(row.outcome_id)
                yield row
        for row in self._calls.iter_hot_rows():
            if row.outcome_id in seen:
                continue
            seen.add(row.outcome_id)
            yield row

    def call_hot_signature(self) -> tuple[int, int] | None:
        return self._calls.hot_signature()

    # ---------------------------------------------------------------- cases

    def load_call_charges(self) -> dict[str, int]:
        """Per-call-outcome charges recorded by the committee runtime."""
        raw = self._load_index_payload(self.call_charge_file)
        entries = raw.get("entries")
        if not isinstance(entries, Mapping):
            return {}
        return {
            key: value
            for key, value in entries.items()
            if isinstance(key, str) and type(value) is int and value >= 0
        }

    def save_call_charges(self, charges: Mapping[str, int]) -> None:
        """Persist per-call charges so a redelivery cannot respend the ceiling."""
        payload = {
            "schema_version": 1,
            "kind": "CALL_CHARGE",
            "entries": {key: int(value) for key, value in charges.items()},
        }
        self._write_index(self.call_charge_file, payload, label="call charge")

    def append_case_outcome(self, outcome: CommitteeCaseOutcome) -> StoreAppendResult:
        self.root.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.case_lock_file):
            self._cases.repair_tail()
            known = self._load_case_ids()
            if outcome.case_outcome_id in known:
                return StoreAppendResult(False, outcome.case_outcome_id, REASON_DUPLICATE)
            # A case may hold only one case outcome. A second outcome for the same
            # case with a different canonical binding would durably attribute an
            # opinion obtained for one decision to an unrelated one, so it fails
            # closed rather than being appended.
            for existing in self.iter_case_outcomes():
                if existing.case_id != outcome.case_id:
                    continue
                if existing.canonical_binding != outcome.canonical_binding:
                    raise CommitteeSerializationError(
                        f"case {outcome.case_id!r} already has a case outcome with a "
                        "different canonical binding; a reused case cannot be "
                        "reattributed to another decision"
                    )
                return StoreAppendResult(False, outcome.case_outcome_id, REASON_DUPLICATE)
            self._cases.append_encoded_locked(encode_row(case_outcome_to_dict(outcome)))
            known.add(outcome.case_outcome_id)
            self._save_case_ids(known)
            self._compact(self._cases, "case outcome")
            return StoreAppendResult(True, outcome.case_outcome_id, REASON_STORED)

    def iter_case_outcomes(self, *, include_archive: bool = True) -> Iterator[CommitteeCaseOutcome]:
        seen: set[str] = set()
        if include_archive:
            for row in self._cases.iter_archive_rows():
                if row.case_outcome_id in seen:
                    continue
                seen.add(row.case_outcome_id)
                yield row
        for row in self._cases.iter_hot_rows():
            if row.case_outcome_id in seen:
                continue
            seen.add(row.case_outcome_id)
            yield row

    # ----------------------------------------------------------- evaluations

    def append_evaluation_report(self, report) -> StoreAppendResult:
        """Append a bake-off report, or acknowledge a duplicate without writing."""
        self.root.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.evaluation_lock_file):
            self._evaluations.repair_tail()
            known = self._load_evaluation_ids()
            if report.report_id in known:
                return StoreAppendResult(False, report.report_id, REASON_DUPLICATE)
            self._evaluations.append_encoded_locked(
                encode_row(evaluation_report_to_dict(report))
            )
            known.add(report.report_id)
            self._save_evaluation_ids(known)
            self._compact(self._evaluations, "evaluation report")
            return StoreAppendResult(True, report.report_id, REASON_STORED)

    def iter_evaluation_reports(self, *, include_archive: bool = True):
        seen: set[str] = set()
        if include_archive:
            for row in self._evaluations.iter_archive_rows():
                if row.report_id in seen:
                    continue
                seen.add(row.report_id)
                yield row
        for row in self._evaluations.iter_hot_rows():
            if row.report_id in seen:
                continue
            seen.add(row.report_id)
            yield row

    def _load_id_set(
        self,
        path: Path,
        *,
        rebuild: Callable[[], set[str]],
        persist: Callable[[set[str]], None],
    ) -> set[str]:
        """Return a record-id set, reconciled against the authoritative log.

        The sidecar is a cache, never the authority. A missing, empty, stale, or
        partially written sidecar must not make an existing record look new,
        because a redelivery would then be appended a second time. The durable
        stream is therefore scanned and the sidecar reconciled to it on every
        load, exactly as the call index is.
        """
        stored = self._parse_id_set(path)
        rebuilt = rebuild()
        if rebuilt != stored:
            persist(rebuilt)
            if stored:
                logger.warning(
                    "O'Pip committee sidecar reconciled from the durable log at "
                    "%s (stored=%d, durable=%d); durable evidence is authoritative",
                    path.name,
                    len(stored),
                    len(rebuilt),
                )
        return rebuilt

    def _parse_id_set(self, path: Path) -> set[str]:
        raw = self._load_index_payload(path)
        entries = raw.get("entries")
        if not isinstance(entries, Mapping):
            return set()
        return {key for key in entries if isinstance(key, str)}

    def _rebuild_case_ids(self) -> set[str]:
        """Derive the case-outcome id set from the authoritative durable log."""
        return {row.case_outcome_id for row in self.iter_case_outcomes()}

    def _rebuild_evaluation_ids(self) -> set[str]:
        """Derive the evaluation-report id set from the durable log."""
        return {row.report_id for row in self.iter_evaluation_reports()}

    def _rebuild_prospective_ids(self) -> set[str]:
        """Derive the prospective id set from the durable log."""
        return {
            record_id
            for record_id in (
                _prospective_record_id(row)
                for row in self.iter_prospective_records()
            )
            if record_id is not None
        }

    def _rebuild_attribution_ids(self) -> set[str]:
        """Derive the attribution-report id set from the durable log."""
        return {row.attribution_id for row in self.iter_attribution_reports()}

    def _save_id_set(
        self, path: Path, *, kind: str, ids: set[str], label: str
    ) -> None:
        payload = {
            "schema_version": 1,
            "kind": kind,
            "entries": {key: key for key in sorted(ids)},
        }
        self._write_index(path, payload, label=label)

    def _load_evaluation_ids(self) -> set[str]:
        return self._load_id_set(
            self.evaluations_index_file,
            rebuild=self._rebuild_evaluation_ids,
            persist=self._save_evaluation_ids,
        )

    def _save_evaluation_ids(self, ids: set[str]) -> None:
        self._save_id_set(
            self.evaluations_index_file, kind="EVALUATION", ids=ids, label="evaluation"
        )

    # ----------------------------------------------------------- prospective

    def append_sealed_prediction(self, prediction) -> StoreAppendResult:
        return self._append_prospective(
            row=sealed_prediction_to_dict(prediction),
            record_id=prediction.prediction_id,
            label="sealed prediction",
        )

    def append_outcome_observation(self, observation) -> StoreAppendResult:
        return self._append_prospective(
            row=outcome_observation_to_dict(observation),
            record_id=observation.observation_id,
            label="outcome observation",
        )

    def append_prospective_evaluation(self, evaluation) -> StoreAppendResult:
        return self._append_prospective(
            row=prospective_evaluation_to_dict(evaluation),
            record_id=evaluation.evaluation_id,
            label="prospective evaluation",
        )

    def _append_prospective(
        self, *, row: Mapping[str, Any], record_id: str, label: str
    ) -> StoreAppendResult:
        self.root.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.prospective_lock_file):
            self._prospective.repair_tail()
            known = self._load_prospective_ids()
            if record_id in known:
                return StoreAppendResult(False, record_id, REASON_DUPLICATE)
            self._prospective.append_encoded_locked(encode_row(dict(row)))
            known.add(record_id)
            self._save_prospective_ids(known)
            self._compact(self._prospective, label)
            return StoreAppendResult(True, record_id, REASON_STORED)

    def iter_prospective_records(self, *, include_archive: bool = True) -> Iterator[Any]:
        # Keyed by record type as well as id: a prediction and its evaluation
        # legitimately share the prediction_id, and two evaluations of the same
        # prediction are distinct records, so id alone would collapse them.
        seen: set[tuple[str, str]] = set()
        rows: list[Any] = []
        if include_archive:
            rows.extend(self._prospective.iter_archive_rows())
        rows.extend(self._prospective.iter_hot_rows())
        for row in rows:
            record_id = _prospective_record_id(row)
            key = (type(row).__name__, str(record_id))
            if key in seen:
                continue
            seen.add(key)
            yield row

    def iter_sealed_predictions(self, *, include_archive: bool = True):
        from app.opip.committee.prospective import SealedPrediction

        for row in self.iter_prospective_records(include_archive=include_archive):
            if isinstance(row, SealedPrediction):
                yield row

    def iter_outcome_observations(self, *, include_archive: bool = True):
        from app.opip.committee.prospective import OutcomeObservation

        for row in self.iter_prospective_records(include_archive=include_archive):
            if isinstance(row, OutcomeObservation):
                yield row

    def iter_prospective_evaluations(self, *, include_archive: bool = True):
        from app.opip.committee.prospective import ProspectiveEvaluation

        for row in self.iter_prospective_records(include_archive=include_archive):
            if isinstance(row, ProspectiveEvaluation):
                yield row

    def _load_prospective_ids(self) -> set[str]:
        return self._load_id_set(
            self.prospective_index_file,
            rebuild=self._rebuild_prospective_ids,
            persist=self._save_prospective_ids,
        )

    def _save_prospective_ids(self, ids: set[str]) -> None:
        self._save_id_set(
            self.prospective_index_file, kind="PROSPECTIVE", ids=ids, label="prospective"
        )

    # ----------------------------------------------------------- attributions

    def append_attribution_report(self, report) -> StoreAppendResult:
        """Append an attribution report, or acknowledge a duplicate."""
        self.root.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.attribution_lock_file):
            self._attributions.repair_tail()
            known = self._load_id_set(
                self.attribution_index_file,
                rebuild=self._rebuild_attribution_ids,
                persist=lambda ids: self._save_id_set(
                    self.attribution_index_file,
                    kind="ATTRIBUTION",
                    ids=ids,
                    label="attribution",
                ),
            )
            if report.attribution_id in known:
                return StoreAppendResult(False, report.attribution_id, REASON_DUPLICATE)
            self._attributions.append_encoded_locked(
                encode_row(attribution_report_to_dict(report))
            )
            known.add(report.attribution_id)
            self._save_id_set(
                self.attribution_index_file,
                kind="ATTRIBUTION",
                ids=known,
                label="attribution",
            )
            self._compact(self._attributions, "attribution report")
            return StoreAppendResult(True, report.attribution_id, REASON_STORED)

    def iter_attribution_reports(self, *, include_archive: bool = True):
        seen: set[str] = set()
        if include_archive:
            for row in self._attributions.iter_archive_rows():
                if row.attribution_id in seen:
                    continue
                seen.add(row.attribution_id)
                yield row
        for row in self._attributions.iter_hot_rows():
            if row.attribution_id in seen:
                continue
            seen.add(row.attribution_id)
            yield row

    # --------------------------------------------------------------- helpers

    def _load_call_index(self) -> dict[str, _CommittedIndexEntry]:
        """Return the committed-call index, reconciled against the durable log.

        The sidecar is a cache, never the authority. A missing, empty, stale, or
        partially-written sidecar must not cause an existing logical observation
        to look new, because that would append a duplicate - or admit a divergent
        opinion - and repoint the sidecar at it. The durable log is therefore
        scanned and the sidecar is reconciled to it on every load.
        """
        stored = self._parse_call_index()
        rebuilt = self._rebuild_call_index()
        if rebuilt != stored:
            self._save_call_index(rebuilt)
            logger.warning(
                "O'Pip committee call index reconciled from the durable log "
                "(stored=%d, durable=%d); durable evidence is authoritative",
                len(stored),
                len(rebuilt),
            )
        return rebuilt

    def _parse_call_index(self) -> dict[str, _CommittedIndexEntry]:
        raw = self._load_index_payload(self.calls_index_file)
        entries = raw.get("entries")
        if not isinstance(entries, Mapping):
            return {}
        parsed: dict[str, _CommittedIndexEntry] = {}
        for key, value in entries.items():
            if not isinstance(key, str) or not isinstance(value, Mapping):
                continue
            outcome_id = value.get("outcome_id")
            opinion_hash = value.get("opinion_hash")
            if isinstance(outcome_id, str) and isinstance(opinion_hash, str):
                parsed[key] = _CommittedIndexEntry(
                    outcome_id=outcome_id, opinion_hash=opinion_hash
                )
        return parsed

    def _rebuild_call_index(self) -> dict[str, _CommittedIndexEntry]:
        rebuilt: dict[str, _CommittedIndexEntry] = {}
        for outcome in self.iter_call_outcomes():
            if (
                outcome.status in COMMITTED_STATUSES
                and outcome.opinion is not None
            ):
                rebuilt.setdefault(
                    outcome.logical_observation_id,
                    _CommittedIndexEntry(
                        outcome_id=outcome.outcome_id,
                        opinion_hash=outcome.opinion.opinion_hash,
                    ),
                )
        return rebuilt

    def _save_call_index(self, index: Mapping[str, _CommittedIndexEntry]) -> None:
        payload = {
            "schema_version": 1,
            "kind": "CALL_OUTCOME",
            "entries": {
                key: {
                    "outcome_id": entry.outcome_id,
                    "opinion_hash": entry.opinion_hash,
                }
                for key, entry in index.items()
            },
        }
        self._write_index(self.calls_index_file, payload, label="call outcome")

    def _load_case_ids(self) -> set[str]:
        return self._load_id_set(
            self.cases_index_file,
            rebuild=self._rebuild_case_ids,
            persist=self._save_case_ids,
        )

    def _save_case_ids(self, ids: set[str]) -> None:
        self._save_id_set(
            self.cases_index_file, kind="CASE_OUTCOME", ids=ids, label="case outcome"
        )

    def _load_index_payload(self, path: Path) -> Mapping[str, Any]:
        if not path.exists():
            # A first run has no index yet; the durable log is the authority.
            return {}
        try:
            raw = load_json(path)
        except Exception:
            logger.warning(
                "O'Pip committee index unreadable at %s; rebuilding from durable log",
                path,
            )
            return {}
        if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
            logger.warning(
                "O'Pip committee index schema mismatch at %s; rebuilding from durable log",
                path,
            )
            return {}
        return raw

    def _write_index(self, path: Path, payload: Mapping[str, Any], *, label: str) -> None:
        try:
            save_json_atomic(path, dict(payload))
        except Exception:
            # A missing index is recoverable: the durable log stays authoritative
            # and the next lookup rebuilds from it.
            logger.exception(
                "O'Pip committee %s index update failed; durable log preserved",
                label,
            )

    def _compact(self, archive: BoundedJsonlArchive, label: str) -> None:
        try:
            archive.compact_locked()
        except Exception:
            logger.exception(
                "O'Pip committee %s archive/compaction failed; HOT evidence preserved",
                label,
            )


class DurableObservationLedger:
    """Idempotency ledger backed by the durable committee evidence store.

    A lookup answers "has this exact logical seat observation been committed?"
    from durable evidence, so a process restart cannot create a second
    independent opinion for the same case, provider, model, prompt, policy, and
    evidence snapshot.
    """

    def __init__(self, *, store: CommitteeEvidenceStore) -> None:
        self._store = store
        self._committed: dict[str, ProviderCallOutcome] = {}
        self._attempts: dict[str, int] = {}
        self._charges: dict[str, int] = {}
        self._case_bindings: dict[str, object | None] = {}
        self._loaded_signature: tuple[int, int] | None = None
        self._loaded = False

    def _ensure_loaded(self) -> None:
        signature = self._store.call_hot_signature()
        if self._loaded and self._loaded_signature == signature:
            return
        committed: dict[str, ProviderCallOutcome] = {}
        attempts: dict[str, int] = {}
        for row in self._store.iter_call_outcomes():
            key = row.logical_observation_id
            attempts[key] = attempts.get(key, 0) + 1
            if row.status in COMMITTED_STATUSES:
                committed.setdefault(key, row)
        self._committed = committed
        self._attempts = attempts
        self._charges = self._store.load_call_charges()
        self._case_bindings = {
            outcome.case_id: outcome.canonical_binding
            for outcome in self._store.iter_case_outcomes()
        }
        self._loaded_signature = signature
        self._loaded = True

    def case_spend_microunits(self, case_id: str) -> int:
        """Known spend for a case.

        Each attempt contributes its explicitly recorded charge, falling back to
        its reported cost for evidence written before charges were recorded. That
        combines legacy and current evidence: choosing the greater of the two
        totals would drop one of them for a case that spans the upgrade, since
        legacy calls have only reported cost and new calls have only a charge.
        """
        self._ensure_loaded()
        total = 0
        for row in self._store.iter_call_outcomes():
            if row.case_id != case_id:
                continue
            total += self._charges.get(
                row.outcome_id, row.estimated_microunits_reported()
            )
        return total

    def recorded_case_binding(self, case_id: str) -> tuple[bool, object | None]:
        """The binding already durably recorded for a case, and whether it was.

        Read from the case-outcome artifact itself rather than a cache, because
        the cache can be populated before the artifact is appended.
        """
        for outcome in self._store.iter_case_outcomes():
            if outcome.case_id == case_id:
                return True, outcome.canonical_binding
        if case_id in self._case_bindings:
            return True, self._case_bindings[case_id]
        return False, None

    def note_case_binding(self, case_id: str, binding: object | None) -> None:
        # Durability for the binding comes from the case-outcome artifact itself,
        # which the store refuses to rewrite with a different binding.
        self._case_bindings.setdefault(case_id, binding)

    def committed_opinion(self, logical_observation_id: str) -> ProviderCallOutcome | None:
        self._ensure_loaded()
        return self._committed.get(logical_observation_id)

    def attempt_count(self, logical_observation_id: str) -> int:
        self._ensure_loaded()
        return self._attempts.get(logical_observation_id, 0)

    def record(
        self,
        outcome: ProviderCallOutcome,
        *,
        charge_microunits: int | None = None,
    ) -> None:
        """Persist one attempt, refusing to publish a rejected observation.

        The charge is written together with the call outcome, under the same
        store lock, so a crash between the two cannot lose a reservation and let
        a redelivery respend the case ceiling.

        If the durable log already holds a different opinion for this logical
        seat, the append is refused as a divergence. That result cannot be
        discarded: the caller must not proceed to publish a case outcome
        containing an opinion the evidence log explicitly rejected.
        """
        result = self._store.append_call_outcome(
            outcome, charge_microunits=charge_microunits
        )
        if not result.stored and result.reason == REASON_DIVERGENCE:
            raise CommitteeReplayDivergenceError(
                "the durable call log already holds a different opinion for this "
                f"logical observation ({outcome.logical_observation_id}); the new "
                "opinion was refused and no case outcome may be published from it"
            )
        # Invalidate so the next lookup re-reads durable evidence rather than
        # trusting a locally derived view.
        self._loaded = False


__all__ = [
    "ATTRIBUTIONS_KEEP_LINES",
    "ATTRIBUTIONS_MAX_BYTES",
    "CALL_OUTCOMES_KEEP_LINES",
    "CALL_OUTCOMES_MAX_BYTES",
    "CASE_OUTCOMES_KEEP_LINES",
    "CASE_OUTCOMES_MAX_BYTES",
    "COMMITTEE_DIR",
    "EVALUATIONS_KEEP_LINES",
    "EVALUATIONS_MAX_BYTES",
    "PROSPECTIVE_KEEP_LINES",
    "PROSPECTIVE_MAX_BYTES",
    "REASON_DIVERGENCE",
    "REASON_DUPLICATE",
    "REASON_STORED",
    "CommitteeEvidenceStore",
    "DurableObservationLedger",
    "StoreAppendResult",
]
