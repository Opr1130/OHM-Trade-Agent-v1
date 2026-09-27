"""Profit Intelligence read model (read-only, analytics plane).

Composes the Profit Intelligence projections into one envelope a consumer can
read without re-deriving anything: contract and projection versions, the
lineage scope, per-currency economic integrity, population counts, and the
evidence-gap registry.

Planning-layer values are counted, never invented
-------------------------------------------------
This module aggregates **counts and already-derived values only**. It computes
no new economic formula: economic totals come from
:mod:`app.opip.profit_intelligence.economics`, which itself only sums the
canonical per-trade values and re-checks the writer's own conservation
relationship. No cross-currency sum is ever produced, because USD and USDT are
distinct portfolios.

Unavailable is never empty-but-healthy
--------------------------------------
When the canonical replica cannot be read, the envelope has exactly the same
structure as a healthy one - same keys, empty collections - and carries an
explicit ``UNAVAILABLE`` trust envelope plus a reason. It is never returned as an
apparently healthy empty result, and it never raises into the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.opip.cockpit.ledger import PaperLedger, ReconciledPaperTrade
from app.opip.cockpit.trust import (
    Completeness,
    Freshness,
    TrustEnvelope,
    unavailable,
)
from app.opip.profit_intelligence.economics import (
    ECONOMIC_INTEGRITY_VERSION,
    EconomicIntegrity,
    build_economic_integrity,
)
from app.opip.profit_intelligence.lineage import (
    LINEAGE_PROJECTION_VERSION,
    LINEAGE_SCOPE,
    TradeLineage,
    build_trade_lineage,
)
from app.opip.profit_intelligence.semantics import (
    EVIDENCE_GAPS,
    METRIC_AUTHORITY,
    MISSED_OPPORTUNITY_DISPOSITION,
    MISSED_OPPORTUNITY_SAFEGUARDS,
    PROFIT_INTELLIGENCE_CONTRACT_VERSION,
)

#: Bumped whenever the composition of this envelope changes.
PROFIT_INTELLIGENCE_READ_MODEL_VERSION = "profit-intelligence-read-model-v2"


def _counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


@dataclass(frozen=True)
class ProfitIntelligenceOverview:
    """The Profit Intelligence overview envelope."""

    populations: Mapping[str, Any] = field(default_factory=dict)
    economic_integrity: tuple[EconomicIntegrity, ...] = ()
    trust: TrustEnvelope = field(
        default_factory=lambda: TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=Completeness.COMPLETE,
        )
    )
    details: tuple[str, ...] = ()
    contract_version: str = PROFIT_INTELLIGENCE_CONTRACT_VERSION
    read_model_version: str = PROFIT_INTELLIGENCE_READ_MODEL_VERSION
    economic_integrity_version: str = ECONOMIC_INTEGRITY_VERSION
    scope: str = LINEAGE_SCOPE

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "read_model_version": self.read_model_version,
            "economic_integrity_version": self.economic_integrity_version,
            "scope": self.scope,
            "populations": dict(self.populations),
            "economic_integrity": [item.to_dict() for item in self.economic_integrity],
            "metric_authority": dict(METRIC_AUTHORITY),
            "evidence_gaps": [gap.to_dict() for gap in EVIDENCE_GAPS],
            "missed_opportunity": {
                "disposition": MISSED_OPPORTUNITY_DISPOSITION,
                "safeguards": MISSED_OPPORTUNITY_SAFEGUARDS.to_dict(),
            },
            "trust": self.trust.to_dict(),
            "details": list(self.details),
        }


def _population_counts(rows: tuple[ReconciledPaperTrade, ...]) -> dict[str, Any]:
    return {
        "trades": len(rows),
        "by_lifecycle_status": _counts([row.lifecycle_status.value for row in rows]),
        "by_economic_result": _counts([row.economic_result.value for row in rows]),
        "by_execution_result": _counts([row.execution_result.value for row in rows]),
        "by_exit_mechanism": _counts([row.exit_mechanism.value for row in rows]),
        "settled": sum(1 for row in rows if row.is_settled),
        "quote_currencies": sorted(
            {str(row.quote_currency or "UNKNOWN") for row in rows}
        ),
    }


def build_profit_intelligence_overview(ledger: PaperLedger) -> ProfitIntelligenceOverview:
    """Compose the overview envelope from an already-read ledger.

    Deterministic and pure: the same ledger always yields the same envelope.

    An unreadable ledger is reported as unavailable rather than counted. A ledger
    whose trust envelope is not healthy could not be authoritatively read, so
    emitting population counts for it would fabricate measured zeros for evidence
    that was never observed - the failure mode this plane exists to prevent.
    """
    if not isinstance(ledger, PaperLedger):
        raise TypeError("ledger must be a PaperLedger")

    if not ledger.trust.is_healthy:
        reason = (
            (ledger.details[0] if ledger.details else None)
            or ",".join(ledger.trust.reasons)
            or "LEDGER_NOT_HEALTHY"
        )
        return unavailable_profit_intelligence(reason)

    rows = ledger.entries
    integrity = build_economic_integrity(rows)
    details = list(ledger.details)
    for summary in integrity:
        details.extend(summary.details)

    return ProfitIntelligenceOverview(
        populations=_population_counts(rows),
        economic_integrity=integrity,
        trust=ledger.trust,
        details=tuple(details),
    )


def unavailable_profit_intelligence(reason: str) -> ProfitIntelligenceOverview:
    """The overview envelope for an unreadable store.

    Structurally identical to the healthy envelope so a consumer has one code
    path, and never an apparently healthy empty result.

    ``populations`` is left empty rather than populated with zeros: "the store
    could not be read" and "the store was read and held nothing" are different
    truths, and a fabricated ``0`` would assert the second while meaning the
    first.
    """
    return ProfitIntelligenceOverview(
        populations={},
        economic_integrity=(),
        trust=unavailable(reason),
        details=(reason,),
    )


def build_lineage_for_trade(
    ledger: PaperLedger,
    paper_trade_id: str,
) -> TradeLineage | None:
    """Build the lineage for one trade, or ``None`` when it is not present.

    ``None`` means "not found in this ledger". A caller must not read ``None`` as
    proof of absence unless the ledger's own trust envelope is healthy - an
    unreadable ledger cannot prove a trade does not exist.
    """
    wanted = str(paper_trade_id or "").strip()
    if not wanted:
        raise ValueError("paper_trade_id is required")
    match = next(
        (row for row in ledger.entries if row.paper_trade_id == wanted), None
    )
    if match is None:
        return None
    return build_trade_lineage(match)


__all__ = [
    "PROFIT_INTELLIGENCE_READ_MODEL_VERSION",
    "ProfitIntelligenceOverview",
    "build_lineage_for_trade",
    "build_profit_intelligence_overview",
    "unavailable_profit_intelligence",
]
