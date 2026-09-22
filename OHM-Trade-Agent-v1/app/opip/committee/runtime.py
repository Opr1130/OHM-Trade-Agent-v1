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
    ) -> None:
        self._providers: Mapping[ProviderFamily, CommitteeProvider] = dict(providers)
        self._ledger = ledger
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._now: Callable[[], datetime] = now or (
            lambda: datetime.now(timezone.utc)
        )

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
        spent_microunits = 0
        for family in case.policy.seated_providers:
            seat = self._run_seat(
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
            if seat.outcome.estimated_cost_microunits is not None:
                spent_microunits += seat.outcome.estimated_cost_microunits
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
    ) -> CommitteeSeatResult:
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
            # A committed logical observation is never asked again. This is the
            # ACK-loss case: the caller may not know the write landed, but a
            # second independent opinion must not be manufactured.
            outcome = ProviderCallOutcome(
                logical_observation_id=logical_id,
                case_id=case.case_id,
                provider_family=family,
                requested_model=requested_model,
                status=ObservationStatus.DUPLICATE_OK,
                attempt=committed.attempt,
                reproducibility=committed.reproducibility,
                request_at=self._now(),
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
                detail="logical observation already committed; not re-queried",
            )
            return CommitteeSeatResult(family, logical_id, outcome)

        skip_reason = self._budget_skip_reason(
            case=case, provider=provider, wire=wire, spent=spent_microunits
        )
        if skip_reason is not None:
            outcome = ProviderCallOutcome(
                logical_observation_id=logical_id,
                case_id=case.case_id,
                provider_family=family,
                requested_model=requested_model,
                status=ObservationStatus.SKIPPED_BUDGET,
                attempt=1,
                reproducibility=ReproducibilityClass.REPEATABLE_CONFIGURATION,
                request_at=self._now(),
                input_hash=input_hash,
                reported_provider=provider.model_identifier(),
                reported_model=None,
                detail=skip_reason,
                cost_completeness=CostCompleteness.UNKNOWN,
            )
            return CommitteeSeatResult(family, logical_id, outcome)

        attempt = self._ledger.attempt_count(logical_id) + 1
        while True:
            outcome = self._attempt_seat(
                case=case,
                family=family,
                provider=provider,
                wire=wire,
                input_hash=input_hash,
                attempt=attempt,
            )
            if not (
                outcome.status is ObservationStatus.FAILED
                and outcome.failure_class in RETRYABLE_FAILURE_CLASSES
                and attempt < case.policy.max_attempts_per_seat
            ):
                break
            # Persist the failed attempt before retrying, so the audit trail
            # keeps every try rather than only the last one. A failed attempt is
            # not a committed observation, so this cannot create a second
            # opinion.
            self._ledger.record(outcome)
            attempt += 1

        if replay_existing and committed is not None and outcome.opinion is not None:
            if outcome.opinion.opinion_hash != committed.opinion.opinion_hash:
                raise CommitteeReplayDivergenceError(
                    "replayed observation diverges from the sealed opinion for "
                    f"{family.value}; history is preserved and the replay is refused"
                )

        self._ledger.record(outcome)
        return CommitteeSeatResult(family, logical_id, outcome)

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
            provider=provider,
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
        provider: CommitteeProvider,
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

    def _budget_skip_reason(
        self,
        *,
        case: CommitteeCase,
        provider: CommitteeProvider,
        wire: ProviderWireRequest,
        spent: int,
    ) -> str | None:
        """Why this seat must be skipped, or ``None`` to proceed.

        A declared ceiling is only meaningful if it can actually be enforced. An
        unbounded cost therefore skips the seat rather than permitting spend the
        ceiling was meant to prevent; an operator who wants the seat to run
        without a ceiling simply does not declare one.
        """
        ceiling = case.policy.max_estimated_cost_microunits
        if ceiling is None:
            return None
        estimate = provider.estimate_cost_microunits(wire)
        if estimate is None:
            return (
                "committee cost ceiling is declared but this seat's cost cannot "
                "be bounded within it"
            )
        if spent + estimate > ceiling:
            return "committee cost ceiling would be exceeded"
        return None

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
