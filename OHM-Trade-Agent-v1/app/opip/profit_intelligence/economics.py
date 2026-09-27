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
* separates the **realized** population (canonically FINAL_VERIFIED) from the
  **indicative** population, so an unverified number never reads as a settled
  result and is never summed into one;
* states per-component availability, so a missing or inapplicable cost is never
  rendered as a measured zero;
* never mixes quote currencies, because USD and USDT are distinct portfolios.

Why realized and indicative are reported separately
---------------------------------------------------
The registered metric ``paper.realized_net_pnl`` declares its eligible
population as *ACTUAL_REALIZED and FINAL_VERIFIED outcomes* and explicitly
excludes ``UNRESOLVED_EVIDENCE``. A total that mixed unverified rows into a
component carrying that metric id would be a competing definition of the
registered metric, so realized components are computed **only** over rows whose
economics the canonical writer verified. Unverified rows are reported in their
own structurally identical component list, labelled ``DERIVED`` and carrying no
metric id. The same separation is what
:mod:`app.opip.cockpit.portfolio` already applies to the currency portfolio.

Cost-component availability
---------------------------
The canonical fill contract requires ``fee_cost``, ``spread_cost``,
``slippage_cost`` and ``other_supported_cost`` on **every** fill. So a summed
``0.0`` over a population that has fills is a genuine measured zero, while the
same ``0.0`` over a population with no fills is ``NOT_APPLICABLE``. The two are
reported differently rather than both shown as ``0``.

Residual (unmodelled) cost
--------------------------
``execution_costs`` is the writer's recorded aggregate and may exceed the sum of
the four supported fill components. That residual is surfaced per population and
is never silently folded into a component or dropped. A population that records
costs but no fills is a contradiction, and it is reported as one rather than
being zeroed into silence.

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
ECONOMIC_INTEGRITY_VERSION = "profit-intelligence-economics-v2"

#: Same tolerance the canonical writer uses for money equality
#: (``paper_execution_events``: ``abs(net - (gross - costs)) <= 1e-6``). Using a
#: stricter tolerance would report a canonically-conserving row as a violation,
#: which is a false defect claim rather than a safety margin.
_MONEY_TOLERANCE = 1e-6

#: Quantity tolerance for "this population recorded a fill".
_QUANTITY_TOLERANCE = 1e-9

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

#: Components whose value is only meaningful once economics are verified.
_REALIZED_ONLY_COMPONENTS: frozenset[EconomicComponent] = frozenset(
    {
        EconomicComponent.GROSS_PNL,
        EconomicComponent.NET_PNL,
    }
)


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
    """Conservation and completeness posture for one quote-currency portfolio.

    ``realized_components`` covers only canonically verified rows and is the only
    list allowed to carry a registered metric id. ``indicative_components``
    covers the remaining rows and is structurally identical but labelled
    ``DERIVED``. Consumers must not add the two lists together: that would
    recreate the mixed population this split exists to prevent.
    """

    quote_currency: str
    population: int = 0
    definitive: int = 0
    indicative: int = 0
    unresolved: int = 0
    realized_components: tuple[CostComponentEvidence, ...] = ()
    indicative_components: tuple[CostComponentEvidence, ...] = ()
    unmodelled_cost_residual: float = 0.0
    indicative_cost_residual: float = 0.0
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
            "realized_components": [item.to_dict() for item in self.realized_components],
            "indicative_components": [
                item.to_dict() for item in self.indicative_components
            ],
            "unmodelled_cost_residual": self.unmodelled_cost_residual,
            "indicative_cost_residual": self.indicative_cost_residual,
            "conservation_violations": list(self.conservation_violations),
            "metric_authority": dict(METRIC_AUTHORITY),
            "trust": self.trust.to_dict(),
            "details": list(self.details),
        }

    def component(
        self, component: EconomicComponent, *, realized: bool = True
    ) -> CostComponentEvidence:
        """Look a component up in one population's component list."""
        source = self.realized_components if realized else self.indicative_components
        for item in source:
            if item.component is component:
                return item
        raise KeyError(
            f"unknown economic component {component} in "
            f"{'realized' if realized else 'indicative'} population"
        )


def _has_fill_evidence(rows: Sequence[ReconciledPaperTrade]) -> bool:
    for row in rows:
        if row.first_entry_fill_at is not None:
            return True
        if float(row.entry_quantity) > _QUANTITY_TOLERANCE:
            return True
    return False


def _component_availability(
    component: EconomicComponent,
    *,
    has_fills: bool,
    has_rows: bool,
    realized: bool,
) -> FactAvailability:
    """Availability for one component inside one population.

    Cost components cannot exist without a fill. Realized values are ``KNOWN``
    (the canonical writer verified them); the same value in the indicative
    population is ``DERIVED`` because it is a deterministic function of
    unverified evidence, not a settled fact.
    """
    if component in SUPPORTED_COST_COMPONENTS or component in {
        EconomicComponent.EXECUTION_COSTS,
        EconomicComponent.RESERVED_CAPITAL,
    }:
        if not has_fills:
            return FactAvailability.NOT_APPLICABLE
        return FactAvailability.KNOWN if realized else FactAvailability.DERIVED
    if component in _REALIZED_ONLY_COMPONENTS:
        if not has_rows:
            return FactAvailability.NOT_APPLICABLE
        return FactAvailability.KNOWN if realized else FactAvailability.DERIVED
    return FactAvailability.KNOWN if has_rows else FactAvailability.NOT_APPLICABLE


def _metric_for(component: EconomicComponent, *, realized: bool) -> str | None:
    """The registered metric owning a component, only in the realized list.

    Gross profit has no registered metric of its own, so it deliberately maps to
    ``None`` rather than borrowing the net metric's id.
    """
    if not realized:
        return None
    if component is EconomicComponent.EXECUTION_COSTS:
        return METRIC_AUTHORITY.get("execution_costs")
    if component is EconomicComponent.NET_PNL:
        return METRIC_AUTHORITY.get("net_pnl")
    return None


def _components_for(
    rows: Sequence[ReconciledPaperTrade],
    *,
    realized: bool,
) -> tuple[CostComponentEvidence, ...]:
    has_rows = bool(rows)
    has_fills = _has_fill_evidence(rows)
    return tuple(
        CostComponentEvidence(
            component=component,
            total=sum(float(getattr(row, _COMPONENT_FIELDS[component])) for row in rows),
            availability=_component_availability(
                component,
                has_fills=has_fills,
                has_rows=has_rows,
                realized=realized,
            ),
            metric_id=_metric_for(component, realized=realized),
        )
        for component in _COMPONENT_ORDER
    )


def _supported_costs_of(row: ReconciledPaperTrade) -> float:
    return supported_costs_sum(
        {
            EconomicComponent.FEE_COST: row.fee_cost,
            EconomicComponent.SPREAD_COST: row.spread_cost,
            EconomicComponent.SLIPPAGE_COST: row.slippage_cost,
            EconomicComponent.OTHER_SUPPORTED_COST: row.other_cost,
        }
    )


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


def _conservation_violations(
    rows: Sequence[ReconciledPaperTrade],
) -> tuple[str, ...]:
    """Ids of rows that do not conserve net = gross - execution costs."""
    return tuple(
        sorted(
            row.paper_trade_id
            for row in rows
            if not net_pnl_reconciles(
                gross_pnl=row.gross_pnl,
                execution_costs=row.execution_costs,
                net_pnl=row.net_pnl,
            )
        )
    )


def _residual(rows: Sequence[ReconciledPaperTrade]) -> float:
    """Recorded aggregate cost minus the four supported fill components."""
    return sum(
        float(row.execution_costs) - _supported_costs_of(row) for row in rows
    )


def _count_unresolved(rows: Sequence[ReconciledPaperTrade]) -> int:
    """Rows that are not verified but have reached a terminal reconciliation."""
    return sum(
        1
        for row in rows
        if not row.net_pnl_definitive and row.terminal_reconciliation_state
    )


def _completeness(
    violations: tuple[str, ...],
    indicative: int,
) -> tuple[Completeness, tuple[str, ...]]:
    """Completeness and reasons, derived from the evidence rather than violations alone.

    An entirely unverified population is never reported complete or healthy:
    completeness requires both conservation and verified economics.
    """
    reasons: list[str] = []
    if violations:
        reasons.append("ECONOMIC_CONSERVATION_VIOLATION")
    if indicative:
        reasons.append("ECONOMICS_NOT_FINAL_VERIFIED")
    complete = not violations and indicative == 0
    return (
        Completeness.COMPLETE if complete else Completeness.INCOMPLETE,
        tuple(reasons),
    )


def _population_details(
    label: str,
    population_rows: Sequence[ReconciledPaperTrade],
    residual: float,
) -> list[str]:
    """Honest notes for one population: what cannot exist, and any residual."""
    if not population_rows:
        return []
    notes: list[str] = []
    if not _has_fill_evidence(population_rows):
        notes.append(
            f"the {label} population records no entry fill, so cost components "
            "are NOT_APPLICABLE rather than a measured zero"
        )
        recorded = sum(float(row.execution_costs) for row in population_rows)
        if abs(recorded) > _MONEY_TOLERANCE:
            # A cost with no underlying fill is a contradiction, not a zero.
            notes.append(
                f"the {label} population records execution costs with no fill "
                "evidence; the residual is reported rather than dropped"
            )
    if abs(residual) > _MONEY_TOLERANCE:
        notes.append(
            f"canonical recorded execution cost exceeds the four supported fill "
            f"components in the {label} population by the reported unmodelled "
            "residual"
        )
    return notes


def _integrity_details(
    *,
    violations: tuple[str, ...],
    indicative: int,
    realized_rows: Sequence[ReconciledPaperTrade],
    indicative_rows: Sequence[ReconciledPaperTrade],
    residual_realized: float,
    residual_indicative: float,
) -> tuple[str, ...]:
    details: list[str] = []
    if violations:
        details.append(
            f"{len(violations)} row(s) violate net = gross - execution_costs"
        )
    if indicative:
        details.append(
            f"{indicative} row(s) are not canonically FINAL_VERIFIED; their "
            "economics are indicative (DERIVED) and are reported separately from "
            "the realized population"
        )
    details.extend(_population_details("realized", realized_rows, residual_realized))
    details.extend(
        _population_details("indicative", indicative_rows, residual_indicative)
    )
    return tuple(details)


def _build_one(
    quote_currency: str,
    rows: list[ReconciledPaperTrade],
) -> EconomicIntegrity:
    realized_rows = [row for row in rows if row.net_pnl_definitive]
    indicative_rows = [row for row in rows if not row.net_pnl_definitive]

    violations = _conservation_violations(rows)
    residual_realized = _residual(realized_rows)
    residual_indicative = _residual(indicative_rows)
    definitive = len(realized_rows)
    indicative = len(indicative_rows)
    completeness, reasons = _completeness(violations, indicative)

    return EconomicIntegrity(
        quote_currency=quote_currency,
        population=len(rows),
        definitive=definitive,
        indicative=indicative,
        unresolved=_count_unresolved(rows),
        realized_components=_components_for(realized_rows, realized=True),
        indicative_components=_components_for(indicative_rows, realized=False),
        unmodelled_cost_residual=(residual_realized if realized_rows else 0.0),
        indicative_cost_residual=(
            residual_indicative if indicative_rows else 0.0
        ),
        conservation_violations=violations,
        trust=TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=completeness,
            reasons=reasons,
        ),
        details=_integrity_details(
            violations=violations,
            indicative=indicative,
            realized_rows=realized_rows,
            indicative_rows=indicative_rows,
            residual_realized=residual_realized,
            residual_indicative=residual_indicative,
        ),
    )


__all__ = [
    "ECONOMIC_INTEGRITY_VERSION",
    "CostComponentEvidence",
    "EconomicIntegrity",
    "build_economic_integrity",
]
