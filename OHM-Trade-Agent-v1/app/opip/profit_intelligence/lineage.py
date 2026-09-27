"""Profit Intelligence decision/trade lineage explorer (read-only).

Turns one reconciled Paper-v2 trade into an ordered, availability-annotated
evidence chain: decision context, policy provenance, intent, attempt, fill,
protection, exit, economics, reconciliation and traceability.

Two rules make this different from dumping the trade row:

1. **Every stage states whether it is evidence.** A stage is ``KNOWN`` only when
   the canonical value it asserts is actually present; ``DERIVED`` when the
   value is a deterministic function of canonical evidence; ``NOT_APPLICABLE``
   when the fact cannot exist for this trade; and ``UNAVAILABLE`` when it should
   exist but was not readable. ``UNAVAILABLE`` is never rendered as ``0``.
2. **The chain is bounded and named.** The projection starts at the canonical
   decision context, not at opportunity detection, because pre-trade
   qualification evidence is not on this plane. That boundary is declared in
   :data:`LINEAGE_SCOPE` rather than left for a consumer to infer from a missing
   key.

This module reads already-derived rows and performs no aggregation, no query
and no write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from app.opip.cockpit.ledger import (
    ExecutionResult,
    LifecycleStatus,
    ReconciledPaperTrade,
)
from app.opip.cockpit.trust import Completeness, Freshness, TrustEnvelope
from app.opip.profit_intelligence.semantics import (
    PROFIT_INTELLIGENCE_CONTRACT_VERSION,
    LINEAGE_STAGE_ORDER,
    FactAvailability,
    LineageStage,
    net_pnl_reconciles,
)

#: Bumped whenever a derived definition in this projection changes.
LINEAGE_PROJECTION_VERSION = "profit-intelligence-lineage-v1"

#: What this projection can and cannot prove, stated once for every consumer.
LINEAGE_SCOPE = (
    "Execution lineage from the canonical decision context through fill, "
    "protection, exit, economics and reconciliation. Pre-trade opportunity "
    "detection, screening, qualification and rejection are not carried by the "
    "canonical replica and are declared in the Profit Intelligence evidence-gap "
    "registry instead of being approximated."
)

#: Same tolerance the cockpit ledger uses for quantity equality, so "no fill"
#: means the same thing on both surfaces.
_QUANTITY_TOLERANCE = 1e-9


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


@dataclass(frozen=True)
class StageEvidence:
    """One lineage stage: what is claimed, whether it is evidence, and when."""

    stage: LineageStage
    availability: FactAvailability
    occurred_at: datetime | None
    facts: Mapping[str, Any]
    evidence_event_ids: tuple[str, ...] = ()
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "availability": self.availability.value,
            "occurred_at": _iso(self.occurred_at),
            "facts": dict(self.facts),
            "evidence_event_ids": list(self.evidence_event_ids),
            "note": self.note,
        }


@dataclass(frozen=True)
class TradeLineage:
    """The ordered, availability-annotated evidence chain for one trade."""

    paper_trade_id: str
    contract_version: str = PROFIT_INTELLIGENCE_CONTRACT_VERSION
    projection_version: str = LINEAGE_PROJECTION_VERSION
    scope: str = LINEAGE_SCOPE
    stages: tuple[StageEvidence, ...] = ()
    trust: TrustEnvelope = field(
        default_factory=lambda: TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=Completeness.COMPLETE,
        )
    )
    details: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_trade_id": self.paper_trade_id,
            "contract_version": self.contract_version,
            "projection_version": self.projection_version,
            "scope": self.scope,
            "stages": [stage.to_dict() for stage in self.stages],
            "trust": self.trust.to_dict(),
            "details": list(self.details),
        }

    def stage(self, stage: LineageStage) -> StageEvidence:
        """Return one stage. Raises ``KeyError`` if the stage is unknown."""
        for candidate in self.stages:
            if candidate.stage is stage:
                return candidate
        raise KeyError(f"unknown lineage stage: {stage}")


def _not_applicable_reason(row: ReconciledPaperTrade) -> bool:
    return row.execution_result is ExecutionResult.NO_FILL


def _build_stage(
    row: ReconciledPaperTrade,
    stage: LineageStage,
) -> StageEvidence:
    trade_id = row.paper_trade_id
    if stage is LineageStage.DECISION_CONTEXT:
        known = row.decision_context_id is not None
        return StageEvidence(
            stage=stage,
            availability=(
                FactAvailability.KNOWN if known else FactAvailability.UNAVAILABLE
            ),
            occurred_at=row.evaluation_at,
            facts={
                "decision_context_id": row.decision_context_id,
                "disposition_id": row.disposition_id,
                "candidate_id": row.candidate_id,
                "episode_id": row.episode_id,
                "cohort_id": row.cohort_id,
                "instrument_version": row.instrument_version,
                "native_symbol": row.native_symbol,
                "quote_currency": row.quote_currency,
            },
            note=None if known else "canonical decision context is not recorded",
        )

    if stage is LineageStage.POLICY_PROVENANCE:
        known = row.policy_version is not None and row.policy_fingerprint is not None
        return StageEvidence(
            stage=stage,
            availability=(
                FactAvailability.KNOWN if known else FactAvailability.UNAVAILABLE
            ),
            occurred_at=None,
            facts={
                "policy_version": row.policy_version,
                "policy_fingerprint": row.policy_fingerprint,
                "execution_model_version": row.execution_model_version,
                "economic_model_version": row.economic_model_version,
            },
            note=(
                None
                if known
                else "policy version/fingerprint pair is not both recorded"
            ),
        )

    if stage is LineageStage.INTENT:
        known = row.entry_intent_at is not None
        return StageEvidence(
            stage=stage,
            availability=(
                FactAvailability.KNOWN if known else FactAvailability.UNAVAILABLE
            ),
            occurred_at=row.entry_intent_at,
            facts={"requested_entry_quantity": row.requested_entry_quantity},
            note=None if known else "no canonical entry order intent instant",
        )

    if stage is LineageStage.ATTEMPT:
        known = row.entry_attempt_at is not None
        if known:
            availability = FactAvailability.KNOWN
            note = None
        elif row.entry_intent_at is None:
            availability = FactAvailability.NOT_APPLICABLE
            note = "no entry intent was recorded, so no attempt can exist"
        else:
            availability = FactAvailability.UNAVAILABLE
            note = "entry intent exists but no attempt instant is recorded"
        return StageEvidence(
            stage=stage,
            availability=availability,
            occurred_at=row.entry_attempt_at,
            facts={},
            note=note,
        )

    if stage is LineageStage.FILL:
        # Mirrors the EXIT stage: a committed fill is evidenced by its timestamp
        # OR by the recorded quantity. Temporal evidence can legitimately be
        # BOUNDED or UNKNOWN precision, in which case `temporal_point` yields no
        # instant even though the fill itself is readable evidence - reporting
        # that as UNAVAILABLE would deny evidence the store actually holds.
        has_fill = (
            row.first_entry_fill_at is not None
            or row.entry_quantity > _QUANTITY_TOLERANCE
        )
        if has_fill:
            availability = FactAvailability.KNOWN
            note = None
        elif _not_applicable_reason(row):
            availability = FactAvailability.NOT_APPLICABLE
            note = "execution returned NO_FILL, so no entry fill can exist"
        else:
            availability = FactAvailability.UNAVAILABLE
            note = "no entry fill quantity or instant is readable"
        return StageEvidence(
            stage=stage,
            availability=availability,
            occurred_at=row.first_entry_fill_at,
            facts={
                "entry_quantity": row.entry_quantity,
                "entry_price_vwap": row.entry_price_vwap,
                "entry_notional": row.entry_notional,
                "execution_result": row.execution_result.value,
            },
            note=note,
        )

    if stage is LineageStage.PROTECTION:
        has_plan = (
            row.plan_seq is not None
            or row.planned_stop_price is not None
            or bool(row.planned_targets)
        )
        if has_plan:
            availability = FactAvailability.KNOWN
            note = None
        elif _not_applicable_reason(row):
            availability = FactAvailability.NOT_APPLICABLE
            note = "no position was opened, so no protection plan can exist"
        else:
            availability = FactAvailability.UNAVAILABLE
            note = "position evidence exists but no protection plan is recorded"
        return StageEvidence(
            stage=stage,
            availability=availability,
            occurred_at=None,
            facts={
                "plan_seq": row.plan_seq,
                "planned_stop_price": row.planned_stop_price,
                "planned_targets": [dict(target) for target in row.planned_targets],
                "planned_max_hold_seconds": row.planned_max_hold_seconds,
                "protection_state": row.protection_state,
                "early_close": row.early_close,
            },
            note=note,
        )

    if stage is LineageStage.EXIT:
        has_exit = (
            row.last_exit_fill_at is not None
            or row.exited_quantity > _QUANTITY_TOLERANCE
        )
        if has_exit:
            availability = FactAvailability.KNOWN
            note = None
        elif row.lifecycle_status in {
            LifecycleStatus.PENDING,
            LifecycleStatus.OPEN,
        }:
            availability = FactAvailability.NOT_APPLICABLE
            note = "the position has not been exited yet"
        else:
            availability = FactAvailability.UNAVAILABLE
            note = "trade is flat but no exit fill evidence is readable"
        return StageEvidence(
            stage=stage,
            availability=availability,
            occurred_at=row.last_exit_fill_at,
            facts={
                "exited_quantity": row.exited_quantity,
                "remaining_quantity": row.remaining_quantity,
                "exit_price_vwap": row.exit_price_vwap,
                "exit_notional": row.exit_notional,
                "exit_mechanism": row.exit_mechanism.value,
                "exit_mechanisms": [item.value for item in row.exit_mechanisms],
                "holding_seconds": row.holding_seconds,
            },
            note=note,
        )

    if stage is LineageStage.ECONOMICS:
        conserves = net_pnl_reconciles(
            gross_pnl=row.gross_pnl,
            execution_costs=row.execution_costs,
            net_pnl=row.net_pnl,
        )
        return StageEvidence(
            stage=stage,
            availability=(
                FactAvailability.KNOWN
                if row.net_pnl_definitive
                else FactAvailability.DERIVED
            ),
            occurred_at=None,
            facts={
                "gross_pnl": row.gross_pnl,
                "fee_cost": row.fee_cost,
                "spread_cost": row.spread_cost,
                "slippage_cost": row.slippage_cost,
                "other_cost": row.other_cost,
                "execution_costs": row.execution_costs,
                "net_pnl": row.net_pnl,
                "reserved_capital": row.reserved_capital,
                "economics_source": row.economics_source.value,
                "net_pnl_definitive": row.net_pnl_definitive,
                "economic_result": row.economic_result.value,
                "conserves_net_equals_gross_minus_costs": conserves,
            },
            note=(
                None
                if row.net_pnl_definitive
                else "economics are indicative until canonical FINAL_VERIFIED"
            ),
        )

    if stage is LineageStage.RECONCILIATION:
        known = row.terminal_reconciliation_state is not None
        if known:
            availability = FactAvailability.KNOWN
            note = None
        elif row.lifecycle_status is LifecycleStatus.PENDING:
            availability = FactAvailability.NOT_APPLICABLE
            note = "the trade has not reached a reconciliation point"
        else:
            availability = FactAvailability.UNAVAILABLE
            note = "no terminal reconciliation state is recorded"
        return StageEvidence(
            stage=stage,
            availability=availability,
            occurred_at=None,
            facts={
                "terminal_reconciliation_state": row.terminal_reconciliation_state,
                "net_pnl_definitive": row.net_pnl_definitive,
            },
            note=note,
        )

    if stage is LineageStage.TRACEABILITY:
        known = bool(row.event_ids)
        return StageEvidence(
            stage=stage,
            availability=(
                FactAvailability.KNOWN if known else FactAvailability.UNAVAILABLE
            ),
            occurred_at=None,
            facts={"event_id_count": len(row.event_ids)},
            evidence_event_ids=tuple(row.event_ids),
            note=None if known else f"no canonical event ids recorded for {trade_id}",
        )

    raise KeyError(f"unsupported lineage stage: {stage}")


def build_trade_lineage(row: ReconciledPaperTrade) -> TradeLineage:
    """Project one reconciled trade into an ordered evidence chain.

    Deterministic: the stage order is fixed by :data:`LINEAGE_STAGE_ORDER`, and
    every value is read from the committed row. Nothing is aggregated, queried or
    inferred beyond the availability rules documented per stage.
    """
    if not isinstance(row, ReconciledPaperTrade):
        raise TypeError("row must be a ReconciledPaperTrade")

    stages = tuple(_build_stage(row, stage) for stage in LINEAGE_STAGE_ORDER)
    details = tuple(
        f"{stage.stage.value}: {stage.note}"
        for stage in stages
        if stage.availability is FactAvailability.UNAVAILABLE and stage.note
    )
    return TradeLineage(
        paper_trade_id=row.paper_trade_id,
        stages=stages,
        trust=row.trust,
        details=details,
    )


__all__ = [
    "LINEAGE_PROJECTION_VERSION",
    "LINEAGE_SCOPE",
    "StageEvidence",
    "TradeLineage",
    "build_trade_lineage",
]
