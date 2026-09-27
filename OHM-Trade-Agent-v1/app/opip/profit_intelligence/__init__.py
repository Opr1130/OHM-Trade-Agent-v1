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

See :mod:`app.opip.profit_intelligence.semantics` for the contract, the stage
vocabulary, and the machine-readable evidence-gap registry.
"""

from __future__ import annotations

from app.opip.profit_intelligence.lineage import (
    LINEAGE_PROJECTION_VERSION,
    StageEvidence,
    TradeLineage,
    build_trade_lineage,
)
from app.opip.profit_intelligence.semantics import (
    PROFIT_INTELLIGENCE_CONTRACT_VERSION,
    EVIDENCE_GAPS,
    LINEAGE_STAGE_ORDER,
    MISSED_OPPORTUNITY_DISPOSITION,
    MISSED_OPPORTUNITY_SAFEGUARDS,
    EconomicComponent,
    EvidenceGap,
    EvidencePlane,
    FactAvailability,
    LineageStage,
    MissedOpportunityCause,
    MissedOpportunitySafeguards,
    net_pnl_reconciles,
)

__all__ = [
    "EVIDENCE_GAPS",
    "LINEAGE_PROJECTION_VERSION",
    "LINEAGE_STAGE_ORDER",
    "MISSED_OPPORTUNITY_DISPOSITION",
    "MISSED_OPPORTUNITY_SAFEGUARDS",
    "PROFIT_INTELLIGENCE_CONTRACT_VERSION",
    "EconomicComponent",
    "EvidenceGap",
    "EvidencePlane",
    "FactAvailability",
    "LineageStage",
    "MissedOpportunityCause",
    "MissedOpportunitySafeguards",
    "StageEvidence",
    "TradeLineage",
    "build_trade_lineage",
    "net_pnl_reconciles",
]
