"""Canonical decision-context bridge (B/C-3 increment 3b).

B/C-1 admission requires a canonical ``decision_context_id`` to already exist.
This module is the narrow adapter that constructs and commits exactly that piece
of evidence, so the Paper v2 runtime can obtain a context id **without** importing
the decision-intelligence plane.

Contract version
----------------

It produces the **schema-v2** DecisionContext, which is the contract that
describes what the real production qualification path can truthfully produce.
Schema v1 remains the backward-compatibility authority for previously recorded
committee evidence and is untouched; this adapter does not emit it and the two
are never reinterpreted as one another.

Schema v1's committee-oriented facts are intentionally not carried here. A
required v2 fact this adapter cannot obtain truthfully must fail closed rather
than be defaulted or invented.

Dependency inversion boundary
-----------------------------

The frozen rule (``test_acceptance_import_and_disabled_authority_boundaries``)
forbids the runtime roots - ``app/services``, ``app/jobs``, ``app/api``,
``app/opip/discovery``, ``app/opip/decision``, ``app/opip/risk`` - from importing
``app.opip.decision_intelligence`` at all. The canonical layer already owns DI
evidence validation (the canonical writer imports and validates the DI contract),
so this adapter lives in ``app/opip/canonical`` and is the only seam runtime code
touches::

    Paper-v2 runtime  ->  canonical decision-context bridge  ->  DI contract
                                                                     |
                                                                     v
                                                            canonical writer

Runtime Paper-v2 modules import **only** this adapter.

Authority boundary
------------------

This is an evidence-construction adapter, not the DI runtime plane. It writes one
event type - the decision context - and nothing else. It does not run a committee,
invoke AI models, create requests, transitions, role results, assessments or
comparisons, make admission or risk decisions, and it holds no execution, funded
or live authority. Its public input carries plain source facts: no DI types cross
the boundary.

Fail-closed
-----------

Every mandatory fact is required source evidence. Nothing is defaulted or
invented: a missing fact raises naming that fact, and a non-committed ACK raises
rather than letting admission proceed on unproven ancestry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol

from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.decision_intelligence.events import (
    DECISION_INTELLIGENCE_CONTEXT_RECORDED,
    context_idempotency_key,
    context_identity_v2,
)
from app.opip.decision_intelligence.identity import DecisionContextV2, Provenance

#: Canonical ACK statuses that prove a record is durably committed. Anything else
#: - REJECTED, RETRYABLE, SPOOLED, DISABLED - is not proof.
COMMITTED_ACK_STATUSES = frozenset({"OK", "DUPLICATE_OK"})

#: The Paper v2 execution path is spot-long and evaluates in the paper
#: environment. Frozen: a context that cannot assert them must not be produced.
PAPER_ENVIRONMENT = "paper"

#: The DI contract derives ``context_id`` from the assembled payload, so the id
#: cannot be supplied independently of the values it names. This sentinel only
#: satisfies construction and is replaced by the derived identity.
_CONTEXT_ID_PLACEHOLDER = "pending"


class CanonicalCommitError(RuntimeError):
    """A canonical record could not be proven committed, so callers must not proceed."""


class CanonicalCommit(Protocol):
    """Proof that one canonical record is durably committed."""

    idempotency_key: str
    event_id: str
    history_epoch: int
    local_sequence: int
    status: str


@dataclass(frozen=True)
class CanonicalCommitProof:
    """Durable-commit proof for one canonical record.

    The canonical position (``history_epoch``/``local_sequence``) is carried so a
    caller can use the record as consumed-input evidence without a second lookup.
    """

    idempotency_key: str
    event_id: str
    history_epoch: int
    local_sequence: int
    status: str

    @property
    def watermark(self) -> ConsumedInputWatermark:
        return ConsumedInputWatermark(
            history_epoch=self.history_epoch,
            local_sequence=self.local_sequence,
        )


def require_canonical_commit(
    ack: Any,
    *,
    idempotency_key: str,
    what: str,
) -> CanonicalCommitProof:
    """Convert a writer ACK into proof, or fail closed.

    A missing ACK, a non-committed status, a missing event id or a missing
    canonical sequence means persistence is unproven; callers must not continue on
    the assumption that the record exists.
    """
    if ack is None:
        raise CanonicalCommitError(f"{what} was not acknowledged by the canonical writer")
    status = str(getattr(ack, "status", "") or "")
    if status not in COMMITTED_ACK_STATUSES:
        error_code = getattr(ack, "error_code", None)
        raise CanonicalCommitError(
            f"{what} was not proven committed (status={status or 'UNKNOWN'}"
            f"{', error=' + str(error_code) if error_code else ''})"
        )
    event_id = getattr(ack, "event_id", None)
    history_epoch = getattr(ack, "history_epoch", None)
    local_sequence = getattr(ack, "local_sequence", None)
    if not event_id or history_epoch is None or local_sequence is None:
        raise CanonicalCommitError(
            f"{what} acknowledgement is missing canonical identity or sequence"
        )
    return CanonicalCommitProof(
        idempotency_key=idempotency_key,
        event_id=str(event_id),
        history_epoch=int(history_epoch),
        local_sequence=int(local_sequence),
        status=status,
    )


class WriterSubmitClient(Protocol):
    """The only writer capability this adapter needs."""

    def submit(self, intent: Any) -> Any: ...


def _require_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _require_canonical_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a canonical string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not have leading or trailing whitespace")
    if not value:
        raise ValueError(f"{field_name} is required")
    return value


@dataclass(frozen=True)
class DecisionContextFacts:
    """Plain source facts for one canonical decision context.

    Deliberately composed of primitives and shared contract types only: no DI type
    appears here, which is what lets runtime code supply it without importing the
    DI plane. Every field is required; there are no defaults, so a caller cannot
    omit a fact and silently get a placeholder.

    The shape is the **schema v2** production contract. Schema v1's
    committee-oriented facts - ``evaluation_id``, ``consumed_input_watermark``,
    ``feature_version``, ``detector_version``, ``forecast_version``,
    ``candidate_set_ref`` and the evidence-eligibility manifest - are deliberately
    absent rather than carried as placeholders: the production qualification path
    has no truthful value for them, and B/C-3A forbids fabricating them. A committee
    path that needs them must fail closed on a schema-v2 context instead of having
    them invented here.

    In particular the instrument-version registration coordinate is **not** a
    consumed-input watermark: registration proves the instrument exists, it does not
    describe the canonical input position the decision consumed, and B/C-3A forbids
    that mapping.
    """

    candidate_id: str
    episode_id: str
    instrument_version_id: str
    #: Proof that the referenced instrument version is already committed
    #: canonically (the event id returned by registration). Required, so a context
    #: can never be built for an instrument whose registration was not proven.
    instrument_registration_event_id: str
    #: Proof that the market snapshot this decision was taken against is committed
    #: canonically (the event id returned by the decision-snapshot commit). Required
    #: for the same reason: a context must be able to cite its snapshot as durable
    #: evidence rather than as a claim.
    snapshot_record_event_id: str
    snapshot_id: str
    snapshot_hash: str
    evaluation_time: datetime
    evidence_cutoff: datetime
    policy_version: str
    policy_fingerprint: str
    producing_component: str
    artifact_or_build_id: str
    process_instance_id: str
    emitted_at: datetime
    #: Additional upstream refs. May be empty: the two durable ancestry proofs
    #: above are always composed in first, so the resulting provenance is never
    #: empty and a caller never has to invent a ref merely to satisfy a
    #: precondition. A bare string is still rejected, and every supplied item must
    #: be a real non-empty reference.
    source_record_refs: tuple[str, ...]


def _build_provenance(facts: DecisionContextFacts) -> Provenance:
    """Compose provenance, forcing the durable ancestry proofs into the refs.

    The instrument-registration and decision-snapshot event ids are *proofs*, not
    optional citations: a context that names an instrument or a snapshot must also
    name the durable record that establishes it. They are therefore composed here
    unconditionally rather than trusted to the caller's ref list, so an empty or
    unrelated proof can never leave a context claiming ancestry it does not have.

    Ordering is deterministic - the proofs first in a fixed order, then the
    caller's refs in their supplied order - and duplicates are dropped on first
    occurrence, so the same facts always produce the same ref list.
    """
    instrument_proof = _require_text(
        facts.instrument_registration_event_id,
        field_name="instrument_registration_event_id",
    )
    snapshot_proof = _require_text(
        facts.snapshot_record_event_id, field_name="snapshot_record_event_id"
    )
    refs = facts.source_record_refs
    if isinstance(refs, str) or not isinstance(refs, (tuple, list)):
        raise ValueError("source_record_refs must be a sequence of references")
    composed: list[str] = []
    for reference in (instrument_proof, snapshot_proof, *refs):
        canonical = _require_text(reference, field_name="source_record_refs")
        if canonical not in composed:
            composed.append(canonical)
    return Provenance(
        producing_component=_require_text(
            facts.producing_component, field_name="producing_component"
        ),
        artifact_or_build_id=_require_text(
            facts.artifact_or_build_id, field_name="artifact_or_build_id"
        ),
        process_instance_id=_require_text(
            facts.process_instance_id, field_name="process_instance_id"
        ),
        emitted_at=facts.emitted_at,
        source_record_refs=tuple(composed),
    )


def build_decision_context_payload(facts: DecisionContextFacts) -> dict:
    """Build the canonical schema-v2 context payload with its derived ``context_id``.

    Construction goes through the frozen ``DecisionContextV2`` contract, so every
    v2 invariant is enforced by the contract itself: the required-string type rules,
    the ``evidence_cutoff <= evaluation_time`` ordering, and the paired
    supersession rule. Identity comes from the existing ``context_identity_v2``
    helper - no hash is reconstructed here.
    """
    if not isinstance(facts, DecisionContextFacts):
        raise ValueError("facts must be DecisionContextFacts")

    # Registration and snapshot commit must be proven before a context may exist,
    # so neither an unregistered instrument nor an unrecorded snapshot can produce
    # context evidence.
    _require_text(
        facts.instrument_registration_event_id,
        field_name="instrument_registration_event_id",
    )
    _require_text(
        facts.snapshot_record_event_id,
        field_name="snapshot_record_event_id",
    )

    context = DecisionContextV2(
        context_id=_CONTEXT_ID_PLACEHOLDER,
        candidate_id=_require_text(facts.candidate_id, field_name="candidate_id"),
        episode_id=_require_text(facts.episode_id, field_name="episode_id"),
        instrument_version=_require_canonical_text(
            facts.instrument_version_id, field_name="instrument_version_id"
        ),
        snapshot_id=_require_text(facts.snapshot_id, field_name="snapshot_id"),
        snapshot_hash=_require_text(facts.snapshot_hash, field_name="snapshot_hash"),
        evaluation_time=facts.evaluation_time,
        evidence_cutoff=facts.evidence_cutoff,
        policy_version=_require_text(facts.policy_version, field_name="policy_version"),
        policy_fingerprint=_require_text(
            facts.policy_fingerprint, field_name="policy_fingerprint"
        ),
        environment=PAPER_ENVIRONMENT,
        eligibility=True,
        provenance=_build_provenance(facts),
    )
    payload = context.as_dict()
    # ``context_id`` is a derived identity over the assembled payload: the
    # contract's own helper computes it, exactly as the canonical writer expects.
    payload["context_id"] = context_identity_v2(payload)
    return payload


def decision_context_intent(payload: Mapping[str, Any]) -> Any:
    """The canonical WriterIntent for one context payload."""
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import SCHEMA_VERSION

    context_id = _require_text(payload.get("context_id"), field_name="context_id")
    return WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority="LOW",
        idempotency_key=context_idempotency_key(context_id=context_id),
        event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
        payload=dict(payload),
    )


def submit_decision_context(
    payload: Mapping[str, Any],
    *,
    client: WriterSubmitClient,
) -> CanonicalCommitProof:
    """Submit the canonical context and return durable-commit proof."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a canonical decision context mapping")
    intent = decision_context_intent(payload)
    ack = client.submit(intent)
    return require_canonical_commit(
        ack,
        idempotency_key=intent.idempotency_key,
        what=f"decision context {payload.get('context_id')}",
    )


def commit_decision_context(
    facts: DecisionContextFacts,
    *,
    client: WriterSubmitClient,
) -> tuple[str, CanonicalCommitProof]:
    """Build, commit and prove one context, returning ``(context_id, proof)``.

    This is the seam the Paper v2 runtime calls after the canonical instrument
    version is registered. It returns the canonical ``decision_context_id`` that
    B/C-1 admission must reference, and only after the commit is proven.
    """
    payload = build_decision_context_payload(facts)
    proof = submit_decision_context(payload, client=client)
    return str(payload["context_id"]), proof


__all__ = [
    "COMMITTED_ACK_STATUSES",
    "PAPER_ENVIRONMENT",
    "CanonicalCommitError",
    "CanonicalCommitProof",
    "DecisionContextFacts",
    "WriterSubmitClient",
    "build_decision_context_payload",
    "commit_decision_context",
    "decision_context_intent",
    "require_canonical_commit",
    "submit_decision_context",
]
