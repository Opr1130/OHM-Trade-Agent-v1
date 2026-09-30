"""Opportunity lifecycle vocabulary and typed episode/event contracts (R3 F4).

This is the shared, pure vocabulary for the second R3 slice: the F4 Opportunity
Lifecycle over frozen F3 ``DetectorClaim`` evidence. It owns the lifecycle
state and terminal-reason tokens, the claim-driven deferral input, the versioned
lifecycle policy, the immutable ``OpportunityEpisode`` lineage record and the
deterministic lifecycle event / result types.

Boundaries that are deliberate and enforced here:

* ``episode_id`` is a deterministic function of the episode schema version and
  the source ``DetectorClaim.claim_id`` only. No wall clock, UUID, process
  identity, invocation order, retry count or database sequence takes part, and a
  directly constructed episode whose identity does not match its own claim
  lineage is refused rather than accepted as a valid-looking episode.
* Lifecycle states are exactly ``ACTIVE``/``DEFERRED``/``TERMINAL``. Detector
  phases such as ``DORMANT``/``IGNITION`` are never episode states.
* The only v1 terminal reason is ``EXPIRED``. It is deliberately distinct from
  funnel/scan reason codes, forecast timeout, order-fill, TARGET/STOP/risk-exit,
  protection, cancellation and learning vocabularies.
* Deadlines are caller-supplied explicit UTC instants. This module invents no
  numeric validity duration; it only enforces well-ordering
  (``evaluation_time <= defer_deadline <= validity_deadline`` at deferral time).

Nothing here reads a clock, the environment, the filesystem or the network, and
nothing here grants trading, admission, paper or risk authority.

SHADOW / NON-AUTHORITATIVE. This vocabulary is a research artifact. It is not
wired into ``run_cycle``, it activates no Feature Bus, and it writes no
canonical evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from app.opip.contracts import detector as detector_vocab
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import TemporalIntegrityError, require_utc

#: Schema version of the durable ``OpportunityEpisode`` record.
OPPORTUNITY_EPISODE_SCHEMA_VERSION = "opportunity-episode-v1"

#: Applied lifecycle implementation version. A deterministic code artifact.
OPPORTUNITY_LIFECYCLE_VERSION = "opportunity-lifecycle-v1"

#: Applied lifecycle policy version. A deterministic, replayable code artifact
#: tagged by this token; it is not a caller-selected input.
OPPORTUNITY_POLICY_VERSION = "opportunity-shadow-policy-v1"

#: Semantic prefix of the deterministic ``OPEP:`` episode identity.
EPISODE_ID_PREFIX = "OPEP"

#: Semantic prefix of the deterministic ``OPEV:`` lifecycle-event identity.
EVENT_ID_PREFIX = "OPEV"


class OpportunityContractError(ValueError):
    """A structural contract violation. Always fails closed."""


class OpportunityLifecycleState(str, Enum):
    """The v1 episode lifecycle states. There is no fourth state."""

    ACTIVE = "ACTIVE"
    DEFERRED = "DEFERRED"
    TERMINAL = "TERMINAL"


class OpportunityTerminalReason(str, Enum):
    """The canonical v1 F4 episode terminal vocabulary.

    Only deadline expiry exists in v1. Funnel/scan reason codes, forecast
    timeouts, order/fill dispositions, TARGET/STOP, RISK_EXIT, protection,
    cancellation and learning labels are deliberately absent.
    """

    EXPIRED = "EXPIRED"


class OpportunityLifecycleEventType(str, Enum):
    """The lifecycle events emitted by the v1 transition surface.

    Exactly one event is emitted when an episode is created (``OPENED`` for a
    creation into ``ACTIVE``, ``DEFERRED`` for a creation directly into
    ``DEFERRED``) and exactly one ``EXPIRED`` event is emitted on the actual
    deferral-to-terminal expiry transition.
    """

    OPENED = "OPENED"
    DEFERRED = "DEFERRED"
    EXPIRED = "EXPIRED"


def require_opportunity_enum(
    enum_type: type[Enum], value: Any, *, field_name: str
) -> Any:
    """Coerce one enum token strictly; reject malformed or unsupported tokens.

    Accepts an existing member or its exact string token. Rejects bools,
    numbers and unknown strings, so a mistyped durable token is refused rather
    than silently substituted.
    """
    if isinstance(value, enum_type):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise OpportunityContractError(
            f"{field_name} must be a {enum_type.__name__} token"
        )
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            raise OpportunityContractError(
                f"{field_name} has an unsupported token: {value!r}"
            ) from exc
    raise OpportunityContractError(f"{field_name} must be a {enum_type.__name__} token")


def require_opportunity_text(value: Any, *, field_name: str) -> str:
    """Validate one required canonical text token.

    A number or bool is never coerced into text, and a token with surrounding
    whitespace is refused so it cannot silently diverge from its digest.
    """
    if not isinstance(value, str):
        raise OpportunityContractError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise OpportunityContractError(
            f"{field_name} must be a non-empty, whitespace-free token"
        )
    return value


def require_opportunity_utc(value: Any, *, field_name: str) -> datetime:
    """Validate one required explicit UTC instant (naive/malformed fails closed)."""
    if not isinstance(value, datetime):
        raise OpportunityContractError(f"{field_name} must be an explicit datetime")
    try:
        return require_utc(value, field_name=field_name)
    except TemporalIntegrityError as exc:
        raise OpportunityContractError(str(exc)) from exc


def _require_optional_utc(value: Any, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    return require_opportunity_utc(value, field_name=field_name)


def opportunity_episode_identity(
    *,
    episode_schema_version: str = OPPORTUNITY_EPISODE_SCHEMA_VERSION,
    claim_id: str,
) -> str:
    """The deterministic ``OPEP:<digest>`` episode identity for one claim.

    Identity is a pure function of the episode schema version and the source
    ``DetectorClaim.claim_id``. It reuses the existing canonical serialization
    and ``stable_hash`` helpers; no new hashing framework is introduced. Because
    the claim identity already binds the detector family/version, policy
    version, instrument, transition and sealed snapshot evidence, no wall clock,
    UUID, process identity, invocation order or retry count can take part.
    """
    schema = require_opportunity_text(
        episode_schema_version, field_name="episode_schema_version"
    )
    claim = require_opportunity_text(claim_id, field_name="claim_id")
    return stable_hash(
        EPISODE_ID_PREFIX,
        {"episode_schema_version": schema, "claim_id": claim},
    )


def opportunity_event_identity(
    *,
    episode_id: str,
    event_type: OpportunityLifecycleEventType | str,
    evaluation_time: datetime,
    lifecycle_version: str = OPPORTUNITY_LIFECYCLE_VERSION,
    policy_version: str = OPPORTUNITY_POLICY_VERSION,
    source_claim_id: str | None = None,
) -> str:
    """The deterministic ``OPEV:<digest>`` identity of one lifecycle event.

    Identity binds the episode, the event type, the explicit evaluation instant,
    the lifecycle/policy versions and - where applicable - the source claim. It
    contains no random UUID and no hidden timestamp, so re-deriving the same
    transition yields the same event id and an unchanged evaluation emits none.
    """
    kind = require_opportunity_enum(
        OpportunityLifecycleEventType, event_type, field_name="event_type"
    )
    instant = require_opportunity_utc(evaluation_time, field_name="evaluation_time")
    payload = {
        "episode_id": require_opportunity_text(episode_id, field_name="episode_id"),
        "event_type": kind.value,
        "evaluation_time": iso_z(instant, field_name="evaluation_time"),
        "lifecycle_version": require_opportunity_text(
            lifecycle_version, field_name="lifecycle_version"
        ),
        "policy_version": require_opportunity_text(
            policy_version, field_name="policy_version"
        ),
        "source_claim_id": (
            None
            if source_claim_id is None
            else require_opportunity_text(source_claim_id, field_name="source_claim_id")
        ),
    }
    return stable_hash(EVENT_ID_PREFIX, payload)


@dataclass(frozen=True)
class OpportunityDeferral:
    """An explicit F4 deferral request carried on a claim-driven creation.

    Both deadlines are caller-supplied explicit UTC instants. The deferral
    deadline is bounded by the validity deadline; the creation evaluation time
    is additionally checked against the deferral deadline by the transition
    surface. This type invents no numeric duration.
    """

    defer_deadline: datetime
    validity_deadline: datetime

    def __post_init__(self) -> None:
        defer = require_opportunity_utc(self.defer_deadline, field_name="defer_deadline")
        validity = require_opportunity_utc(
            self.validity_deadline, field_name="validity_deadline"
        )
        if defer > validity:
            raise OpportunityContractError(
                "defer_deadline must not exceed validity_deadline"
            )
        object.__setattr__(self, "defer_deadline", defer)
        object.__setattr__(self, "validity_deadline", validity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "defer_deadline": iso_z(self.defer_deadline, field_name="defer_deadline"),
            "validity_deadline": iso_z(
                self.validity_deadline, field_name="validity_deadline"
            ),
        }


@dataclass(frozen=True)
class OpportunityLifecyclePolicy:
    """The applied, versioned lifecycle policy.

    The policy is a deterministic code artifact. A policy that does not carry
    the ratified version tokens is refused rather than silently substituted, so
    a caller cannot select an unratified policy.
    """

    episode_schema_version: str = OPPORTUNITY_EPISODE_SCHEMA_VERSION
    lifecycle_version: str = OPPORTUNITY_LIFECYCLE_VERSION
    policy_version: str = OPPORTUNITY_POLICY_VERSION

    def __post_init__(self) -> None:
        for name, expected in (
            ("episode_schema_version", OPPORTUNITY_EPISODE_SCHEMA_VERSION),
            ("lifecycle_version", OPPORTUNITY_LIFECYCLE_VERSION),
            ("policy_version", OPPORTUNITY_POLICY_VERSION),
        ):
            value = require_opportunity_text(getattr(self, name), field_name=name)
            if value != expected:
                raise OpportunityContractError(
                    f"{name} is not the ratified {expected}"
                )
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_schema_version": self.episode_schema_version,
            "lifecycle_version": self.lifecycle_version,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class OpportunityEpisode:
    """One immutable opportunity episode and its claim-derived lineage.

    Lineage reuses the identities already carried by the frozen F3
    ``DetectorClaim``; no FeatureSnapshot payload is copied. The episode is
    immutable: a terminal episode is never reopened and an unchanged evaluation
    returns the same episode object rather than rewriting history.
    """

    episode_id: str
    episode_schema_version: str
    lifecycle_version: str
    policy_version: str
    source_claim_id: str
    source_claim_idempotency_key: str
    instrument_version_id: str
    venue_instrument_id: str
    detector_family: detector_vocab.DetectorFamily
    detector_version: str
    detector_policy_version: str
    claim_transition: detector_vocab.DetectorTransition
    snapshot_id: str
    detector_input_fingerprint: str
    claim_evaluation_cutoff: datetime
    lifecycle_state: OpportunityLifecycleState
    last_evaluation_time: datetime
    defer_deadline: datetime | None = None
    validity_deadline: datetime | None = None
    terminal_reason: OpportunityTerminalReason | None = None
    terminal_evaluation_time: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "episode_id",
            "episode_schema_version",
            "lifecycle_version",
            "policy_version",
            "source_claim_id",
            "source_claim_idempotency_key",
            "instrument_version_id",
            "venue_instrument_id",
            "detector_version",
            "detector_policy_version",
            "snapshot_id",
            "detector_input_fingerprint",
        ):
            object.__setattr__(
                self, name, require_opportunity_text(getattr(self, name), field_name=name)
            )

        # The episode's version fields are bound to the ratified code artifacts,
        # so a reconstituted episode carrying an unratified schema/lifecycle/
        # policy tag is refused rather than accepted and later advanced.
        for name, expected in (
            ("episode_schema_version", OPPORTUNITY_EPISODE_SCHEMA_VERSION),
            ("lifecycle_version", OPPORTUNITY_LIFECYCLE_VERSION),
            ("policy_version", OPPORTUNITY_POLICY_VERSION),
        ):
            if getattr(self, name) != expected:
                raise OpportunityContractError(
                    f"{name} is not the ratified {expected}"
                )

        family = require_opportunity_enum(
            detector_vocab.DetectorFamily, self.detector_family, field_name="detector_family"
        )
        if family is not detector_vocab.DetectorFamily.IGNITION:
            raise OpportunityContractError("detector_family must be IGNITION")
        object.__setattr__(self, "detector_family", family)

        transition = require_opportunity_enum(
            detector_vocab.DetectorTransition, self.claim_transition, field_name="claim_transition"
        )
        if transition is not detector_vocab.DetectorTransition.DORMANT_TO_IGNITION:
            raise OpportunityContractError(
                "claim_transition must be DORMANT_TO_IGNITION"
            )
        object.__setattr__(self, "claim_transition", transition)

        state = require_opportunity_enum(
            OpportunityLifecycleState, self.lifecycle_state, field_name="lifecycle_state"
        )
        object.__setattr__(self, "lifecycle_state", state)

        object.__setattr__(
            self,
            "claim_evaluation_cutoff",
            require_opportunity_utc(
                self.claim_evaluation_cutoff, field_name="claim_evaluation_cutoff"
            ),
        )
        object.__setattr__(
            self,
            "last_evaluation_time",
            require_opportunity_utc(
                self.last_evaluation_time, field_name="last_evaluation_time"
            ),
        )
        object.__setattr__(
            self,
            "defer_deadline",
            _require_optional_utc(self.defer_deadline, field_name="defer_deadline"),
        )
        object.__setattr__(
            self,
            "validity_deadline",
            _require_optional_utc(
                self.validity_deadline, field_name="validity_deadline"
            ),
        )
        object.__setattr__(
            self,
            "terminal_evaluation_time",
            _require_optional_utc(
                self.terminal_evaluation_time, field_name="terminal_evaluation_time"
            ),
        )

        reason = self.terminal_reason
        if reason is not None:
            reason = require_opportunity_enum(
                OpportunityTerminalReason, reason, field_name="terminal_reason"
            )
        object.__setattr__(self, "terminal_reason", reason)

        self._validate_state_invariants()
        self._validate_identity()

    def _validate_state_invariants(self) -> None:
        state = self.lifecycle_state
        # Applies to every state, including ACTIVE.
        if self.last_evaluation_time < self.claim_evaluation_cutoff:
            raise OpportunityContractError(
                "last_evaluation_time cannot precede the claim evaluation cutoff"
            )
        if state is OpportunityLifecycleState.ACTIVE:
            if (
                self.defer_deadline is not None
                or self.validity_deadline is not None
                or self.terminal_reason is not None
                or self.terminal_evaluation_time is not None
            ):
                raise OpportunityContractError(
                    "an ACTIVE episode carries no deferral deadline and no terminal reason"
                )
            return

        if self.defer_deadline is None or self.validity_deadline is None:
            raise OpportunityContractError(
                "a DEFERRED or TERMINAL episode requires explicit deadlines"
            )
        if self.defer_deadline > self.validity_deadline:
            raise OpportunityContractError(
                "defer_deadline must not exceed validity_deadline"
            )

        if state is OpportunityLifecycleState.DEFERRED:
            if self.last_evaluation_time > self.defer_deadline:
                raise OpportunityContractError(
                    "a DEFERRED episode's last evaluation cannot follow its deadline"
                )
            if self.terminal_reason is not None or self.terminal_evaluation_time is not None:
                raise OpportunityContractError(
                    "a DEFERRED episode carries no terminal reason"
                )
            return

        # TERMINAL
        if self.terminal_reason is not OpportunityTerminalReason.EXPIRED:
            raise OpportunityContractError(
                "the only v1 terminal reason is EXPIRED"
            )
        if self.terminal_evaluation_time is None:
            raise OpportunityContractError(
                "a TERMINAL episode requires a terminal evaluation time"
            )
        if self.terminal_evaluation_time < self.defer_deadline:
            raise OpportunityContractError(
                "an EXPIRED episode cannot terminate before its deadline"
            )
        if self.last_evaluation_time != self.terminal_evaluation_time:
            raise OpportunityContractError(
                "a TERMINAL episode's last evaluation is its terminal evaluation"
            )

    def _validate_identity(self) -> None:
        expected = opportunity_episode_identity(
            episode_schema_version=self.episode_schema_version,
            claim_id=self.source_claim_id,
        )
        if self.episode_id != expected:
            raise OpportunityContractError(
                "episode_id does not match its claim lineage; build episodes with "
                "the lifecycle transition surface"
            )

        # The episode's copied detector/evidence fields must reproduce the F3
        # claim identity, so a reconstructed episode cannot carry contradictory
        # claim provenance.
        expected_claim_id, expected_key = detector_vocab.detector_claim_identity(
            detector_family=self.detector_family,
            detector_version=self.detector_version,
            policy_version=self.detector_policy_version,
            instrument_version_id=self.instrument_version_id,
            venue_instrument_id=self.venue_instrument_id,
            transition=self.claim_transition,
            snapshot_id=self.snapshot_id,
            detector_input_fingerprint=self.detector_input_fingerprint,
        )
        if (
            self.source_claim_id != expected_claim_id
            or self.source_claim_idempotency_key != expected_key
        ):
            raise OpportunityContractError(
                "episode claim lineage does not match its source claim identity"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "episode_schema_version": self.episode_schema_version,
            "lifecycle_version": self.lifecycle_version,
            "policy_version": self.policy_version,
            "source_claim_id": self.source_claim_id,
            "source_claim_idempotency_key": self.source_claim_idempotency_key,
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "detector_family": self.detector_family.value,
            "detector_version": self.detector_version,
            "detector_policy_version": self.detector_policy_version,
            "claim_transition": self.claim_transition.value,
            "snapshot_id": self.snapshot_id,
            "detector_input_fingerprint": self.detector_input_fingerprint,
            "claim_evaluation_cutoff": iso_z(
                self.claim_evaluation_cutoff, field_name="claim_evaluation_cutoff"
            ),
            "lifecycle_state": self.lifecycle_state.value,
            "last_evaluation_time": iso_z(
                self.last_evaluation_time, field_name="last_evaluation_time"
            ),
            "defer_deadline": (
                None
                if self.defer_deadline is None
                else iso_z(self.defer_deadline, field_name="defer_deadline")
            ),
            "validity_deadline": (
                None
                if self.validity_deadline is None
                else iso_z(self.validity_deadline, field_name="validity_deadline")
            ),
            "terminal_reason": (
                None if self.terminal_reason is None else self.terminal_reason.value
            ),
            "terminal_evaluation_time": (
                None
                if self.terminal_evaluation_time is None
                else iso_z(
                    self.terminal_evaluation_time,
                    field_name="terminal_evaluation_time",
                )
            ),
        }


@dataclass(frozen=True)
class OpportunityLifecycleEvent:
    """One immutable, deterministic lifecycle event."""

    event_id: str
    event_type: OpportunityLifecycleEventType
    episode_id: str
    lifecycle_version: str
    policy_version: str
    evaluation_time: datetime
    source_claim_id: str | None = None

    def __post_init__(self) -> None:
        kind = require_opportunity_enum(
            OpportunityLifecycleEventType, self.event_type, field_name="event_type"
        )
        object.__setattr__(self, "event_type", kind)
        for name in ("event_id", "episode_id", "lifecycle_version", "policy_version"):
            object.__setattr__(
                self, name, require_opportunity_text(getattr(self, name), field_name=name)
            )
        if self.source_claim_id is not None:
            object.__setattr__(
                self,
                "source_claim_id",
                require_opportunity_text(
                    self.source_claim_id, field_name="source_claim_id"
                ),
            )
        instant = require_opportunity_utc(
            self.evaluation_time, field_name="evaluation_time"
        )
        object.__setattr__(self, "evaluation_time", instant)

        expected = opportunity_event_identity(
            episode_id=self.episode_id,
            event_type=self.event_type,
            evaluation_time=instant,
            lifecycle_version=self.lifecycle_version,
            policy_version=self.policy_version,
            source_claim_id=self.source_claim_id,
        )
        if self.event_id != expected:
            raise OpportunityContractError(
                "event_id does not match its transition; events are produced by the "
                "lifecycle transition surface"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "episode_id": self.episode_id,
            "lifecycle_version": self.lifecycle_version,
            "policy_version": self.policy_version,
            "evaluation_time": iso_z(
                self.evaluation_time, field_name="evaluation_time"
            ),
            "source_claim_id": self.source_claim_id,
        }


@dataclass(frozen=True)
class OpportunityLifecycleResult:
    """The outcome of one pure lifecycle transition.

    ``changed`` is ``True`` only when a new episode was created or an actual
    transition occurred; an unchanged duplicate, pre-deadline evaluation or
    terminal replay returns the prior episode with no events and
    ``changed=False``.
    """

    episode: OpportunityEpisode
    events: tuple[OpportunityLifecycleEvent, ...] = ()
    changed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.episode, OpportunityEpisode):
            raise OpportunityContractError("result episode must be an OpportunityEpisode")
        if not isinstance(self.changed, bool):
            raise OpportunityContractError("result changed must be a bool")
        events = tuple(self.events)
        for event in events:
            if not isinstance(event, OpportunityLifecycleEvent):
                raise OpportunityContractError(
                    "result events must be OpportunityLifecycleEvent values"
                )
            if event.episode_id != self.episode.episode_id:
                raise OpportunityContractError(
                    "every event must reference the result episode"
                )
        object.__setattr__(self, "events", events)
        if events and not self.changed:
            raise OpportunityContractError(
                "a result that emits events must report changed=True"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode": self.episode.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "changed": self.changed,
        }


__all__ = [
    "EPISODE_ID_PREFIX",
    "EVENT_ID_PREFIX",
    "OPPORTUNITY_EPISODE_SCHEMA_VERSION",
    "OPPORTUNITY_LIFECYCLE_VERSION",
    "OPPORTUNITY_POLICY_VERSION",
    "OpportunityContractError",
    "OpportunityDeferral",
    "OpportunityEpisode",
    "OpportunityLifecycleEvent",
    "OpportunityLifecycleEventType",
    "OpportunityLifecyclePolicy",
    "OpportunityLifecycleResult",
    "OpportunityLifecycleState",
    "OpportunityTerminalReason",
    "opportunity_episode_identity",
    "opportunity_event_identity",
    "require_opportunity_enum",
    "require_opportunity_text",
    "require_opportunity_utc",
]
