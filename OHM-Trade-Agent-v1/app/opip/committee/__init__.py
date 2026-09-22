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
from app.opip.committee.ledger import (
    InMemoryObservationLedger,
    ObservationLedger,
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
    "AUTHORITATIVE",
    "CAN_PLACE_ORDERS",
    "COMMITTEE_DIR",
    "COMMITTEE_SYSTEM_PROMPT",
    "MEASUREMENT_ONLY",
    "CaseType",
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
    "CostCompleteness",
    "DirectionalAssessment",
    "DurableObservationLedger",
    "EvaluationPhase",
    "EvidenceItem",
    "EvidencePolicyError",
    "EvidenceSnapshot",
    "EvidenceSufficiency",
    "InMemoryObservationLedger",
    "ObservationLedger",
    "ObservationStatus",
    "OpinionParseError",
    "OutboundPolicyError",
    "ProviderAvailability",
    "ProviderCallOutcome",
    "ProviderFailureClass",
    "ProviderFamily",
    "ProviderInvocationError",
    "ProviderRawResponse",
    "ProviderTransport",
    "ProviderWireRequest",
    "ReproducibilityClass",
    "ResearchAction",
    "StoreAppendResult",
    "StructuredOpinion",
    "TransportBackedProvider",
    "UnavailableProvider",
    "build_evidence_item",
    "build_evidence_snapshot",
    "call_outcome_from_dict",
    "call_outcome_to_dict",
    "case_outcome_from_dict",
    "case_outcome_to_dict",
    "logical_observation_id",
    "opinion_from_dict",
    "opinion_to_dict",
    "parse_structured_opinion",
    "screen_model_bound_view",
]
