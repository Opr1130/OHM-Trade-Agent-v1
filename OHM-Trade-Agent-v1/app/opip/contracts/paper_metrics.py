"""Versioned semantic registry for O'Pip Paper v2 measurement.

The dashboard, analytical read model, and later learning evaluation must use
these definitions rather than reimplementing similarly named metrics.

This module defines semantics only. It performs no queries and has no runtime
trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


PAPER_METRIC_REGISTRY_VERSION = "paper-metrics-v1"


class MetricUnit(str, Enum):
    QUOTE_CURRENCY = "QUOTE_CURRENCY"
    PERCENT = "PERCENT"
    BASIS_POINTS = "BASIS_POINTS"
    SECONDS = "SECONDS"
    RATIO = "RATIO"
    COUNT = "COUNT"
    PRICE = "PRICE"


class UncertaintyRequirement(str, Enum):
    NONE = "NONE"
    SHOW_SUPPORT = "SHOW_SUPPORT"
    SHOW_INTERVAL = "SHOW_INTERVAL"


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    definition_version: str
    description: str
    formula: str
    eligible_population: str
    exclusions: tuple[str, ...]
    authoritative_source: str
    unit: MetricUnit
    uncertainty_requirement: UncertaintyRequirement
    window: str = "TRADE_OR_EXPLICIT_QUERY_WINDOW"

    def __post_init__(self) -> None:
        for field_name in (
            "metric_id",
            "definition_version",
            "description",
            "formula",
            "eligible_population",
            "authoritative_source",
            "window",
        ):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)

        if not isinstance(self.exclusions, tuple):
            object.__setattr__(self, "exclusions", tuple(self.exclusions))

        try:
            unit = self.unit if isinstance(self.unit, MetricUnit) else MetricUnit(str(self.unit))
        except ValueError as exc:
            raise ValueError("unsupported metric unit") from exc
        object.__setattr__(self, "unit", unit)

        try:
            requirement = (
                self.uncertainty_requirement
                if isinstance(self.uncertainty_requirement, UncertaintyRequirement)
                else UncertaintyRequirement(str(self.uncertainty_requirement))
            )
        except ValueError as exc:
            raise ValueError("unsupported uncertainty requirement") from exc
        object.__setattr__(self, "uncertainty_requirement", requirement)


def _metric(
    metric_id: str,
    *,
    description: str,
    formula: str,
    eligible_population: str,
    exclusions: tuple[str, ...],
    authoritative_source: str,
    unit: MetricUnit,
    uncertainty: UncertaintyRequirement = UncertaintyRequirement.NONE,
    window: str = "TRADE_OR_EXPLICIT_QUERY_WINDOW",
) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        definition_version=PAPER_METRIC_REGISTRY_VERSION,
        description=description,
        formula=formula,
        eligible_population=eligible_population,
        exclusions=exclusions,
        authoritative_source=authoritative_source,
        unit=unit,
        uncertainty_requirement=uncertainty,
        window=window,
    )


_PAPER_METRICS = {
    "paper.realized_net_pnl": _metric(
        "paper.realized_net_pnl",
        description="Authoritative realized paper profit after all recorded execution costs.",
        formula="SUM(realized_gross_pnl - recorded_execution_costs)",
        eligible_population="ACTUAL_REALIZED and FINAL_VERIFIED outcomes, grouped by quote_currency",
        exclusions=("COUNTERFACTUAL_POLICY", "HINDSIGHT_DIAGNOSTIC", "UNRESOLVED_EVIDENCE"),
        authoritative_source="canonical fills + canonical execution cost components + final reconciliation",
        unit=MetricUnit.QUOTE_CURRENCY,
    ),
    "paper.net_expectancy": _metric(
        "paper.net_expectancy",
        description="Mean authoritative realized net P/L per final verified actual paper trade.",
        formula="SUM(realized_net_pnl) / COUNT(final_verified_actual_trades)",
        eligible_population="ACTUAL_REALIZED and FINAL_VERIFIED outcomes, same quote_currency",
        exclusions=("COUNTERFACTUAL_POLICY", "HINDSIGHT_DIAGNOSTIC", "UNRESOLVED_EVIDENCE"),
        authoritative_source="canonical terminal economic projection derived from fills",
        unit=MetricUnit.QUOTE_CURRENCY,
        uncertainty=UncertaintyRequirement.SHOW_INTERVAL,
    ),
    "paper.execution_cost": _metric(
        "paper.execution_cost",
        description="Recorded fees plus modeled spread/slippage components actually charged by the declared simulator version.",
        formula="SUM(fee_cost + spread_cost + slippage_cost + other_supported_execution_cost)",
        eligible_population="ACTUAL_REALIZED executions with complete cost evidence",
        exclusions=("NOT_MODELED cost components", "UNRESOLVED_EVIDENCE"),
        authoritative_source="canonical execution attempts/fills and execution-model version",
        unit=MetricUnit.QUOTE_CURRENCY,
    ),
    "paper.fill_ratio": _metric(
        "paper.fill_ratio",
        description="Fraction of requested executable quantity confirmed by canonical fills.",
        formula="confirmed_filled_quantity / requested_quantity",
        eligible_population="paper order intents with requested_quantity > 0",
        exclusions=("intent missing requested quantity",),
        authoritative_source="canonical order intent + canonical fills",
        unit=MetricUnit.RATIO,
    ),
    "paper.entry_latency": _metric(
        "paper.entry_latency",
        description="Decision-to-entry execution latency using only defensible temporal evidence.",
        formula="first_entry_fill_time - decision_time",
        eligible_population="entries whose temporal evidence supports a point or bounded latency",
        exclusions=("UNKNOWN temporal evidence",),
        authoritative_source="decision context + canonical entry fill temporal evidence",
        unit=MetricUnit.SECONDS,
    ),
    "paper.exit_latency": _metric(
        "paper.exit_latency",
        description="Exit-trigger/decision-to-exit execution latency using only defensible temporal evidence.",
        formula="first_exit_fill_time - authoritative_exit_trigger_or_decision_time",
        eligible_population="exits whose trigger/decision and fill temporal evidence are comparable",
        exclusions=("UNKNOWN temporal evidence",),
        authoritative_source="canonical protection/exit intent + canonical exit fill temporal evidence",
        unit=MetricUnit.SECONDS,
    ),
    "paper.entry_price_drift_bps": _metric(
        "paper.entry_price_drift_bps",
        description="Direction-aware execution drift from decision reference price to entry VWAP.",
        formula="direction_signed(entry_vwap - decision_reference_price) / decision_reference_price * 10000",
        eligible_population="actual entries with a decision reference price and canonical entry VWAP",
        exclusions=("missing decision reference price", "UNRESOLVED_EVIDENCE"),
        authoritative_source="decision context market evidence + canonical fills",
        unit=MetricUnit.BASIS_POINTS,
    ),
    "paper.exit_price_drift_bps": _metric(
        "paper.exit_price_drift_bps",
        description="Direction-aware execution drift from exit decision/trigger reference to exit VWAP.",
        formula="direction_signed(exit_reference_price - exit_vwap) / exit_reference_price * 10000",
        eligible_population="actual exits with an authoritative exit reference and canonical exit VWAP",
        exclusions=("missing exit reference price", "UNRESOLVED_EVIDENCE"),
        authoritative_source="canonical protection/exit evidence + canonical fills",
        unit=MetricUnit.BASIS_POINTS,
    ),
    "paper.in_position_mfe_pct": _metric(
        "paper.in_position_mfe_pct",
        description="Maximum favorable excursion on the retained market path during the actual modeled holding interval; diagnostic, never achievable profit.",
        formula="direction_signed(best_path_price - entry_vwap) / entry_vwap * 100",
        eligible_population="actual positions with complete retained path coverage over holding interval",
        exclusions=("path coverage gap", "HINDSIGHT_DIAGNOSTIC outside actual holding interval"),
        authoritative_source="retained market path bound to paper trade holding interval",
        unit=MetricUnit.PERCENT,
    ),
    "paper.in_position_mae_pct": _metric(
        "paper.in_position_mae_pct",
        description="Maximum adverse excursion on the retained market path during the actual modeled holding interval.",
        formula="direction_signed(entry_vwap - worst_path_price) / entry_vwap * 100",
        eligible_population="actual positions with complete retained path coverage over holding interval",
        exclusions=("path coverage gap",),
        authoritative_source="retained market path bound to paper trade holding interval",
        unit=MetricUnit.PERCENT,
    ),
    "paper.capture_efficiency": _metric(
        "paper.capture_efficiency",
        description="Actual realized net result relative to the positive declared-policy counterfactual result for the same opportunity; never relative to raw MFE.",
        formula="actual_realized_net_pnl / declared_policy_counterfactual_net_pnl",
        eligible_population="paired ACTUAL_REALIZED + COUNTERFACTUAL_POLICY rows where counterfactual net pnl > 0 and quote_currency matches",
        exclusions=("HINDSIGHT_DIAGNOSTIC", "counterfactual_net_pnl <= 0", "unpaired population", "mixed quote_currency"),
        authoritative_source="canonical actual outcome + versioned declared-policy counterfactual replay",
        unit=MetricUnit.RATIO,
        uncertainty=UncertaintyRequirement.SHOW_SUPPORT,
    ),
    "paper.realized_equity_drawdown_pct": _metric(
        "paper.realized_equity_drawdown_pct",
        description="Peak-to-trough drawdown of realized-equity series for one quote-currency portfolio.",
        formula="MAX((running_realized_equity_peak - running_realized_equity) / running_realized_equity_peak * 100)",
        eligible_population="chronologically committed FINAL_VERIFIED actual outcomes in one quote_currency portfolio",
        exclusions=("COUNTERFACTUAL_POLICY", "HINDSIGHT_DIAGNOSTIC", "mixed quote_currency"),
        authoritative_source="canonical final outcomes ordered by authoritative close/commit sequence",
        unit=MetricUnit.PERCENT,
    ),
    "paper.unresolved_count": _metric(
        "paper.unresolved_count",
        description="Count of paper lifecycles whose economics cannot be proven complete.",
        formula="COUNT(terminal_reconciliation_state = UNRESOLVED_EVIDENCE)",
        eligible_population="all paper lifecycles in query window",
        exclusions=(),
        authoritative_source="canonical reconciliation/evidence-completeness projection",
        unit=MetricUnit.COUNT,
    ),
}


for key, metric in _PAPER_METRICS.items():
    if key != metric.metric_id:
        raise ValueError(
            f"metric key {key!r} does not match metric_id {metric.metric_id!r}"
        )

PAPER_METRICS: Mapping[str, MetricDefinition] = MappingProxyType(_PAPER_METRICS)


def metric_definition(metric_id: str) -> MetricDefinition:
    key = str(metric_id or "").strip()
    try:
        return PAPER_METRICS[key]
    except KeyError as exc:
        raise KeyError(f"unknown paper metric: {key}") from exc


__all__ = [
    "MetricDefinition",
    "MetricUnit",
    "PAPER_METRIC_REGISTRY_VERSION",
    "PAPER_METRICS",
    "UncertaintyRequirement",
    "metric_definition",
]
