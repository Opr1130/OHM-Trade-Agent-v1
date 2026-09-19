"""B/C-3 Increment 6A canonical Paper-v2 decision-snapshot evidence.

A DecisionContext must be able to cite the exact market snapshot the decision was
taken against, as durable canonical evidence. This module is the single converter
from the production canonical episode snapshot payload into that record, and the
narrow adapter that commits it.

Why a wrapper rather than the snapshot alone
--------------------------------------------

The canonical episode snapshot is a *producer-side* payload built for the retired
P1 outbox path. Committing it as Paper-v2 evidence needs a canonical event with a
stable shape, an explicit engine, and a content binding that ties the record to
the exact snapshot contents. The wrapper supplies exactly that and nothing more:
the inner payload is carried through unchanged, so there is one snapshot
representation and no second schema to drift.

Content binding
---------------

``snapshot_hash`` is never taken on trust. It is derived here as the canonical
content hash of the inner payload, and the canonical event contract recomputes it
again on commit. A caller cannot assert one snapshot's identity over another
snapshot's contents, and a ``SNAP:`` snapshot identity can never be substituted
for the ``PSNAP:`` content hash.

Identity, not content, keys the record
--------------------------------------

The idempotency key derives from snapshot identity, so an identical retry is
``DUPLICATE_OK`` while different content filed under the same snapshot identity
is refused as a same-key conflict. That is what makes "the snapshot a decision
cites" immutable once recorded.

Authority boundary
------------------

This adapter writes one event type and holds no execution authority. It performs
no market-data read, no admission, no risk decision, and no exchange call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from app.opip.canonical.decision_context_bridge import require_canonical_commit
from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
)
from app.opip.contracts.paper_execution_runtime import (
    DECISION_SNAPSHOT_EPISODE_RECORD_TYPE,
    DECISION_SNAPSHOT_EPISODE_SCHEMA_VERSION,
    PAPER_DECISION_SNAPSHOT_RECORDED,
    decision_snapshot_idempotency_key,
    validate_decision_snapshot_payload,
)
from app.opip.contracts.serialization import episode_snapshot_hash


class DecisionSnapshotUnavailableError(RuntimeError):
    """No defensible snapshot record could be produced, so execution must not proceed."""


class WriterSubmitClient(Protocol):
    """The only writer capability this adapter needs."""

    def submit(self, intent: Any) -> Any: ...


def _canonical_identity(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a canonical string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not have leading or trailing whitespace")
    if not value:
        raise ValueError(f"{field_name} is required")
    return value


@dataclass(frozen=True)
class DecisionSnapshot:
    """Immutable, self-proving handoff of one canonical episode snapshot.

    Construction re-derives every identity and the content hash from the payload
    itself, so an instance cannot exist in a state where its stated hash or
    identity disagrees with its contents - regardless of how it was constructed.
    """

    snapshot_payload: Mapping[str, Any]
    snapshot_id: str
    episode_id: str
    cohort_id: str
    snapshot_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_payload, Mapping):
            raise ValueError(
                "snapshot_payload must be a canonical episode snapshot object"
            )
        inner = dict(self.snapshot_payload)
        if inner.get("record_type") != DECISION_SNAPSHOT_EPISODE_RECORD_TYPE:
            raise ValueError(
                "snapshot_payload record_type must be "
                f"{DECISION_SNAPSHOT_EPISODE_RECORD_TYPE}"
            )
        if (
            type(inner.get("schema_version")) is not int
            or inner["schema_version"] != DECISION_SNAPSHOT_EPISODE_SCHEMA_VERSION
        ):
            raise ValueError("unsupported canonical episode snapshot schema version")

        for field_name in ("snapshot_id", "episode_id", "cohort_id"):
            _canonical_identity(inner.get(field_name), field_name=f"snapshot_payload.{field_name}")

        # The stated identities and hash must be the payload's own, so a
        # constructed instance can never misdescribe its contents.
        for field_name in ("snapshot_id", "episode_id", "cohort_id"):
            if getattr(self, field_name) != inner[field_name]:
                raise ValueError(
                    f"{field_name} does not match the nested snapshot identity"
                )
        expected_hash = episode_snapshot_hash(inner)
        if self.snapshot_hash != expected_hash:
            raise ValueError(
                "snapshot_hash must equal the canonical episode snapshot content hash"
            )
        object.__setattr__(self, "snapshot_payload", inner)

    @classmethod
    def from_payload(
        cls,
        snapshot_payload: Mapping[str, Any],
        *,
        expected_episode_id: str | None = None,
        expected_cohort_id: str | None = None,
    ) -> "DecisionSnapshot":
        """Derive a snapshot record, optionally checked against the decision subject.

        ``expected_episode_id`` / ``expected_cohort_id`` bind the snapshot to the
        opportunity being executed. A snapshot that belongs to a different episode
        or cohort is refused here rather than being filed under the wrong decision.
        """
        inner = dict(snapshot_payload) if isinstance(snapshot_payload, Mapping) else {}
        snapshot = cls(
            snapshot_payload=inner,
            snapshot_id=str(inner.get("snapshot_id") or ""),
            episode_id=str(inner.get("episode_id") or ""),
            cohort_id=str(inner.get("cohort_id") or ""),
            snapshot_hash=episode_snapshot_hash(inner) if inner else "",
        )
        if expected_episode_id is not None and snapshot.episode_id != expected_episode_id:
            raise ValueError(
                "decision snapshot episode does not match the executed opportunity"
            )
        if expected_cohort_id is not None and snapshot.cohort_id != expected_cohort_id:
            raise ValueError(
                "decision snapshot cohort does not match the executed opportunity"
            )
        return snapshot

    def as_wrapper(self) -> dict[str, Any]:
        """The canonical decision-snapshot payload, validated by the frozen contract."""
        return validate_decision_snapshot_payload(
            {
                "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
                "engine": ENGINE_OPIP_PAPER_V2,
                "snapshot_id": self.snapshot_id,
                "episode_id": self.episode_id,
                "cohort_id": self.cohort_id,
                "snapshot_hash": self.snapshot_hash,
                "snapshot_payload": dict(self.snapshot_payload),
            }
        )


def build_decision_snapshot_payload(snapshot: DecisionSnapshot) -> dict[str, Any]:
    """The canonical wrapper payload for one validated decision snapshot."""
    if not isinstance(snapshot, DecisionSnapshot):
        raise ValueError("snapshot must be a DecisionSnapshot")
    return snapshot.as_wrapper()


def decision_snapshot_intent(payload: Mapping[str, Any]) -> Any:
    """The canonical WriterIntent for one decision-snapshot payload."""
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import SCHEMA_VERSION

    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a canonical decision snapshot mapping")
    normalized = validate_decision_snapshot_payload(payload)
    return WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority="LOW",
        idempotency_key=decision_snapshot_idempotency_key(normalized),
        event_type=PAPER_DECISION_SNAPSHOT_RECORDED,
        payload=normalized,
    )


def submit_decision_snapshot(
    payload: Mapping[str, Any],
    *,
    client: WriterSubmitClient,
) -> Any:
    """Submit the decision snapshot and return durable-commit proof."""
    intent = decision_snapshot_intent(payload)
    ack = client.submit(intent)
    return require_canonical_commit(
        ack,
        idempotency_key=intent.idempotency_key,
        what=f"decision snapshot {intent.payload.get('snapshot_id')}",
    )


def commit_decision_snapshot(
    snapshot: DecisionSnapshot,
    *,
    client: WriterSubmitClient,
) -> Any:
    """Build, commit and prove one decision snapshot.

    Returns the durable-commit proof, whose ``event_id`` is the canonical record
    the downstream DecisionContext cites as its snapshot provenance. A caller that
    does not hold this proof has no snapshot evidence to point at.
    """
    payload = build_decision_snapshot_payload(snapshot)
    return submit_decision_snapshot(payload, client=client)


__all__ = [
    "DecisionSnapshot",
    "DecisionSnapshotUnavailableError",
    "build_decision_snapshot_payload",
    "commit_decision_snapshot",
    "decision_snapshot_intent",
    "submit_decision_snapshot",
]
