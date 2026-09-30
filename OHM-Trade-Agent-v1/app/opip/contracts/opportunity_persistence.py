"""Durable F4 opportunity-lifecycle persistence vocabulary.

This module owns the durable *persistence* vocabulary for the F4 Opportunity
Lifecycle: the canonical writer event type, the watermark stream, the LOW
priority class, the payload schema token, the single deterministic idempotency
key, the payload builder/validator, the durable-history validator and the typed
read/projection model.

It is deliberately separate from the pure lifecycle semantics in
``app.opip.contracts.opportunity``: this module knows how one already-computed
lifecycle transition becomes durable evidence, but it computes no transition of
its own and holds no canonical-writer transaction code. It opens no database,
reads no clock and grants no trading, admission, paper or risk authority.

ONE record is ONE real state change. The producer emits exactly one event for a
changed lifecycle result and none for an unchanged one; a duplicate or replay of
the same event reaches ``DUPLICATE_OK`` with no second durable row, and the same
idempotency key carrying a semantically different payload fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.opip.contracts.opportunity import (
    EVENT_ID_PREFIX,
    OpportunityContractError,
    OpportunityEpisode,
    OpportunityLifecycleEvent,
    OpportunityLifecycleEventType,
    OpportunityLifecycleState,
    OpportunityTerminalReason,
)
from app.opip.contracts.serialization import iso_z

#: Canonical writer event type for one durable lifecycle transition. Renaming is
#: a contract change: already-written evidence carries this exact token.
OPPORTUNITY_LIFECYCLE_TRANSITION_RECORDED = "opportunity_lifecycle.transition.recorded"

#: The single registered F4 durable event type for v1.
OPPORTUNITY_LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset(
    {OPPORTUNITY_LIFECYCLE_TRANSITION_RECORDED}
)

#: Canonical watermark stream. Separate from every other stream so F4 progress
#: can never rewind alert-governor, feature-bus, paper or DI progress.
OPPORTUNITY_LIFECYCLE_STREAM = "opportunity_lifecycle"

#: F4 transitions are telemetry class only. LOW is a scheduling class in the
#: canonical writer, never an eviction policy.
OPPORTUNITY_LIFECYCLE_PRIORITY = "LOW"

#: Payload schema token for the F4 transition record, independent of the
#: canonical DB physical schema version. ``# nosec B105`` - this is a schema
#: identifier, not a secret.
OPPORTUNITY_LIFECYCLE_TRANSITION_SCHEMA_TOKEN = (  # nosec B105
    "opportunity-lifecycle-transition-v1"
)

#: ``record_type`` discriminator carried inside the payload.
OPPORTUNITY_LIFECYCLE_RECORD_TYPE = "opportunity_lifecycle_transition"

#: Durable idempotency-key prefix. The remainder is the deterministic lifecycle
#: event identity (``OPEV:``), so the key contains no receipt time, random
#: envelope id, process id, retry count, database sequence or current clock.
OPPORTUNITY_LIFECYCLE_IDEMPOTENCY_PREFIX = "OPLT:"

#: The canonical episode identity prefix, re-exported so the writer can reject a
#: malformed projection key without importing the pure contract module directly.
OPPORTUNITY_EPISODE_ID_PREFIX = "OPEP:"

#: The exact durable payload key set.
_PAYLOAD_KEYS: tuple[str, ...] = (
    "record_type",
    "schema_version",
    "episode",
    "event",
)

#: Episode fields that must be identical across every durable record of one
#: episode. A change in any of these between two records is lineage mutation and
#: is refused even when the individual record is internally well formed.
_LINEAGE_FIELDS: tuple[str, ...] = (
    "source_claim_id",
    "source_claim_idempotency_key",
    "instrument_version_id",
    "venue_instrument_id",
    "detector_family",
    "detector_version",
    "detector_policy_version",
    "claim_transition",
    "snapshot_id",
    "detector_input_fingerprint",
    "claim_evaluation_cutoff",
)


class OpportunityPersistenceError(OpportunityContractError):
    """A durable persistence contract violation. Always fails closed."""


# ---------------------------------------------------------------------------
# Deterministic identity / key
# ---------------------------------------------------------------------------


def opportunity_lifecycle_transition_idempotency_key(event_id: str) -> str:
    """The single deterministic idempotency key for one durable lifecycle event.

    Derived only from the validated, deterministic ``OpportunityLifecycleEvent``
    identity (``OPEV:<digest>``). No receipt time, random envelope id, process
    id, retry count, database sequence or current clock participates, so an exact
    replay reproduces the same key and a semantically different payload under the
    same key is an integrity conflict rather than a duplicate.
    """
    if not isinstance(event_id, str):
        raise OpportunityPersistenceError("event_id must be a string")
    token = event_id
    prefix = f"{EVENT_ID_PREFIX}:"
    if not token or token != token.strip() or not token.startswith(prefix):
        raise OpportunityPersistenceError(
            f"event_id must be a canonical {prefix}<digest> identity"
        )
    if len(token) <= len(prefix):
        raise OpportunityPersistenceError("event_id digest must be non-empty")
    return f"{OPPORTUNITY_LIFECYCLE_IDEMPOTENCY_PREFIX}{token}"


# ---------------------------------------------------------------------------
# Payload accessors (validated payload required)
# ---------------------------------------------------------------------------


def _payload_episode(payload: Mapping[str, Any]) -> OpportunityEpisode:
    body = _require_payload(payload)
    return OpportunityEpisode.from_dict(body["episode"])


def _payload_event(payload: Mapping[str, Any]) -> OpportunityLifecycleEvent:
    body = _require_payload(payload)
    return OpportunityLifecycleEvent.from_dict(body["event"])


def opportunity_transition_episode_id(payload: Mapping[str, Any]) -> str:
    """The episode identity named by one validated durable payload."""
    return _payload_episode(payload).episode_id


def opportunity_transition_event_id(payload: Mapping[str, Any]) -> str:
    """The deterministic lifecycle event identity named by one durable payload."""
    return _payload_event(payload).event_id


def opportunity_transition_event_time(payload: Mapping[str, Any]) -> str:
    """The canonical UTC serialization of the event's evaluation instant.

    The canonical writer stores this as ``event_time``. It is derived from the
    validated event, never from the writer's receipt clock.
    """
    event = _payload_event(payload)
    return iso_z(event.evaluation_time, field_name="event.evaluation_time")


def opportunity_transition_correlation_id(payload: Mapping[str, Any]) -> str:
    """The correlation id: the episode id."""
    return _payload_episode(payload).episode_id


def opportunity_transition_causation_id(payload: Mapping[str, Any]) -> str | None:
    """The causation id: the source claim id, or ``None`` when there is none.

    Claim-driven OPENED/DEFERRED transitions carry the source claim; a
    TIME-DRIVEN EXPIRED transition never fabricates one.
    """
    return _payload_event(payload).source_claim_id


def opportunity_transition_idempotency_key(payload: Mapping[str, Any]) -> str:
    """Convenience wrapper delegating to the single idempotency-key helper."""
    return opportunity_lifecycle_transition_idempotency_key(
        _payload_event(payload).event_id
    )


# ---------------------------------------------------------------------------
# Payload construction and validation
# ---------------------------------------------------------------------------


def _require_payload(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise OpportunityPersistenceError("F4 transition payload must be a mapping")
    present = set(payload.keys())
    expected = set(_PAYLOAD_KEYS)
    missing = sorted(expected - present)
    if missing:
        raise OpportunityPersistenceError(
            "F4 transition payload is missing keys: " + ", ".join(missing)
        )
    unknown = sorted(str(key) for key in present - expected)
    if unknown:
        raise OpportunityPersistenceError(
            "F4 transition payload carries unknown keys: " + ", ".join(unknown)
        )
    return payload


def _validate_record(episode: OpportunityEpisode, event: OpportunityLifecycleEvent) -> None:
    """Enforce the valid event/state pairing and lineage agreement.

    Only three pairings exist in v1: OPENED -> ACTIVE, DEFERRED -> DEFERRED and
    EXPIRED -> TERMINAL(EXPIRED). Every other pairing fails closed, the event's
    evaluation instant must agree with the resulting episode's corresponding
    lifecycle instant, and a claim-driven event must carry the episode's own
    source claim while an expiry must not fabricate one.
    """
    if event.episode_id != episode.episode_id:
        raise OpportunityPersistenceError(
            "lifecycle event does not reference the episode it is recorded with"
        )
    if (
        event.lifecycle_version != episode.lifecycle_version
        or event.policy_version != episode.policy_version
    ):
        raise OpportunityPersistenceError(
            "lifecycle event and episode version tokens disagree"
        )

    kind = event.event_type
    state = episode.lifecycle_state

    if kind is OpportunityLifecycleEventType.OPENED:
        if state is not OpportunityLifecycleState.ACTIVE:
            raise OpportunityPersistenceError("OPENED must result in an ACTIVE episode")
        if event.source_claim_id is None or event.source_claim_id != episode.source_claim_id:
            raise OpportunityPersistenceError(
                "OPENED must carry the episode's own source claim"
            )
        if event.evaluation_time != episode.last_evaluation_time:
            raise OpportunityPersistenceError(
                "OPENED evaluation_time must equal the episode's lifecycle instant"
            )
        if event.evaluation_time != episode.claim_evaluation_cutoff:
            raise OpportunityPersistenceError(
                "an OPENED creation must occur at the claim evaluation cutoff"
            )
        return

    if kind is OpportunityLifecycleEventType.DEFERRED:
        if state is not OpportunityLifecycleState.DEFERRED:
            raise OpportunityPersistenceError(
                "DEFERRED must result in a DEFERRED episode"
            )
        if event.source_claim_id is None or event.source_claim_id != episode.source_claim_id:
            raise OpportunityPersistenceError(
                "DEFERRED must carry the episode's own source claim"
            )
        if event.evaluation_time != episode.last_evaluation_time:
            raise OpportunityPersistenceError(
                "DEFERRED evaluation_time must equal the episode's lifecycle instant"
            )
        if event.evaluation_time != episode.claim_evaluation_cutoff:
            raise OpportunityPersistenceError(
                "a DEFERRED creation must occur at the claim evaluation cutoff"
            )
        return

    if kind is OpportunityLifecycleEventType.EXPIRED:
        if state is not OpportunityLifecycleState.TERMINAL:
            raise OpportunityPersistenceError(
                "EXPIRED must result in a TERMINAL episode"
            )
        if episode.terminal_reason is not OpportunityTerminalReason.EXPIRED:
            raise OpportunityPersistenceError(
                "an EXPIRED event requires the EXPIRED terminal reason"
            )
        if event.source_claim_id is not None:
            raise OpportunityPersistenceError(
                "EXPIRED must not fabricate a source claim"
            )
        if event.evaluation_time != episode.terminal_evaluation_time:
            raise OpportunityPersistenceError(
                "EXPIRED evaluation_time must equal the terminal evaluation time"
            )
        if event.evaluation_time != episode.last_evaluation_time:
            raise OpportunityPersistenceError(
                "EXPIRED evaluation_time must equal the episode's lifecycle instant"
            )
        return

    raise OpportunityPersistenceError(f"unsupported lifecycle event type: {kind!r}")


def build_opportunity_transition_payload(
    episode: OpportunityEpisode,
    event: OpportunityLifecycleEvent,
) -> dict[str, Any]:
    """Build and validate the durable payload for one lifecycle transition."""
    if not isinstance(episode, OpportunityEpisode):
        raise OpportunityPersistenceError("episode must be an OpportunityEpisode")
    if not isinstance(event, OpportunityLifecycleEvent):
        raise OpportunityPersistenceError(
            "event must be an OpportunityLifecycleEvent"
        )
    _validate_record(episode, event)
    payload = {
        "record_type": OPPORTUNITY_LIFECYCLE_RECORD_TYPE,
        "schema_version": OPPORTUNITY_LIFECYCLE_TRANSITION_SCHEMA_TOKEN,
        "episode": episode.to_dict(),
        "event": event.to_dict(),
    }
    return validate_opportunity_transition_record(
        OPPORTUNITY_LIFECYCLE_TRANSITION_RECORDED, payload
    )


def validate_opportunity_transition_record(
    event_type: str, payload: Any
) -> dict[str, Any]:
    """Validate one durable F4 transition payload, returning a normalized copy.

    This is the persistence trust boundary: the producer's payload is not
    trusted because it arrived over IPC. The event type must be the registered
    F4 type, the record must carry exactly the canonical keys, the episode and
    event must round-trip through their strict ``from_dict`` constructors, and
    the event/state pairing and lineage must hold. Raises
    ``OpportunityPersistenceError`` on any defect; the canonical writer turns
    that into ``REJECTED``.
    """
    if event_type != OPPORTUNITY_LIFECYCLE_TRANSITION_RECORDED:
        raise OpportunityPersistenceError(
            f"unsupported F4 persistence event type: {event_type!r}"
        )
    body = _require_payload(payload)
    if body["record_type"] != OPPORTUNITY_LIFECYCLE_RECORD_TYPE:
        raise OpportunityPersistenceError("unsupported F4 record_type")
    if body["schema_version"] != OPPORTUNITY_LIFECYCLE_TRANSITION_SCHEMA_TOKEN:
        raise OpportunityPersistenceError("unsupported F4 transition schema token")
    episode = OpportunityEpisode.from_dict(body["episode"])
    event = OpportunityLifecycleEvent.from_dict(body["event"])
    _validate_record(episode, event)
    # Return a payload rebuilt from the reconstituted objects rather than the
    # caller's raw mapping, so the durable bytes and the idempotency comparison
    # are canonical. A producer that submits an equivalent instant as
    # ``+00:00`` or ``.000Z`` is stored in the canonical bare-``Z`` form, so an
    # exact replay reproduces the same bytes and reaches DUPLICATE_OK.
    return {
        "record_type": OPPORTUNITY_LIFECYCLE_RECORD_TYPE,
        "schema_version": OPPORTUNITY_LIFECYCLE_TRANSITION_SCHEMA_TOKEN,
        "episode": episode.to_dict(),
        "event": event.to_dict(),
    }


# ---------------------------------------------------------------------------
# Durable-history reconstruction (restart / incremental)
# ---------------------------------------------------------------------------


def _lineage(episode: OpportunityEpisode) -> tuple[Any, ...]:
    return tuple(getattr(episode, name) for name in _LINEAGE_FIELDS)


def apply_opportunity_transition_record(
    prior_episode: OpportunityEpisode | None,
    payload: Mapping[str, Any],
) -> OpportunityEpisode:
    """Fold one validated durable record onto the prior episode, or refuse.

    Valid histories are exactly: a single OPENED (ACTIVE), a single DEFERRED
    (DEFERRED), or DEFERRED followed by EXPIRED (TERMINAL/EXPIRED). A first
    record that is EXPIRED, a second OPENED or DEFERRED, any transition after a
    terminal state, an ACTIVE->DEFERRED/TERMINAL move, or a lineage/version
    change between records all fail closed. There is no repair heuristic.
    """
    episode = OpportunityEpisode.from_dict(_require_payload(payload)["episode"])
    event = OpportunityLifecycleEvent.from_dict(_require_payload(payload)["event"])
    _validate_record(episode, event)

    if prior_episode is None:
        if event.event_type is OpportunityLifecycleEventType.EXPIRED:
            raise OpportunityPersistenceError(
                "a durable history cannot begin with an EXPIRED transition"
            )
        return episode

    if not isinstance(prior_episode, OpportunityEpisode):
        raise OpportunityPersistenceError("prior episode must be an OpportunityEpisode")

    if prior_episode.episode_id != episode.episode_id:
        raise OpportunityPersistenceError(
            "durable history mixes two episode identities"
        )
    if (
        prior_episode.lifecycle_version != episode.lifecycle_version
        or prior_episode.policy_version != episode.policy_version
        or prior_episode.episode_schema_version != episode.episode_schema_version
    ):
        raise OpportunityPersistenceError(
            "lifecycle/policy/schema version changed mid-episode"
        )
    if _lineage(prior_episode) != _lineage(episode):
        raise OpportunityPersistenceError("durable history mutates episode lineage")

    if prior_episode.lifecycle_state is OpportunityLifecycleState.ACTIVE:
        raise OpportunityPersistenceError(
            "an ACTIVE episode has no valid further lifecycle transition"
        )
    if prior_episode.lifecycle_state is OpportunityLifecycleState.TERMINAL:
        raise OpportunityPersistenceError(
            "a terminal episode cannot advance"
        )

    # prior DEFERRED: the only valid next record is the expiry.
    if event.event_type is not OpportunityLifecycleEventType.EXPIRED:
        raise OpportunityPersistenceError(
            "a DEFERRED episode can only transition to TERMINAL/EXPIRED"
        )
    if (
        prior_episode.defer_deadline != episode.defer_deadline
        or prior_episode.validity_deadline != episode.validity_deadline
    ):
        raise OpportunityPersistenceError(
            "the DEFERRED deadlines were altered by the expiry record"
        )
    return episode


def reconstruct_opportunity_transition_history(
    payloads: Sequence[Mapping[str, Any]],
) -> OpportunityEpisode:
    """Reconstruct the latest episode from one episode's ordered durable records.

    The records must already be in canonical commit order. Any impossible or
    corrupt history fails closed; nothing is repaired or silently dropped.
    """
    if not payloads:
        raise OpportunityPersistenceError("no durable records to reconstruct")
    latest: OpportunityEpisode | None = None
    for payload in payloads:
        latest = apply_opportunity_transition_record(latest, payload)
    if latest is None:
        # Unreachable: a non-empty sequence always yields an episode.
        raise OpportunityPersistenceError("no durable records to reconstruct")
    return latest


# ---------------------------------------------------------------------------
# Typed read / projection model (READ ONLY)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpportunityEpisodeProjection:
    """Read-only projection of one episode's latest persisted F4 state.

    It exposes the latest persisted ``OpportunityEpisode``, the last deterministic
    lifecycle event id, the canonical writer envelope id and sequence position,
    and the history epoch/local sequence. It is a read model only: reading it
    performs no lifecycle evaluation, advances no clock, creates no episode and
    infers nothing. An unknown episode is reported as ``NOT_FOUND``; ACTIVE is
    never invented.
    """

    status: str
    episode_id: str
    episode: OpportunityEpisode | None = None
    lifecycle_state: str | None = None
    last_event_id: str | None = None
    canonical_event_id: str | None = None
    history_epoch: int | None = None
    local_sequence: int | None = None
    event_count: int = 0
    error_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "episode_id": self.episode_id,
            "episode": None if self.episode is None else self.episode.to_dict(),
            "lifecycle_state": self.lifecycle_state,
            "last_event_id": self.last_event_id,
            "canonical_event_id": self.canonical_event_id,
            "history_epoch": self.history_epoch,
            "local_sequence": self.local_sequence,
            "event_count": self.event_count,
            "error_code": self.error_code,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OpportunityEpisodeProjection":
        # Tolerant of a generic error envelope (for example a WriterAck
        # server-error reply, which carries only ``status``/``error_code``): the
        # read model degrades to a typed RETRYABLE projection instead of raising.
        episode = raw.get("episode")
        return cls(
            status=str(raw.get("status") or "RETRYABLE"),
            episode_id=str(raw.get("episode_id") or ""),
            episode=(
                OpportunityEpisode.from_dict(episode)
                if isinstance(episode, Mapping)
                else None
            ),
            lifecycle_state=(
                str(raw["lifecycle_state"])
                if raw.get("lifecycle_state") is not None
                else None
            ),
            last_event_id=(
                str(raw["last_event_id"])
                if raw.get("last_event_id") is not None
                else None
            ),
            canonical_event_id=(
                str(raw["canonical_event_id"])
                if raw.get("canonical_event_id") is not None
                else None
            ),
            history_epoch=(
                int(raw["history_epoch"])
                if raw.get("history_epoch") is not None
                else None
            ),
            local_sequence=(
                int(raw["local_sequence"])
                if raw.get("local_sequence") is not None
                else None
            ),
            event_count=int(raw.get("event_count") or 0),
            error_code=(str(raw["error_code"]) if raw.get("error_code") else None),
            detail=(str(raw["detail"]) if raw.get("detail") else None),
        )


__all__ = [
    "OPPORTUNITY_EPISODE_ID_PREFIX",
    "OPPORTUNITY_LIFECYCLE_EVENT_TYPES",
    "OPPORTUNITY_LIFECYCLE_IDEMPOTENCY_PREFIX",
    "OPPORTUNITY_LIFECYCLE_PRIORITY",
    "OPPORTUNITY_LIFECYCLE_RECORD_TYPE",
    "OPPORTUNITY_LIFECYCLE_STREAM",
    "OPPORTUNITY_LIFECYCLE_TRANSITION_RECORDED",
    "OPPORTUNITY_LIFECYCLE_TRANSITION_SCHEMA_TOKEN",
    "OpportunityEpisodeProjection",
    "OpportunityPersistenceError",
    "apply_opportunity_transition_record",
    "build_opportunity_transition_payload",
    "opportunity_lifecycle_transition_idempotency_key",
    "opportunity_transition_causation_id",
    "opportunity_transition_correlation_id",
    "opportunity_transition_episode_id",
    "opportunity_transition_event_id",
    "opportunity_transition_event_time",
    "opportunity_transition_idempotency_key",
    "reconstruct_opportunity_transition_history",
    "validate_opportunity_transition_record",
]
