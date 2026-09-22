"""The committee case runner: bounded, idempotent, independent seats.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The runner performs one governed pass over the seated providers for a single
case. It is deliberately an observer: it returns an immutable case outcome and
has no other effect. There is no code path from here to admission, ranking,
sizing, protection, or exchange execution.

Guarantees enforced here:

* every seat receives the identical screened evidence view for the case;
* a seat never sees another seat's answer, because only the evidence snapshot
  is ever placed on the wire;
* a committed logical observation is never re-queried and never duplicated;
* a served identity that does not match the requested seat is rejected rather
  than attributed to the wrong model;
* retries are bounded, and only for failure classes where a second attempt can
  plausibly differ;
* a provider failure, an abstention, and a missing seat are each preserved as
  themselves - never as agreement and never as a vote.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from app.opip.committee.contracts import (
    CommitteeCase,
    CommitteeCaseOutcome,
    CostCompleteness,
    EvaluationPhase,
    ObservationStatus,
    ProviderCallOutcome,
    ProviderFailureClass,
    ProviderFamily,
    ReproducibilityClass,
    logical_observation_id,
)
from app.opip.committee.ledger import COMMITTED_STATUSES, ObservationLedger
from app.opip.committee.opinion import OpinionParseError, parse_structured_opinion
from app.opip.committee.outbound import screen_model_bound_view
from app.opip.committee.providers import (
    CommitteeProvider,
    ProviderAvailability,
    ProviderInvocationError,
    ProviderRawResponse,
    ProviderWireRequest,
    RETRYABLE_FAILURE_CLASSES,
    resolve_seated_providers,
)
from app.opip.committee.settings import (
    committee_shadow_enabled,
    resolve_committee_cost_ceiling,
)
from app.opip.decision_intelligence.serialization import stable_hash

#: The provider-agnostic instruction. One prompt for every seat is what makes
#: the evidence logically equivalent across members.
COMMITTEE_SYSTEM_PROMPT = (
    "You are one independent reviewer on a research committee. You are given a "
    "frozen evidence snapshot with a cutoff time. Answer only from that "
    "evidence.\n"
    "You have no authority to act: you cannot place, modify, or cancel orders, "
    "size positions, or change risk limits. Never propose execution.\n"
    "Reply with a single JSON object and nothing else - no prose, no markdown "
    "fence - with exactly these keys: schema_version (integer 1), "
    "evidence_sufficiency (SUFFICIENT|PARTIAL|INSUFFICIENT), assessment "
    "(SUPPORTIVE|OPPOSING|NEUTRAL|UNCERTAIN), hypothesis (string), confidence "
    "(integer 0-100 or null), supporting_evidence_refs (array of evidence_id "
    "strings), contradicting_evidence_refs (array of evidence_id strings), "
    "major_assumptions (array of strings), risk_factors (array of strings), "
    "missing_evidence (array of strings), alternative_explanations (array of "
    "strings), recommended_research_action (NO_ACTION|GATHER_MORE_EVIDENCE|"
    "REVISIT_AT_NEXT_WINDOW|ESCALATE_FOR_HUMAN_REVIEW), abstention_reason "
    "(string or null).\n"
    "Cite only evidence_id values that appear in the snapshot. If the evidence "
    "is not sufficient to form a view, abstain with a reason instead of "
    "guessing."
)

DEFAULT_MAX_OUTPUT_TOKENS = 1_200
DEFAULT_TIMEOUT_SECONDS = 60

#: The largest attempt number ``ProviderCallOutcome`` accepts. Reaching it means
#: the seat may not be invoked again, because no legal outcome could be recorded.
MAX_RECORDED_ATTEMPTS = 5

PROMPT_HASH_DOMAIN = "COMMITTEE-PROMPT"
WIRE_HASH_DOMAIN = "COMMITTEE-WIRE"


class CommitteePolicyViolation(RuntimeError):
    """The committee refused to proceed because a policy invariant was at risk."""


class CommitteeReplayDivergenceError(CommitteePolicyViolation):
    """A replay of a committed logical observation produced different content.

    Raised instead of overwriting history. The committed observation is left
    untouched and the divergence is surfaced for a human.
    """


@dataclass(frozen=True)
class CommitteeSeatResult:
    """One seat's result for one case."""

    provider_family: ProviderFamily
    logical_observation_id: str
    outcome: ProviderCallOutcome

    @property
    def answered(self) -> bool:
        return self.outcome.status in COMMITTED_STATUSES

    @property
    def abstained(self) -> bool:
        return self.outcome.opinion is not None and self.outcome.opinion.abstained


@dataclass(frozen=True)
class CommitteeRunResult:
    """The complete, auditable result of one governed committee pass."""

    case: CommitteeCase
    case_outcome: CommitteeCaseOutcome
    seats: tuple[CommitteeSeatResult, ...]
    evidence_view_hash: str
    prompt_hash: str
    phase: EvaluationPhase

    @property
    def independent_opinions(self) -> tuple[ProviderCallOutcome, ...]:
        return tuple(seat.outcome for seat in self.seats if seat.answered)

    @property
    def answered_count(self) -> int:
        return sum(1 for seat in self.seats if seat.answered)

    @property
    def failed_count(self) -> int:
        return sum(
            1
            for seat in self.seats
            if seat.outcome.status in (ObservationStatus.FAILED, ObservationStatus.INVALID)
        )

    @property
    def completeness_basis_points(self) -> int:
        seated = len(self.case.policy.seated_providers)
        return (self.answered_count * 10_000) // seated


class CommitteeRunner:
    """Runs one governed committee case over the seated providers."""

    def __init__(
        self,
        *,
        providers: Mapping[ProviderFamily, CommitteeProvider],
        ledger: ObservationLedger,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        now: Callable[[], datetime] | None = None,
        settings: object | None = None,
    ) -> None:
        self._providers: Mapping[ProviderFamily, CommitteeProvider] = dict(providers)
        self._ledger = ledger
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._now: Callable[[], datetime] = now or (
            lambda: datetime.now(timezone.utc)
        )
        # Injected so callers and tests can state the enablement gate explicitly
        # rather than depending on ambient process settings.
        self._settings = settings

    def run_case(
        self,
        case: CommitteeCase,
        *,
        phase: EvaluationPhase = EvaluationPhase.PROSPECTIVE,
        replay_existing: bool = False,
    ) -> CommitteeRunResult:
        """Execute one governed pass.

        ``replay_existing`` opts into re-asking seats whose observation is
        already committed. When the answer differs from the committed opinion,
        the run fails explicitly rather than overwriting sealed evidence.
        """
        if not isinstance(case, CommitteeCase):
            raise CommitteePolicyViolation("run_case requires a CommitteeCase")
        if not isinstance(phase, EvaluationPhase):
            raise CommitteePolicyViolation("invalid evaluation phase")
        # The enablement gate is enforced here, at the execution API, so the
        # advertised off/shadow switch actually governs model egress and spend.
        # An operator who has not enabled the committee cannot reach a provider
        # by constructing a runner directly.
        if not committee_shadow_enabled(self._settings):
            raise CommitteePolicyViolation(
                "the Intelligence Committee is disabled; set OPIP_COMMITTEE_MODE="
                "shadow to permit committee work"
            )

        # Screened once, shared by every seat: this is what makes the evidence
        # logically equivalent across members and prevents cross-contamination.
        screened_view = screen_model_bound_view(case.snapshot.model_bound_view())
        evidence_view_hash = stable_hash(WIRE_HASH_DOMAIN, screened_view)
        prompt_hash = stable_hash(PROMPT_HASH_DOMAIN, COMMITTEE_SYSTEM_PROMPT)

        providers = resolve_seated_providers(
            policy_families=case.policy.seated_providers, providers=self._providers
        )

        started_at = self._now()
        seats: list[CommitteeSeatResult] = []
        # Seed the case budget with spend already recorded for this case, so a
        # redelivery cannot spend the whole ceiling again: duplicate
        # acknowledgements contribute nothing, so without this the case could
        # exceed its declared budget across runs.
        spent_microunits = self._ledger.case_spend_microunits(case.case_id)
        for family in case.policy.seated_providers:
            seat, reserved = self._run_seat(
                case=case,
                family=family,
                provider=providers[family],
                screened_view=screened_view,
                evidence_view_hash=evidence_view_hash,
                prompt_hash=prompt_hash,
                spent_microunits=spent_microunits,
                replay_existing=replay_existing,
            )
            seats.append(seat)
            # Carry forward the reservations this seat actually consumed, not
            # just its final reported cost. A timed-out or retried seat can have
            # reserved more than it finally reported (or reported nothing), and
            # dropping that would let the next seat spend past the case ceiling.
            spent_microunits += reserved
        completed_at = self._now()
        if completed_at < started_at:
            completed_at = started_at

        from app.opip.decision_intelligence.identity import Provenance

        case_outcome = CommitteeCaseOutcome(
            case_id=case.case_id,
            evidence_snapshot_hash=case.snapshot.snapshot_hash,
            committee_policy_version=case.policy.policy_version,
            phase=phase,
            started_at=started_at,
            completed_at=completed_at,
            outcomes=tuple(seat.outcome for seat in seats),
            canonical_binding=case.canonical_binding,
            provenance=Provenance(
                producing_component="app.opip.committee.runtime",
                artifact_or_build_id=case.policy.policy_version,
                process_instance_id=case.provenance.process_instance_id,
                emitted_at=completed_at,
                source_record_refs=(case.snapshot.snapshot_hash,),
            ),
        )
        return CommitteeRunResult(
            case=case,
            case_outcome=case_outcome,
            seats=tuple(seats),
            evidence_view_hash=evidence_view_hash,
            prompt_hash=prompt_hash,
            phase=phase,
        )

    # ------------------------------------------------------------------ seats

    def _run_seat(
        self,
        *,
        case: CommitteeCase,
        family: ProviderFamily,
        provider: CommitteeProvider,
        screened_view: Mapping[str, object],
        evidence_view_hash: str,
        prompt_hash: str,
        spent_microunits: int,
        replay_existing: bool,
    ) -> tuple[CommitteeSeatResult, int]:
        """Run one seat, returning its result and the cost it reserved."""
        requested_model = provider.model_identifier()
        logical_id = logical_observation_id(
            case_id=case.case_id,
            provider_family=family,
            requested_model=requested_model,
            prompt_version=case.policy.prompt_version,
            committee_policy_version=case.policy.policy_version,
            evidence_snapshot_hash=case.snapshot.snapshot_hash,
        )
        wire = ProviderWireRequest(
            case_id=case.case_id,
            logical_observation_id=logical_id,
            model=requested_model,
            system_prompt=COMMITTEE_SYSTEM_PROMPT,
            user_payload=screened_view,
            max_output_tokens=self._max_output_tokens,
            timeout_seconds=self._timeout_seconds,
        )
        input_hash = stable_hash(
            WIRE_HASH_DOMAIN,
            {
                "model": requested_model,
                "prompt_version": case.policy.prompt_version,
                "prompt_hash": prompt_hash,
                "evidence_view_hash": evidence_view_hash,
            },
        )

        committed = self._ledger.committed_opinion(logical_id)
        if committed is not None and not replay_existing:
            return (
                CommitteeSeatResult(
                    family,
                    logical_id,
                    self._duplicate_acknowledgement(
                        case=case,
                        family=family,
                        requested_model=requested_model,
                        logical_id=logical_id,
                        input_hash=input_hash,
                        committed=committed,
                    ),
                ),
                # A duplicate acknowledgement performs no new invocation and
                # therefore reserves nothing.
                0,
            )

        attempt = self._ledger.attempt_count(logical_id) + 1
        # The attempt budget bounds *new* invocations for a seat that has not yet
        # produced an opinion. It is cumulative per logical seat, bounded by both
        # the policy's declared retry limit and the contract's serialization cap,
        # because a redelivery must not restart the budget: with
        # max_attempts_per_seat=1 a seat that already failed once must not be
        # invoked again, otherwise repeated deliveries multiply provider calls
        # and spend past the declared retry policy. A seat that already holds a
        # committed opinion is exempt, since re-asking it is a drift
        # verification rather than a spend decision.
        attempt_ceiling = (
            MAX_RECORDED_ATTEMPTS
            if committed is not None
            else min(case.policy.max_attempts_per_seat, MAX_RECORDED_ATTEMPTS)
        )
        if attempt > attempt_ceiling:
            # The provider must not be called again just to construct an outcome
            # the contract would reject, nor to exceed the declared policy.
            # Return a governed disposition instead of turning a typed, nonfatal
            # state into an exception after another external call.
            return (
                CommitteeSeatResult(
                    family,
                    logical_id,
                    self._exhausted_attempt_outcome(
                        case=case,
                        family=family,
                        requested_model=requested_model,
                        logical_id=logical_id,
                        input_hash=input_hash,
                        provider=provider,
                        recorded_attempts=min(attempt - 1, MAX_RECORDED_ATTEMPTS),
                    ),
                ),
                0,
            )

        outcome, reserved, already_recorded = self._invoke_within_budget(            case=case,
            family=family,
            provider=provider,
            wire=wire,
            input_hash=input_hash,
            attempt=attempt,
            spent_microunits=spent_microunits,
        )
        if outcome is None:
            return (
                CommitteeSeatResult(
                    family,
                    logical_id,
                    self._budget_skipped_outcome(
                        case=case,
                        family=family,
                        requested_model=requested_model,
                        logical_id=logical_id,
                        input_hash=input_hash,
                        provider=provider,
                        attempt=attempt,
                        reason=self._attempt_budget_reason(
                            case=case,
                            estimate=provider.estimate_cost_microunits(wire),
                            committed_spend=spent_microunits,
                            seat_reserved=0,
                        )
                        or "committee cost ceiling would be exceeded",
                    ),
                ),
                0,
            )

        if replay_existing and committed is not None and outcome.opinion is not None:
            if outcome.opinion.opinion_hash != committed.opinion.opinion_hash:
                raise CommitteeReplayDivergenceError(
                    "replayed observation diverges from the sealed opinion for "
                    f"{family.value}; history is preserved and the replay is refused"
                )

        if not already_recorded:
            # A retry that was blocked by the budget already persisted its prior
            # failure; recording it again would double-count the attempt and
            # append duplicate raw evidence.
            self._ledger.record(outcome)
        # The seat is charged the cumulative per-attempt cost computed while
        # invoking, so a retried or under-estimated seat cannot leave room for the
        # next seat to spend past the case ceiling.
        return CommitteeSeatResult(family, logical_id, outcome), reserved

    def _duplicate_acknowledgement(
        self,
        *,
        case: CommitteeCase,
        family: ProviderFamily,
        requested_model: str,
        logical_id: str,
        input_hash: str,
        committed: ProviderCallOutcome,
    ) -> ProviderCallOutcome:
        """Acknowledge an already-committed logical observation.

        The acknowledgement reuses the original call's timings rather than
        stamping a fresh request time. A replay can legitimately arrive later than
        the original response, and a synthetic request time after it would be a
        contract-violating, falsified provider timing. The provider is not asked
        again: a second independent opinion must not be manufactured.
        """
        return ProviderCallOutcome(
            logical_observation_id=logical_id,
            case_id=case.case_id,
            provider_family=family,
            requested_model=requested_model,
            status=ObservationStatus.DUPLICATE_OK,
            attempt=committed.attempt,
            reproducibility=committed.reproducibility,
            request_at=committed.request_at,
            input_hash=input_hash,
            reported_provider=committed.reported_provider,
            reported_model=committed.reported_model,
            opinion=committed.opinion,
            raw_response_ref=committed.raw_response_ref,
            response_at=committed.response_at,
            latency_micros=committed.latency_micros,
            input_tokens=committed.input_tokens,
            output_tokens=committed.output_tokens,
            estimated_cost_microunits=None,
            cost_completeness=CostCompleteness.UNKNOWN,
            detail=(
                "logical observation already committed; not re-queried; "
                "timings are the original call's"
            ),
        )

    def _invoke_within_budget(
        self,
        *,
        case: CommitteeCase,
        family: ProviderFamily,
        provider: CommitteeProvider,
        wire: ProviderWireRequest,
        input_hash: str,
        attempt: int,
        spent_microunits: int,
    ) -> tuple[ProviderCallOutcome | None, int, bool]:
        """Invoke the seat with bounded, budget-reserved retries.

        The estimate is reserved per provider invocation, not once per seat, so a
        permitted retry cannot push cumulative spend past the declared case
        ceiling. Returns ``(outcome, reserved_microunits, already_recorded)``;
        ``outcome`` is ``None`` when no invocation was affordable at all, and
        ``already_recorded`` is true when the returned failure was persisted
        during the loop.
        """
        estimate = provider.estimate_cost_microunits(wire)
        seat_reserved = 0
        seat_charge = 0
        outcome: ProviderCallOutcome | None = None
        while True:
            skip_reason = self._attempt_budget_reason(
                case=case,
                estimate=estimate,
                committed_spend=spent_microunits,
                seat_reserved=seat_reserved,
            )
            if skip_reason is not None:
                # A previous attempt already happened, was persisted below, and
                # is retained as the seat's disposition.
                return outcome, seat_charge, outcome is not None
            outcome = self._attempt_seat(
                case=case,
                family=family,
                provider=provider,
                wire=wire,
                input_hash=input_hash,
                attempt=attempt,
            )
            if estimate is not None:
                seat_reserved += estimate
            # Charge each attempt the greater of its own reservation and its
            # reported cost. An attempt's actual spend can exceed its estimate,
            # and summing per attempt (rather than comparing a total reservation
            # with only the final report) keeps a retried seat's overage on the
            # case ledger so the next seat cannot spend past the ceiling. A seat
            # that was never invoked (an unavailable adapter) incurs no spend, so
            # its estimate is not charged.
            if outcome.status is not ObservationStatus.UNAVAILABLE:
                seat_charge += max(
                    estimate or 0, outcome.estimated_cost_microunits or 0
                )
            if not (
                outcome.status is ObservationStatus.FAILED
                and outcome.failure_class in RETRYABLE_FAILURE_CLASSES
                and attempt < case.policy.max_attempts_per_seat
                and attempt < MAX_RECORDED_ATTEMPTS
            ):
                return outcome, seat_charge, False
            # Persist the failed attempt before retrying, so the audit trail
            # keeps every try rather than only the last one. A failed attempt is
            # not a committed observation, so this cannot create a second
            # opinion.
            self._ledger.record(outcome)
            if attempt + 1 > case.policy.max_attempts_per_seat:
                # The retry would exceed the declared per-seat policy, so stop
                # with the recorded failure rather than spending again.
                return outcome, seat_charge, True
            attempt += 1

    def _attempt_seat(
        self,
        *,
        case: CommitteeCase,
        family: ProviderFamily,
        provider: CommitteeProvider,
        wire: ProviderWireRequest,
        input_hash: str,
        attempt: int,
    ) -> ProviderCallOutcome:
        request_at = self._now()
        if provider.availability() is not ProviderAvailability.AVAILABLE:
            return self._failure_outcome(
                case=case,
                family=family,
                wire=wire,
                input_hash=input_hash,
                attempt=attempt,
                request_at=request_at,
                failure_class=ProviderFailureClass.PROVIDER_UNAVAILABLE,
                status=ObservationStatus.UNAVAILABLE,
                detail="no supported integration for this seat",
            )
        try:
            response = provider.invoke(wire)
        except ProviderInvocationError as exc:
            return self._failure_outcome(
                case=case,
                family=family,
                wire=wire,
                input_hash=input_hash,
                attempt=attempt,
                request_at=request_at,
                failure_class=exc.failure_class,
                status=ObservationStatus.FAILED,
                detail=str(exc),
            )
        return self._admit_response(
            case=case,
            family=family,
            wire=wire,
            input_hash=input_hash,
            attempt=attempt,
            request_at=request_at,
            response=response,
        )

    def _admit_response(
        self,
        *,
        case: CommitteeCase,
        family: ProviderFamily,
        wire: ProviderWireRequest,
        input_hash: str,
        attempt: int,
        request_at: datetime,
        response: ProviderRawResponse,
    ) -> ProviderCallOutcome:
        mismatch = self._identity_mismatch(
            family=family, requested_model=wire.model, response=response
        )
        if mismatch is not None:
            return self._failure_outcome(
                case=case,
                family=family,
                wire=wire,
                input_hash=input_hash,
                attempt=attempt,
                request_at=request_at,
                failure_class=ProviderFailureClass.PROVIDER_IDENTITY_MISMATCH,
                status=ObservationStatus.INVALID,
                detail=mismatch,
                response=response,
            )
        try:
            opinion = parse_structured_opinion(
                raw_text=response.text,
                case_id=case.case_id,
                provider=response.reported_provider,
                model=response.reported_model,
                allowed_evidence_refs=(
                    item.evidence_id for item in case.snapshot.items
                ),
            )
        except OpinionParseError as exc:
            return self._failure_outcome(
                case=case,
                family=family,
                wire=wire,
                input_hash=input_hash,
                attempt=attempt,
                request_at=request_at,
                failure_class=exc.failure_class,
                status=ObservationStatus.INVALID,
                detail=str(exc),
                response=response,
            )
        latency = max(
            0,
            int((response.received_at - request_at).total_seconds() * 1_000_000),
        )
        return ProviderCallOutcome(
            logical_observation_id=wire.logical_observation_id,
            case_id=case.case_id,
            provider_family=family,
            requested_model=wire.model,
            status=ObservationStatus.COMPLETED,
            attempt=attempt,
            reproducibility=self._reproducibility(),
            request_at=request_at,
            input_hash=input_hash,
            reported_provider=response.reported_provider,
            reported_model=response.reported_model,
            opinion=opinion,
            raw_response_ref=response.raw_response_ref,
            response_at=response.received_at,
            latency_micros=latency,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost_microunits=response.estimated_cost_microunits,
            cost_completeness=response.cost_completeness,
        )

    # --------------------------------------------------------------- helpers

    def _reproducibility(self) -> ReproducibilityClass:
        """Declare only what can be guaranteed.

        The committee controls its inputs exactly, but no external provider
        contract in this repository guarantees bit-identical sampling, so the
        honest classification is nondeterministic provider output.
        """
        return ReproducibilityClass.NONDETERMINISTIC_PROVIDER_OUTPUT

    def _identity_mismatch(
        self,
        *,
        family: ProviderFamily,
        requested_model: str,
        response: ProviderRawResponse,
    ) -> str | None:
        """Reject a response that came from a different provider or model.

        Silently attributing another model's answer to this seat would corrupt
        the committee record, so a mismatch is an explicit invalid observation.
        """
        if response.reported_provider.strip().lower() != family.value:
            return (
                f"response came from provider {response.reported_provider!r}, "
                f"not the seated {family.value!r}"
            )
        if response.reported_model.strip() != requested_model.strip():
            return (
                f"response came from model {response.reported_model!r}, "
                f"not the requested {requested_model!r}"
            )
        return None

    def _effective_cost_ceiling(self, case: CommitteeCase) -> int | None:
        """The ceiling in force for this case.

        A policy ceiling wins; otherwise the operator-level configured ceiling
        applies. Reading only the policy would silently ignore
        ``OPIP_COMMITTEE_MAX_ESTIMATED_COST_MICROUNITS``, so an operator's
        spending guard would have no effect unless every caller copied it into
        every policy by hand.
        """
        policy_ceiling = case.policy.max_estimated_cost_microunits
        if policy_ceiling is not None:
            return policy_ceiling
        return resolve_committee_cost_ceiling(self._settings)

    def _attempt_budget_reason(
        self,
        *,
        case: CommitteeCase,
        estimate: int | None,
        committed_spend: int,
        seat_reserved: int,
    ) -> str | None:
        """Why this provider invocation must not happen, or ``None`` to proceed.

        A declared ceiling is only meaningful if it can actually be enforced, so
        the reservation is rechecked for every invocation rather than once per
        seat: a permitted retry cannot push cumulative spend past the ceiling. An
        unbounded cost also skips, because it cannot be shown to fit; an operator
        who wants the seat to run without a ceiling simply does not declare one.
        """
        ceiling = self._effective_cost_ceiling(case)
        if ceiling is None:
            return None
        if estimate is None:
            return (
                "committee cost ceiling is declared but this seat's cost cannot "
                "be bounded within it"
            )
        if committed_spend + seat_reserved + estimate > ceiling:
            return "committee cost ceiling would be exceeded"
        return None

    def _budget_skipped_outcome(
        self,
        *,
        case: CommitteeCase,
        family: ProviderFamily,
        requested_model: str,
        logical_id: str,
        input_hash: str,
        provider: CommitteeProvider,
        attempt: int,
        reason: str,
    ) -> ProviderCallOutcome:
        return ProviderCallOutcome(
            logical_observation_id=logical_id,
            case_id=case.case_id,
            provider_family=family,
            requested_model=requested_model,
            status=ObservationStatus.SKIPPED_BUDGET,
            attempt=attempt,
            reproducibility=ReproducibilityClass.REPEATABLE_CONFIGURATION,
            request_at=self._now(),
            input_hash=input_hash,
            reported_provider=provider.model_identifier(),
            reported_model=None,
            detail=reason,
            cost_completeness=CostCompleteness.UNKNOWN,
        )

    def _exhausted_attempt_outcome(
        self,
        *,
        case: CommitteeCase,
        family: ProviderFamily,
        requested_model: str,
        logical_id: str,
        input_hash: str,
        provider: CommitteeProvider,
        recorded_attempts: int,
    ) -> ProviderCallOutcome:
        """A governed disposition for a seat that has used every legal attempt.

        The provider is not called: the recorded-attempt budget is exhausted, so
        the committee cannot obtain an opinion from this seat. Reported as an
        unavailable seat with a precise reason rather than raising, so a
        persistently failing seat cannot convert a typed, nonfatal disposition
        into an exception after one more external call.
        """
        attempt = max(1, min(recorded_attempts, MAX_RECORDED_ATTEMPTS))
        return ProviderCallOutcome(
            logical_observation_id=logical_id,
            case_id=case.case_id,
            provider_family=family,
            requested_model=requested_model,
            status=ObservationStatus.UNAVAILABLE,
            attempt=attempt,
            reproducibility=ReproducibilityClass.REPEATABLE_CONFIGURATION,
            request_at=self._now(),
            input_hash=input_hash,
            reported_provider=provider.model_identifier(),
            reported_model=None,
            failure_class=ProviderFailureClass.PROVIDER_UNAVAILABLE,
            detail=(
                "attempt budget exhausted for this logical seat; no further "
                "provider call was attempted"
            ),
            cost_completeness=CostCompleteness.UNKNOWN,
        )

    def _failure_outcome(
        self,
        *,
        case: CommitteeCase,
        family: ProviderFamily,
        wire: ProviderWireRequest,
        input_hash: str,
        attempt: int,
        request_at: datetime,
        failure_class: ProviderFailureClass,
        status: ObservationStatus,
        detail: str,
        response: ProviderRawResponse | None = None,
    ) -> ProviderCallOutcome:
        response_at = None if response is None else response.received_at
        latency = None
        if response is not None:
            latency = max(
                0,
                int((response.received_at - request_at).total_seconds() * 1_000_000),
            )
        return ProviderCallOutcome(
            logical_observation_id=wire.logical_observation_id,
            case_id=case.case_id,
            provider_family=family,
            requested_model=wire.model,
            status=status,
            attempt=attempt,
            reproducibility=self._reproducibility(),
            request_at=request_at,
            input_hash=input_hash,
            reported_provider=(
                None if response is None else response.reported_provider
            ),
            reported_model=None if response is None else response.reported_model,
            failure_class=failure_class,
            detail=detail,
            raw_response_ref=None if response is None else response.raw_response_ref,
            response_at=response_at,
            latency_micros=latency,
            input_tokens=None if response is None else response.input_tokens,
            output_tokens=None if response is None else response.output_tokens,
            estimated_cost_microunits=(
                None if response is None else response.estimated_cost_microunits
            ),
            cost_completeness=CostCompleteness.UNKNOWN,
        )


__all__ = [
    "COMMITTEE_SYSTEM_PROMPT",
    "CommitteePolicyViolation",
    "CommitteeReplayDivergenceError",
    "CommitteeRunResult",
    "CommitteeRunner",
    "CommitteeSeatResult",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_TIMEOUT_SECONDS",
    "PROMPT_HASH_DOMAIN",
    "WIRE_HASH_DOMAIN",
]
