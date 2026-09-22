"""O'Pip Intelligence Committee plane.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The Intelligence Committee obtains, preserves, evaluates, and learns from
independent model opinions. It is shadow-only, read-only, and non-authoritative.

What this plane structurally cannot do:

* place, modify, resize, or cancel an order;
* admit or reserve capital;
* change a risk limit, protection level, or forced exit;
* activate Paper v2 or alter funded/live state;
* promote a model, strategy, or threshold;
* influence production ranking, alerts, or execution by any path.

A committee result is research evidence. A model emitting an opinion never makes
that opinion canonical trading truth, and no downstream consumer may treat an
aggregate of opinions as an instruction.

The plane extends the frozen ``app.opip.decision_intelligence`` contracts rather
than replacing them, and depends on them only for canonical vocabulary
(``Provenance``, content-derived identity, canonical serialization).
"""

from __future__ import annotations

from app.opip.committee.contracts import (
    CaseType,
    CommitteeCase,
    CommitteeCaseOutcome,
    CommitteePolicy,
    CostCompleteness,
    DirectionalAssessment,
    EvaluationPhase,
    EvidenceItem,
    EvidenceSnapshot,
    EvidenceSufficiency,
    ObservationStatus,
    ProviderCallOutcome,
    ProviderFailureClass,
    ProviderFamily,
    ReproducibilityClass,
    ResearchAction,
    StructuredOpinion,
    logical_observation_id,
)
from app.opip.committee.evidence import (
    EvidencePolicyError,
    build_evidence_item,
    build_evidence_snapshot,
)
from app.opip.committee.evaluation import (
    ADEQUACY_ADEQUATE,
    ADEQUACY_INSUFFICIENT_SAMPLE,
    ArmEvaluation,
    ArmKind,
    BaselineCall,
    CaseObservation,
    DirectionalCall,
    EvaluationReport,
    MIN_EVALUATION_SAMPLES,
    ProbabilityForecast,
    ReplayComparison,
    ResolvedOutcome,
    committee_research_signal,
    directional_call,
    evaluate_model_bake_off,
)
from app.opip.committee.ledger import (
    InMemoryObservationLedger,
    ObservationLedger,
)
from app.opip.committee.metrics import (
    CalibrationBin,
    CalibrationReport,
    ClassificationReport,
    ConfusionMatrix,
    CostAggregate,
    EvaluationMetric,
    LatencyDistribution,
    ProbabilitySample,
    brier_score,
    calibration_report,
    classification_report,
    confusion_matrix,
    cost_aggregate,
    latency_distribution,
    log_loss,
    repeatability_metric,
)
from app.opip.committee.pricing import (
    COMMITTEE_PRICES_ENV,
    MICROUNITS_PER_UNIT,
    TOKENS_PER_PRICING_UNIT,
    PriceBook,
    PriceBookConfigError,
    TokenPrice,
    scale_tokens,
)
from app.opip.committee.opinion import OpinionParseError, parse_structured_opinion
from app.opip.committee.outbound import OutboundPolicyError, screen_model_bound_view
from app.opip.committee.providers import (
    CommitteeProvider,
    ProviderAvailability,
    ProviderInvocationError,
    ProviderRawResponse,
    ProviderTransport,
    ProviderWireRequest,
    TransportBackedProvider,
    UnavailableProvider,
)
from app.opip.committee.runtime import (
    COMMITTEE_SYSTEM_PROMPT,
    CommitteePolicyViolation,
    CommitteeReplayDivergenceError,
    CommitteeRunResult,
    CommitteeRunner,
    CommitteeSeatResult,
)
from app.opip.committee.serialization import (
    CommitteeSerializationError,
    call_outcome_from_dict,
    call_outcome_to_dict,
    case_outcome_from_dict,
    case_outcome_to_dict,
    opinion_from_dict,
    opinion_to_dict,
)
from app.opip.committee.store import (
    COMMITTEE_DIR,
    CommitteeEvidenceStore,
    DurableObservationLedger,
    StoreAppendResult,
)

#: Declared here so an architecture check can assert non-authority without
#: importing the runtime.
AUTHORITATIVE = False
CAN_PLACE_ORDERS = False
MEASUREMENT_ONLY = True

__all__ = [
    "ADEQUACY_ADEQUATE",
    "ADEQUACY_INSUFFICIENT_SAMPLE",
    "AUTHORITATIVE",
    "ArmEvaluation",
    "ArmKind",
    "BaselineCall",
    "CAN_PLACE_ORDERS",
    "COMMITTEE_DIR",
    "COMMITTEE_PRICES_ENV",
    "COMMITTEE_SYSTEM_PROMPT",
    "CaseObservation",
    "CaseType",
    "CalibrationBin",
    "CalibrationReport",
    "ClassificationReport",
    "CommitteeCase",
    "CommitteeCaseOutcome",
    "CommitteeEvidenceStore",
    "CommitteePolicy",
    "CommitteePolicyViolation",
    "CommitteeProvider",
    "CommitteeReplayDivergenceError",
    "CommitteeRunResult",
    "CommitteeRunner",
    "CommitteeSeatResult",
    "CommitteeSerializationError",
    "ConfusionMatrix",
    "CostAggregate",
    "CostCompleteness",
    "DirectionalAssessment",
    "DirectionalCall",
    "DurableObservationLedger",
    "EvaluationMetric",
    "EvaluationPhase",
    "EvaluationReport",
    "EvidenceItem",
    "EvidencePolicyError",
    "EvidenceSnapshot",
    "EvidenceSufficiency",
    "InMemoryObservationLedger",
    "LatencyDistribution",
    "MEASUREMENT_ONLY",
    "MICROUNITS_PER_UNIT",
    "MIN_EVALUATION_SAMPLES",
    "ObservationLedger",
    "ObservationStatus",
    "OpinionParseError",
    "OutboundPolicyError",
    "PriceBook",
    "PriceBookConfigError",
    "ProbabilityForecast",
    "ProbabilitySample",
    "ProviderAvailability",
    "ProviderCallOutcome",
    "ProviderFailureClass",
    "ProviderFamily",
    "ProviderInvocationError",
    "ProviderRawResponse",
    "ProviderTransport",
    "ProviderWireRequest",
    "ReplayComparison",
    "ReproducibilityClass",
    "ResearchAction",
    "ResolvedOutcome",
    "StoreAppendResult",
    "StructuredOpinion",
    "TOKENS_PER_PRICING_UNIT",
    "TokenPrice",
    "TransportBackedProvider",
    "UnavailableProvider",
    "brier_score",
    "build_evidence_item",
    "build_evidence_snapshot",
    "calibration_report",
    "call_outcome_from_dict",
    "call_outcome_to_dict",
    "case_outcome_from_dict",
    "case_outcome_to_dict",
    "classification_report",
    "committee_research_signal",
    "confusion_matrix",
    "cost_aggregate",
    "directional_call",
    "evaluate_model_bake_off",
    "latency_distribution",
    "log_loss",
    "logical_observation_id",
    "opinion_from_dict",
    "opinion_to_dict",
    "parse_structured_opinion",
    "repeatability_metric",
    "scale_tokens",
    "screen_model_bound_view",
]
