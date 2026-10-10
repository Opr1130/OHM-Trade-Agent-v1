"""R5-A opportunity-phase projection onto the existing canonical F4 lifecycle.

Adopted authority: `docs/architecture/v1.5.0/ARCHITECTURE.md` section 5
(`LC-01`-`LC-03`), section 10's episode lineage and the R5-0 structural contract
`SC-05`. The clauses state that the adopted logical lifecycle states are
*logical state contracts to map onto existing F4 enums during contract freeze*,
and that they are not instructions to create parallel state machines or replace
canonical identifiers.

This module therefore does exactly three things:

* It declares the R5-A logical phases as a **non-durable research projection**.
* It declares, in one frozen table, which canonical F4 record each phase maps
  onto, which logical phases have no canonical record in v1 (so nothing invents
  one), and that no phase carries downstream authority.
* It supplies the missing F4 *transition surface* for the one transition F3 can
  legally request - `DORMANT -> IGNITION` - building canonical
  `OpportunityEpisode`/`OpportunityLifecycleEvent` records through the existing
  canonical identity functions.

It introduces no second episode record, no new terminal vocabulary, no store and
no selection, reservation, sizing or execution authority: `SELECTED` and
`RESERVED` are declared downstream-only and this module refuses them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from app.opip.contracts.detector import DetectorClaim
from app.opip.contracts.opportunity import (
    OPPORTUNITY_EPISODE_SCHEMA_VERSION,
    OPPORTUNITY_LIFECYCLE_VERSION,
    OPPORTUNITY_POLICY_VERSION,
    OpportunityContractError,
    OpportunityDeferral,
    OpportunityEpisode,
    OpportunityLifecycleEvent,
    OpportunityLifecycleEventType,
    OpportunityLifecycleResult,
    OpportunityLifecycleState,
    OpportunityTerminalReason,
    opportunity_episode_identity,
    opportunity_event_identity,
    require_opportunity_text,
    require_opportunity_utc,
)

#: Schema version of the R5-A phase projection. Research evidence only.
OPPORTUNITY_PHASE_SCHEMA_VERSION = "opportunity-phase-projection-v1"

#: The phases R5-A itself may reach.
R5A_REACHABLE_PHASES: tuple[str, ...] = (
    "OBSERVED",
    "QUALIFYING",
    "ELIGIBLE",
    "WAIT",
    "REJECTED",
    "EXPIRED",
    "SUPERSEDED",
)

#: Phases owned by later increments. R5-A may never emit one.
DOWNSTREAM_ONLY_PHASES: tuple[str, ...] = ("SELECTED", "RESERVED")

#: Every R5-A phase carries zero downstream authority.
PHASE_AUTHORITY = "NONE"


class PhaseContractError(OpportunityContractError):
    """A phase-projection contract violation. Always fails closed."""


class OpportunityPhase(str, Enum):
    """The R5-A logical opportunity phases. Not durable state."""

    OBSERVED = "OBSERVED"
    QUALIFYING = "QUALIFYING"
    ELIGIBLE = "ELIGIBLE"
    WAIT = "WAIT"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class OpportunityPhaseMapping:
    """One row of the phase-to-F4-canonical table."""

    phase: OpportunityPhase
    has_canonical_record: bool
    f4_state: OpportunityLifecycleState | None
    terminal_reason: OpportunityTerminalReason | None
    requires_deferral_deadline: bool
    downstream_authority: str = PHASE_AUTHORITY
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.phase, OpportunityPhase):
            raise PhaseContractError("phase must be an OpportunityPhase")
        if not isinstance(self.has_canonical_record, bool):
            raise PhaseContractError("has_canonical_record must be a bool")
        if self.has_canonical_record:
            if not isinstance(self.f4_state, OpportunityLifecycleState):
                raise PhaseContractError(
                    "a canonical phase requires an OpportunityLifecycleState"
                )
        elif self.f4_state is not None or self.terminal_reason is not None:
            raise PhaseContractError(
                "a phase without a canonical record carries no F4 state or reason"
            )
        if self.terminal_reason is not None and (
            self.terminal_reason is not OpportunityTerminalReason.EXPIRED
        ):
            raise PhaseContractError("the only v1 terminal reason is EXPIRED")
        if not isinstance(self.requires_deferral_deadline, bool):
            raise PhaseContractError("requires_deferral_deadline must be a bool")
        if self.requires_deferral_deadline and (
            self.f4_state is not OpportunityLifecycleState.DEFERRED
        ):
            raise PhaseContractError(
                "only a DEFERRED mapping may require a deferral deadline"
            )
        if self.downstream_authority != PHASE_AUTHORITY:
            raise PhaseContractError("no R5-A phase may carry downstream authority")
        object.__setattr__(
            self, "note", require_opportunity_text(self.note, field_name="note")
        )


#: The single frozen mapping table. Every R5-A phase appears exactly once.
PHASE_MAPPINGS: Mapping[OpportunityPhase, OpportunityPhaseMapping] = MappingProxyType(
    {
        OpportunityPhase.OBSERVED: OpportunityPhaseMapping(
            phase=OpportunityPhase.OBSERVED,
            has_canonical_record=False,
            f4_state=None,
            terminal_reason=None,
            requires_deferral_deadline=False,
            note="pre-canonical observation; F4 opens a record only on a detector claim",
        ),
        OpportunityPhase.QUALIFYING: OpportunityPhaseMapping(
            phase=OpportunityPhase.QUALIFYING,
            has_canonical_record=False,
            f4_state=None,
            terminal_reason=None,
            requires_deferral_deadline=False,
            note="pre-canonical evaluation; no episode exists yet",
        ),
        OpportunityPhase.ELIGIBLE: OpportunityPhaseMapping(
            phase=OpportunityPhase.ELIGIBLE,
            has_canonical_record=True,
            f4_state=OpportunityLifecycleState.ACTIVE,
            terminal_reason=None,
            requires_deferral_deadline=False,
            note="an open claim-derived episode",
        ),
        OpportunityPhase.WAIT: OpportunityPhaseMapping(
            phase=OpportunityPhase.WAIT,
            has_canonical_record=True,
            f4_state=OpportunityLifecycleState.DEFERRED,
            terminal_reason=None,
            requires_deferral_deadline=True,
            note="bounded deferral; the deadline is caller-supplied, never invented",
        ),
        OpportunityPhase.REJECTED: OpportunityPhaseMapping(
            phase=OpportunityPhase.REJECTED,
            has_canonical_record=False,
            f4_state=None,
            terminal_reason=None,
            requires_deferral_deadline=False,
            note="research evidence only; v1 F4 opens no record for a rejected claim",
        ),
        OpportunityPhase.EXPIRED: OpportunityPhaseMapping(
            phase=OpportunityPhase.EXPIRED,
            has_canonical_record=True,
            f4_state=OpportunityLifecycleState.TERMINAL,
            terminal_reason=OpportunityTerminalReason.EXPIRED,
            requires_deferral_deadline=False,
            note="the only v1 terminal transition",
        ),
        OpportunityPhase.SUPERSEDED: OpportunityPhaseMapping(
            phase=OpportunityPhase.SUPERSEDED,
            has_canonical_record=False,
            f4_state=None,
            terminal_reason=None,
            requires_deferral_deadline=False,
            note=(
                "supersession has no v1 canonical token; it needs a linked new "
                "decision and remains an owner-gated later increment"
            ),
        ),
    }
)


def project_phase(phase: OpportunityPhase | str) -> OpportunityPhaseMapping:
    """Return the frozen mapping row for one R5-A phase.

    A downstream-only token (`SELECTED`, `RESERVED`) is refused rather than
    silently mapped, because R5-A holds no selection or reservation authority.
    """

    if isinstance(phase, OpportunityPhase):
        resolved = phase
    elif isinstance(phase, str):
        if phase in DOWNSTREAM_ONLY_PHASES:
            raise PhaseContractError(
                f"{phase} is downstream-only and unreachable from R5-A"
            )
        try:
            resolved = OpportunityPhase(phase)
        except ValueError as exc:
            raise PhaseContractError(
                f"phase has an unsupported token: {phase!r}"
            ) from exc
    else:
        raise PhaseContractError("phase must be an OpportunityPhase or its token")
    return PHASE_MAPPINGS[resolved]


def episode_phase(
    episode: OpportunityEpisode, *, evaluation_time: datetime
) -> OpportunityPhase:
    """Project one canonical episode onto its R5-A phase.

    A terminal episode projects to `EXPIRED` and an expired claim never resumes:
    there is no path from this function back to `ELIGIBLE`.
    """

    if not isinstance(episode, OpportunityEpisode):
        raise PhaseContractError("episode must be an OpportunityEpisode")
    instant = require_opportunity_utc(evaluation_time, field_name="evaluation_time")
    if episode.lifecycle_state is OpportunityLifecycleState.TERMINAL:
        return OpportunityPhase.EXPIRED
    if episode.lifecycle_state is OpportunityLifecycleState.DEFERRED:
        if episode.defer_deadline is not None and instant >= episode.defer_deadline:
            # The deadline has passed; the canonical next transition is expiry,
            # not an extension, so the projection must not report WAIT.
            return OpportunityPhase.EXPIRED
        return OpportunityPhase.WAIT
    return OpportunityPhase.ELIGIBLE


@dataclass(frozen=True)
class EpisodeRequalification:
    """A fresh, linked requalification of one existing episode.

    The episode identity is preserved and the clock is not reset: this record
    links a new decision to the existing episode at an explicit evaluation time,
    which is what `HZ-02` and the adopted lifecycle require instead of relabelling
    or reopening history.
    """

    episode_id: str
    prior_decision_id: str | None
    new_decision_id: str
    evidence_cutoff: datetime
    evaluation_time: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "episode_id",
            require_opportunity_text(self.episode_id, field_name="episode_id"),
        )
        if self.prior_decision_id is not None:
            object.__setattr__(
                self,
                "prior_decision_id",
                require_opportunity_text(
                    self.prior_decision_id, field_name="prior_decision_id"
                ),
            )
        object.__setattr__(
            self,
            "new_decision_id",
            require_opportunity_text(self.new_decision_id, field_name="new_decision_id"),
        )
        cutoff = require_opportunity_utc(
            self.evidence_cutoff, field_name="evidence_cutoff"
        )
        instant = require_opportunity_utc(
            self.evaluation_time, field_name="evaluation_time"
        )
        if instant < cutoff:
            raise PhaseContractError(
                "requalification cannot precede the evidence cutoff it cites"
            )
        if self.prior_decision_id is not None and (
            self.prior_decision_id == self.new_decision_id
        ):
            raise PhaseContractError(
                "a requalification requires a new decision identity"
            )
        object.__setattr__(self, "evidence_cutoff", cutoff)
        object.__setattr__(self, "evaluation_time", instant)

    @property
    def downstream_authority(self) -> str:
        return PHASE_AUTHORITY


def episode_from_claim(
    claim: DetectorClaim,
    *,
    evaluation_time: datetime,
    deferral: OpportunityDeferral | None = None,
) -> OpportunityLifecycleResult:
    """Open the canonical F4 episode for a `DORMANT -> IGNITION` claim.

    An `ACTIVE` episode is opened unless an explicit, caller-supplied deferral is
    provided, in which case the episode opens `DEFERRED` with a bounded deadline.
    No duration is invented: a deferral without deadlines is impossible because
    `OpportunityDeferral` requires both instants.
    """

    if not isinstance(claim, DetectorClaim):
        raise PhaseContractError("claim must be a DetectorClaim")
    instant = require_opportunity_utc(evaluation_time, field_name="evaluation_time")
    if instant < claim.evaluation_cutoff:
        raise PhaseContractError(
            "evaluation_time cannot precede the claim evaluation cutoff"
        )
    episode_id = opportunity_episode_identity(claim_id=claim.claim_id)
    state = (
        OpportunityLifecycleState.ACTIVE
        if deferral is None
        else OpportunityLifecycleState.DEFERRED
    )
    if deferral is not None and instant > deferral.defer_deadline:
        raise PhaseContractError(
            "an episode cannot open deferred after its deferral deadline"
        )
    episode = OpportunityEpisode(
        episode_id=episode_id,
        episode_schema_version=OPPORTUNITY_EPISODE_SCHEMA_VERSION,
        lifecycle_version=OPPORTUNITY_LIFECYCLE_VERSION,
        policy_version=OPPORTUNITY_POLICY_VERSION,
        source_claim_id=claim.claim_id,
        source_claim_idempotency_key=claim.idempotency_key,
        instrument_version_id=claim.instrument_version_id,
        venue_instrument_id=claim.venue_instrument_id,
        detector_family=claim.detector_family,
        detector_version=claim.detector_version,
        detector_policy_version=claim.policy_version,
        claim_transition=claim.transition,
        snapshot_id=claim.snapshot_id,
        detector_input_fingerprint=claim.detector_input_fingerprint,
        claim_evaluation_cutoff=claim.evaluation_cutoff,
        lifecycle_state=state,
        last_evaluation_time=instant,
        defer_deadline=None if deferral is None else deferral.defer_deadline,
        validity_deadline=None if deferral is None else deferral.validity_deadline,
    )
    event_type = (
        OpportunityLifecycleEventType.OPENED
        if state is OpportunityLifecycleState.ACTIVE
        else OpportunityLifecycleEventType.DEFERRED
    )
    event = OpportunityLifecycleEvent(
        event_id=opportunity_event_identity(
            episode_id=episode_id,
            event_type=event_type,
            evaluation_time=instant,
            source_claim_id=claim.claim_id,
        ),
        event_type=event_type,
        episode_id=episode_id,
        lifecycle_version=OPPORTUNITY_LIFECYCLE_VERSION,
        policy_version=OPPORTUNITY_POLICY_VERSION,
        evaluation_time=instant,
        source_claim_id=claim.claim_id,
    )
    return OpportunityLifecycleResult(episode=episode, events=(event,), changed=True)



def advance_episode(
    prior: OpportunityEpisode, *, evaluation_time: datetime
) -> OpportunityLifecycleResult:
    """Advance one episode by the only v1 transition: bounded-deferral expiry.

    A terminal episode is returned unchanged, so an expired claim cannot resume.
    An `ACTIVE` episode is returned unchanged, because v1 has no
    active-to-terminal path without a deferral deadline. A non-monotonic
    evaluation instant is refused rather than silently absorbed.
    """

    if not isinstance(prior, OpportunityEpisode):
        raise PhaseContractError("prior must be an OpportunityEpisode")
    instant = require_opportunity_utc(evaluation_time, field_name="evaluation_time")
    if instant < prior.last_evaluation_time:
        raise PhaseContractError(
            "evaluation_time cannot precede the episode's last evaluation"
        )
    if prior.lifecycle_state is not OpportunityLifecycleState.DEFERRED:
        return OpportunityLifecycleResult(episode=prior, events=(), changed=False)
    deadline = prior.defer_deadline
    if deadline is None or instant < deadline:
        return OpportunityLifecycleResult(episode=prior, events=(), changed=False)
    expired = replace(
        prior,
        lifecycle_state=OpportunityLifecycleState.TERMINAL,
        last_evaluation_time=instant,
        terminal_reason=OpportunityTerminalReason.EXPIRED,
        terminal_evaluation_time=instant,
    )
    event = OpportunityLifecycleEvent(
        event_id=opportunity_event_identity(
            episode_id=expired.episode_id,
            event_type=OpportunityLifecycleEventType.EXPIRED,
            evaluation_time=instant,
        ),
        event_type=OpportunityLifecycleEventType.EXPIRED,
        episode_id=expired.episode_id,
        lifecycle_version=OPPORTUNITY_LIFECYCLE_VERSION,
        policy_version=OPPORTUNITY_POLICY_VERSION,
        evaluation_time=instant,
    )
    return OpportunityLifecycleResult(episode=expired, events=(event,), changed=True)


def requalify_episode(
    prior: OpportunityEpisode,
    *,
    evaluation_time: datetime,
    new_decision_id: str,
    evidence_cutoff: datetime,
    prior_decision_id: str | None = None,
) -> tuple[OpportunityEpisode, EpisodeRequalification]:
    """Link a fresh decision to an existing, non-terminal episode.

    The episode identity, its source claim lineage and its clock are preserved:
    requalification does not reset the episode, mint a second opportunity or
    extend an expired claim.
    """

    if not isinstance(prior, OpportunityEpisode):
        raise PhaseContractError("prior must be an OpportunityEpisode")
    instant = require_opportunity_utc(evaluation_time, field_name="evaluation_time")
    if instant < prior.last_evaluation_time:
        raise PhaseContractError(
            "evaluation_time cannot precede the episode's last evaluation"
        )
    if prior.lifecycle_state is OpportunityLifecycleState.TERMINAL:
        raise PhaseContractError(
            "a terminal episode cannot be requalified; it needs a new linked episode"
        )
    link = EpisodeRequalification(
        episode_id=prior.episode_id,
        prior_decision_id=prior_decision_id,
        new_decision_id=new_decision_id,
        evidence_cutoff=evidence_cutoff,
        evaluation_time=instant,
    )
    updated = replace(prior, last_evaluation_time=instant)
    return updated, link


__all__ = [
    "DOWNSTREAM_ONLY_PHASES",
    "EpisodeRequalification",
    "OPPORTUNITY_PHASE_SCHEMA_VERSION",
    "OpportunityPhase",
    "OpportunityPhaseMapping",
    "PHASE_AUTHORITY",
    "PHASE_MAPPINGS",
    "PhaseContractError",
    "R5A_REACHABLE_PHASES",
    "advance_episode",
    "episode_from_claim",
    "episode_phase",
    "project_phase",
    "requalify_episode",
]

