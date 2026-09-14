"""Committed observation-revision ledger (PR3 cycle-3).

Feature-state promotion may fail after observations have already committed.
Revision numbers that reached the canonical WAL must not be forgotten: the next
cycle consults this ledger so a later correction mints the next revision instead
of colliding on a durable observation_id.

Additive reads from the generic events table — no physical schema bump.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from app.opip.contracts.events import MARKET_OBSERVATION_RECORDED
from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.contracts.observation import Observation
from app.opip.features.publisher import PublishOutcome
from app.opip.market.aggregates import DEFAULT_INTERVAL_SECONDS
from app.opip.market.observations import aggregate_content_fingerprint


class RevisionLedgerIntegrityError(ValueError):
    """Committed observation/revision payload is corrupt or non-reconstructable."""


@dataclass(frozen=True)
class CommittedObservationRevision:
    """One durable observation revision known to be in canonical history."""

    interval_epoch: int
    revision: int
    content_fingerprint: str
    observation_id: str
    commit_watermark: ConsumedInputWatermark
    #: Durable receipt from the committed WAL payload. Used when a ledger-only
    #: restart rebuilds rolling state so re-poll wall clocks cannot rewrite
    #: snapshot availability / late-arrival provenance.
    receipt_time: datetime | None = None


@dataclass(frozen=True)
class RevisionLedger:
    """Per-instrument map of interval epoch → highest committed revision."""

    instrument_version_id: str
    interval_seconds: int
    entries: Mapping[int, CommittedObservationRevision] = field(default_factory=dict)

    @classmethod
    def empty(
        cls,
        instrument_version: InstrumentVersion,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> "RevisionLedger":
        return cls(
            instrument_version_id=instrument_version.instrument_version_id,
            interval_seconds=int(interval_seconds),
            entries={},
        )

    def entry_for(self, interval_epoch: int) -> CommittedObservationRevision | None:
        return self.entries.get(int(interval_epoch))

    def with_committed(
        self,
        observations: Sequence[Observation],
        outcomes: Sequence[PublishOutcome],
    ) -> "RevisionLedger":
        """Record every observation whose publish outcome committed with a watermark."""
        if len(observations) != len(outcomes):
            return self
        updated = dict(self.entries)
        for observation, outcome in zip(observations, outcomes):
            if not outcome.committed or outcome.watermark is None:
                continue
            if observation.instrument_version_id != self.instrument_version_id:
                continue
            if observation.aggregate_interval_seconds != self.interval_seconds:
                continue
            epoch = int(observation.source_event_time.timestamp())
            fingerprint = aggregate_content_fingerprint(dict(observation.values))
            incoming = CommittedObservationRevision(
                interval_epoch=epoch,
                revision=int(observation.revision),
                content_fingerprint=fingerprint,
                observation_id=observation.observation_id,
                commit_watermark=outcome.watermark,
                receipt_time=observation.receipt_time,
            )
            prior = updated.get(epoch)
            if prior is not None and int(prior.revision) > int(incoming.revision):
                continue
            updated[epoch] = incoming
        return RevisionLedger(
            instrument_version_id=self.instrument_version_id,
            interval_seconds=self.interval_seconds,
            entries=updated,
        )

    def pruned_to(self, first_interval_epoch: int | None) -> "RevisionLedger":
        """Drop intervals that have aged out of the retained window."""
        if first_interval_epoch is None:
            return self
        floor = int(first_interval_epoch)
        kept = {
            epoch: entry
            for epoch, entry in self.entries.items()
            if int(epoch) >= floor
        }
        if len(kept) == len(self.entries):
            return self
        return RevisionLedger(
            instrument_version_id=self.instrument_version_id,
            interval_seconds=self.interval_seconds,
            entries=kept,
        )


def load_revision_ledger(
    instrument_version_id: str,
    *,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    db_path: Path | None = None,
    since_interval_epoch: int | None = None,
) -> RevisionLedger:
    """Rebuild the ledger from committed market.observation.recorded events.

    Committed payloads that are not JSON objects, or matching-instrument
    aggregate rows missing required identity/provenance fields, fail closed.
    Other instruments and other aggregate cadences are filtered out without
    reinterpretation.
    """
    from app.opip.canonical.schema import connect

    if db_path is None:
        from app.opip.canonical.paths import db_path as default_db_path

        db_path = default_db_path()
    target = Path(db_path)
    empty = RevisionLedger(
        instrument_version_id=instrument_version_id,
        interval_seconds=int(interval_seconds),
        entries={},
    )
    if not target.exists():
        return empty
    conn = connect(target, read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT payload_json, history_epoch, local_sequence
            FROM events
            WHERE event_type = ?
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (MARKET_OBSERVATION_RECORDED,),
        ).fetchall()
    finally:
        conn.close()

    updated: dict[int, CommittedObservationRevision] = {}
    # Fingerprints for every (epoch, revision) seen in commit order. Selection
    # keeps only the highest revision per epoch, but conflicting evidence for a
    # superseded lower revision must still fail closed.
    seen_fingerprints: dict[tuple[int, int], str] = {}
    step = int(interval_seconds)
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise RevisionLedgerIntegrityError(
                "committed market.observation.recorded payload_json did not "
                f"decode to a JSON object (got {type(payload).__name__}); "
                "refusing to silently skip malformed canonical evidence"
            )
        if str(payload.get("instrument_version_id") or "") != instrument_version_id:
            continue
        if int(payload.get("aggregate_interval_seconds") or 0) != step:
            continue
        source = payload.get("source_event_time")
        if source is None or str(source).strip() == "":
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} missing source_event_time; "
                "refusing to skip or invent revision provenance"
            )
        try:
            moment = datetime.fromisoformat(str(source).replace("Z", "+00:00"))
            epoch = int(moment.timestamp())
        except (TypeError, ValueError) as exc:
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} has unparseable source_event_time "
                f"{source!r}; refusing to skip malformed evidence"
            ) from exc
        if since_interval_epoch is not None and epoch < int(since_interval_epoch):
            continue
        values = payload.get("values")
        if not isinstance(values, Mapping):
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} at epoch {epoch} has non-object "
                "values; refusing to skip or invent a content fingerprint"
            )
        fingerprint = aggregate_content_fingerprint(dict(values))
        raw_revision = payload.get("revision")
        if isinstance(raw_revision, bool) or not isinstance(raw_revision, int):
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} at epoch {epoch} has non-integer "
                f"revision {raw_revision!r}"
            )
        revision = raw_revision
        if revision < 1:
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} at epoch {epoch} has invalid "
                f"revision {revision}"
            )
        observation_id = str(payload.get("observation_id") or "").strip()
        if not observation_id:
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} at epoch {epoch} missing "
                "observation_id; refusing to synthesize durable identity"
            )
        expected_observation_id = (
            f"OBS:{instrument_version_id}:{step}s:{epoch}:{revision}"
        )
        if observation_id != expected_observation_id:
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} at epoch {epoch} has observation_id "
                f"{observation_id!r} != expected {expected_observation_id!r}; "
                "refusing to trust mismatched durable identity"
            )
        raw_receipt = payload.get("receipt_time")
        if raw_receipt is None or str(raw_receipt).strip() == "":
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} at epoch {epoch} missing durable "
                "receipt_time; refusing to reconstruct provenance from a re-poll"
            )
        try:
            receipt_time = datetime.fromisoformat(
                str(raw_receipt).replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as exc:
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} at epoch {epoch} has "
                f"unparseable receipt_time {raw_receipt!r}"
            ) from exc
        if receipt_time.tzinfo is None or receipt_time.utcoffset() is None:
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} at epoch {epoch} has timezone-naive "
                f"receipt_time {raw_receipt!r}; durable receipt provenance "
                "must be timezone-aware"
            )
        revision_key = (epoch, revision)
        prior_fp = seen_fingerprints.get(revision_key)
        if prior_fp is not None and prior_fp != fingerprint:
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} at epoch {epoch} revision {revision} "
                "has conflicting content fingerprints; refusing to hide "
                "incompatible canonical evidence"
            )
        seen_fingerprints[revision_key] = fingerprint
        incoming = CommittedObservationRevision(
            interval_epoch=epoch,
            revision=revision,
            content_fingerprint=fingerprint,
            observation_id=observation_id,
            commit_watermark=ConsumedInputWatermark(
                history_epoch=int(row["history_epoch"]),
                local_sequence=int(row["local_sequence"]),
            ),
            receipt_time=receipt_time,
        )
        prior = updated.get(epoch)
        if prior is not None and int(prior.revision) > revision:
            continue
        if (
            prior is not None
            and int(prior.revision) == revision
            and prior.content_fingerprint != fingerprint
        ):
            raise RevisionLedgerIntegrityError(
                "committed observation for "
                f"{instrument_version_id} at epoch {epoch} revision {revision} "
                "has conflicting content fingerprints; refusing to hide "
                "incompatible canonical evidence"
            )
        updated[epoch] = incoming
    return RevisionLedger(
        instrument_version_id=instrument_version_id,
        interval_seconds=step,
        entries=updated,
    )


__all__ = [
    "CommittedObservationRevision",
    "RevisionLedger",
    "RevisionLedgerIntegrityError",
    "load_revision_ledger",
]
