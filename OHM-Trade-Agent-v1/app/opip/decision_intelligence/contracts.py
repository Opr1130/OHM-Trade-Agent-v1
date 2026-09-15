from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable, Mapping

from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.decision_intelligence.identity import DecisionContext, Provenance
from app.opip.decision_intelligence.serialization import require_utc


class RequestState(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    SELECTED = "SELECTED"
    SKIPPED_BUDGET = "SKIPPED_BUDGET"
    SKIPPED_CAPACITY = "SKIPPED_CAPACITY"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    INVALID = "INVALID"
    COMPLETED = "COMPLETED"


class ResultDisposition(str, Enum):
    ON_TIME = "ON_TIME"
    LATE = "LATE"


def timely_evidence_eligible(dispositions: Iterable[ResultDisposition]) -> bool:
    values = tuple(dispositions)
    return bool(values) and all(
        disposition is ResultDisposition.ON_TIME for disposition in values
    )


class AdvisoryStance(str, Enum):
    SUPPORT = "SUPPORT"
    OPPOSE = "OPPOSE"
    WATCH = "WATCH"
    ABSTAIN = "ABSTAIN"


LEGAL_REQUEST_TRANSITIONS = {
    RequestState.ELIGIBLE: frozenset({
        RequestState.SELECTED,
        RequestState.SKIPPED_BUDGET,
        RequestState.SKIPPED_CAPACITY,
        RequestState.EXPIRED,
        RequestState.INVALID,
    }),
    RequestState.SELECTED: frozenset({
        RequestState.COMPLETED,
        RequestState.FAILED,
        RequestState.EXPIRED,
        RequestState.INVALID,
    }),
}
TERMINAL_REQUEST_STATES = frozenset({
    RequestState.SKIPPED_BUDGET,
    RequestState.SKIPPED_CAPACITY,
    RequestState.EXPIRED,
    RequestState.FAILED,
    RequestState.INVALID,
    RequestState.COMPLETED,
})


class CommitteeRole(str, Enum):
    REGIME_ANALYST = "REGIME_ANALYST"
    LIQUIDITY_STRUCTURE_ANALYST = "LIQUIDITY_STRUCTURE_ANALYST"
    EVENT_SENTIMENT_ANALYST = "EVENT_SENTIMENT_ANALYST"
    BULL_ADVOCATE = "BULL_ADVOCATE"
    BEAR_ADVOCATE = "BEAR_ADVOCATE"
    RISK_CRITIC = "RISK_CRITIC"
    DECISION_SYNTHESIZER = "DECISION_SYNTHESIZER"


@dataclass(frozen=True)
class ContextDecisionLink:
    """P1A read/link-only reference; no canonical event is emitted."""
    link_id: str
    context_id: str
    decision_id: str
    decision_record_type: str
    decision_schema_version: int
    architecture_disposition: str
    provenance: Provenance
    schema_version: int = 1
    supersedes_id: str | None = None
    supersession_reason: str | None = None


@dataclass(frozen=True)
class CommitteeRequest:
    request_id: str
    context_id: str
    experiment_id: str
    cohort_selection_rule_version: str
    frozen_snapshot_hash: str
    route_version: str
    prompt_version: str
    role_configuration_version: str
    eligibility_at: datetime
    deadline_at: datetime
    budget_reservation: int
    enqueue_time: datetime
    result_selection_rule_version: str
    provenance: Provenance
    schema_version: int = 1
    supersedes_id: str | None = None
    supersession_reason: str | None = None
    context_snapshot_hash: str | None = None

    def validate_against_context(self, context: DecisionContext) -> None:
        if self.context_id != context.context_id:
            raise ValueError("CommitteeRequest.context_id must match DecisionContext.context_id")
        if self.frozen_snapshot_hash != context.snapshot_hash:
            raise ValueError("CommitteeRequest.frozen_snapshot_hash must match DecisionContext.snapshot_hash")

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported CommitteeRequest schema_version")
        for field_name in ("request_id", "context_id", "experiment_id"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if self.context_snapshot_hash is not None and self.frozen_snapshot_hash != self.context_snapshot_hash:
            raise ValueError("CommitteRequest.frozen_snapshot_hash must equal DecisionContext.snapshot_hash")
        for field_name in ("eligibility_at", "deadline_at", "enqueue_time"):
            object.__setattr__(
                self,
                field_name,
                require_utc(getattr(self, field_name), field_name=field_name),
            )
        if self.deadline_at < self.eligibility_at:
            raise ValueError("deadline_at must be >= eligibility_at")


@dataclass(frozen=True)
class CommitteeRequestTransition:
    transition_id: str
    request_id: str
    from_state: RequestState
    to_state: RequestState
    reason: str
    transition_time: datetime
    provenance: Provenance
    schema_version: int = 1
    supersedes_id: str | None = None
    supersession_reason: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported transition schema_version")
        if not self.transition_id.strip() or not self.request_id.strip():
            raise ValueError("transition_id and request_id are required")
        if self.from_state == self.to_state:
            raise ValueError("no self-transition")
        if self.from_state in TERMINAL_REQUEST_STATES:
            raise ValueError("terminal state cannot transition")
        if self.to_state not in LEGAL_REQUEST_TRANSITIONS.get(self.from_state, frozenset()):
            raise ValueError("undeclared transition")
        object.__setattr__(
            self,
            "transition_time",
            require_utc(self.transition_time, field_name="transition_time"),
        )


@dataclass(frozen=True)
class CommitteeRoleResult:
    result_id: str
    request_id: str
    role: CommitteeRole
    role_version: str
    attempt: int
    route_version: str
    prompt_version: str
    model_version: str
    invocation_ref: str | None
    status: str
    result_disposition: ResultDisposition
    stance: AdvisoryStance
    thesis: str
    risks: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    rubric_score: int | None
    score_schema_version: int
    self_reported_confidence: int | None
    bull_score: int | None
    bear_score: int | None
    risk_score: int | None
    provenance: Provenance
    schema_version: int = 1
    supersedes_id: str | None = None
    supersession_reason: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported role result schema_version")
        for field_name in ("result_id", "request_id"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        if not isinstance(self.role, CommitteeRole):
            raise ValueError("invalid committee role")
        if not isinstance(self.stance, AdvisoryStance):
            raise ValueError("invalid stance")
        for field_name in ("rubric_score", "self_reported_confidence", "bull_score", "bear_score", "risk_score"):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or not 0 <= value <= 100):
                raise ValueError(f"{field_name} must be an ordinal integer from 0 to 100")


@dataclass(frozen=True)
class CommitteeAssessmentSummary:
    assessment_id: str
    request_id: str
    referenced_role_result_ids: tuple[str, ...]
    synthesis: str
    advisory_stance: AdvisoryStance
    disagreement: bool
    completeness: int | None
    unsupported_claims: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    status: str
    result_disposition: ResultDisposition
    result_selection_rule_version: str
    completion_time: datetime
    commit_time: datetime
    invocation_references: tuple[str, ...]
    provenance: Provenance
    schema_version: int = 1
    supersedes_id: str | None = None
    supersession_reason: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported assessment schema_version")
        for field_name in ("assessment_id", "request_id"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.advisory_stance, AdvisoryStance):
            raise ValueError("invalid advisory stance")
        if self.completeness is not None and (type(self.completeness) is not int or not 0 <= self.completeness <= 10000):
            raise ValueError("completeness must be integer basis points from 0 to 10000")
        object.__setattr__(
            self,
            "completion_time",
            require_utc(self.completion_time, field_name="completion_time"),
        )
        object.__setattr__(
            self,
            "commit_time",
            require_utc(self.commit_time, field_name="commit_time"),
        )
        if self.commit_time < self.completion_time:
            raise ValueError("commit_time must be >= completion_time")

    def idempotency_key(self) -> str:
        from app.opip.decision_intelligence.events import assessment_idempotency_key

        return assessment_idempotency_key(assessment_id=self.assessment_id)


@dataclass(frozen=True)
class ModelInvocation:
    invocation_id: str
    request_id: str
    role: CommitteeRole
    attempt: int
    provider: str
    model: str
    provider_model_version: str
    route_version: str
    prompt_version: str
    reasoning_mode: str
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    latency_micros: int | None
    queue_time_micros: int | None
    provider_time_micros: int | None
    billed_cost_microunits: int | None
    estimated_cost_microunits: int | None
    price_version: str
    currency: str
    reconciliation_status: str
    cost_completeness: str
    started_at: datetime
    completed_at: datetime
    provenance: Provenance
    schema_version: int = 1
    supersedes_id: str | None = None
    supersession_reason: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported invocation schema_version")
        for field_name in ("invocation_id", "request_id"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        if not isinstance(self.role, CommitteeRole):
            raise ValueError("invalid committee role")
        if self.billed_cost_microunits is None and self.estimated_cost_microunits is None:
            if self.cost_completeness not in {"UNKNOWN", "INCOMPLETE"}:
                raise ValueError("unknown invocation cost must be marked UNKNOWN or INCOMPLETE")
        object.__setattr__(
            self,
            "started_at",
            require_utc(self.started_at, field_name="started_at"),
        )
        object.__setattr__(
            self,
            "completed_at",
            require_utc(self.completed_at, field_name="completed_at"),
        )
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must be >= started_at")


@dataclass(frozen=True)
class ComparisonRecord:
    comparison_id: str
    decision_context_id: str
    baseline_decision_id: str
    committee_assessment_id: str
    committee_request_id: str
    experiment_id: str
    variant_version: str
    environment: str
    research_account_id: str
    evaluation_window_start: datetime
    evaluation_window_end: datetime
    common_outcome_horizon: int
    as_of_watermark: ConsumedInputWatermark
    timeliness_eligibility: bool
    baseline_policy_version: str
    simulated_policy_ref: str | None
    execution_model_version: str
    fee_policy_version: str
    attribution_method_version: str
    cost_allocation_version: str
    currency: str
    invocation_refs: tuple[str, ...]
    ai_cost_attributed: int | None
    other_incremental_operating_cost: int | None
    cost_reconciliation_status: str
    cost_completeness: str
    baseline_trading_net: int
    variant_trading_net: int
    incremental_trading_net: int
    incremental_operating_net: int
    coverage_grade: str
    uncertainty_method_version: str
    uncertainty_result: str
    provenance: Provenance
    schema_version: int = 1
    supersedes_id: str | None = None
    supersession_reason: str | None = None
    advisory_disposition: ResultDisposition | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported comparison schema_version")
        required = (
            "comparison_id", "decision_context_id", "baseline_decision_id",
            "committee_assessment_id", "committee_request_id", "experiment_id",
        )
        if any(not str(getattr(self, name)).strip() for name in required):
            raise ValueError("comparison references are required")
        object.__setattr__(
            self,
            "evaluation_window_start",
            require_utc(
                self.evaluation_window_start,
                field_name="evaluation_window_start",
            ),
        )
        object.__setattr__(
            self,
            "evaluation_window_end",
            require_utc(
                self.evaluation_window_end,
                field_name="evaluation_window_end",
            ),
        )
        if self.evaluation_window_end < self.evaluation_window_start:
            raise ValueError("evaluation_window_end must be >= evaluation_window_start")
        watermark = self.as_of_watermark
        if not isinstance(watermark, ConsumedInputWatermark):
            if not isinstance(watermark, Mapping):
                raise ValueError("as_of_watermark must be a watermark object")
            if set(watermark) != {"history_epoch", "local_sequence"}:
                raise ValueError("invalid as_of_watermark")
            history_epoch = watermark["history_epoch"]
            local_sequence = watermark["local_sequence"]
            if type(history_epoch) is not int or type(local_sequence) is not int:
                raise ValueError("invalid as_of_watermark")
            if history_epoch < 0 or local_sequence < 0:
                raise ValueError("invalid as_of_watermark")
            watermark = ConsumedInputWatermark(
                history_epoch=history_epoch,
                local_sequence=local_sequence,
            )
            object.__setattr__(self, "as_of_watermark", watermark)
        if type(self.timeliness_eligibility) is not bool:
            raise ValueError("timeliness_eligibility must be a boolean")
        if (
            self.timeliness_eligibility
            and self.advisory_disposition is not ResultDisposition.ON_TIME
        ):
            raise ValueError(
                "LATE or unknown evidence cannot be timely; explicit ON_TIME advisory disposition required"
            )


RequestTransition = CommitteeRequestTransition

__all__ = [
    "CommitteeAssessmentSummary",
    "AdvisoryStance",
    "CommitteeRequest",
    "CommitteeRequestTransition",
    "CommitteeRoleResult",
    "ComparisonRecord",
    "ContextDecisionLink",
    "ModelInvocation",
    "RequestState",
    "RequestTransition",
    "ResultDisposition",
    "timely_evidence_eligible",
]
