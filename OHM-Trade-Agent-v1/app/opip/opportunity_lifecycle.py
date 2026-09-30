"""The pure v1 Opportunity Lifecycle transition surface (R3 F4).

Two pure surfaces cover the two frozen stimulus classes:

* ``apply_claim(claim, prior_episode, evaluation_time, policy, deferral_request=None)``
  is CLAIM-DRIVEN: it binds a frozen F3 ``DetectorClaim`` to an episode, records
  an explicit deferral, or opens a new episode after a terminal one.
* ``evaluate_time(prior_episode, evaluation_time, policy)`` is TIME-DRIVEN: it
  evaluates an episode against its already-recorded deadline at an explicit
  instant, with **no** ``DetectorClaim`` required. Deadline expiry uses this
  surface and never requires or fabricates a claim.

Both surfaces are pure: they read no clock, environment, filesystem, database,
network or global mutable state, and they observe no input outside the supplied
arguments plus the versioned policy bound to this module.

SHADOW / NON-AUTHORITATIVE. The lifecycle is a research artifact. It grants no
trading, admission, allocation, risk, paper or funded authority, it is not wired
into ``run_cycle``, it activates no Feature Bus, and it writes no canonical
evidence. The caller supplies every deadline explicitly; no numeric validity
duration is invented here.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.opip.contracts import detector as detector_vocab
from app.opip.contracts.opportunity import (
    OPPORTUNITY_EPISODE_SCHEMA_VERSION,
    OPPORTUNITY_LIFECYCLE_VERSION,
    OPPORTUNITY_POLICY_VERSION,
    OpportunityContractError,
    OpportunityDeferral,
    OpportunityEpisode,
    OpportunityLifecycleEvent,
    OpportunityLifecycleEventType,
    OpportunityLifecyclePolicy,
    OpportunityLifecycleResult,
    OpportunityLifecycleState,
    OpportunityTerminalReason,
    opportunity_episode_identity,
    opportunity_event_identity,
    require_opportunity_enum,
    require_opportunity_text,
    require_opportunity_utc,
)

#: The applied lifecycle implementation version (deterministic code artifact).
LIFECYCLE_VERSION = OPPORTUNITY_LIFECYCLE_VERSION

#: The applied, versioned lifecycle policy (deterministic code artifact).
POLICY_VERSION = OPPORTUNITY_POLICY_VERSION

#: The episode schema version this surface mints.
EPISODE_SCHEMA_VERSION = OPPORTUNITY_EPISODE_SCHEMA_VERSION

_CLAIM_IDENTITY_FIELDS = (
    "instrument_version_id",
    "venue_instrument_id",
    "detector_version",
    "policy_version",
)


def _require_policy(policy: object) -> OpportunityLifecyclePolicy:
    if not isinstance(policy, OpportunityLifecyclePolicy):
        raise OpportunityContractError(
            "policy must be an OpportunityLifecyclePolicy"
        )
    return policy


def _require_episode(episode: object) -> OpportunityEpisode:
    if not isinstance(episode, OpportunityEpisode):
        raise OpportunityContractError("episode must be an OpportunityEpisode")
    return episode


def _require_episode_policy(
    episode: OpportunityEpisode, policy: OpportunityLifecyclePolicy
) -> None:
    """Re-validate an episode's version binding against the applied policy.

    The episode constructor already binds these fields, but the transition
    surfaces re-check them at the boundary (defense in depth, matching F3) so an
    episode that bypassed construction cannot be advanced under a mismatched
    policy or emit an event carrying an unratified version.
    """
    if (
        episode.episode_schema_version != policy.episode_schema_version
        or episode.lifecycle_version != policy.lifecycle_version
        or episode.policy_version != policy.policy_version
    ):
        raise OpportunityContractError(
            "prior episode versions do not match the applied policy"
        )


def _require_utc_instant(value: object, *, field_name: str) -> datetime:
    return require_opportunity_utc(value, field_name=field_name)


def _validate_claim(claim: object) -> datetime:
    """Validate one eligible F3 IGNITION positive claim. Returns its UTC cutoff."""
    if not isinstance(claim, detector_vocab.DetectorClaim):
        raise OpportunityContractError("claim must be a DetectorClaim")

    family = require_opportunity_enum(
        detector_vocab.DetectorFamily, claim.detector_family, field_name="claim.detector_family"
    )
    if family is not detector_vocab.DetectorFamily.IGNITION:
        raise OpportunityContractError("claim detector_family must be IGNITION")

    phase = require_opportunity_enum(
        detector_vocab.DetectorPhase, claim.phase, field_name="claim.phase"
    )
    transition = require_opportunity_enum(
        detector_vocab.DetectorTransition, claim.transition, field_name="claim.transition"
    )
    if phase is not detector_vocab.DetectorPhase.IGNITION:
        raise OpportunityContractError("claim phase must be IGNITION")
    if transition is not detector_vocab.DetectorTransition.DORMANT_TO_IGNITION:
        raise OpportunityContractError(
            "claim transition must be DORMANT_TO_IGNITION"
        )

    if claim.episode_id is not None:
        raise OpportunityContractError(
            "an eligible F3 claim must not already carry an episode identity"
        )

    for name in _CLAIM_IDENTITY_FIELDS:
        require_opportunity_text(getattr(claim, name), field_name=f"claim.{name}")
    require_opportunity_text(claim.claim_id, field_name="claim.claim_id")
    require_opportunity_text(
        claim.idempotency_key, field_name="claim.idempotency_key"
    )
    require_opportunity_text(claim.snapshot_id, field_name="claim.snapshot_id")
    require_opportunity_text(
        claim.detector_input_fingerprint,
        field_name="claim.detector_input_fingerprint",
    )

    # The claim identity is a pure function of its evidence; a directly
    # constructed claim whose identifiers do not match its own evidence is
    # refused rather than accepted as a valid F4 input.
    expected_id, expected_key = detector_vocab.detector_claim_identity(
        detector_family=family,
        detector_version=claim.detector_version,
        policy_version=claim.policy_version,
        instrument_version_id=claim.instrument_version_id,
        venue_instrument_id=claim.venue_instrument_id,
        transition=transition,
        snapshot_id=claim.snapshot_id,
        detector_input_fingerprint=claim.detector_input_fingerprint,
    )
    if claim.claim_id != expected_id or claim.idempotency_key != expected_key:
        raise OpportunityContractError(
            "claim identity does not match its own evidence; F4 refuses a forged claim"
        )

    return _require_utc_instant(
        claim.evaluation_cutoff, field_name="claim.evaluation_cutoff"
    )


def _build_event(
    *,
    episode: OpportunityEpisode,
    event_type: OpportunityLifecycleEventType,
    evaluation_time: datetime,
    source_claim_id: str | None,
) -> OpportunityLifecycleEvent:
    event_id = opportunity_event_identity(
        episode_id=episode.episode_id,
        event_type=event_type,
        evaluation_time=evaluation_time,
        lifecycle_version=episode.lifecycle_version,
        policy_version=episode.policy_version,
        source_claim_id=source_claim_id,
    )
    return OpportunityLifecycleEvent(
        event_id=event_id,
        event_type=event_type,
        episode_id=episode.episode_id,
        lifecycle_version=episode.lifecycle_version,
        policy_version=episode.policy_version,
        evaluation_time=evaluation_time,
        source_claim_id=source_claim_id,
    )


def _create_episode(
    *,
    claim: detector_vocab.DetectorClaim,
    claim_cutoff: datetime,
    evaluation_time: datetime,
    policy: OpportunityLifecyclePolicy,
    deferral_request: OpportunityDeferral | None,
) -> OpportunityLifecycleResult:
    if evaluation_time != claim_cutoff:
        raise OpportunityContractError(
            "at creation evaluation_time must equal claim.evaluation_cutoff"
        )

    episode_id = opportunity_episode_identity(
        episode_schema_version=policy.episode_schema_version,
        claim_id=claim.claim_id,
    )

    if deferral_request is None:
        state = OpportunityLifecycleState.ACTIVE
        defer_deadline = None
        validity_deadline = None
        event_type = OpportunityLifecycleEventType.OPENED
    else:
        if evaluation_time > deferral_request.defer_deadline:
            raise OpportunityContractError(
                "defer_deadline cannot precede the creation evaluation_time"
            )
        state = OpportunityLifecycleState.DEFERRED
        defer_deadline = deferral_request.defer_deadline
        validity_deadline = deferral_request.validity_deadline
        event_type = OpportunityLifecycleEventType.DEFERRED

    episode = OpportunityEpisode(
        episode_id=episode_id,
        episode_schema_version=policy.episode_schema_version,
        lifecycle_version=policy.lifecycle_version,
        policy_version=policy.policy_version,
        source_claim_id=claim.claim_id,
        source_claim_idempotency_key=claim.idempotency_key,
        instrument_version_id=claim.instrument_version_id,
        venue_instrument_id=claim.venue_instrument_id,
        detector_family=detector_vocab.DetectorFamily.IGNITION,
        detector_version=claim.detector_version,
        detector_policy_version=claim.policy_version,
        claim_transition=detector_vocab.DetectorTransition.DORMANT_TO_IGNITION,
        snapshot_id=claim.snapshot_id,
        detector_input_fingerprint=claim.detector_input_fingerprint,
        claim_evaluation_cutoff=claim_cutoff,
        lifecycle_state=state,
        last_evaluation_time=evaluation_time,
        defer_deadline=defer_deadline,
        validity_deadline=validity_deadline,
        terminal_reason=None,
        terminal_evaluation_time=None,
    )
    event = _build_event(
        episode=episode,
        event_type=event_type,
        evaluation_time=evaluation_time,
        source_claim_id=claim.claim_id,
    )
    return OpportunityLifecycleResult(episode=episode, events=(event,), changed=True)


def apply_claim(
    claim: detector_vocab.DetectorClaim,
    prior_episode: OpportunityEpisode | None,
    evaluation_time: datetime,
    policy: OpportunityLifecyclePolicy,
    deferral_request: OpportunityDeferral | None = None,
) -> OpportunityLifecycleResult:
    """Apply one CLAIM-DRIVEN lifecycle stimulus. Pure and deterministic.

    With no prior episode this creates the episode (``ACTIVE`` without a
    deferral request, ``DEFERRED`` with a valid one), emitting exactly one
    deterministic event. A duplicate delivery of the same claim against its own
    episode is idempotent: unchanged, no event, no evaluation-time change, no
    deadline extension and no expiry. A different claim fails closed while the
    prior episode is unresolved, and opens a new episode once the prior episode
    is terminal.
    """
    policy = _require_policy(policy)
    claim_cutoff = _validate_claim(claim)
    evaluation_time = _require_utc_instant(evaluation_time, field_name="evaluation_time")

    if deferral_request is not None and not isinstance(
        deferral_request, OpportunityDeferral
    ):
        raise OpportunityContractError(
            "deferral_request must be an OpportunityDeferral or None"
        )

    if prior_episode is None:
        return _create_episode(
            claim=claim,
            claim_cutoff=claim_cutoff,
            evaluation_time=evaluation_time,
            policy=policy,
            deferral_request=deferral_request,
        )

    prior = _require_episode(prior_episode)
    _require_episode_policy(prior, policy)

    if prior.source_claim_id == claim.claim_id:
        # At-least-once delivery of the same claim against its own episode is
        # idempotent and never rewrites history, regardless of the delivery
        # instant supplied: no new episode, no event, no deadline extension, no
        # expiry, and the recorded evaluation instant is left untouched. This
        # branch intentionally precedes any time-ordering guard so a restart
        # redelivery at the claim's own cutoff still returns the recorded
        # outcome, including against an already-terminal episode.
        return OpportunityLifecycleResult(episode=prior, events=(), changed=False)

    if prior.lifecycle_state is not OpportunityLifecycleState.TERMINAL:
        raise OpportunityContractError(
            "a different claim cannot bind while the prior episode is unresolved"
        )

    # A new lifecycle cannot be opened before the terminal episode it follows.
    if evaluation_time < prior.last_evaluation_time:
        raise OpportunityContractError(
            "evaluation_time cannot precede the prior episode's terminal evaluation"
        )

    result = _create_episode(
        claim=claim,
        claim_cutoff=claim_cutoff,
        evaluation_time=evaluation_time,
        policy=policy,
        deferral_request=deferral_request,
    )
    if result.episode.episode_id == prior.episode_id:
        raise OpportunityContractError(
            "a new lifecycle requires a new episode identity"
        )
    return result


def evaluate_time(
    prior_episode: OpportunityEpisode,
    evaluation_time: datetime,
    policy: OpportunityLifecyclePolicy,
) -> OpportunityLifecycleResult:
    """Apply one TIME-DRIVEN lifecycle stimulus. Pure and deterministic.

    Requires no ``DetectorClaim``. An ``ACTIVE`` episode is unchanged (no
    invented transition). A ``DEFERRED`` episode before its deadline is
    unchanged. At or after its deadline it terminates with an explicit
    ``EXPIRED`` reason, emitting exactly one deterministic event on the actual
    transition. A terminal episode is unchanged. A time that precedes the
    episode's previously recorded lifecycle evaluation instant fails closed, so
    replay never rewrites history.
    """
    policy = _require_policy(policy)
    episode = _require_episode(prior_episode)
    _require_episode_policy(episode, policy)
    evaluation_time = _require_utc_instant(evaluation_time, field_name="evaluation_time")

    if evaluation_time < episode.last_evaluation_time:
        raise OpportunityContractError(
            "evaluation_time cannot precede the episode's last lifecycle evaluation"
        )

    state = episode.lifecycle_state
    if state is OpportunityLifecycleState.TERMINAL:
        return OpportunityLifecycleResult(episode=episode, events=(), changed=False)

    if state is OpportunityLifecycleState.ACTIVE:
        # An ACTIVE episode carries no deadline, so time alone cannot advance it.
        return OpportunityLifecycleResult(episode=episode, events=(), changed=False)

    # DEFERRED.
    if evaluation_time < episode.defer_deadline:
        return OpportunityLifecycleResult(episode=episode, events=(), changed=False)

    terminal = replace(
        episode,
        lifecycle_state=OpportunityLifecycleState.TERMINAL,
        terminal_reason=OpportunityTerminalReason.EXPIRED,
        terminal_evaluation_time=evaluation_time,
        last_evaluation_time=evaluation_time,
    )
    event = _build_event(
        episode=terminal,
        event_type=OpportunityLifecycleEventType.EXPIRED,
        evaluation_time=evaluation_time,
        source_claim_id=None,
    )
    return OpportunityLifecycleResult(episode=terminal, events=(event,), changed=True)


__all__ = [
    "EPISODE_SCHEMA_VERSION",
    "LIFECYCLE_VERSION",
    "POLICY_VERSION",
    "apply_claim",
    "evaluate_time",
]
