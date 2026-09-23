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
    call_replay_rejection_from_dict,
    call_replay_rejection_to_dict,
    case_outcome_from_dict,
    case_outcome_to_dict,
    prospective_ineligibility_from_dict,
    prospective_ineligibility_to_dict,
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
REJECTIONS_MAX_BYTES = 4 * 1024 * 1024
REJECTIONS_KEEP_LINES = 20_000
INELIGIBILITIES_MAX_BYTES = 4 * 1024 * 1024
INELIGIBILITIES_KEEP_LINES = 20_000

#: Reasons an append is acknowledged without writing a new row.
REASON_STORED = "STORED"
REASON_DUPLICATE = "DUPLICATE_UNCHANGED_INPUTS"
REASON_DIVERGENCE = "REPLAY_DIVERGENCE_PRESERVED_ORIGINAL"

#: Recorded in a rejection when the refused replay carried no opinion at all, so
#: the field keeps a stable non-empty value instead of implying a hashed opinion.
REFUSED_OPINION_ABSENT = "NO_OPINION_IN_REFUSED_REPLAY"


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


def _parse_rejection_line(line: bytes):
    return call_replay_rejection_from_dict(parse_json_object_line(line))


def _parse_ineligibility_line(line: bytes):
    return prospective_ineligibility_from_dict(parse_json_object_line(line))


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
        rejections_max_bytes: int = REJECTIONS_MAX_BYTES,
        rejections_keep_lines: int = REJECTIONS_KEEP_LINES,
        ineligibilities_max_bytes: int = INELIGIBILITIES_MAX_BYTES,
        ineligibilities_keep_lines: int = INELIGIBILITIES_KEEP_LINES,
    ) -> None:
        self.root = Path(root)
        self.call_lock_file = self.root / ".call_outcomes.lock"
        self.case_lock_file = self.root / ".case_outcomes.lock"
        self.evaluation_lock_file = self.root / ".evaluations.lock"
        self.prospective_lock_file = self.root / ".prospective.lock"
        self.attribution_lock_file = self.root / ".attributions.lock"
        self.rejection_lock_file = self.root / ".call_rejections.lock"
        self.ineligibility_lock_file = self.root / ".prospective_ineligible.lock"
        self.calls_index_file = self.root / "call_outcome_index.json"
        self.cases_index_file = self.root / "case_outcome_index.json"
        self.evaluations_index_file = self.root / "evaluation_index.json"
        self.prospective_index_file = self.root / "prospective_index.json"
        self.attribution_index_file = self.root / "attribution_index.json"
        self.rejections_index_file = self.root / "call_rejection_index.json"
        self.ineligibility_index_file = self.root / "prospective_ineligible_index.json"
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
        self._rejections = BoundedJsonlArchive(
            data_file=self.root / "call_rejections.jsonl",
            archive_dir=self.root / "archive_rejections",
            max_bytes=rejections_max_bytes,
            keep_lines=rejections_keep_lines,
            archive_prefix="rejections",
            parse_line=_parse_rejection_line,
            visible_at=lambda row: row.refused_at,
        )
        self._ineligibilities = BoundedJsonlArchive(
            data_file=self.root / "prospective_ineligible.jsonl",
            archive_dir=self.root / "archive_ineligible",
            max_bytes=ineligibilities_max_bytes,
            keep_lines=ineligibilities_keep_lines,
            archive_prefix="ineligible",
            parse_line=_parse_ineligibility_line,
            visible_at=lambda row: row.detected_at,
        )

    # ---------------------------------------------------------------- calls

    def append_call_outcome(self, outcome: ProviderCallOutcome) -> StoreAppendResult:
        """Append one call outcome, or acknowledge an identical one as a duplicate.

        Every invocation is its own durable row, keyed by its attempt-scoped
        ``outcome_id``, so a replay attempt and the charge it consumed are
        persisted as separate evidence rather than being folded into the
        original. The first committed opinion for a logical seat is what the
        index remembers, so a replay can never replace it; a replay carrying a
        materially different opinion for the same logical seat is still refused
        as a divergence.

        The charge travels inside the row, so a reservation is fsynced with the
        call it belongs to instead of in a separate write that could be lost.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.call_lock_file):
            self._calls.repair_tail()
            index = self._load_call_index()
            previous = index.get(outcome.logical_observation_id)
            if previous is not None:
                if previous.outcome_id == outcome.outcome_id:
                    # The same attempt re-delivered: already durable.
                    return StoreAppendResult(
                        False, previous.outcome_id, REASON_DUPLICATE
                    )
                if (
                    outcome.opinion is not None
                    and outcome.opinion.opinion_hash != previous.opinion_hash
                ):
                    # The committed opinion stays the only accepted opinion. The
                    # refusal itself is made durable before returning, so a
                    # divergent replay cannot leave an unexplained gap between
                    # what a worker attempted and what the store holds. The
                    # rejection is written to its own stream, not the call stream,
                    # so it can never be read as an observation, a vote, or a cost.
                    self._record_replay_rejection(
                        outcome=outcome, previous=previous
                    )
                    return StoreAppendResult(
                        False, previous.outcome_id, REASON_DIVERGENCE
                    )
            self._calls.append_encoded_locked(encode_row(call_outcome_to_dict(outcome)))
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

    # ---------------------------------------------------- replay rejections

    def _record_replay_rejection(
        self, *, outcome: ProviderCallOutcome, previous: _CommittedIndexEntry
    ) -> None:
        """Persist durable evidence that a divergent replay was refused.

        Called while the call lock is already held, so the refusal is ordered
        against the call stream it refers to. The rejection timestamp is taken
        from the refused outcome rather than a clock read, so the record stays
        reproducible and the plane keeps reading no wall clock.

        A repeated identical divergence maps to the same content-derived
        ``rejection_id`` and is therefore acknowledged rather than appended a
        second time, so a replay cannot multiply rejection evidence.
        """
        from app.opip.committee.contracts import CallReplayRejection

        refused_at = outcome.response_at or outcome.request_at
        rejection = CallReplayRejection(
            case_id=outcome.case_id,
            logical_observation_id=outcome.logical_observation_id,
            committed_outcome_id=previous.outcome_id,
            committed_opinion_hash=previous.opinion_hash,
            refused_outcome_id=outcome.outcome_id,
            refused_opinion_hash=(
                outcome.opinion.opinion_hash
                if outcome.opinion is not None
                else REFUSED_OPINION_ABSENT
            ),
            refused_at=refused_at,
        )
        self.append_call_rejection(rejection)

    def append_call_rejection(self, rejection) -> StoreAppendResult:
        """Append one replay-rejection record, or acknowledge a duplicate."""
        self.root.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.rejection_lock_file):
            self._rejections.repair_tail()
            known = self._load_rejection_ids()
            if rejection.rejection_id in known:
                return StoreAppendResult(
                    False, rejection.rejection_id, REASON_DUPLICATE
                )
            self._rejections.append_encoded_locked(
                encode_row(call_replay_rejection_to_dict(rejection))
            )
            known.add(rejection.rejection_id)
            self._save_rejection_ids(known)
            self._compact(self._rejections, "call replay rejection")
            return StoreAppendResult(True, rejection.rejection_id, REASON_STORED)

    def iter_call_rejections(self, *, include_archive: bool = True) -> Iterator[Any]:
        seen: set[str] = set()
        if include_archive:
            for row in self._rejections.iter_archive_rows():
                if row.rejection_id in seen:
                    continue
                seen.add(row.rejection_id)
                yield row
        for row in self._rejections.iter_hot_rows():
            if row.rejection_id in seen:
                continue
            seen.add(row.rejection_id)
            yield row

    def _load_rejection_ids(self) -> set[str]:
        return self._load_id_set(
            self.rejections_index_file,
            rebuild=self._rebuild_rejection_ids,
            persist=self._save_rejection_ids,
        )

    def _save_rejection_ids(self, ids: set[str]) -> None:
        self._save_id_set(
            self.rejections_index_file,
            kind="CALL_REPLAY_REJECTION",
            ids=ids,
            label="call replay rejection",
        )

    def _rebuild_rejection_ids(self) -> set[str]:
        """Derive the rejection id set from the authoritative durable log."""
        return {row.rejection_id for row in self.iter_call_rejections()}

    # ------------------------------------------- prospective ineligibilities

    def append_prospective_ineligibility(self, record) -> StoreAppendResult:
        """Persist one ineligible-disposition record, or acknowledge a duplicate.

        Kept in its own stream so an ineligible case can never be read as a
        prospective evaluation. That is what structurally excludes a drifted
        release from prospective trust metrics and economic attribution.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.ineligibility_lock_file):
            self._ineligibilities.repair_tail()
            known = self._load_ineligibility_ids()
            if record.ineligibility_id in known:
                return StoreAppendResult(
                    False, record.ineligibility_id, REASON_DUPLICATE
                )
            self._ineligibilities.append_encoded_locked(
                encode_row(prospective_ineligibility_to_dict(record))
            )
            known.add(record.ineligibility_id)
            self._save_ineligibility_ids(known)
            self._compact(self._ineligibilities, "prospective ineligibility")
            return StoreAppendResult(True, record.ineligibility_id, REASON_STORED)

    def iter_prospective_ineligibilities(self, *, include_archive: bool = True) -> Iterator[Any]:
        seen: set[str] = set()
        if include_archive:
            for row in self._ineligibilities.iter_archive_rows():
                if row.ineligibility_id in seen:
                    continue
                seen.add(row.ineligibility_id)
                yield row
        for row in self._ineligibilities.iter_hot_rows():
            if row.ineligibility_id in seen:
                continue
            seen.add(row.ineligibility_id)
            yield row

    def _load_ineligibility_ids(self) -> set[str]:
        return self._load_id_set(
            self.ineligibility_index_file,
            rebuild=self._rebuild_ineligibility_ids,
            persist=self._save_ineligibility_ids,
        )

    def _save_ineligibility_ids(self, ids: set[str]) -> None:
        self._save_id_set(
            self.ineligibility_index_file,
            kind="PROSPECTIVE_INELIGIBILITY",
            ids=ids,
            label="prospective ineligibility",
        )

    def _rebuild_ineligibility_ids(self) -> set[str]:
        """Derive the ineligibility id set from the authoritative durable log."""
        return {row.ineligibility_id for row in self.iter_prospective_ineligibilities()}

    # ---------------------------------------------------------------- cases

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
        self._case_bindings = {
            outcome.case_id: outcome.canonical_binding
            for outcome in self._store.iter_case_outcomes()
        }
        self._loaded_signature = signature
        self._loaded = True

    def case_spend_microunits(self, case_id: str) -> int:
        """Known spend already recorded for a case.

        Each attempt contributes the charge persisted inside its own call row,
        falling back to that attempt's reported cost for evidence written before
        charges were recorded. Because the charge is part of the authoritative
        record it cannot be lost separately from the call it belongs to, and a
        replay attempt is counted as its own row.
        """
        return sum(
            row.charge_microunits
            if row.charge_microunits is not None
            else row.estimated_microunits_reported()
            for row in self._store.iter_call_outcomes()
            if row.case_id == case_id
        )

    def recorded_case_binding(self, case_id: str) -> tuple[bool, object | None]:
        """The binding already durably recorded for a case, and whether it was.

        A case counts as recorded if it has a case outcome or any durable call
        evidence. The latter covers the crash window between the first call being
        persisted and the aggregate case outcome being appended: without it, a
        redelivery could reattribute a case whose calls are already durable.
        """
        for outcome in self._store.iter_case_outcomes():
            if outcome.case_id == case_id:
                return True, outcome.canonical_binding
        if any(
            row.case_id == case_id for row in self._store.iter_call_outcomes()
        ):
            return True, self._case_bindings.get(case_id)
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

    def record(self, outcome: ProviderCallOutcome) -> None:
        """Persist one attempt, refusing to publish a rejected observation.

        The charge travels inside the outcome, so it is written with the call it
        belongs to rather than in a separate write that could be lost.

        If the durable log already holds a different opinion for this logical
        seat, the append is refused as a divergence. That result cannot be
        discarded: the caller must not proceed to publish a case outcome
        containing an opinion the evidence log explicitly rejected.
        """
        result = self._store.append_call_outcome(outcome)
        if not result.stored and result.reason == REASON_DIVERGENCE:
            raise CommitteeReplayDivergenceError(
                "the durable call log already holds a different opinion for this "
                f"logical observation ({outcome.logical_observation_id}); the new "
                "opinion was refused and no case outcome may be published from it"
            )
        # Invalidate so the next lookup re-reads durable evidence rather than
        # trusting a locally derived view.
        self._loaded = False

    def record_replay_rejection(
        self,
        *,
        refused: ProviderCallOutcome,
        committed: ProviderCallOutcome,
    ) -> None:
        """Persist durable evidence that a divergent replay was refused.

        The runtime detects a divergence by comparing against the committed
        opinion it already holds and raises before reaching :meth:`record`, so
        without this the refusal would leave no trace at all. The committed
        observation is untouched; only the refusal is written, into its own
        stream, so it can never be read as an observation or as spend.
        """
        from app.opip.committee.ledger import build_replay_rejection

        self._store.append_call_rejection(
            build_replay_rejection(refused=refused, committed=committed)
        )


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
    "REFUSED_OPINION_ABSENT",
    "REJECTIONS_KEEP_LINES",
    "REJECTIONS_MAX_BYTES",
    "CommitteeEvidenceStore",
    "DurableObservationLedger",
    "StoreAppendResult",
]
