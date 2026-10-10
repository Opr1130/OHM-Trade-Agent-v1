"""EYE: bounded, stateful market attention for the R5-A slice.

Adopted authority: `docs/architecture/v1.5.0/ARCHITECTURE.md` section 3
(`ME-02`-`ME-06`), section 10's `AttentionTrigger` record and the R5-0
structural contract `SC-02`.

Attention is *attention*: it is not an F3 detector claim, not an F4 trade
approval, not a notification and not a recommendation. The states are a closed
declared set, `STALE`/`RECOVERING` are health overlays rather than states, and
every transition request keeps its identity, source events, policy version,
cutoff, watermark, expiry and suppression context.

Two owner-gated boundaries are deliberate:

* Every numeric bound (debounce, cooldown, expiry, oscillation guard, work
  budget) is `OQ-04` owner policy. This module ships no number. A policy without
  them cannot advance attention at all, so an unratified deployment is inert
  rather than conservatively noisy.
* This module never synthesizes favourable evidence. A trigger is an *evaluation
  request*: it cannot create a `FeatureSnapshot`, cannot bypass warm-up or
  coverage, and carries `authority = NONE`.

Nothing here reads a clock, the environment, the filesystem, the network or a
database: every instant is caller-supplied, so a burst, a restart or a backfill
replays identically.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Any

from app.opip.contracts.features import DEFAULT_EVALUATION_GRID_SECONDS
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import require_utc
from app.opip.events.coverage import ObservationCoverage

#: Schema version of the attention trigger record.
ATTENTION_SCHEMA_VERSION = "attention-trigger-v1"

#: The owner-gated policy token. `OQ-04` remains an owner decision.
ATTENTION_POLICY_VERSION = "attention-shadow-policy-v1"

#: Explicitly labelled engineering-scenario token, for reproducible measurements
#: that must not pretend an owner ratified the numbers.
ATTENTION_ENGINEERING_SCENARIO_POLICY_VERSION = "attention-engineering-scenario-v1"

DECLARED_POLICY_VERSIONS: tuple[str, ...] = (
    ATTENTION_POLICY_VERSION,
    ATTENTION_ENGINEERING_SCENARIO_POLICY_VERSION,
)

#: `OQ-04` remains open; this module never claims otherwise.
OWNER_INPUT_STATUS = "OWNER_DECISION_REQUIRED"

TRIGGER_ID_PREFIX = "ATRG"

#: Every attention record carries zero authority.
ATTENTION_AUTHORITY = "NONE"


class AttentionContractError(ValueError):
    """An attention contract violation. Always fails closed."""


class AttentionState(str, Enum):
    """The closed attention-state set from `ME-02`. There is no sixth state."""

    DORMANT = "DORMANT"
    WATCH = "WATCH"
    DEVELOPING = "DEVELOPING"
    FOCUSED = "FOCUSED"
    COOLDOWN = "COOLDOWN"


#: Progression order. `COOLDOWN` is a bounded retreat, not a rank.
ATTENTION_RANK: Mapping[AttentionState, int] = MappingProxyType(
    {
        AttentionState.DORMANT: 0,
        AttentionState.WATCH: 1,
        AttentionState.DEVELOPING: 2,
        AttentionState.FOCUSED: 3,
    }
)


class AttentionHealthOverlay(str, Enum):
    """Health overlays. Never states, never signals."""

    HEALTHY = "HEALTHY"
    STALE = "STALE"
    RECOVERING = "RECOVERING"


class AttentionReason(str, Enum):
    """The declared attention reason tokens."""

    APPROACHING_LEVEL = "APPROACHING_LEVEL"
    RELATIVE_VOLUME_STRENGTHENING = "RELATIVE_VOLUME_STRENGTHENING"
    STRUCTURE_IMPROVEMENT = "STRUCTURE_IMPROVEMENT"
    REGIME_CHANGE = "REGIME_CHANGE"
    BREAKOUT_DEVELOPING = "BREAKOUT_DEVELOPING"
    VOLATILITY_TRANSITION = "VOLATILITY_TRANSITION"
    LIQUIDITY_DETERIORATION = "LIQUIDITY_DETERIORATION"
    SETUP_CONDITION_LOST = "SETUP_CONDITION_LOST"


class AttentionPopulation(str, Enum):
    """The population vocabulary of `ME-04`.

    Counts over these members reconcile exactly: every eligible request reaches
    one terminal population or remains outstanding. `BUDGET_SKIPPED` is a
    per-cycle marking, so a budget-skipped request stays outstanding and is
    counted again when it is finally served, expires or fails.
    """

    ELIGIBLE = "ELIGIBLE"
    ADMITTED = "ADMITTED"
    EVALUATED = "EVALUATED"
    COALESCED = "COALESCED"
    SUPPRESSED = "SUPPRESSED"
    EXPIRED = "EXPIRED"
    BUDGET_SKIPPED = "BUDGET_SKIPPED"
    STALE = "STALE"
    FAILED = "FAILED"
    RECOVERED = "RECOVERED"


#: Populations that terminate a request's life.
TERMINAL_POPULATIONS: tuple[AttentionPopulation, ...] = (
    AttentionPopulation.EVALUATED,
    AttentionPopulation.COALESCED,
    AttentionPopulation.SUPPRESSED,
    AttentionPopulation.EXPIRED,
    AttentionPopulation.STALE,
    AttentionPopulation.FAILED,
    AttentionPopulation.RECOVERED,
)

#: Reason tokens carried on a trigger that records no material transition.
REASON_NO_MATERIAL_CHANGE = "no-material-change"
REASON_POLICY_NOT_RATIFIED = "policy-not-ratified"
REASON_COVERAGE_UNUSABLE = "coverage-unusable"
REASON_HEALTH_RECOVERED = "coverage-recovered"
REASON_OSCILLATION_HOLD = "oscillation-guard-hold"


@dataclass(frozen=True)
class AttentionPolicy:
    """The applied attention policy. Every numeric bound is owner input."""

    policy_version: str = ATTENTION_POLICY_VERSION
    debounce_intervals: int | None = None
    cooldown_intervals: int | None = None
    trigger_expiry_intervals: int | None = None
    oscillation_guard_intervals: int | None = None
    work_budget_per_cycle: int | None = None

    _NUMERIC_FIELDS: tuple[str, ...] = (
        "debounce_intervals",
        "cooldown_intervals",
        "trigger_expiry_intervals",
        "oscillation_guard_intervals",
        "work_budget_per_cycle",
    )

    def __post_init__(self) -> None:
        version = str(self.policy_version or "").strip()
        if version not in DECLARED_POLICY_VERSIONS:
            raise AttentionContractError(
                f"policy_version is not a declared token: {version!r}"
            )
        object.__setattr__(self, "policy_version", version)
        for name in self._NUMERIC_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise AttentionContractError(f"{name} must be an integer or None")
            if value < 0:
                raise AttentionContractError(f"{name} must be non-negative")
            if name in ("trigger_expiry_intervals", "work_budget_per_cycle") and (
                value <= 0
            ):
                raise AttentionContractError(f"{name} must be positive")
            object.__setattr__(self, name, int(value))

    @property
    def configured(self) -> bool:
        """True only when every owner-gated bound is supplied."""

        return all(getattr(self, name) is not None for name in self._NUMERIC_FIELDS)

    @property
    def ratified(self) -> bool:
        """Attention bounds are owner policy; this module never ratifies them."""

        return False

    @property
    def owner_input_status(self) -> str:
        return OWNER_INPUT_STATUS

    def _require_configured(self) -> None:
        if not self.configured:
            raise AttentionContractError(
                "attention policy is NOT_CONFIGURED; the owner-gated bounds are "
                f"{OWNER_INPUT_STATUS}"
            )

    def expires_at(self, *, request_time: datetime) -> datetime:
        self._require_configured()
        return request_time + timedelta(
            seconds=int(self.trigger_expiry_intervals) * DEFAULT_EVALUATION_GRID_SECONDS
        )


@dataclass(frozen=True)
class AttentionEvidence:
    """Point-in-time material-change evidence for one instrument.

    `requested_state` is the strongest attention state the observed material
    change would support if the policy allowed immediate movement. The engine
    never treats it as a claim: the transition surface decides whether the state
    may actually move.
    """

    instrument_version_id: str
    venue_instrument_id: str
    evaluation_time: datetime
    evidence_cutoff: datetime
    coverage: ObservationCoverage
    source_event_ids: tuple[str, ...] = ()
    progression_reasons: tuple[AttentionReason, ...] = ()
    regression_reasons: tuple[AttentionReason, ...] = ()
    requested_state: AttentionState | None = None

    def __post_init__(self) -> None:
        for name in ("instrument_version_id", "venue_instrument_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise AttentionContractError(f"{name} must be a non-empty string")
        instant = require_utc(self.evaluation_time, field_name="evaluation_time")
        cutoff = require_utc(self.evidence_cutoff, field_name="evidence_cutoff")
        if cutoff > instant:
            raise AttentionContractError(
                "evidence_cutoff cannot follow the evaluation instant"
            )
        object.__setattr__(self, "evaluation_time", instant)
        object.__setattr__(self, "evidence_cutoff", cutoff)
        if not isinstance(self.coverage, ObservationCoverage):
            raise AttentionContractError("coverage must be an ObservationCoverage")
        if self.coverage.instrument_version_id != self.instrument_version_id:
            raise AttentionContractError(
                "coverage belongs to a different instrument version"
            )
        sequences = tuple(str(item) for item in self.source_event_ids)
        if any(not item.strip() for item in sequences):
            raise AttentionContractError("source_event_ids must be non-empty tokens")
        object.__setattr__(self, "source_event_ids", sequences)
        for name in ("progression_reasons", "regression_reasons"):
            values = tuple(getattr(self, name))
            for value in values:
                if not isinstance(value, AttentionReason):
                    raise AttentionContractError(f"{name} must hold AttentionReason")
            object.__setattr__(self, name, tuple(sorted(values, key=lambda x: x.value)))
        if self.requested_state is not None and not isinstance(
            self.requested_state, AttentionState
        ):
            raise AttentionContractError("requested_state must be an AttentionState")
        if self.requested_state is AttentionState.COOLDOWN:
            raise AttentionContractError(
                "COOLDOWN is a bounded retreat the policy alone may enter"
            )

    @property
    def coverage_usable(self) -> bool:
        return self.coverage.usable_at(cutoff=self.evaluation_time)

    @property
    def material_change(self) -> bool:
        if self.requested_state is None:
            return False
        return bool(
            attention_rank(self.requested_state) != 0 or self.regression_reasons
        )



def attention_rank(state: AttentionState | str) -> int:
    """Return the progression rank of a state. `COOLDOWN` ranks below WATCH."""

    if isinstance(state, str):
        state = AttentionState(state)
    if state is AttentionState.COOLDOWN:
        return 0
    return ATTENTION_RANK[state]


def next_progression_state(state: AttentionState) -> AttentionState:
    """The next state one qualifying evaluation may step into."""

    if state in (AttentionState.DORMANT, AttentionState.COOLDOWN):
        return AttentionState.WATCH
    if state is AttentionState.WATCH:
        return AttentionState.DEVELOPING
    if state is AttentionState.DEVELOPING:
        return AttentionState.FOCUSED
    return AttentionState.FOCUSED


def retreat_state(state: AttentionState) -> AttentionState:
    """The retreat target when a state regresses by one step."""

    if state is AttentionState.FOCUSED:
        return AttentionState.COOLDOWN
    if state is AttentionState.DEVELOPING:
        return AttentionState.WATCH
    if state is AttentionState.WATCH:
        return AttentionState.DORMANT
    return AttentionState.DORMANT


@dataclass(frozen=True)
class AttentionStateRecord:
    """Deterministic attention state for one instrument.

    The record is the entire input the next evaluation needs beyond the sealed
    coverage evidence and the explicit evaluation instant.
    """

    instrument_version_id: str
    state: AttentionState = AttentionState.DORMANT
    health_overlay: AttentionHealthOverlay = AttentionHealthOverlay.HEALTHY
    pending_state: AttentionState | None = None
    qualifying_intervals: int = 0
    intervals_in_state: int = 0
    last_transition_time: datetime | None = None
    last_transition_was_progression: bool = False
    last_trigger_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_version_id, str) or (
            not self.instrument_version_id.strip()
        ):
            raise AttentionContractError("instrument_version_id is required")
        if not isinstance(self.state, AttentionState):
            raise AttentionContractError("state must be an AttentionState")
        if not isinstance(self.health_overlay, AttentionHealthOverlay):
            raise AttentionContractError(
                "health_overlay must be an AttentionHealthOverlay"
            )
        if self.pending_state is not None and not isinstance(
            self.pending_state, AttentionState
        ):
            raise AttentionContractError("pending_state must be an AttentionState")
        for name in ("qualifying_intervals", "intervals_in_state"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AttentionContractError(f"{name} must be a non-negative int")
        if self.last_transition_time is not None:
            object.__setattr__(
                self,
                "last_transition_time",
                require_utc(self.last_transition_time, field_name="last_transition_time"),
            )
        if not isinstance(self.last_transition_was_progression, bool):
            raise AttentionContractError("last_transition_was_progression must be bool")

    @classmethod
    def initial(cls, instrument_version_id: str) -> "AttentionStateRecord":
        return cls(instrument_version_id=instrument_version_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_version_id": self.instrument_version_id,
            "state": self.state.value,
            "health_overlay": self.health_overlay.value,
            "pending_state": (
                self.pending_state.value if self.pending_state is not None else None
            ),
            "qualifying_intervals": self.qualifying_intervals,
            "intervals_in_state": self.intervals_in_state,
            "last_transition_time": (
                iso_z(self.last_transition_time, field_name="last_transition_time")
                if self.last_transition_time is not None
                else None
            ),
            "last_transition_was_progression": self.last_transition_was_progression,
            "last_trigger_id": self.last_trigger_id,
        }



def attention_trigger_identity(
    *,
    instrument_version_id: str,
    prior_state: AttentionState,
    new_state: AttentionState,
    policy_version: str,
    evidence_cutoff: datetime,
    source_event_ids: tuple[str, ...],
) -> str:
    """Deterministic ``ATRG:`` identity of one attention transition request.

    Identity binds the instrument, both states, the applied policy version, the
    evidence cutoff and the source events. No wall clock, UUID, process identity,
    invocation order or retry count takes part, so re-deriving the same
    transition reproduces the same trigger and a duplicate delivery converges.
    """

    if not isinstance(instrument_version_id, str) or not instrument_version_id.strip():
        raise AttentionContractError("instrument_version_id is required")
    for name, value in (("prior_state", prior_state), ("new_state", new_state)):
        if not isinstance(value, AttentionState):
            raise AttentionContractError(f"{name} must be an AttentionState")
    version = str(policy_version or "").strip()
    if version not in DECLARED_POLICY_VERSIONS:
        raise AttentionContractError("policy_version is not a declared token")
    cutoff = require_utc(evidence_cutoff, field_name="evidence_cutoff")
    events = tuple(sorted(str(item) for item in source_event_ids))
    return stable_hash(
        TRIGGER_ID_PREFIX,
        {
            "instrument_version_id": instrument_version_id,
            "prior_state": prior_state.value,
            "new_state": new_state.value,
            "policy_version": version,
            "evidence_cutoff": iso_z(cutoff, field_name="evidence_cutoff"),
            "source_event_ids": list(events),
        },
    )



@dataclass(frozen=True)
class AttentionTrigger:
    """One attention transition request and its retained context (`ME-03`)."""

    trigger_id: str
    schema_version: str
    instrument_version_id: str
    venue_instrument_id: str
    prior_state: AttentionState
    new_state: AttentionState
    health_overlay: AttentionHealthOverlay
    disposition: AttentionPopulation
    policy_version: str
    policy_ratified: bool
    reason_codes: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    event_time_utc: datetime | None
    receipt_time_utc: datetime | None
    availability_time_utc: datetime | None
    request_time_utc: datetime
    evidence_cutoff: datetime
    watermark_through_utc: datetime | None
    watermark_local_sequence: int
    expires_at_utc: datetime | None
    suppression_detail: str = "none"

    def __post_init__(self) -> None:
        if self.schema_version != ATTENTION_SCHEMA_VERSION:
            raise AttentionContractError(
                f"schema_version is not {ATTENTION_SCHEMA_VERSION}"
            )
        for name in ("instrument_version_id", "venue_instrument_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise AttentionContractError(f"{name} is required")
        for name in ("prior_state", "new_state"):
            if not isinstance(getattr(self, name), AttentionState):
                raise AttentionContractError(f"{name} must be an AttentionState")
        if not isinstance(self.health_overlay, AttentionHealthOverlay):
            raise AttentionContractError(
                "health_overlay must be an AttentionHealthOverlay"
            )
        if not isinstance(self.disposition, AttentionPopulation):
            raise AttentionContractError("disposition must be an AttentionPopulation")
        if self.disposition is AttentionPopulation.ELIGIBLE:
            raise AttentionContractError(
                "ELIGIBLE is a population count, not a trigger disposition"
            )
        if not isinstance(self.policy_ratified, bool):
            raise AttentionContractError("policy_ratified must be a bool")
        request_time = require_utc(self.request_time_utc, field_name="request_time_utc")
        cutoff = require_utc(self.evidence_cutoff, field_name="evidence_cutoff")
        if cutoff > request_time:
            raise AttentionContractError(
                "evidence_cutoff cannot follow the request time"
            )
        object.__setattr__(self, "request_time_utc", request_time)
        object.__setattr__(self, "evidence_cutoff", cutoff)
        for name in ("event_time_utc", "receipt_time_utc", "availability_time_utc"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, require_utc(value, field_name=name))
        if self.watermark_through_utc is not None:
            object.__setattr__(
                self,
                "watermark_through_utc",
                require_utc(
                    self.watermark_through_utc, field_name="watermark_through_utc"
                ),
            )
        sequence = self.watermark_local_sequence
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise AttentionContractError(
                "watermark_local_sequence must be a non-negative integer"
            )
        if self.expires_at_utc is not None:
            expiry = require_utc(self.expires_at_utc, field_name="expires_at_utc")
            if expiry <= request_time:
                raise AttentionContractError(
                    "a trigger must expire after the instant it was created"
                )
            object.__setattr__(self, "expires_at_utc", expiry)
        reasons = tuple(str(item) for item in self.reason_codes)
        if not reasons or any(not item.strip() for item in reasons):
            raise AttentionContractError("reason_codes must be non-empty tokens")
        object.__setattr__(self, "reason_codes", reasons)
        events = tuple(str(item) for item in self.source_event_ids)
        if any(not item.strip() for item in events):
            raise AttentionContractError("source_event_ids must be non-empty tokens")
        object.__setattr__(self, "source_event_ids", events)
        if not str(self.suppression_detail or "").strip():
            raise AttentionContractError("suppression_detail must be recorded")
        if self.new_state is self.prior_state and self.disposition not in (
            AttentionPopulation.SUPPRESSED,
            AttentionPopulation.STALE,
            AttentionPopulation.BUDGET_SKIPPED,
            AttentionPopulation.COALESCED,
            AttentionPopulation.EXPIRED,
            AttentionPopulation.FAILED,
            AttentionPopulation.RECOVERED,
        ):
            raise AttentionContractError(
                "a no-progress trigger must record why it did not move"
            )
        expected = attention_trigger_identity(
            instrument_version_id=self.instrument_version_id,
            prior_state=self.prior_state,
            new_state=self.new_state,
            policy_version=self.policy_version,
            evidence_cutoff=cutoff,
            source_event_ids=events,
        )
        if self.trigger_id != expected:
            raise AttentionContractError(
                "trigger_id does not match its own evidence; build triggers with "
                "advance_attention()"
            )

    @property
    def authority(self) -> str:
        return ATTENTION_AUTHORITY

    @property
    def progressed(self) -> bool:
        return attention_rank(self.new_state) > attention_rank(self.prior_state)


    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "AttentionTrigger",
            "schema_version": self.schema_version,
            "trigger_id": self.trigger_id,
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "prior_state": self.prior_state.value,
            "new_state": self.new_state.value,
            "health_overlay": self.health_overlay.value,
            "disposition": self.disposition.value,
            "policy_version": self.policy_version,
            "policy_ratified": self.policy_ratified,
            "reason_codes": list(self.reason_codes),
            "source_event_ids": list(self.source_event_ids),
            "event_time_utc": (
                iso_z(self.event_time_utc, field_name="event_time_utc")
                if self.event_time_utc is not None
                else None
            ),
            "receipt_time_utc": (
                iso_z(self.receipt_time_utc, field_name="receipt_time_utc")
                if self.receipt_time_utc is not None
                else None
            ),
            "availability_time_utc": (
                iso_z(self.availability_time_utc, field_name="availability_time_utc")
                if self.availability_time_utc is not None
                else None
            ),
            "request_time_utc": iso_z(
                self.request_time_utc, field_name="request_time_utc"
            ),
            "evidence_cutoff": iso_z(
                self.evidence_cutoff, field_name="evidence_cutoff"
            ),
            "watermark_through_utc": (
                iso_z(self.watermark_through_utc, field_name="watermark_through_utc")
                if self.watermark_through_utc is not None
                else None
            ),
            "watermark_local_sequence": self.watermark_local_sequence,
            "expires_at_utc": (
                iso_z(self.expires_at_utc, field_name="expires_at_utc")
                if self.expires_at_utc is not None
                else None
            ),
            "suppression_detail": self.suppression_detail,
            "authority": self.authority,
        }


@dataclass(frozen=True)
class AttentionTransition:
    """The pure outcome of one attention evaluation."""

    prior: AttentionStateRecord
    current: AttentionStateRecord
    trigger: AttentionTrigger | None
    disposition: AttentionPopulation
    progressed: bool = False
    regressed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.prior, AttentionStateRecord) or not isinstance(
            self.current, AttentionStateRecord
        ):
            raise AttentionContractError("prior and current must be state records")
        if self.prior.instrument_version_id != self.current.instrument_version_id:
            raise AttentionContractError(
                "a transition cannot move between instruments"
            )
        if not isinstance(self.disposition, AttentionPopulation):
            raise AttentionContractError("disposition must be an AttentionPopulation")
        for name in ("progressed", "regressed"):
            if not isinstance(getattr(self, name), bool):
                raise AttentionContractError(f"{name} must be a bool")
        if self.progressed and self.regressed:
            raise AttentionContractError(
                "a transition cannot both progress and regress"
            )
        if self.trigger is not None:
            if self.trigger.disposition is not self.disposition:
                raise AttentionContractError(
                    "the trigger disposition must match the transition disposition"
                )
            if self.trigger.new_state is not self.current.state:
                raise AttentionContractError(
                    "the trigger's new state must be the resulting state"
                )
            if self.trigger.prior_state is not self.prior.state:
                raise AttentionContractError(
                    "the trigger's prior state must be the incoming state"
                )



def _health_overlay(
    prior: AttentionHealthOverlay, *, usable: bool
) -> AttentionHealthOverlay:
    if not usable:
        return AttentionHealthOverlay.STALE
    if prior is AttentionHealthOverlay.STALE:
        return AttentionHealthOverlay.RECOVERING
    return AttentionHealthOverlay.HEALTHY


def _trigger(
    *,
    evidence: AttentionEvidence,
    policy: AttentionPolicy,
    prior_state: AttentionState,
    new_state: AttentionState,
    overlay: AttentionHealthOverlay,
    disposition: AttentionPopulation,
    reasons: tuple[str, ...],
    expires_at: datetime | None,
    suppression_detail: str,
) -> AttentionTrigger:
    coverage = evidence.coverage
    availability = coverage.availability
    trigger_id = attention_trigger_identity(
        instrument_version_id=evidence.instrument_version_id,
        prior_state=prior_state,
        new_state=new_state,
        policy_version=policy.policy_version,
        evidence_cutoff=evidence.evidence_cutoff,
        source_event_ids=evidence.source_event_ids,
    )
    return AttentionTrigger(
        trigger_id=trigger_id,
        schema_version=ATTENTION_SCHEMA_VERSION,
        instrument_version_id=evidence.instrument_version_id,
        venue_instrument_id=evidence.venue_instrument_id,
        prior_state=prior_state,
        new_state=new_state,
        health_overlay=overlay,
        disposition=disposition,
        policy_version=policy.policy_version,
        policy_ratified=policy.ratified,
        reason_codes=tuple(sorted(set(reasons))),
        source_event_ids=evidence.source_event_ids,
        event_time_utc=coverage.latest_source_event_time,
        receipt_time_utc=coverage.latest_receipt_time,
        availability_time_utc=(
            availability.visible_at_utc if availability is not None else None
        ),
        request_time_utc=evidence.evaluation_time,
        evidence_cutoff=evidence.evidence_cutoff,
        watermark_through_utc=coverage.watermark_through_utc,
        watermark_local_sequence=0,
        expires_at_utc=expires_at,
        suppression_detail=suppression_detail,
    )


def _record(
    prior: AttentionStateRecord,
    *,
    state: AttentionState,
    overlay: AttentionHealthOverlay,
    pending_state: AttentionState | None,
    qualifying_intervals: int,
    intervals_in_state: int,
    transitioned: bool,
    progressed: bool,
    evaluation_time: datetime,
    trigger_id: str,
) -> AttentionStateRecord:
    return AttentionStateRecord(
        instrument_version_id=prior.instrument_version_id,
        state=state,
        health_overlay=overlay,
        pending_state=pending_state,
        qualifying_intervals=qualifying_intervals,
        intervals_in_state=intervals_in_state,
        last_transition_time=evaluation_time if transitioned else prior.last_transition_time,
        last_transition_was_progression=(
            progressed if transitioned else prior.last_transition_was_progression
        ),
        last_trigger_id=trigger_id,
    )



def advance_attention(
    prior: AttentionStateRecord,
    evidence: AttentionEvidence,
    policy: AttentionPolicy,
) -> AttentionTransition:
    """Advance one instrument's attention by exactly one evaluation.

    The function is pure and total: it always returns the prior state, the next
    state and (when a transition request exists) one trigger. Ordering:

    1. unusable coverage holds the state, sets `STALE` and records why;
    2. an unratified policy is inert: it cannot advance attention;
    3. the first healthy evaluation after `STALE` records `RECOVERING`;
    4. regression always de-escalates, entering `COOLDOWN` when leaving
       `FOCUSED`;
    5. progression needs the owner-gated debounce, respects the oscillation
       guard and steps one rank at a time.
    """

    if not isinstance(prior, AttentionStateRecord):
        raise AttentionContractError("prior must be an AttentionStateRecord")
    if not isinstance(evidence, AttentionEvidence):
        raise AttentionContractError("evidence must be an AttentionEvidence")
    if not isinstance(policy, AttentionPolicy):
        raise AttentionContractError("policy must be an AttentionPolicy")
    if prior.instrument_version_id != evidence.instrument_version_id:
        raise AttentionContractError(
            "attention evidence belongs to a different instrument version"
        )
    instant = evidence.evaluation_time
    if (
        prior.last_transition_time is not None
        and instant < prior.last_transition_time
    ):
        raise AttentionContractError(
            "an evaluation cannot precede the last observed transition"
        )

    usable = evidence.coverage_usable
    overlay = _health_overlay(prior.health_overlay, usable=usable)

    if not usable:
        reasons = tuple(
            [REASON_COVERAGE_UNUSABLE]
            + [item.value for item in evidence.regression_reasons]
        )
        trigger = _trigger(
            evidence=evidence,
            policy=policy,
            prior_state=prior.state,
            new_state=prior.state,
            overlay=overlay,
            disposition=AttentionPopulation.STALE,
            reasons=reasons,
            expires_at=None,
            suppression_detail="coverage unusable at the evaluation instant",
        )
        return AttentionTransition(
            prior=prior,
            current=_record(
                prior,
                state=prior.state,
                overlay=overlay,
                pending_state=prior.pending_state,
                qualifying_intervals=0,
                intervals_in_state=prior.intervals_in_state + 1,
                transitioned=False,
                progressed=False,
                evaluation_time=instant,
                trigger_id=trigger.trigger_id,
            ),
            trigger=trigger,
            disposition=AttentionPopulation.STALE,
        )

    if not policy.configured:
        trigger = _trigger(
            evidence=evidence,
            policy=policy,
            prior_state=prior.state,
            new_state=prior.state,
            overlay=overlay,
            disposition=AttentionPopulation.BUDGET_SKIPPED,
            reasons=(REASON_POLICY_NOT_RATIFIED,),
            expires_at=None,
            suppression_detail=(
                "attention bounds are owner input and are not configured; the "
                "engine is inert"
            ),
        )
        return AttentionTransition(
            prior=prior,
            current=_record(
                prior,
                state=prior.state,
                overlay=overlay,
                pending_state=prior.pending_state,
                qualifying_intervals=0,
                intervals_in_state=prior.intervals_in_state + 1,
                transitioned=False,
                progressed=False,
                evaluation_time=instant,
                trigger_id=trigger.trigger_id,
            ),
            trigger=trigger,
            disposition=AttentionPopulation.BUDGET_SKIPPED,
        )


    expires_at = policy.expires_at(request_time=instant)

    if prior.health_overlay is AttentionHealthOverlay.STALE and (
        overlay is AttentionHealthOverlay.RECOVERING
    ):
        trigger = _trigger(
            evidence=evidence,
            policy=policy,
            prior_state=prior.state,
            new_state=prior.state,
            overlay=overlay,
            disposition=AttentionPopulation.RECOVERED,
            reasons=(REASON_HEALTH_RECOVERED,),
            expires_at=None,
            suppression_detail=(
                "coverage recovered; the state is re-established before any "
                "transition is attempted"
            ),
        )
        return AttentionTransition(
            prior=prior,
            current=_record(
                prior,
                state=prior.state,
                overlay=overlay,
                pending_state=None,
                qualifying_intervals=0,
                intervals_in_state=prior.intervals_in_state + 1,
                transitioned=False,
                progressed=False,
                evaluation_time=instant,
                trigger_id=trigger.trigger_id,
            ),
            trigger=trigger,
            disposition=AttentionPopulation.RECOVERED,
        )

    requested = evidence.requested_state
    requested_rank = None if requested is None else attention_rank(requested)
    cooldown_seconds = int(policy.cooldown_intervals) * DEFAULT_EVALUATION_GRID_SECONDS

    if prior.state is AttentionState.COOLDOWN:
        elapsed = (
            instant - prior.last_transition_time
            if prior.last_transition_time is not None
            else timedelta(0)
        )
        if elapsed < timedelta(seconds=cooldown_seconds):
            trigger = _trigger(
                evidence=evidence,
                policy=policy,
                prior_state=prior.state,
                new_state=prior.state,
                overlay=overlay,
                disposition=AttentionPopulation.SUPPRESSED,
                reasons=tuple(
                    [REASON_OSCILLATION_HOLD]
                    + [item.value for item in evidence.progression_reasons]
                ),
                expires_at=expires_at,
                suppression_detail="bounded cooldown has not elapsed",
            )
            return AttentionTransition(
                prior=prior,
                current=_record(
                    prior,
                    state=prior.state,
                    overlay=overlay,
                    pending_state=None,
                    qualifying_intervals=0,
                    intervals_in_state=prior.intervals_in_state + 1,
                    transitioned=False,
                    progressed=False,
                    evaluation_time=instant,
                    trigger_id=trigger.trigger_id,
                ),
                trigger=trigger,
                disposition=AttentionPopulation.SUPPRESSED,
            )
        target = (
            AttentionState.DORMANT
            if requested is None or requested_rank == 0
            else AttentionState.WATCH
        )
        trigger = _trigger(
            evidence=evidence,
            policy=policy,
            prior_state=prior.state,
            new_state=target,
            overlay=overlay,
            disposition=AttentionPopulation.ADMITTED,
            reasons=tuple(
                [item.value for item in evidence.progression_reasons]
                + [item.value for item in evidence.regression_reasons]
            )
            or (REASON_NO_MATERIAL_CHANGE,),
            expires_at=expires_at,
            suppression_detail="cooldown elapsed; attention re-established",
        )
        return AttentionTransition(
            prior=prior,
            current=_record(
                prior,
                state=target,
                overlay=overlay,
                pending_state=None,
                qualifying_intervals=0,
                intervals_in_state=0,
                transitioned=True,
                progressed=True,
                evaluation_time=instant,
                trigger_id=trigger.trigger_id,
            ),
            trigger=trigger,
            disposition=AttentionPopulation.ADMITTED,
            progressed=True,
        )


    current_rank = attention_rank(prior.state)
    guard_elapsed = (
        instant - prior.last_transition_time
        if prior.last_transition_time is not None
        else None
    )
    guard_active = guard_elapsed is not None and guard_elapsed < timedelta(
        seconds=(
            int(policy.oscillation_guard_intervals) * DEFAULT_EVALUATION_GRID_SECONDS
        )
    )

    if requested_rank is not None and requested_rank < current_rank:
        target = retreat_state(prior.state)
        trigger = _trigger(
            evidence=evidence,
            policy=policy,
            prior_state=prior.state,
            new_state=target,
            overlay=overlay,
            disposition=AttentionPopulation.ADMITTED,
            reasons=tuple(item.value for item in evidence.regression_reasons)
            or (REASON_NO_MATERIAL_CHANGE,),
            expires_at=expires_at,
            suppression_detail="attention de-escalated on regression evidence",
        )
        return AttentionTransition(
            prior=prior,
            current=_record(
                prior,
                state=target,
                overlay=overlay,
                pending_state=None,
                qualifying_intervals=0,
                intervals_in_state=0,
                transitioned=True,
                progressed=False,
                evaluation_time=instant,
                trigger_id=trigger.trigger_id,
            ),
            trigger=trigger,
            disposition=AttentionPopulation.ADMITTED,
            regressed=True,
        )

    if requested_rank is None or requested_rank <= current_rank:
        trigger = _trigger(
            evidence=evidence,
            policy=policy,
            prior_state=prior.state,
            new_state=prior.state,
            overlay=overlay,
            disposition=AttentionPopulation.SUPPRESSED,
            reasons=(REASON_NO_MATERIAL_CHANGE,),
            expires_at=expires_at,
            suppression_detail="no material transition was requested",
        )
        return AttentionTransition(
            prior=prior,
            current=_record(
                prior,
                state=prior.state,
                overlay=overlay,
                pending_state=None,
                qualifying_intervals=0,
                intervals_in_state=prior.intervals_in_state + 1,
                transitioned=False,
                progressed=False,
                evaluation_time=instant,
                trigger_id=trigger.trigger_id,
            ),
            trigger=trigger,
            disposition=AttentionPopulation.SUPPRESSED,
        )

    target = next_progression_state(prior.state)
    if guard_active:
        trigger = _trigger(
            evidence=evidence,
            policy=policy,
            prior_state=prior.state,
            new_state=prior.state,
            overlay=overlay,
            disposition=AttentionPopulation.SUPPRESSED,
            reasons=tuple(
                [REASON_OSCILLATION_HOLD]
                + [item.value for item in evidence.progression_reasons]
            ),
            expires_at=expires_at,
            suppression_detail=(
                "oscillation guard is active inside the owner-gated minimum dwell"
            ),
        )
        return AttentionTransition(
            prior=prior,
            current=_record(
                prior,
                state=prior.state,
                overlay=overlay,
                pending_state=target,
                qualifying_intervals=prior.qualifying_intervals + 1,
                intervals_in_state=prior.intervals_in_state + 1,
                transitioned=False,
                progressed=False,
                evaluation_time=instant,
                trigger_id=trigger.trigger_id,
            ),
            trigger=trigger,
            disposition=AttentionPopulation.SUPPRESSED,
        )


    debounce = int(policy.debounce_intervals)
    qualifying = prior.qualifying_intervals + 1 if prior.pending_state is target else 1
    if debounce > 0 and qualifying < debounce:
        trigger = _trigger(
            evidence=evidence,
            policy=policy,
            prior_state=prior.state,
            new_state=prior.state,
            overlay=overlay,
            disposition=AttentionPopulation.SUPPRESSED,
            reasons=tuple(item.value for item in evidence.progression_reasons)
            or (REASON_NO_MATERIAL_CHANGE,),
            expires_at=expires_at,
            suppression_detail=(
                "debounce has not completed the owner-gated qualifying intervals"
            ),
        )
        return AttentionTransition(
            prior=prior,
            current=_record(
                prior,
                state=prior.state,
                overlay=overlay,
                pending_state=target,
                qualifying_intervals=qualifying,
                intervals_in_state=prior.intervals_in_state + 1,
                transitioned=False,
                progressed=False,
                evaluation_time=instant,
                trigger_id=trigger.trigger_id,
            ),
            trigger=trigger,
            disposition=AttentionPopulation.SUPPRESSED,
        )

    trigger = _trigger(
        evidence=evidence,
        policy=policy,
        prior_state=prior.state,
        new_state=target,
        overlay=overlay,
        disposition=AttentionPopulation.ADMITTED,
        reasons=tuple(item.value for item in evidence.progression_reasons)
        or (REASON_NO_MATERIAL_CHANGE,),
        expires_at=expires_at,
        suppression_detail="none; the transition was admitted for evaluation",
    )
    return AttentionTransition(
        prior=prior,
        current=_record(
            prior,
            state=target,
            overlay=overlay,
            pending_state=None,
            qualifying_intervals=0,
            intervals_in_state=0,
            transitioned=True,
            progressed=True,
            evaluation_time=instant,
            trigger_id=trigger.trigger_id,
        ),
        trigger=trigger,
        disposition=AttentionPopulation.ADMITTED,
        progressed=True,
    )



@dataclass(frozen=True)
class AttentionPopulations:
    """The reconciled populations of `ME-04`.

    `eligible == evaluated + coalesced + suppressed + expired + budget_skipped +
    stale + failed + recovered + outstanding` holds by construction, counting
    only `budget_skipped` as terminal; `cycle_budget_skipped` marks requests that
    stayed outstanding because a cycle budget was exhausted.
    """

    eligible: int = 0
    outstanding: int = 0
    evaluated: int = 0
    coalesced: int = 0
    suppressed: int = 0
    expired: int = 0
    budget_skipped: int = 0
    cycle_budget_skipped: int = 0
    stale: int = 0
    failed: int = 0
    recovered: int = 0

    @property
    def reconciles(self) -> bool:
        return self.eligible == (
            self.evaluated
            + self.coalesced
            + self.suppressed
            + self.expired
            + self.budget_skipped
            + self.stale
            + self.failed
            + self.recovered
            + self.outstanding
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "eligible": self.eligible,
            "outstanding": self.outstanding,
            "evaluated": self.evaluated,
            "coalesced": self.coalesced,
            "suppressed": self.suppressed,
            "expired": self.expired,
            "budget_skipped": self.budget_skipped,
            "cycle_budget_skipped": self.cycle_budget_skipped,
            "stale": self.stale,
            "failed": self.failed,
            "recovered": self.recovered,
        }


@dataclass(frozen=True)
class AttentionTelemetry:
    """Bounded-work telemetry for one ledger."""

    submissions: int = 0
    admissions: int = 0
    coalesced: int = 0
    expired: int = 0
    cycle_budget_skipped: int = 0
    max_outstanding: int = 0
    transitions_by_state: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    failure_reasons: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "submissions": self.submissions,
            "admissions": self.admissions,
            "coalesced": self.coalesced,
            "expired": self.expired,
            "cycle_budget_skipped": self.cycle_budget_skipped,
            "max_outstanding": self.max_outstanding,
            "transitions_by_state": dict(self.transitions_by_state),
            "failure_reasons": dict(self.failure_reasons),
        }



class AttentionTriggerLedger:
    """In-memory, bounded attention request ledger.

    Not a store: nothing is persisted, and the ledger is deterministic
    bookkeeping that can be rebuilt by replaying the same submitted triggers. It
    coalesces duplicate deliveries per instrument, policy and cutoff, expires
    stale requests, bounds each cycle's work and never drops a request silently.
    """

    def __init__(self, *, policy: AttentionPolicy | None = None) -> None:
        self._policy = policy or AttentionPolicy()
        self._counts: Counter[str] = Counter()
        self._outstanding: dict[str, AttentionTrigger] = {}
        self._seen: set[str] = set()
        self._order: list[str] = []
        self._transitions: Counter[str] = Counter()
        self._failures: Counter[str] = Counter()
        self._max_outstanding = 0
        self._cycle_budget_skipped = 0

    @property
    def policy(self) -> AttentionPolicy:
        return self._policy

    @staticmethod
    def coalesce_key(trigger: AttentionTrigger) -> str:
        """The per-instrument, per-policy, per-cutoff convergence key."""

        return "|".join(
            (
                trigger.instrument_version_id,
                trigger.policy_version,
                iso_z(trigger.evidence_cutoff, field_name="evidence_cutoff"),
            )
        )

    def submit(
        self, trigger: AttentionTrigger, *, evaluation_time: datetime
    ) -> AttentionPopulation:
        """Record one trigger and return its population disposition."""

        if not isinstance(trigger, AttentionTrigger):
            raise AttentionContractError("trigger must be an AttentionTrigger")
        instant = require_utc(evaluation_time, field_name="evaluation_time")
        self._counts[AttentionPopulation.ELIGIBLE.value] += 1
        if trigger.disposition is not AttentionPopulation.ADMITTED:
            self._counts[trigger.disposition.value] += 1
            return trigger.disposition
        key = self.coalesce_key(trigger)
        if key in self._seen:
            self._counts[AttentionPopulation.COALESCED.value] += 1
            return AttentionPopulation.COALESCED
        if trigger.expires_at_utc is not None and trigger.expires_at_utc <= instant:
            self._counts[AttentionPopulation.EXPIRED.value] += 1
            return AttentionPopulation.EXPIRED
        self._seen.add(key)
        self._outstanding[trigger.trigger_id] = trigger
        self._order.append(trigger.trigger_id)
        self._counts[AttentionPopulation.ADMITTED.value] += 1
        self._max_outstanding = max(self._max_outstanding, len(self._outstanding))
        return AttentionPopulation.ADMITTED

    def due(
        self,
        *,
        evaluation_time: datetime,
        budget: int | None = None,
    ) -> tuple[AttentionTrigger, ...]:
        """Return at most `budget` due requests, fairly across instruments.

        Expired requests expire here. Requests left behind are counted as
        cycle-skipped and remain outstanding, so nothing is dropped silently.
        """

        instant = require_utc(evaluation_time, field_name="evaluation_time")
        for trigger_id in list(self._order):
            trigger = self._outstanding.get(trigger_id)
            if trigger is None:
                continue
            if trigger.expires_at_utc is not None and trigger.expires_at_utc <= instant:
                self._outstanding.pop(trigger_id, None)
                self._order.remove(trigger_id)
                self._counts[AttentionPopulation.EXPIRED.value] += 1
        if (
            not self._outstanding
            and budget is None
            and self._policy.work_budget_per_cycle is None
        ):
            # Nothing is queued and no budget is ratified: an inert configuration
            # is a no-op, not an error.
            return ()
        if budget is None:
            budget = self._policy.work_budget_per_cycle
        if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
            raise AttentionContractError("budget must be a positive integer")
        by_instrument: dict[str, list[AttentionTrigger]] = {}
        for trigger_id in self._order:
            trigger = self._outstanding[trigger_id]
            by_instrument.setdefault(trigger.instrument_version_id, []).append(trigger)
        selected: list[AttentionTrigger] = []
        round_index = 0
        while len(selected) < budget:
            progressed = False
            for instrument in sorted(by_instrument):
                queue = by_instrument[instrument]
                if round_index < len(queue):
                    selected.append(queue[round_index])
                    progressed = True
                    if len(selected) >= budget:
                        break
            if not progressed:
                break
            round_index += 1
        skipped = len(self._outstanding) - len(selected)
        if skipped > 0:
            self._cycle_budget_skipped += skipped
        return tuple(selected)


    def acknowledge(
        self,
        trigger_id: str,
        *,
        population: AttentionPopulation = AttentionPopulation.EVALUATED,
        state: AttentionState | None = None,
    ) -> bool:
        """Acknowledge one evaluated request. Idempotent on repeat."""

        if population not in TERMINAL_POPULATIONS:
            raise AttentionContractError(
                "an acknowledgement must use a terminal population"
            )
        removed = self._outstanding.pop(trigger_id, None)
        if removed is None:
            return False
        if trigger_id in self._order:
            self._order.remove(trigger_id)
        self._counts[population.value] += 1
        if state is not None:
            if not isinstance(state, AttentionState):
                raise AttentionContractError("state must be an AttentionState")
            self._transitions[state.value] += 1
        return True

    def fail(self, trigger_id: str, *, reason: str) -> bool:
        """Record a terminal failure for one request with its reason."""

        token = str(reason or "").strip()
        if not token:
            raise AttentionContractError("a failure reason is required")
        removed = self._outstanding.pop(trigger_id, None)
        if removed is None:
            return False
        if trigger_id in self._order:
            self._order.remove(trigger_id)
        self._counts[AttentionPopulation.FAILED.value] += 1
        self._failures[token] += 1
        return True

    def record_recovery(self, *, instrument_version_id: str) -> None:
        """Record one recovered request population for an instrument."""

        if not isinstance(instrument_version_id, str) or (
            not instrument_version_id.strip()
        ):
            raise AttentionContractError("instrument_version_id is required")
        self._counts[AttentionPopulation.RECOVERED.value] += 1
        self._counts[AttentionPopulation.ELIGIBLE.value] += 1

    def outstanding(self) -> tuple[AttentionTrigger, ...]:
        return tuple(
            self._outstanding[trigger_id]
            for trigger_id in self._order
            if trigger_id in self._outstanding
        )

    def populations(self) -> AttentionPopulations:
        counts = self._counts
        return AttentionPopulations(
            eligible=counts[AttentionPopulation.ELIGIBLE.value],
            outstanding=len(self._outstanding),
            evaluated=counts[AttentionPopulation.EVALUATED.value],
            coalesced=counts[AttentionPopulation.COALESCED.value],
            suppressed=counts[AttentionPopulation.SUPPRESSED.value],
            expired=counts[AttentionPopulation.EXPIRED.value],
            budget_skipped=counts[AttentionPopulation.BUDGET_SKIPPED.value],
            cycle_budget_skipped=self._cycle_budget_skipped,
            stale=counts[AttentionPopulation.STALE.value],
            failed=counts[AttentionPopulation.FAILED.value],
            recovered=counts[AttentionPopulation.RECOVERED.value],
        )

    def telemetry(self) -> AttentionTelemetry:
        counts = self._counts
        return AttentionTelemetry(
            submissions=counts[AttentionPopulation.ELIGIBLE.value],
            admissions=counts[AttentionPopulation.ADMITTED.value],
            coalesced=counts[AttentionPopulation.COALESCED.value],
            expired=counts[AttentionPopulation.EXPIRED.value],
            cycle_budget_skipped=self._cycle_budget_skipped,
            max_outstanding=self._max_outstanding,
            transitions_by_state=MappingProxyType(dict(self._transitions)),
            failure_reasons=MappingProxyType(dict(self._failures)),
        )



__all__ = [
    "ATTENTION_AUTHORITY",
    "ATTENTION_ENGINEERING_SCENARIO_POLICY_VERSION",
    "ATTENTION_POLICY_VERSION",
    "ATTENTION_RANK",
    "ATTENTION_SCHEMA_VERSION",
    "AttentionContractError",
    "AttentionEvidence",
    "AttentionHealthOverlay",
    "AttentionPolicy",
    "AttentionPopulation",
    "AttentionPopulations",
    "AttentionReason",
    "AttentionState",
    "AttentionStateRecord",
    "AttentionTelemetry",
    "AttentionTransition",
    "AttentionTrigger",
    "AttentionTriggerLedger",
    "DECLARED_POLICY_VERSIONS",
    "OWNER_INPUT_STATUS",
    "REASON_COVERAGE_UNUSABLE",
    "REASON_HEALTH_RECOVERED",
    "REASON_NO_MATERIAL_CHANGE",
    "REASON_OSCILLATION_HOLD",
    "REASON_POLICY_NOT_RATIFIED",
    "TERMINAL_POPULATIONS",
    "TRIGGER_ID_PREFIX",
    "advance_attention",
    "attention_rank",
    "attention_trigger_identity",
    "next_progression_state",
    "retreat_state",
]

