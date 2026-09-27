"""Profit Intelligence economic integrity projection (read-only).

Answers one owner question truthfully: *do the economics we display conserve,
and is any cost component missing?*

Why this is not a second P&L truth
----------------------------------
It computes no new economic value. Every figure comes from the reconciled
Paper-v2 row, whose economics are either the canonical writer's own verified
statement or a derivation using the same relationship the writer validates. This
module only:

* re-checks conservation, so a projection defect is caught rather than shown;
* separates definitive from indicative population, so an unverified number never
  reads as a settled result;
* states per-component availability, so a missing or inapplicable cost is never
  rendered as a measured zero;
* never mixes quote currencies, because USD and USDT are distinct portfolios.

Cost-component availability
---------------------------
The canonical fill contract requires ``fee_cost``, ``spread_cost``,
``slippage_cost`` and ``other_supported_cost`` on **every** fill. So a summed
``0.0`` over a population that has fills is a genuine measured zero, while the
same ``0.0`` over a population with no fills is ``NOT_APPLICABLE``. The two are
reported differently rather than both shown as ``0``.

Residual (unmodelled) cost
--------------------------
When economics come from canonical reconciliation, ``execution_costs`` is the
writer's recorded aggregate and may exceed the sum of the four supported fill
components. That residual is surfaced explicitly as
``unmodelled_cost_residual`` and is never silently folded into a component or
dropped.

Metric semantics stay in the registry
-------------------------------------
:data:`~app.opip.profit_intelligence.semantics.METRIC_AUTHORITY` names the
registered metric that owns each quantity. No formula is restated here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from app.opip.cockpit.ledger import ReconciledPaperTrade
from app.opip.cockpit.trust import Completeness, Freshness, TrustEnvelope
from app.opip.profit_intelligence.semantics import (
    METRIC_AUTHORITY,
    SUPPORTED_COST_COMPONENTS,
    EconomicComponent,
    FactAvailability,
    net_pnl_reconciles,
    supported_costs_sum,
)

#: Bumped whenever a derived definition in this projection changes.
ECONOMIC_INTEGRITY_VERSION = "profit-intelligence-economics-v1"

#: Same tolerance the cockpit ledger and the canonical writer use for money.
_MONEY_TOLERANCE = 1e-9

#: The canonical presentation order of the reported components.
_COMPONENT_ORDER: tuple[EconomicComponent, ...] = (
    EconomicComponent.FEE_COST,
    EconomicComponent.SPREAD_COST,
    EconomicComponent.SLIPPAGE_COST,
    EconomicComponent.OTHER_SUPPORTED_COST,
    EconomicComponent.EXECUTION_COSTS,
    EconomicComponent.GROSS_PNL,
    EconomicComponent.NET_PNL,
    EconomicComponent.RESERVED_CAPITAL,
)

_COMPONENT_FIELDS: Mapping[EconomicComponent, str] = {
    EconomicComponent.FEE_COST: "fee_cost",
    EconomicComponent.SPREAD_COST: "spread_cost",
    EconomicComponent.SLIPPAGE_COST: "slippage_cost",
    EconomicComponent.OTHER_SUPPORTED_COST: "other_cost",
    EconomicComponent.EXECUTION_COSTS: "execution_costs",
    EconomicComponent.GROSS_PNL: "gross_pnl",
    EconomicComponent.NET_PNL: "net_pnl",
    EconomicComponent.RESERVED_CAPITAL: "reserved_capital",
}


@dataclass(frozen=True)
class CostComponentEvidence:
    """One reported economic quantity and whether it is evidence."""

    component: EconomicComponent
    total: float
    availability: FactAvailability
    metric_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component.value,
            "total": self.total,
            "availability": self.availability.value,
            "metric_id": self.metric_id,
        }


@dataclass(frozen=True)
class EconomicIntegrity:
    """Conservation and completeness posture for one quote-currency portfolio."""

    quote_currency: str
    population: int = 0
    definitive: int = 0
    indicative: int = 0
    unresolved: int = 0
    components: tuple[CostComponentEvidence, ...] = ()
    unmodelled_cost_residual: float = 0.0
    conservation_violations: tuple[str, ...] = ()
    version: str = ECONOMIC_INTEGRITY_VERSION
    trust: TrustEnvelope = field(
        default_factory=lambda: TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=Completeness.COMPLETE,
        )
    )
    details: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "quote_currency": self.quote_currency,
            "version": self.version,
            "population": self.population,
            "definitive": self.definitive,
            "indicative": self.indicative,
            "unresolved": self.unresolved,
            "components": [item.to_dict() for item in self.components],
            "unmodelled_cost_residual": self.unmodelled_cost_residual,
            "conservation_violations": list(self.conservation_violations),
            "metric_authority": dict(METRIC_AUTHORITY),
            "trust": self.trust.to_dict(),
            "details": list(self.details),
        }

    def component(self, component: EconomicComponent) -> CostComponentEvidence:
        for item in self.components:
            if item.component is component:
                return item
        raise KeyError(f"unknown economic component: {component}")


def _has_fill_evidence(rows: Sequence[ReconciledPaperTrade]) -> bool:
    for row in rows:
        if row.first_entry_fill_at is not None:
            return True
        if float(row.entry_quantity) > _MONEY_TOLERANCE:
            return True
    return False


def _component_availability(
    component: EconomicComponent,
    *,
    has_fills: bool,
    has_rows: bool,
) -> FactAvailability:
    """Availability for one component, derived from the fill contract.

    Cost components cannot exist without a fill. Realised values are ``KNOWN``
    only when at least one row is canonically verified, otherwise ``DERIVED``.
    """
    if component in SUPPORTED_COST_COMPONENTS or component in {
        EconomicComponent.EXECUTION_COSTS,
        EconomicComponent.RESERVED_CAPITAL,
    }:
        return FactAvailability.KNOWN if has_fills else FactAvailability.NOT_APPLICABLE
    if component in {
        EconomicComponent.NET_PNL,
        EconomicComponent.GROSS_PNL,
    }:
        return FactAvailability.DERIVED if has_rows else FactAvailability.NOT_APPLICABLE
    return FactAvailability.KNOWN if has_rows else FactAvailability.NOT_APPLICABLE


def _metric_for(component: EconomicComponent) -> str | None:
    if component is EconomicComponent.EXECUTION_COSTS:
        return METRIC_AUTHORITY.get("execution_costs")
    if component in {EconomicComponent.NET_PNL, EconomicComponent.GROSS_PNL}:
        return METRIC_AUTHORITY.get("net_pnl")
    return None


def build_economic_integrity(
    rows: Iterable[ReconciledPaperTrade],
) -> tuple[EconomicIntegrity, ...]:
    """Build one integrity summary per quote currency, in stable order.

    Currencies are never summed together, matching the canonical portfolio
    grouping: USD and USDT are distinct portfolios.
    """
    grouped: dict[str, list[ReconciledPaperTrade]] = {}
    for row in rows:
        if not isinstance(row, ReconciledPaperTrade):
            raise TypeError("rows must contain ReconciledPaperTrade")
        key = str(row.quote_currency or "UNKNOWN")
        grouped.setdefault(key, []).append(row)

    return tuple(
        _build_one(currency, grouped[currency]) for currency in sorted(grouped)
    )


def _build_one(
    quote_currency: str,
    rows: list[ReconciledPaperTrade],
) -> EconomicIntegrity:
    has_fills = _has_fill_evidence(rows)
    has_rows = bool(rows)

    totals: dict[EconomicComponent, float] = {
        component: sum(float(getattr(row, _COMPONENT_FIELDS[component])) for row in rows)
        for component in _COMPONENT_ORDER
    }

    definitive_flags = [row.net_pnl_definitive for row in rows]
    realised_availability = (
        FactAvailability.KNOWN
        if any(definitive_flags)
        else (FactAvailability.DERIVED if has_rows else FactAvailability.NOT_APPLICABLE)
    )

    components = tuple(
        CostComponentEvidence(
            component=component,
            total=totals[component],
            availability=(
                realised_availability
                if component
                in {EconomicComponent.NET_PNL, EconomicComponent.GROSS_PNL}
                else _component_availability(
                    component, has_fills=has_fills, has_rows=has_rows
                )
            ),
            metric_id=_metric_for(component),
        )
        for component in _COMPONENT_ORDER
    )

    violations: list[str] = []
    residual = 0.0
    definitive = 0
    unresolved = 0
    for row in rows:
        if not net_pnl_reconciles(
            gross_pnl=row.gross_pnl,
            execution_costs=row.execution_costs,
            net_pnl=row.net_pnl,
        ):
            violations.append(row.paper_trade_id)
        if has_fills:
            supported = supported_costs_sum(
                {
                    EconomicComponent.FEE_COST: row.fee_cost,
                    EconomicComponent.SPREAD_COST: row.spread_cost,
                    EconomicComponent.SLIPPAGE_COST: row.slippage_cost,
                    EconomicComponent.OTHER_SUPPORTED_COST: row.other_cost,
                }
            )
            residual += float(row.execution_costs) - supported
        if row.net_pnl_definitive:
            definitive += 1
        if not row.net_pnl_definitive and row.terminal_reconciliation_state:
            unresolved += 1

    total = len(rows)
    indicative = total - definitive

    details: list[str] = []
    if violations:
        details.append(
            f"{len(violations)} row(s) violate net = gross - execution_costs"
        )
    if not has_fills and has_rows:
        details.append(
            "no entry fill exists in this population, so cost components are "
            "NOT_APPLICABLE rather than a measured zero"
        )
    if abs(residual) > _MONEY_TOLERANCE and has_fills:
        details.append(
            "canonical recorded execution cost exceeds the four supported fill "
            "components by the reported unmodelled residual"
        )

    healthy = not violations
    return EconomicIntegrity(
        quote_currency=quote_currency,
        population=total,
        definitive=definitive,
        indicative=indicative,
        unresolved=unresolved,
        components=components,
        unmodelled_cost_residual=residual if has_fills else 0.0,
        conservation_violations=tuple(sorted(violations)),
        trust=TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=(
                Completeness.COMPLETE if healthy else Completeness.INCOMPLETE
            ),
            reasons=() if healthy else ("ECONOMIC_CONSERVATION_VIOLATION",),
        ),
        details=tuple(details),
    )


__all__ = [
    "ECONOMIC_INTEGRITY_VERSION",
    "CostComponentEvidence",
    "EconomicIntegrity",
    "build_economic_integrity",
]
