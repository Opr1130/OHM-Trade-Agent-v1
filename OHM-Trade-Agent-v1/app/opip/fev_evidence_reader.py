"""Read-only reader over canonically committed F5 feasibility-evidence records.

This is the seam between the F5 feasibility-evidence writer (which *persists*
``feasibility.evidence.recorded`` records) and the non-authoritative target spine
(which *consumes* them). It opens the canonical store through
``CanonicalWriter.for_reads`` - a read-only connection with no store lock and no
schema initialization - so it can never mutate, quarantine or rewrite canonical
evidence.

Properties this reader guarantees:

* reads only committed ``feasibility.evidence.recorded`` records, in canonical
  ``(history_epoch, local_sequence)`` order;
* validates each durable payload through the frozen event trust boundary and
  reconstructs the exact typed ``FeasibilityEvidence`` (recomputing both the F5
  ``evidence_fingerprint`` and the exact-content ``payload_hash``), so a payload
  whose declared identities do not match its content is rejected rather than
  trusted;
* fails closed on malformed or corrupt records (they are rejected, never repaired
  or fabricated) without aborting the whole batch;
* exposes a bounded cursor so a consumer processes each committed record once,
  deterministically, and resumes after a restart without reclassifying old records
  as new;
* owns no process-lifetime dedupe state: dedupe is the exclusive canonical
  ``(history_epoch, local_sequence)`` cursor's job, so the reader's memory stays
  bounded however long the process lives. A caller that re-reads from an earlier
  cursor deterministically sees the same committed rows again.

It holds no trading, admission, reservation, execution or exchange authority, and
performs no market read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.feasibility_evidence import FeasibilityEvidence

# Relative import: the event vocabulary reaches the scanner validation types via
# the durable codec. The reconstruction helper is imported with a guard-safe name
# so the F5 dormant-seam guard stays meaningful: no app module references the F5
# runtime seam module by name.
from .fev_evidence_event import reconstruct_feasibility_evidence_recorded_payload

#: A bounded read batch. Production caps the batch so one read cannot hold the
#: reader open for an unbounded time.
DEFAULT_BATCH_LIMIT = 200
MAX_BATCH_LIMIT = 1_000


class FeasibilityEvidenceRecordReadError(ValueError):
    """A committed feasibility-evidence payload could not be trusted as evidence."""


@dataclass(frozen=True)
class CommittedFeasibilityEvidence:
    """One valid committed record: the typed evidence plus its durable identities."""

    evidence: FeasibilityEvidence
    evidence_fingerprint: str
    payload_hash: str


@dataclass(frozen=True)
class CommittedFeasibilityEvidenceBatch:
    """One bounded read: validated records, the advanced cursor, and rejects."""

    records: tuple[CommittedFeasibilityEvidence, ...]
    cursor: tuple[int, int] | None
    rejected: int = 0
    reject_reasons: tuple[str, ...] = field(default_factory=tuple)


def committed_feasibility_evidence_from_payload(
    payload: Mapping[str, Any],
) -> CommittedFeasibilityEvidence:
    """Validate one committed record payload and reconstruct its typed evidence.

    Fails closed: any malformed, tampered or non-canonical payload, or a declared
    identity that does not match the content, raises
    ``FeasibilityEvidenceRecordReadError``. This wrapper guarantees the parser
    never escapes a raw ``KeyError``/``ValueError``/``TypeError`` that could abort a
    whole read batch.
    """
    try:
        wrapper, evidence = reconstruct_feasibility_evidence_recorded_payload(payload)
    except FeasibilityEvidenceRecordReadError:
        raise
    except (
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
        OverflowError,
        ArithmeticError,
    ) as exc:
        raise FeasibilityEvidenceRecordReadError(
            f"feasibility-evidence payload is not record-valid: {exc}"
        ) from exc
    return CommittedFeasibilityEvidence(
        evidence=evidence,
        evidence_fingerprint=wrapper["evidence_fingerprint"],
        payload_hash=wrapper["payload_hash"],
    )


class CommittedFeasibilityEvidenceReader:
    """A bounded, read-only cursor over committed F5 feasibility-evidence records.

    Cursor persistence across a process restart is the caller's responsibility: the
    reader surfaces each batch's cursor so a consumer can persist it and resume
    deterministically. The reader keeps no dedupe set of its own - the exclusive
    canonical cursor is the dedupe authority - so its memory never grows with the
    committed history.
    """

    def __init__(self, *, db_path: Path) -> None:
        self._reader = CanonicalWriter.for_reads(db_path)

    @property
    def is_read_only(self) -> bool:
        return self._reader.is_read_only

    def close(self) -> None:
        """Release the read-only connection."""
        self._reader.close()

    def __enter__(self) -> "CommittedFeasibilityEvidenceReader":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read_batch(
        self,
        *,
        after: tuple[int, int] | None = None,
        limit: int = DEFAULT_BATCH_LIMIT,
    ) -> CommittedFeasibilityEvidenceBatch:
        """Validate and return the next committed records after ``after``."""
        bounded = min(max(1, int(limit)), MAX_BATCH_LIMIT)
        payload_texts, cursor = self._reader.read_feasibility_evidence_records(
            after=after, limit=bounded
        )
        records: list[CommittedFeasibilityEvidence] = []
        rejected = 0
        reasons: list[str] = []
        for payload_text in payload_texts:
            try:
                # Decode per record so a committed row whose payload is not
                # decodable JSON (possible on a store where the JSON guard index
                # is absent) is counted as a rejected record rather than aborting
                # the whole batch.
                payload = json.loads(payload_text)
                record = committed_feasibility_evidence_from_payload(payload)
            except FeasibilityEvidenceRecordReadError as exc:
                rejected += 1
                if len(reasons) < 8:
                    reasons.append(str(exc))
                continue
            except (TypeError, ValueError) as exc:
                rejected += 1
                if len(reasons) < 8:
                    reasons.append(f"committed payload is not decodable JSON: {exc}")
                continue
            records.append(record)
        # Canonical commit order (history_epoch, local_sequence) is the cursor's
        # guarantee and is preserved as returned.
        return CommittedFeasibilityEvidenceBatch(
            records=tuple(records),
            cursor=cursor,
            rejected=rejected,
            reject_reasons=tuple(reasons),
        )


def read_all_committed_feasibility_evidence(
    db_path: Path,
) -> tuple[CommittedFeasibilityEvidence, ...]:
    """Convenience: read every committed record once, in canonical commit order.

    Intended for bounded tooling/tests, not for an unbounded production loop.
    """
    reader = CommittedFeasibilityEvidenceReader(db_path=db_path)
    collected: list[CommittedFeasibilityEvidence] = []
    cursor: tuple[int, int] | None = None
    try:
        while True:
            batch = reader.read_batch(after=cursor)
            collected.extend(batch.records)
            if batch.cursor == cursor or not batch.cursor:
                break
            cursor = batch.cursor
    finally:
        reader.close()
    return tuple(collected)


__all__ = [
    "CommittedFeasibilityEvidence",
    "CommittedFeasibilityEvidenceBatch",
    "CommittedFeasibilityEvidenceReader",
    "DEFAULT_BATCH_LIMIT",
    "FeasibilityEvidenceRecordReadError",
    "MAX_BATCH_LIMIT",
    "committed_feasibility_evidence_from_payload",
    "read_all_committed_feasibility_evidence",
]
