"""Profit Intelligence semantic contract (read-only).

This module is the single place that defines what a Profit Intelligence answer
*means*. It defines semantics only: no queries, no aggregation, no write path,
no trading authority.

Availability vocabulary
-----------------------
Every fact this plane reports carries a :class:`FactAvailability`. The four
values are kept distinct on purpose, because they imply four different operator
actions:

``KNOWN``
    A canonical value was read. It is evidence.
``DERIVED``
    A value computed deterministically from canonical evidence. It is not
    itself canonical - a correction upstream changes it.
``UNAVAILABLE``
    The fact is expected for this population but was not readable here. It must
    never be rendered as ``0``.
``NOT_APPLICABLE``
    The fact cannot exist for this population (for example, a cost component
    for a trade that recorded no fill). It must never be rendered as ``0``
    either, because ``0`` asserts a measured zero.

Lineage stages
--------------
:class:`LineageStage` names the *execution* lineage Profit Intelligence can
prove from the canonical replica. The pre-trade decision funnel
(opportunity detection, screening, qualification, rejection) is canonical
evidence of a different kind and is **not** on the analytics plane; those facts
are declared in :data:`EVIDENCE_GAPS` rather than being approximated.

Economic conservation
---------------------
:func:`net_pnl_reconciles` encodes the relationship the canonical writer itself
validates, taken from the paper evidence contracts::

    realized_net_pnl == realized_gross_pnl - recorded_execution_costs

Profit Intelligence re-checks it on read instead of trusting the projection,
because a displayed economic number that does not conserve is not an economic
result.

Metric authority
----------------
:data:`METRIC_AUTHORITY` records which registered metric in
``app.opip.contracts.paper_metrics`` owns each reported quantity. No formula is
restated here: changing a definition changes it in the registry, not in this
plane.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

#: Bumped whenever a semantic in this module changes, so a stored or cached
#: response can be recognised as predating the change.
PROFIT_INTELLIGENCE_CONTRACT_VERSION = "profit-intelligence-contract-v1"

#: Comparison tolerance for money equality. Deliberately the same value the
#: cockpit ledger uses, so "conserves" means the same thing on both surfaces.
PNL_TOLERANCE = 1e-9


class FactAvailability(str, Enum):
    """Whether a reported fact is evidence, derived, absent, or impossible."""

    KNOWN = "KNOWN"
    DERIVED = "DERIVED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class LineageStage(str, Enum):
    """Ordered execution-lineage stages provable from canonical evidence."""

    DECISION_CONTEXT = "DECISION_CONTEXT"
    POLICY_PROVENANCE = "POLICY_PROVENANCE"
    INTENT = "INTENT"
    ATTEMPT = "ATTEMPT"
    FILL = "FILL"
    PROTECTION = "PROTECTION"
    EXIT = "EXIT"
    ECONOMICS = "ECONOMICS"
    RECONCILIATION = "RECONCILIATION"
    TRACEABILITY = "TRACEABILITY"


#: The canonical presentation order of the lineage. A consumer must not have to
#: re-derive it, and a projection must not reorder it.
LINEAGE_STAGE_ORDER: tuple[LineageStage, ...] = (
    LineageStage.DECISION_CONTEXT,
    LineageStage.POLICY_PROVENANCE,
    LineageStage.INTENT,
    LineageStage.ATTEMPT,
    LineageStage.FILL,
    LineageStage.PROTECTION,
    LineageStage.EXIT,
    LineageStage.ECONOMICS,
    LineageStage.RECONCILIATION,
    LineageStage.TRACEABILITY,
)


class EconomicComponent(str, Enum):
    """The execution-cost components the canonical fill contract carries.

    Names are taken from the canonical fill contract exactly
    (``fee_cost``/``spread_cost``/``slippage_cost``/``other_supported_cost``), so
    a near-miss name cannot silently reference a field no fill can carry.
    """

    FEE_COST = "FEE_COST"
    SPREAD_COST = "SPREAD_COST"
    SLIPPAGE_COST = "SLIPPAGE_COST"
    OTHER_SUPPORTED_COST = "OTHER_SUPPORTED_COST"
    EXECUTION_COSTS = "EXECUTION_COSTS"
    GROSS_PNL = "GROSS_PNL"
    NET_PNL = "NET_PNL"
    RESERVED_CAPITAL = "RESERVED_CAPITAL"


#: Components that must sum to the recorded execution cost, in registry order.
SUPPORTED_COST_COMPONENTS: tuple[EconomicComponent, ...] = (
    EconomicComponent.FEE_COST,
    EconomicComponent.SPREAD_COST,
    EconomicComponent.SLIPPAGE_COST,
    EconomicComponent.OTHER_SUPPORTED_COST,
)


class EvidencePlane(str, Enum):
    """Where a fact physically lives, so a gap names its real owner."""

    CANONICAL_REPLICA = "CANONICAL_REPLICA"
    TRADING_HOST_FILE_WAL = "TRADING_HOST_FILE_WAL"
    LEARNING_PLANE = "LEARNING_PLANE"
    NONE = "NONE"


class MissedOpportunityCause(str, Enum):
    """Canonical cause classes for an opportunity the system did not trade.

    These stay **distinct** on purpose. Collapsing them would erase exactly the
    distinction an owner needs: "we chose not to", "we could not technically",
    "risk blocked it", "we had no capacity", and "we tried and execution failed"
    imply four different remediations.

    ``UNKNOWN`` exists so an unattributable cause is stated rather than being
    folded into a neighbouring class.
    """

    REJECTED = "REJECTED"
    FILTERED = "FILTERED"
    TECHNICALLY_UNAVAILABLE = "TECHNICALLY_UNAVAILABLE"
    RISK_BLOCKED = "RISK_BLOCKED"
    CAPACITY_BLOCKED = "CAPACITY_BLOCKED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MissedOpportunitySafeguards:
    """Pre-registered methodology for missed-opportunity evidence.

    The facts needed to *compute* missed-profitable-opportunity evidence live on
    the learning plane and are not readable from the canonical replica, so the
    projection is blocked at a frozen boundary rather than approximated. This
    record pre-registers the safeguards the analysis must satisfy once that
    evidence is exposed, so no future implementation can quietly relax them.
    """

    decision_time_facts_separate_from_future_outcomes: bool = True
    no_hindsight_leakage: bool = True
    forward_window_provenance_required: bool = True
    outcome_calculation_version_required: bool = True
    cause_classes_distinct: bool = True
    classification_requires_canonical_evidence: bool = True
    uncertainty_and_incomplete_data_preserved: bool = True
    no_threshold_or_strategy_change_authority: bool = True
    consumption: str = "EVIDENCE_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_time_facts_separate_from_future_outcomes": (
                self.decision_time_facts_separate_from_future_outcomes
            ),
            "no_hindsight_leakage": self.no_hindsight_leakage,
            "forward_window_provenance_required": (
                self.forward_window_provenance_required
            ),
            "outcome_calculation_version_required": (
                self.outcome_calculation_version_required
            ),
            "cause_classes_distinct": self.cause_classes_distinct,
            "classification_requires_canonical_evidence": (
                self.classification_requires_canonical_evidence
            ),
            "uncertainty_and_incomplete_data_preserved": (
                self.uncertainty_and_incomplete_data_preserved
            ),
            "no_threshold_or_strategy_change_authority": (
                self.no_threshold_or_strategy_change_authority
            ),
            "consumption": self.consumption,
            "cause_classes": [cause.value for cause in MissedOpportunityCause],
        }


#: The honest disposition of the missed-opportunity increment on this plane.
#: It is blocked at a frozen boundary, not silently skipped: the required
#: canonical evidence (screening, funnel, forward outcomes) is produced on the
#: trading host and the learning plane, neither of which the read-only analytics
#: plane reads.
MISSED_OPPORTUNITY_DISPOSITION = "BLOCKED_AT_FROZEN_BOUNDARY"

#: Pre-registered safeguards for the blocked increment.
MISSED_OPPORTUNITY_SAFEGUARDS = MissedOpportunitySafeguards()


@dataclass(frozen=True)
class EvidenceGap:
    """A fact Profit Intelligence cannot truthfully answer on this plane.

    A gap is a first-class, machine-readable statement of missing evidence. It
    names *what* is missing, *who* produces it, *which plane* holds it, *why*
    the read-only analytics plane cannot use it, and which frozen boundary would
    have to change to close it. It is never a placeholder value.
    """

    fact: str
    availability: FactAvailability
    producer: str
    plane: EvidencePlane
    reason: str
    frozen_boundary: str
    recommended_increment: str

    def to_dict(self) -> dict[str, str]:
        return {
            "fact": self.fact,
            "availability": self.availability.value,
            "producer": self.producer,
            "plane": self.plane.value,
            "reason": self.reason,
            "frozen_boundary": self.frozen_boundary,
            "recommended_increment": self.recommended_increment,
        }


#: The honest inventory of evidence the Profit Intelligence epic asks for that
#: is **not** reachable from the read-only canonical replica.
#:
#: Each entry was established from repository truth: the qualification streams
#: are written by the trading-host scanner into its own file WAL, and the
#: opportunity-accountability / forward-outcome evidence is produced on the
#: learning plane. Neither is replicated to, or mounted by, the analytics plane
#: that serves the Cockpit. Publishing a number for any of these from the data
#: available here would be fabrication, so they are declared instead.
EVIDENCE_GAPS: tuple[EvidenceGap, ...] = (
    EvidenceGap(
        fact="QUALIFICATION_FUNNEL_PROGRESSION",
        availability=FactAvailability.UNAVAILABLE,
        producer="app.opip.decision.store (scan_opportunities / scan_movers)",
        plane=EvidencePlane.TRADING_HOST_FILE_WAL,
        reason=(
            "Stage-0 screening evaluations and funnel gate history are appended to "
            "the trading host's /app/data/opip/qualification JSONL file WAL; that "
            "evidence is not published to the canonical replica the read-only "
            "analytics plane reads."
        ),
        frozen_boundary="trading-host evidence plane / canonical replica scope",
        recommended_increment=(
            "Publish a typed screening/funnel projection into the canonical writer "
            "(or a verified replica stream) under an explicit owner-approved "
            "contract, then project the funnel here."
        ),
    ),
    EvidenceGap(
        fact="REJECTION_REASON_AND_GATE_ATTRIBUTION",
        availability=FactAvailability.UNAVAILABLE,
        producer="app.opip.decision.summary / funnel gate_results",
        plane=EvidencePlane.TRADING_HOST_FILE_WAL,
        reason=(
            "Per-gate decisions and terminal rejection reasons exist only in the "
            "funnel rows on the trading host. The canonical replica carries no "
            "rejection record, so a rejection cannot be attributed here without "
            "inventing one."
        ),
        frozen_boundary="trading-host evidence plane / canonical event vocabulary",
        recommended_increment=(
            "Add a canonical rejection/gate event with an owner-approved schema, "
            "then attribute rejections from canonical evidence."
        ),
    ),
    EvidenceGap(
        fact="MISSED_PROFITABLE_OPPORTUNITY",
        availability=FactAvailability.UNAVAILABLE,
        producer="app.services.opportunity_accountability",
        plane=EvidencePlane.LEARNING_PLANE,
        reason=(
            "Counterfactual and missed-winner classification is built on the "
            "learning plane from screening, funnel and forward-outcome evidence. "
            "The analytics plane serving this read model mounts only the canonical "
            "replica, so the accountability ledger is not readable here."
        ),
        frozen_boundary="learning/analytics plane separation and canonical replica scope",
        recommended_increment=(
            "Expose the accountability summary as a versioned derived read model "
            "with its own freshness and completeness contract, and consume it here "
            "without granting it any policy authority."
        ),
    ),
    EvidenceGap(
        fact="FORWARD_OUTCOME_HORIZON_EVIDENCE",
        availability=FactAvailability.UNAVAILABLE,
        producer="app.jobs.build_phase3c_forward_outcomes / app.opip.discovery.outcomes",
        plane=EvidencePlane.LEARNING_PLANE,
        reason=(
            "Forward outcome windows (MFE/MAE, horizon returns) are matured on the "
            "learning worker. They are not part of the canonical replica this plane "
            "reads, and decision-time versus future evidence must not be mixed "
            "without the horizon contract."
        ),
        frozen_boundary="point-in-time / sealed-evaluation boundary",
        recommended_increment=(
            "Publish forward outcomes as an explicitly point-in-time-labelled derived "
            "stream, keeping decision-time facts and future outcomes in separate "
            "fields."
        ),
    ),
    EvidenceGap(
        fact="UNREALIZED_MARK_TO_MARKET_PNL",
        availability=FactAvailability.UNAVAILABLE,
        producer="none (no canonical mark evidence exists)",
        plane=EvidencePlane.NONE,
        reason=(
            "No canonical mark/valuation evidence is bound to open paper positions, "
            "so an unrealized figure cannot be derived without assuming a price the "
            "repository does not hold as evidence."
        ),
        frozen_boundary="canonical mark evidence absence",
        recommended_increment=(
            "Define a canonical, timestamped mark-evidence contract before any "
            "unrealized P&L is published."
        ),
    ),
    EvidenceGap(
        fact="REGIME_AND_CAPITAL_UTILIZATION_GROUPING",
        availability=FactAvailability.UNAVAILABLE,
        producer="none (no canonical regime or portfolio-occupancy evidence)",
        plane=EvidencePlane.NONE,
        reason=(
            "No canonical regime label or time-weighted capital-occupancy series is "
            "recorded, so grouping economics by regime or exposure would be a "
            "presentation-layer invention."
        ),
        frozen_boundary="canonical evidence addition",
        recommended_increment=(
            "Record regime labels and occupancy against canonical decision contexts "
            "before grouping economics by them."
        ),
    ),
)


#: Which registered metric owns each quantity Profit Intelligence reports.
#: The registry (``app.opip.contracts.paper_metrics``) stays the single
#: definition of the formula; this plane only names it.
METRIC_AUTHORITY: Mapping[str, str] = MappingProxyType(
    {
        "gross_pnl": "paper.realized_net_pnl",
        "net_pnl": "paper.realized_net_pnl",
        "execution_costs": "paper.execution_cost",
        "net_expectancy": "paper.net_expectancy",
        "fill_ratio": "paper.fill_ratio",
        "entry_latency": "paper.entry_latency",
        "exit_latency": "paper.exit_latency",
        "capture_efficiency": "paper.capture_efficiency",
        "unresolved_count": "paper.unresolved_count",
    }
)


def net_pnl_reconciles(
    *,
    gross_pnl: float,
    execution_costs: float,
    net_pnl: float,
    tolerance: float = PNL_TOLERANCE,
) -> bool:
    """Whether a row conserves the canonical writer's own economic relationship.

    The paper evidence contract requires exactly this on reconciliation:

    ``realized_net_pnl == realized_gross_pnl - recorded_execution_costs``

    Re-checking it on read means a projection defect cannot present a
    self-inconsistent number as an economic result.
    """
    return abs(float(net_pnl) - (float(gross_pnl) - float(execution_costs))) <= tolerance


def supported_costs_sum(
    components: Mapping[EconomicComponent, float],
) -> float:
    """Sum the four supported cost components in canonical contract order.

    Order is fixed by :data:`SUPPORTED_COST_COMPONENTS` so the total is
    reproducible rather than dependent on mapping iteration order.
    """
    return sum(
        float(components.get(component, 0.0))
        for component in SUPPORTED_COST_COMPONENTS
    )


__all__ = [
    "EVIDENCE_GAPS",
    "LINEAGE_STAGE_ORDER",
    "METRIC_AUTHORITY",
    "MISSED_OPPORTUNITY_DISPOSITION",
    "MISSED_OPPORTUNITY_SAFEGUARDS",
    "PNL_TOLERANCE",
    "PROFIT_INTELLIGENCE_CONTRACT_VERSION",
    "SUPPORTED_COST_COMPONENTS",
    "EconomicComponent",
    "EvidenceGap",
    "EvidencePlane",
    "FactAvailability",
    "LineageStage",
    "MissedOpportunityCause",
    "MissedOpportunitySafeguards",
    "net_pnl_reconciles",
    "supported_costs_sum",
]
