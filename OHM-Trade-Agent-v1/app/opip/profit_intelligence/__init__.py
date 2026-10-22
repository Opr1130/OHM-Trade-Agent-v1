"""O'Pip Profit Intelligence read-only lineage plane.

Profit Intelligence answers owner questions about *why* an economic result
happened, by projecting the canonical evidence that already exists rather than
inventing an explanation afterwards. It is a **read-only derived plane**:

* it never writes canonical evidence, and never instantiates a writable writer;
* it holds no trading, risk, execution, alert, ranking, promotion or Committee
  authority;
* it never becomes a second source of truth - the canonical writer remains the
  authority for ancestry, conservation and economics, and the versioned paper
  metric registry remains the authority for metric semantics.

The plane is deliberately explicit about what it *cannot* prove. Each lineage
stage and each economic component carries a :class:`FactAvailability`, so
"known", "derived", "not on this plane" and "not applicable" are never
collapsed into a number. Missing evidence is surfaced as a named gap rather
than rendered as ``0``.

Evidence projections
--------------------
Two typed, versioned, derived read models project the pre-trade evidence the
lineage plane cannot reach:

* :mod:`app.opip.profit_intelligence.qualification_funnel` - the ordered
  qualification progression and its canonical terminal/rejection attribution;
* :mod:`app.opip.profit_intelligence.forward_outcomes` - point-in-time forward
  outcomes kept strictly separate from the decision-time facts they follow.

Both are pure read models over canonical evidence, hold no authority, and never
invent a fact. The funnel/forward-outcome join is evidence-only and never
classifies a "missed profitable opportunity", because a market outcome is not
executable profit.

See :mod:`app.opip.profit_intelligence.semantics` for the contract, the stage
vocabulary, the machine-readable evidence-gap registry, and the precise
unblock evidence for the still-blocked missed-opportunity increment.
"""

from __future__ import annotations

from app.opip.profit_intelligence.economics import (
    ECONOMIC_INTEGRITY_VERSION,
    CostComponentEvidence,
    EconomicIntegrity,
    build_economic_integrity,
)
from app.opip.profit_intelligence.forward_outcomes import (
    FORWARD_OUTCOME_PROJECTION_VERSION,
    FORWARD_OUTCOME_SCHEMA_VERSION,
    FORWARD_OUTCOME_SCOPE,
    ForwardOutcomeCompletion,
    ForwardOutcomeHorizon,
    ForwardOutcomeProjection,
    ForwardOutcomeRecord,
    ForwardOutcomeSource,
    build_forward_outcome_projection,
    forward_window_is_point_in_time,
    horizon_duration_seconds,
    read_forward_outcome_projection,
    unavailable_forward_outcomes,
)
from app.opip.profit_intelligence.lineage import (
    LINEAGE_PROJECTION_VERSION,
    StageEvidence,
    TradeLineage,
    build_trade_lineage,
)
from app.opip.profit_intelligence.qualification_funnel import (
    DISPOSITIONS_WITHOUT_CANONICAL_PRODUCER,
    QUALIFICATION_FUNNEL_PROJECTION_VERSION,
    QUALIFICATION_FUNNEL_SCHEMA_VERSION,
    QUALIFICATION_FUNNEL_SCOPE,
    FunnelConservation,
    QualificationDisposition,
    QualificationFunnelProjection,
    QualificationFunnelRecord,
    QualificationGateObservation,
    build_qualification_funnel_projection,
    disposition_for,
    funnel_conservation,
    read_qualification_funnel_projection,
    unavailable_qualification_funnel,
)
from app.opip.profit_intelligence.read_model import (
    FORWARD_OUTCOME_JOIN_VERSION,
    PROFIT_INTELLIGENCE_READ_MODEL_VERSION,
    ForwardOutcomeJoin,
    ForwardOutcomeJoinStatus,
    ProfitIntelligenceOverview,
    build_lineage_for_trade,
    build_profit_intelligence_overview,
    join_funnel_record_to_forward_outcomes,
    unavailable_profit_intelligence,
)
from app.opip.profit_intelligence.semantics import (
    PROFIT_INTELLIGENCE_CONTRACT_VERSION,
    EVIDENCE_GAPS,
    LINEAGE_STAGE_ORDER,
    MISSED_OPPORTUNITY_DISPOSITION,
    MISSED_OPPORTUNITY_SAFEGUARDS,
    MISSED_OPPORTUNITY_UNBLOCK,
    EconomicComponent,
    EvidenceGap,
    EvidencePlane,
    FactAvailability,
    LineageStage,
    MissedOpportunityCause,
    MissedOpportunitySafeguards,
    MissedOpportunityUnblockEvidence,
    net_pnl_reconciles,
)

__all__ = [
    "DISPOSITIONS_WITHOUT_CANONICAL_PRODUCER",
    "ECONOMIC_INTEGRITY_VERSION",
    "EVIDENCE_GAPS",
    "FORWARD_OUTCOME_JOIN_VERSION",
    "FORWARD_OUTCOME_PROJECTION_VERSION",
    "FORWARD_OUTCOME_SCHEMA_VERSION",
    "FORWARD_OUTCOME_SCOPE",
    "LINEAGE_PROJECTION_VERSION",
    "LINEAGE_STAGE_ORDER",
    "MISSED_OPPORTUNITY_DISPOSITION",
    "MISSED_OPPORTUNITY_SAFEGUARDS",
    "MISSED_OPPORTUNITY_UNBLOCK",
    "PROFIT_INTELLIGENCE_CONTRACT_VERSION",
    "PROFIT_INTELLIGENCE_READ_MODEL_VERSION",
    "QUALIFICATION_FUNNEL_PROJECTION_VERSION",
    "QUALIFICATION_FUNNEL_SCHEMA_VERSION",
    "QUALIFICATION_FUNNEL_SCOPE",
    "CostComponentEvidence",
    "EconomicComponent",
    "EconomicIntegrity",
    "EvidenceGap",
    "EvidencePlane",
    "FactAvailability",
    "ForwardOutcomeCompletion",
    "ForwardOutcomeHorizon",
    "ForwardOutcomeJoin",
    "ForwardOutcomeJoinStatus",
    "ForwardOutcomeProjection",
    "ForwardOutcomeRecord",
    "ForwardOutcomeSource",
    "FunnelConservation",
    "LineageStage",
    "MissedOpportunityCause",
    "MissedOpportunitySafeguards",
    "MissedOpportunityUnblockEvidence",
    "ProfitIntelligenceOverview",
    "QualificationDisposition",
    "QualificationFunnelProjection",
    "QualificationFunnelRecord",
    "QualificationGateObservation",
    "StageEvidence",
    "TradeLineage",
    "build_economic_integrity",
    "build_forward_outcome_projection",
    "build_lineage_for_trade",
    "build_profit_intelligence_overview",
    "build_qualification_funnel_projection",
    "build_trade_lineage",
    "disposition_for",
    "forward_window_is_point_in_time",
    "funnel_conservation",
    "horizon_duration_seconds",
    "join_funnel_record_to_forward_outcomes",
    "net_pnl_reconciles",
    "read_forward_outcome_projection",
    "read_qualification_funnel_projection",
    "unavailable_forward_outcomes",
    "unavailable_profit_intelligence",
    "unavailable_qualification_funnel",
]
