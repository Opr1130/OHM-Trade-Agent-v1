"""R5-A Continuous Opportunity Engine: the single pure composition boundary.

Adopted authority: `docs/architecture/v1.5.0/ARCHITECTURE.md` sections 2, 3, 4, 5
and 10, and the R5-0 register row `R5-A` (`SC-01`, `SC-02`, `SC-03`,
authority `SHADOW`, gate `G2`). The engine composes the existing owners into one
incremental path:

``F1`` coverage evidence -> ``EYE`` attention -> unified ``SCHED`` request ->
**existing** ``F2`` snapshot -> **existing** ``F3`` IGNITION -> ``F4`` episode ->
``HORIZON`` thesis.

Deliberate boundaries:

* **Feature-disabled.** `R5A_ENGINE_MODE` is the literal ``"off"`` and this
  module reads no environment variable, so no deployment can activate it. The
  constructor refuses any other mode: activation is an owner release act, not a
  code path.
* **Zero authority.** The engine returns shadow/read-only evidence. It cannot
  place, modify or cancel an order, cannot reserve funds, cannot size a position
  and cannot select or reserve an opportunity.
* **No synthesis.** The engine never builds a `FeatureSnapshot`: it consumes the
  canonical F2 snapshot it is given, refuses a snapshot whose evaluation cutoff
  differs from the request's, and refuses to proceed when the snapshot is absent.
  It cannot bypass warm-up or minimum coverage.
* **Deterministic.** Every instant is caller-supplied; no clock, no I/O, no
  randomness, no process identity.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from app.opip.contracts.detector import DetectorClaim, DetectorState
from app.opip.contracts.features import FeatureSnapshot
from app.opip.contracts.observation import SourceWatermark
from app.opip.contracts.opportunity import (
    OpportunityDeferral,
    OpportunityEpisode,
    OpportunityLifecycleResult,
)
from app.opip.contracts.opportunity_phases import episode_from_claim, episode_phase
from app.opip.contracts.temporal import require_utc
from app.opip.contracts.thesis import (
    HorizonEvidence,
    HorizonPolicy,
    HorizonThesis,
    classify_horizon,
)
from app.opip.detectors import ignition
from app.opip.events.attention import (
    AttentionEvidence,
    AttentionPolicy,
    AttentionPopulation,
    AttentionPopulations,
    AttentionReason,
    AttentionState,
    AttentionStateRecord,
    AttentionTransition,
    AttentionTrigger,
    AttentionTriggerLedger,
    advance_attention,
)
from app.opip.events.coverage import (
    IntervalEvidence,
    ObservationCoverage,
    reconcile_coverage,
)
from app.opip.streaming.coordinator import (
    CoordinatorLimits,
    CoordinatorTelemetry,
    EvaluationPath,
    EvaluationRequest,
    SubmissionOutcome,
    UnifiedEvaluationCoordinator,
    WorkPriority,
)

#: The only reachable mode in this increment. Activation is an owner release act.
R5A_ENGINE_MODE = "off"

#: Declared mode tokens. `shadow` is declared but not reachable here.
DECLARED_ENGINE_MODES: tuple[str, ...] = ("off", "shadow")

#: This component carries no trading, admission, allocation or execution authority.
R5A_ENGINE_AUTHORITY = "NONE"

#: Environment variables read by this module. Empty by construction.
R5A_ENV_VARIABLES_READ: tuple[str, ...] = ()

#: Schema version of one engine cycle report.
R5A_CYCLE_SCHEMA_VERSION = "r5-a-engine-cycle-v1"


class OpportunityEngineError(ValueError):
    """An engine contract violation. Always fails closed."""


def _request_priority(
    transition: AttentionTransition,
) -> WorkPriority:
    """Priority of the work an attention transition requests.

    A regression or a coverage-recovery observation concerns an existing
    obligation, so it is served before new discovery. Discovery never outranks
    protection or reconciliation; this function cannot emit those classes.
    """

    if transition.regressed:
        return WorkPriority.OPEN_POSITION
    if transition.disposition is AttentionPopulation.RECOVERED:
        return WorkPriority.RECONCILIATION
    return WorkPriority.DISCOVERY


def _request_path(transition: AttentionTransition) -> EvaluationPath:
    if transition.disposition is AttentionPopulation.RECOVERED:
        return EvaluationPath.RECOVERY_BACKFILL
    return EvaluationPath.STREAM_EVENT



@dataclass(frozen=True)
class AttentionCycle:
    """The result of one observation-and-attention step."""

    coverage: ObservationCoverage
    transition: AttentionTransition
    request: EvaluationRequest | None
    submission: SubmissionOutcome | None
    populations: AttentionPopulations
    attention_telemetry: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not isinstance(self.coverage, ObservationCoverage):
            raise OpportunityEngineError("coverage must be an ObservationCoverage")
        if not isinstance(self.transition, AttentionTransition):
            raise OpportunityEngineError("transition must be an AttentionTransition")
        if (self.request is None) != (self.submission is None):
            raise OpportunityEngineError(
                "a request and its submission outcome are recorded together"
            )


@dataclass(frozen=True)
class EvaluationOutcome:
    """One served (or failed) evaluation and the evidence it produced."""

    evaluation_identity: str
    instrument_version_id: str
    evaluation_cutoff: datetime
    disposition: str
    snapshot_id: str | None = None
    reason: str = "none"
    claim_id: str | None = None
    episode_id: str | None = None
    episode: OpportunityEpisode | None = None
    lifecycle_result: OpportunityLifecycleResult | None = None
    thesis: HorizonThesis | None = None
    detector_phase: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, str) or not self.disposition.strip():
            raise OpportunityEngineError("disposition is required")
        object.__setattr__(
            self,
            "evaluation_cutoff",
            require_utc(self.evaluation_cutoff, field_name="evaluation_cutoff"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_identity": self.evaluation_identity,
            "instrument_version_id": self.instrument_version_id,
            "evaluation_cutoff": self.evaluation_cutoff.isoformat(),
            "disposition": self.disposition,
            "snapshot_id": self.snapshot_id,
            "reason": self.reason,
            "claim_id": self.claim_id,
            "episode_id": self.episode_id,
            "horizon_class": (
                None if self.thesis is None else self.thesis.horizon_class.value
            ),
            "detector_phase": self.detector_phase,
        }


@dataclass(frozen=True)
class EvaluationCycle:
    """The result of one evaluation step."""

    served: tuple[EvaluationOutcome, ...] = ()
    populations: AttentionPopulations = AttentionPopulations()
    attention_telemetry: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    coordinator_telemetry: CoordinatorTelemetry | None = None

    @property
    def claims(self) -> tuple[EvaluationOutcome, ...]:
        return tuple(outcome for outcome in self.served if outcome.claim_id is not None)

    @property
    def failures(self) -> tuple[EvaluationOutcome, ...]:
        return tuple(
            outcome for outcome in self.served if outcome.disposition == "FAILED"
        )

    @property
    def no_claim_evaluations(self) -> tuple[EvaluationOutcome, ...]:
        return tuple(
            outcome
            for outcome in self.served
            if outcome.disposition == "NO_CLAIM"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": R5A_CYCLE_SCHEMA_VERSION,
            "mode": R5A_ENGINE_MODE,
            "authority": R5A_ENGINE_AUTHORITY,
            "served": [outcome.to_dict() for outcome in self.served],
            "populations": self.populations.to_dict(),
            "attention_telemetry": dict(self.attention_telemetry),
            "coordinator_telemetry": (
                None
                if self.coordinator_telemetry is None
                else self.coordinator_telemetry.to_dict()
            ),
        }



class ContinuousOpportunityEngine:
    """The single R5-A composition. Pure, deterministic and feature-disabled."""

    def __init__(
        self,
        *,
        attention_policy: AttentionPolicy,
        coordinator_limits: CoordinatorLimits,
        horizon_policy: HorizonPolicy | None = None,
        detector_evaluate: Callable[..., tuple[list[DetectorClaim], DetectorState]] = (
            ignition.evaluate
        ),
        mode: str = R5A_ENGINE_MODE,
    ) -> None:
        if mode not in DECLARED_ENGINE_MODES:
            raise OpportunityEngineError(f"undeclared engine mode: {mode!r}")
        if mode != R5A_ENGINE_MODE:
            raise OpportunityEngineError(
                "no activation path exists in this increment; the engine is off"
            )
        if not isinstance(attention_policy, AttentionPolicy):
            raise OpportunityEngineError("attention_policy must be an AttentionPolicy")
        if not isinstance(coordinator_limits, CoordinatorLimits):
            raise OpportunityEngineError("coordinator_limits must be CoordinatorLimits")
        if horizon_policy is not None and not isinstance(horizon_policy, HorizonPolicy):
            raise OpportunityEngineError("horizon_policy must be a HorizonPolicy")
        if not callable(detector_evaluate):
            raise OpportunityEngineError("detector_evaluate must be callable")
        self._mode = mode
        self._attention_policy = attention_policy
        self._horizon_policy = horizon_policy or HorizonPolicy()
        self._detector = detector_evaluate
        self._ledger = AttentionTriggerLedger(policy=attention_policy)
        self._coordinator = UnifiedEvaluationCoordinator(limits=coordinator_limits)
        self._states: dict[str, AttentionStateRecord] = {}
        self._detector_states: dict[str, DetectorState] = {}
        self._episodes: dict[str, OpportunityEpisode] = {}
        self._theses: dict[str, HorizonThesis] = {}
        self._counts: dict[str, int] = {
            "observations_ingested": 0,
            "coverage_reconciled": 0,
            "attention_evaluations": 0,
            "requests_submitted": 0,
            "requests_coalesced": 0,
            "requests_refused": 0,
            "requests_evicted": 0,
            "evaluations_served": 0,
            "claims_emitted": 0,
            "episodes_opened": 0,
            "no_claim_evaluations": 0,
            "failed_evaluations": 0,
            "theses_classified": 0,
            "abstained_theses": 0,
        }

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def authority(self) -> str:
        return R5A_ENGINE_AUTHORITY

    @property
    def attention_policy(self) -> AttentionPolicy:
        return self._attention_policy

    @property
    def coordinator(self) -> UnifiedEvaluationCoordinator:
        return self._coordinator

    @property
    def ledger(self) -> AttentionTriggerLedger:
        return self._ledger

    def attention_state(self, instrument_version_id: str) -> AttentionStateRecord:
        return self._states.get(
            instrument_version_id, AttentionStateRecord.initial(instrument_version_id)
        )

    def detector_state(self, instrument_version_id: str) -> DetectorState | None:
        return self._detector_states.get(instrument_version_id)

    def episodes(self) -> Mapping[str, OpportunityEpisode]:
        return MappingProxyType(dict(self._episodes))

    def theses(self) -> Mapping[str, HorizonThesis]:
        return MappingProxyType(dict(self._theses))

    def counts(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._counts))


    def observe(
        self,
        *,
        instrument_version_id: str,
        venue_instrument_id: str,
        source_label: str,
        reference_data_version: str,
        window_start_utc: datetime,
        interval_seconds: int,
        evidence: Sequence[IntervalEvidence],
        watermark: SourceWatermark,
        evaluation_cutoff: datetime,
        evaluated_at_utc: datetime,
        source_event_ids: Sequence[str] = (),
        progression_reasons: Sequence[AttentionReason] = (),
        regression_reasons: Sequence[AttentionReason] = (),
        requested_state: AttentionState | None = None,
        source_available: bool = True,
        instrument_supported: bool = True,
        metadata_changed: bool = False,
    ) -> AttentionCycle:
        """F1 coverage -> EYE attention -> one deduplicated evaluation request."""

        instant = require_utc(evaluation_cutoff, field_name="evaluation_cutoff")
        self._counts["observations_ingested"] += len(tuple(evidence))
        coverage = reconcile_coverage(
            instrument_version_id=instrument_version_id,
            venue_instrument_id=venue_instrument_id,
            source_label=source_label,
            reference_data_version=reference_data_version,
            window_start_utc=window_start_utc,
            interval_seconds=interval_seconds,
            evidence=evidence,
            watermark=watermark,
            evaluated_at_utc=evaluated_at_utc,
            source_available=source_available,
            instrument_supported=instrument_supported,
            metadata_changed=metadata_changed,
        )
        self._counts["coverage_reconciled"] += 1
        attention_evidence = AttentionEvidence(
            instrument_version_id=instrument_version_id,
            venue_instrument_id=venue_instrument_id,
            evaluation_time=instant,
            evidence_cutoff=instant,
            coverage=coverage,
            source_event_ids=tuple(source_event_ids),
            progression_reasons=tuple(progression_reasons),
            regression_reasons=tuple(regression_reasons),
            requested_state=requested_state,
        )
        transition = advance_attention(
            self.attention_state(instrument_version_id),
            attention_evidence,
            self._attention_policy,
        )
        self._states[instrument_version_id] = transition.current
        self._counts["attention_evaluations"] += 1
        trigger = transition.trigger
        if trigger is None:
            raise OpportunityEngineError(
                "every attention evaluation must produce one trigger record"
            )
        population = self._ledger.submit(trigger, evaluation_time=instant)
        request: EvaluationRequest | None = None
        submission: SubmissionOutcome | None = None
        if population is AttentionPopulation.ADMITTED:
            request = EvaluationRequest.create(
                path=_request_path(transition),
                priority=_request_priority(transition),
                instrument_version_id=instrument_version_id,
                venue_instrument_id=venue_instrument_id,
                evaluation_cutoff=instant,
                requested_at_utc=instant,
                expires_at_utc=trigger.expires_at_utc,
                policy_version=self._attention_policy.policy_version,
                attention_trigger_id=trigger.trigger_id,
            )
            evicted_before = self._coordinator.telemetry().evicted_backpressure
            submission = self._coordinator.submit(request, evaluation_time=instant)
            # An eviction of already-admitted work is a backlog release, not a
            # submission outcome, so it is counted in its own population instead of
            # being added to the refusal count.
            self._counts["requests_evicted"] += (
                self._coordinator.telemetry().evicted_backpressure - evicted_before
            )
            self._counts["requests_submitted"] += 1
            if submission.disposition.value == "COALESCED":
                self._counts["requests_coalesced"] += 1
            elif submission.disposition.value == "DROPPED_BACKPRESSURE":
                # A refused submission was never admitted. An eviction of already
                # admitted work is not a submission outcome and is reported by the
                # coordinator telemetry (`evicted_backpressure`) instead, so the two
                # populations are never summed here.
                self._counts["requests_refused"] += 1
        return AttentionCycle(
            coverage=coverage,
            transition=transition,
            request=request,
            submission=submission,
            populations=self._ledger.populations(),
            attention_telemetry=MappingProxyType(self._ledger.telemetry().to_dict()),
        )


    def evaluate_due(
        self,
        *,
        evaluation_time: datetime,
        snapshots: Mapping[str, FeatureSnapshot],
        detector_states: Mapping[str, DetectorState] | None = None,
        deferrals: Mapping[str, OpportunityDeferral] | None = None,
        budget: int | None = None,
    ) -> EvaluationCycle:
        """F2 snapshot -> F3 IGNITION -> F4 episode -> HORIZON thesis.

        A missing snapshot, a snapshot for a different instrument, a snapshot
        whose cutoff differs from the request, or a detector contract violation
        fails that one evaluation closed. None of them can produce a favourable
        outcome, and each failure is retained as evidence.
        """

        instant = require_utc(evaluation_time, field_name="evaluation_time")
        due = self._ledger.due(evaluation_time=instant, budget=budget)
        outcomes: list[EvaluationOutcome] = []
        for trigger in due:
            outcomes.append(
                self._evaluate_one(
                    trigger=trigger,
                    evaluation_time=instant,
                    snapshots=snapshots,
                    detector_states=detector_states or {},
                    deferrals=deferrals or {},
                )
            )
        return EvaluationCycle(
            served=tuple(outcomes),
            populations=self._ledger.populations(),
            attention_telemetry=MappingProxyType(self._ledger.telemetry().to_dict()),
            coordinator_telemetry=self._coordinator.telemetry(),
        )

    def _fail(
        self,
        *,
        trigger: AttentionTrigger,
        evaluation_time: datetime,
        reason: str,
        snapshot_id: str | None = None,
    ) -> EvaluationOutcome:
        self._ledger.fail(trigger.trigger_id, reason=reason)
        self._counts["failed_evaluations"] += 1
        return EvaluationOutcome(
            evaluation_identity=trigger.trigger_id,
            instrument_version_id=trigger.instrument_version_id,
            evaluation_cutoff=evaluation_time,
            disposition="FAILED",
            snapshot_id=snapshot_id,
            reason=reason,
        )

    def _evaluate_one(
        self,
        *,
        trigger: AttentionTrigger,
        evaluation_time: datetime,
        snapshots: Mapping[str, FeatureSnapshot],
        detector_states: Mapping[str, DetectorState],
        deferrals: Mapping[str, OpportunityDeferral],
    ) -> EvaluationOutcome:
        snapshot = snapshots.get(trigger.instrument_version_id)
        if snapshot is None:
            return self._fail(
                trigger=trigger,
                evaluation_time=evaluation_time,
                reason="snapshot_unavailable",
            )
        if not isinstance(snapshot, FeatureSnapshot):
            return self._fail(
                trigger=trigger,
                evaluation_time=evaluation_time,
                reason="snapshot_not_a_canonical_feature_snapshot",
            )
        if snapshot.instrument_version_id != trigger.instrument_version_id:
            return self._fail(
                trigger=trigger,
                evaluation_time=evaluation_time,
                reason="snapshot_instrument_mismatch",
                snapshot_id=snapshot.snapshot_id,
            )
        if snapshot.evaluation_cutoff != trigger.evidence_cutoff:
            return self._fail(
                trigger=trigger,
                evaluation_time=evaluation_time,
                reason="snapshot_cutoff_mismatch",
                snapshot_id=snapshot.snapshot_id,
            )
        prior_state = detector_states.get(
            trigger.instrument_version_id
        ) or self._detector_states.get(trigger.instrument_version_id)
        if prior_state is None:
            prior_state = DetectorState(
                instrument_version_id=trigger.instrument_version_id,
                venue_instrument_id=trigger.venue_instrument_id,
            )
        try:
            claims, next_state = self._detector(
                snapshot, prior_state, trigger.evidence_cutoff
            )
        except Exception as exc:  # fail closed on any detector contract violation
            return self._fail(
                trigger=trigger,
                evaluation_time=evaluation_time,
                reason=f"detector_contract_violation:{type(exc).__name__}",
                snapshot_id=snapshot.snapshot_id,
            )
        self._detector_states[trigger.instrument_version_id] = next_state
        self._ledger.acknowledge(
            trigger.trigger_id,
            state=self.attention_state(trigger.instrument_version_id).state,
        )
        self._counts["evaluations_served"] += 1


        if not claims:
            self._counts["no_claim_evaluations"] += 1
            return EvaluationOutcome(
                evaluation_identity=trigger.trigger_id,
                instrument_version_id=trigger.instrument_version_id,
                evaluation_cutoff=evaluation_time,
                disposition="NO_CLAIM",
                snapshot_id=snapshot.snapshot_id,
                reason="no_claim",
                detector_phase=next_state.phase.value,
            )
        claim = sorted(claims, key=lambda item: item.claim_id)[0]
        lifecycle = episode_from_claim(
            claim,
            evaluation_time=trigger.evidence_cutoff,
            deferral=deferrals.get(trigger.instrument_version_id),
        )
        self._episodes[lifecycle.episode.episode_id] = lifecycle.episode
        self._counts["claims_emitted"] += 1
        self._counts["episodes_opened"] += 1
        thesis = classify_horizon(
            HorizonEvidence(
                episode_id=lifecycle.episode.episode_id,
                claim_id=claim.claim_id,
                decision_id=f"r5a-attention:{trigger.trigger_id}",
                evidence_cutoff=trigger.evidence_cutoff,
                classified_at=evaluation_time,
                supporting_evidence=tuple(trigger.reason_codes),
                detector_support=True,
            ),
            self._horizon_policy,
        )
        self._theses[thesis.thesis_id] = thesis
        self._counts["theses_classified"] += 1
        if thesis.research_only:
            self._counts["abstained_theses"] += 1
        return EvaluationOutcome(
            evaluation_identity=trigger.trigger_id,
            instrument_version_id=trigger.instrument_version_id,
            evaluation_cutoff=evaluation_time,
            disposition="CLAIM",
            snapshot_id=snapshot.snapshot_id,
            reason="dormant_to_ignition",
            claim_id=claim.claim_id,
            episode_id=lifecycle.episode.episode_id,
            episode=lifecycle.episode,
            lifecycle_result=lifecycle,
            thesis=thesis,
            detector_phase=next_state.phase.value,
        )

    def phase_of(self, episode_id: str, *, evaluation_time: datetime) -> str:
        """Project a retained episode onto its R5-A phase."""

        episode = self._episodes.get(episode_id)
        if episode is None:
            raise OpportunityEngineError("unknown episode")
        return episode_phase(episode, evaluation_time=evaluation_time).value

    def cycle_report(self) -> dict[str, Any]:
        """Deterministic cycle report for evidence and operator inspection."""

        return {
            "schema_version": R5A_CYCLE_SCHEMA_VERSION,
            "mode": self._mode,
            "authority": R5A_ENGINE_AUTHORITY,
            "attention_policy_version": self._attention_policy.policy_version,
            "attention_policy_ratified": self._attention_policy.ratified,
            "horizon_policy_version": self._horizon_policy.policy_version,
            "horizon_policy_configured": self._horizon_policy.configured,
            "counts": dict(self._counts),
            "populations": self._ledger.populations().to_dict(),
            "attention_telemetry": self._ledger.telemetry().to_dict(),
            "coordinator_telemetry": self._coordinator.telemetry().to_dict(),
            "episodes": sorted(self._episodes),
            "theses": [
                self._theses[thesis_id].to_dict() for thesis_id in sorted(self._theses)
            ],
        }


def engine_mode() -> str:
    """Return the compiled engine mode.

    The value is a code constant. It is not read from the environment, so no
    deployment, `.env` file or Compose override can activate the engine.
    """

    return R5A_ENGINE_MODE


def engine_reads_environment() -> bool:
    """Whether this module reads any environment variable. Always False."""

    return bool(R5A_ENV_VARIABLES_READ)


__all__ = [
    "AttentionCycle",
    "ContinuousOpportunityEngine",
    "DECLARED_ENGINE_MODES",
    "EvaluationCycle",
    "EvaluationOutcome",
    "OpportunityEngineError",
    "R5A_CYCLE_SCHEMA_VERSION",
    "R5A_ENGINE_AUTHORITY",
    "R5A_ENGINE_MODE",
    "R5A_ENV_VARIABLES_READ",
    "engine_mode",
    "engine_reads_environment",
]

