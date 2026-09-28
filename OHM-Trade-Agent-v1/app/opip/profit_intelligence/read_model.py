"""Profit Intelligence read model (read-only, analytics plane).

Composes the Profit Intelligence projections into one envelope a consumer can
read without re-deriving anything: contract and projection versions, the
lineage scope, per-currency economic integrity, population counts, the
evidence-gap registry, and - when the caller supplies them - the qualification
funnel and forward-outcome projections.

Planning-layer values are counted, never invented
-------------------------------------------------
This module aggregates **counts and already-derived values only**. It computes
no new economic formula: economic totals come from
:mod:`app.opip.profit_intelligence.economics`, which itself only sums the
canonical per-trade values and re-checks the writer's own conservation
relationship. No cross-currency sum is ever produced, because USD and USDT are
distinct portfolios.

Evidence projections are injected, never inferred
-------------------------------------------------
The qualification funnel and forward-outcome projections are produced from
evidence that lives on the trading host / learning plane. This plane never
reconstructs them from unrelated current state: a caller that has read the
canonical evidence passes the projection in, and a caller that has not leaves it
absent. Absent is reported as absent (``None`` / empty), never as a healthy zero.

The funnel/forward-outcome **join** is evidence-only and deliberately never
classifies a "missed profitable opportunity": a positive market outcome is not
executable profit, so the join states its match status and stops.

Unavailable is never empty-but-healthy
--------------------------------------
When the canonical replica cannot be read, the envelope has exactly the same
structure as a healthy one - same keys, empty collections - and carries an
explicit ``UNAVAILABLE`` trust envelope plus a reason. It is never returned as an
apparently healthy empty result, and it never raises into the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

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
from app.opip.profit_intelligence.forward_outcomes import (
    ForwardOutcomeProjection,
    ForwardOutcomeRecord,
)
from app.opip.profit_intelligence.lineage import (
    LINEAGE_PROJECTION_VERSION,
    LINEAGE_SCOPE,
    TradeLineage,
    build_trade_lineage,
)
from app.opip.profit_intelligence.qualification_funnel import (
    QUALIFICATION_FUNNEL_PROJECTION_VERSION,
    QualificationFunnelProjection,
    QualificationFunnelRecord,
)
from app.opip.profit_intelligence.semantics import (
    EVIDENCE_GAPS,
    METRIC_AUTHORITY,
    MISSED_OPPORTUNITY_DISPOSITION,
    MISSED_OPPORTUNITY_SAFEGUARDS,
    MISSED_OPPORTUNITY_UNBLOCK,
    PROFIT_INTELLIGENCE_CONTRACT_VERSION,
)

#: Bumped whenever the composition of this envelope changes.
PROFIT_INTELLIGENCE_READ_MODEL_VERSION = "profit-intelligence-read-model-v3"

#: Version of the evidence-only funnel/forward-outcome join.
FORWARD_OUTCOME_JOIN_VERSION = "profit-intelligence-forward-outcome-join-v1"


class ForwardOutcomeJoinStatus(str, Enum):
    """Outcome of joining a sealed funnel record to forward-outcome evidence.

    These states are kept distinct so a missing match is never read as a
    measured "no opportunity", and an ambiguous match is never silently reduced
    to one convenient row.
    """

    MATCHED = "MATCHED"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    IDENTITY_UNAVAILABLE = "IDENTITY_UNAVAILABLE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


@dataclass(frozen=True)
class ForwardOutcomeJoin:
    """Evidence-only link from one sealed funnel record to forward outcomes.

    The join never labels a "missed profitable opportunity". ``classification``
    records that the executable-profit question is out of scope for this
    evidence: a market outcome is not proof of executable profit (entry, exit,
    costs, capacity and risk feasibility are not in this evidence), so the
    classification is explicitly unavailable and ``consumption`` is evidence-only.
    """

    status: ForwardOutcomeJoinStatus
    funnel_candidate_id: str | None
    episode_id: str | None
    matches: tuple[ForwardOutcomeRecord, ...] = ()
    detail: str = ""
    join_version: str = FORWARD_OUTCOME_JOIN_VERSION
    consumption: str = "EVIDENCE_ONLY"
    classification: str = "MISSED_PROFIT_CLASSIFICATION_UNAVAILABLE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "funnel_candidate_id": self.funnel_candidate_id,
            "episode_id": self.episode_id,
            "matches": [record.to_dict() for record in self.matches],
            "detail": self.detail,
            "join_version": self.join_version,
            "consumption": self.consumption,
            "classification": self.classification,
        }


def join_funnel_record_to_forward_outcomes(
    record: QualificationFunnelRecord,
    projections: Sequence[ForwardOutcomeProjection],
) -> ForwardOutcomeJoin:
    """Join one sealed funnel candidate to its forward-outcome evidence.

    The only defensible bridge is the canonical episode identity: the funnel
    record's ``episode_id`` and a forward record's ``canonical_episode_id`` are
    both the canonical pair-per-scan episode, so equality is a real identity
    match rather than a coincidence of symbol and direction. A forward family
    that carries no episode identity (Discovery is instrument-level) cannot
    match and is reported as no match, not guessed.

    Only **readable** projections are searched. A projection that could not be
    authoritatively read is never treated as an empty-but-certain source: if no
    supplied projection is readable, or a search across the readable ones finds
    nothing while some source was unreadable, the join reports
    ``SOURCE_UNAVAILABLE`` rather than ``NO_MATCH``. ``NO_MATCH`` means a
    verified absence across every supplied, readable source.

    "Readable" is the source being retrievable, *not* fully matured: a source
    that was read but still has unmatured windows is searched, so a partial
    window never hides a real match or masks a second match that should make the
    join ``AMBIGUOUS``.
    """
    if not projections:
        return ForwardOutcomeJoin(
            status=ForwardOutcomeJoinStatus.SOURCE_UNAVAILABLE,
            funnel_candidate_id=record.candidate_id,
            episode_id=record.episode_id,
            detail="no forward-outcome projection was supplied",
        )
    readable = tuple(
        projection
        for projection in projections
        if projection.trust.freshness is not Freshness.UNAVAILABLE
    )
    if not readable:
        return ForwardOutcomeJoin(
            status=ForwardOutcomeJoinStatus.SOURCE_UNAVAILABLE,
            funnel_candidate_id=record.candidate_id,
            episode_id=record.episode_id,
            detail=(
                "no forward-outcome source could be authoritatively read; "
                "absence cannot be asserted"
            ),
        )
    if not record.episode_id:
        return ForwardOutcomeJoin(
            status=ForwardOutcomeJoinStatus.IDENTITY_UNAVAILABLE,
            funnel_candidate_id=record.candidate_id,
            episode_id=None,
            detail="funnel record carries no canonical episode identity to join on",
        )

    matches: list[ForwardOutcomeRecord] = []
    for projection in readable:
        for forward in projection.records:
            if forward.canonical_episode_id == record.episode_id:
                matches.append(forward)

    if not matches:
        if len(readable) < len(projections):
            return ForwardOutcomeJoin(
                status=ForwardOutcomeJoinStatus.SOURCE_UNAVAILABLE,
                funnel_candidate_id=record.candidate_id,
                episode_id=record.episode_id,
                detail=(
                    "no readable forward-outcome source contains this episode, but "
                    "at least one supplied source was unreadable, so absence is not "
                    "verified"
                ),
            )
        return ForwardOutcomeJoin(
            status=ForwardOutcomeJoinStatus.NO_MATCH,
            funnel_candidate_id=record.candidate_id,
            episode_id=record.episode_id,
            detail="no forward-outcome record shares this canonical episode",
        )
    if len(matches) > 1:
        return ForwardOutcomeJoin(
            status=ForwardOutcomeJoinStatus.AMBIGUOUS,
            funnel_candidate_id=record.candidate_id,
            episode_id=record.episode_id,
            matches=tuple(matches),
            detail=(
                f"{len(matches)} forward-outcome records share this episode; "
                "refusing to choose one"
            ),
        )
    detail = "matched on canonical episode identity"
    if len(readable) < len(projections):
        detail += (
            "; some supplied sources were unreadable and not searched, so an "
            "additional match cannot be excluded"
        )
    return ForwardOutcomeJoin(
        status=ForwardOutcomeJoinStatus.MATCHED,
        funnel_candidate_id=record.candidate_id,
        episode_id=record.episode_id,
        matches=(matches[0],),
        detail=detail,
    )


def _counts(values: Iterable[str]) -> dict[str, int]:
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
    # Optional evidence projections, injected by a caller that read the canonical
    # evidence. Absent means "not supplied on this plane", never a zero.
    qualification_funnel: QualificationFunnelProjection | None = None
    forward_outcomes: tuple[ForwardOutcomeProjection, ...] = ()
    contract_version: str = PROFIT_INTELLIGENCE_CONTRACT_VERSION
    read_model_version: str = PROFIT_INTELLIGENCE_READ_MODEL_VERSION
    economic_integrity_version: str = ECONOMIC_INTEGRITY_VERSION
    funnel_projection_version: str = QUALIFICATION_FUNNEL_PROJECTION_VERSION
    scope: str = LINEAGE_SCOPE

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "read_model_version": self.read_model_version,
            "economic_integrity_version": self.economic_integrity_version,
            "funnel_projection_version": self.funnel_projection_version,
            "scope": self.scope,
            "populations": dict(self.populations),
            "economic_integrity": [item.to_dict() for item in self.economic_integrity],
            "metric_authority": dict(METRIC_AUTHORITY),
            "evidence_gaps": [gap.to_dict() for gap in EVIDENCE_GAPS],
            "qualification_funnel": (
                self.qualification_funnel.to_dict()
                if self.qualification_funnel is not None
                else None
            ),
            "forward_outcomes": [
                projection.to_dict() for projection in self.forward_outcomes
            ],
            "missed_opportunity": {
                "disposition": MISSED_OPPORTUNITY_DISPOSITION,
                "safeguards": MISSED_OPPORTUNITY_SAFEGUARDS.to_dict(),
                "unblock_evidence": MISSED_OPPORTUNITY_UNBLOCK.to_dict(),
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


def build_profit_intelligence_overview(
    ledger: PaperLedger,
    *,
    qualification_funnel: QualificationFunnelProjection | None = None,
    forward_outcomes: Sequence[ForwardOutcomeProjection] = (),
) -> ProfitIntelligenceOverview:
    """Compose the overview envelope from an already-read ledger.

    Deterministic and pure: the same inputs always yield the same envelope.

    An unreadable ledger is reported as unavailable rather than counted. A ledger
    whose trust envelope is not healthy could not be authoritatively read, so
    emitting population counts for it would fabricate measured zeros for evidence
    that was never observed - the failure mode this plane exists to prevent.

    The injected projections are carried through unchanged; this function never
    derives them and never lets them influence the ledger-derived economics.
    """
    if not isinstance(ledger, PaperLedger):
        raise TypeError("ledger must be a PaperLedger")

    projections = tuple(forward_outcomes)

    if not ledger.trust.is_healthy:
        reason = (
            (ledger.details[0] if ledger.details else None)
            or ",".join(ledger.trust.reasons)
            or "LEDGER_NOT_HEALTHY"
        )
        return unavailable_profit_intelligence(
            reason,
            qualification_funnel=qualification_funnel,
            forward_outcomes=projections,
        )

    rows = ledger.entries
    integrity = build_economic_integrity(rows)
    details = list(ledger.details)
    for summary in integrity:
        details.extend(summary.details)
    if qualification_funnel is not None:
        details.extend(qualification_funnel.details)
    for projection in projections:
        details.extend(projection.details)

    return ProfitIntelligenceOverview(
        populations=_population_counts(rows),
        economic_integrity=integrity,
        trust=ledger.trust,
        details=tuple(details),
        qualification_funnel=qualification_funnel,
        forward_outcomes=projections,
    )


def unavailable_profit_intelligence(
    reason: str,
    *,
    qualification_funnel: QualificationFunnelProjection | None = None,
    forward_outcomes: Sequence[ForwardOutcomeProjection] = (),
) -> ProfitIntelligenceOverview:
    """The overview envelope for an unreadable store.

    Structurally identical to the healthy envelope so a consumer has one code
    path, and never an apparently healthy empty result.

    ``populations`` is left empty rather than populated with zeros: "the store
    could not be read" and "the store was read and held nothing" are different
    truths, and a fabricated ``0`` would assert the second while meaning the
    first. Injected evidence projections are still carried: their own trust
    envelope states whether *they* were readable, independently of the ledger.
    """
    return ProfitIntelligenceOverview(
        populations={},
        economic_integrity=(),
        trust=unavailable(reason),
        details=(reason,),
        qualification_funnel=qualification_funnel,
        forward_outcomes=tuple(forward_outcomes),
    )


def build_lineage_for_trade(
    ledger: PaperLedger,
    paper_trade_id: str,
) -> TradeLineage | None:
    """Build the lineage for one trade, or ``None`` when it is not present.

    ``None`` means "not found in this ledger", and it is only ever returned for a
    **trusted** read. A ledger whose own trust envelope is not healthy cannot
    prove either the contents or the absence of a trade, so projecting from it
    would present non-authoritative evidence as authoritative and a missing row as
    a verified absence. That case fails closed with ``ValueError`` instead.
    """
    wanted = str(paper_trade_id or "").strip()
    if not wanted:
        raise ValueError("paper_trade_id is required")
    if not ledger.trust.is_healthy:
        reason = (
            (ledger.details[0] if ledger.details else None)
            or ",".join(ledger.trust.reasons)
            or "LEDGER_NOT_HEALTHY"
        )
        raise ValueError(
            "ledger is not healthy, so a trade lineage cannot be proven: "
            f"{reason}"
        )
    match = next(
        (row for row in ledger.entries if row.paper_trade_id == wanted), None
    )
    if match is None:
        return None
    return build_trade_lineage(match)


__all__ = [
    "FORWARD_OUTCOME_JOIN_VERSION",
    "PROFIT_INTELLIGENCE_READ_MODEL_VERSION",
    "ForwardOutcomeJoin",
    "ForwardOutcomeJoinStatus",
    "ProfitIntelligenceOverview",
    "build_lineage_for_trade",
    "build_profit_intelligence_overview",
    "join_funnel_record_to_forward_outcomes",
    "unavailable_profit_intelligence",
]
