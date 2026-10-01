"""The frozen legacy comparator adapter and the F7 comparison record (R3 F7).

This module is an explicit evaluation helper, not a runtime selector. It reuses
the *live* legacy production code and constants instead of restating them:

* the Top-8 population rule reuses ``MIN_TECHNICAL_SCORE`` and ``MAX_CANDIDATES``
  from :mod:`app.scanner.candidates`;
* the profit-ranking weights, full-credit move and point tables are imported from
  :mod:`app.services.profit_ranking`;
* the portfolio-risk veto calls the live
  :func:`app.services.portfolio_risk.evaluate_portfolio_risk` with its own
  production defaults, so the observed gross-exposure, same-direction and
  position limits are never restated here;
* the observed single-position capital fraction reuses
  ``PRODUCTION_MAX_CAPITAL_FRACTION`` from :mod:`app.services.economic_quality_gate`.

Nothing here modifies a legacy weight, threshold, ordering, alert or paper route,
and nothing here mutates production state. The comparator is evaluated on the
*exact same* frozen candidate panel as F7: the result binds the panel fingerprint
that both sides share.

The comparison record reports F7, cash and the frozen legacy comparator side by
side. It deliberately declares no winner and applies no promotion threshold,
because the canonical architecture defines none.
"""

from __future__ import annotations

import inspect
import math
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.opip.contracts.portfolio import (
    PORTFOLIO_COMPARATOR_ID_PREFIX,
    PORTFOLIO_COMPARATOR_VERSION,
    PORTFOLIO_EVALUATION_VERSION,
    LegacyComparatorObservables,
    PortfolioCandidate,
    PortfolioCapitalState,
    PortfolioContractError,
    PortfolioDecision,
    PortfolioDirection,
    PortfolioEvaluationWindow,
    PortfolioPolicy,
    PortfolioStatus,
    _freeze_mapping,
    _mapping_to_dict,
    _require_durable_mapping,
    portfolio_panel_fingerprint,
    require_finite,
    require_non_negative_int,
    require_portfolio_enum,
    require_portfolio_text,
    require_positive,
)
from app.opip.contracts.serialization import stable_hash
from app.scanner.candidates import MAX_CANDIDATES, MIN_TECHNICAL_SCORE
from app.services.economic_quality_gate import PRODUCTION_MAX_CAPITAL_FRACTION
from app.services.portfolio_risk import evaluate_portfolio_risk
from app.services.profit_ranking import (
    BOOK_COVERAGE_POINTS,
    CROSS_PAIR_POINTS,
    ECONOMIC_FULL_CREDIT_MOVE_PCT,
    ECONOMIC_WEIGHT,
    EXECUTION_DRAG_FULL_PENALTY_PCT,
    EXECUTION_QUALITY_WEIGHT,
    MISSING_EXECUTION_DRAG_POINTS,
    RECENT_TRADE_POINTS,
    REFERENCE_POINTS,
    TARGET_QUALITY_WEIGHT,
    TECHNICAL_QUALITY_WEIGHT,
)

#: Identity prefix of a comparison record (distinct from a comparator result).
PORTFOLIO_COMPARISON_ID_PREFIX = "PCMPX"

#: The frozen legacy population rule, reused by identity (never restated).
LEGACY_MIN_TECHNICAL_SCORE = MIN_TECHNICAL_SCORE
LEGACY_MAX_CANDIDATES = MAX_CANDIDATES

#: The observed production single-position capital fraction, reused by identity.
LEGACY_PRODUCTION_CAPITAL_FRACTION = PRODUCTION_MAX_CAPITAL_FRACTION


def _legacy_risk_default(parameter: str) -> Any:
    """Read one live ``evaluate_portfolio_risk`` keyword default.

    The observed gross-exposure, same-direction and position limits are the live
    function's own defaults; reading them here reuses the production value instead
    of restating it, so a future production change is never silently shadowed.
    """
    parameters = inspect.signature(evaluate_portfolio_risk).parameters
    if parameter not in parameters:
        raise PortfolioContractError(
            f"the live portfolio-risk function no longer declares {parameter!r}"
        )
    default = parameters[parameter].default
    if default is inspect.Parameter.empty:
        raise PortfolioContractError(
            f"the live portfolio-risk function no longer defaults {parameter!r}"
        )
    return default


def observed_legacy_policy(
    *,
    max_symbol_exposure_fraction: float,
    max_common_shock_group_positions: int,
    max_portfolio_loss_fraction: float,
    attributable_operating_cost: float | None = None,
) -> PortfolioPolicy:
    """Build an F7 policy from the live observed production constants.

    The gross-exposure, position-count, same-direction and single-position capital
    values come from the existing production code. The three limits that have no
    legacy counterpart (per-symbol concentration, common-shock group count and the
    drawdown/loss limit) are required explicitly and are never invented here.
    """
    gross_exposure_pct = float(_legacy_risk_default("max_gross_exposure_pct"))
    return PortfolioPolicy(
        max_gross_exposure_fraction=gross_exposure_pct / 100.0,
        max_positions=int(_legacy_risk_default("max_positions")),
        max_same_direction=int(_legacy_risk_default("max_same_direction")),
        max_capital_fraction_per_candidate=float(LEGACY_PRODUCTION_CAPITAL_FRACTION),
        max_symbol_exposure_fraction=max_symbol_exposure_fraction,
        max_common_shock_group_positions=max_common_shock_group_positions,
        max_portfolio_loss_fraction=max_portfolio_loss_fraction,
        attributable_operating_cost=attributable_operating_cost,
    )


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """The legacy clamp: a non-finite value collapses to ``minimum``."""
    number = float(value)
    if not math.isfinite(number):
        return minimum
    return max(minimum, min(maximum, number))


def legacy_unrounded_total(observables: LegacyComparatorObservables) -> float:
    """Reproduce the live profit-ranking total score for one candidate.

    The component weights, the full-credit move and the point tables are the live
    production constants imported above; this only reproduces the arithmetic.
    """
    if not isinstance(observables, LegacyComparatorObservables):
        raise PortfolioContractError("observables must be LegacyComparatorObservables")
    economic = (
        _clamp(observables.target_2_move_pct / ECONOMIC_FULL_CREDIT_MOVE_PCT) * ECONOMIC_WEIGHT
    )
    target = _clamp(observables.attainability_score / 100.0) * TARGET_QUALITY_WEIGHT
    drag = observables.execution_drag_pct
    if drag is None:
        drag_points = MISSING_EXECUTION_DRAG_POINTS
    else:
        drag_points = _clamp(1.0 - drag / EXECUTION_DRAG_FULL_PENALTY_PCT) * 12.0
    coverage = BOOK_COVERAGE_POINTS.get(observables.book_coverage_status, 0.0)
    trade = RECENT_TRADE_POINTS.get(observables.recent_trade_status, 0.0)
    execution = _clamp(drag_points + coverage + trade, 0.0, EXECUTION_QUALITY_WEIGHT)
    technical = _clamp(observables.technical_score / 100.0) * TECHNICAL_QUALITY_WEIGHT
    reference = (
        0.0
        if observables.independent_reference_status is None
        else REFERENCE_POINTS.get(observables.independent_reference_status, 0.0)
    )
    cross_pair = CROSS_PAIR_POINTS.get(observables.cross_pair_confirmation_status, 0.0)
    evidence = reference + cross_pair
    return _clamp(
        economic + target + execution + technical + evidence, minimum=0.0, maximum=100.0
    )


def _legacy_ranking_key(candidate: PortfolioCandidate) -> tuple[Any, ...]:
    """The live profit-ranking ordering key, reproduced without added tie-breaks."""
    observables = candidate.legacy_observables
    unrounded = legacy_unrounded_total(observables)
    drag = observables.execution_drag_pct
    return (
        -unrounded,
        -float(observables.attainability_score),
        drag is None,
        drag if drag is not None else float("inf"),
        -float(observables.technical_score),
        candidate.symbol,
    )


@dataclass(frozen=True)
class LegacyActivePosition:
    """One explicit existing position supplied to the live risk veto."""

    symbol: str
    direction: PortfolioDirection
    capital: float
    margin_leverage: float = 1.0
    status: str = "active"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "symbol", require_portfolio_text(self.symbol, field_name="symbol")
        )
        object.__setattr__(
            self,
            "direction",
            require_portfolio_enum(PortfolioDirection, self.direction, field_name="direction"),
        )
        object.__setattr__(
            self, "capital", require_positive(self.capital, field_name="capital")
        )
        object.__setattr__(
            self,
            "margin_leverage",
            require_positive(self.margin_leverage, field_name="margin_leverage"),
        )
        object.__setattr__(
            self, "status", require_portfolio_text(self.status, field_name="status")
        )


_RESULT_DURABLE_KEYS: tuple[str, ...] = (
    "result_id",
    "comparator_version",
    "panel_fingerprint",
    "population_candidate_ids",
    "ranked_candidate_ids",
    "risk_admitted_candidate_ids",
    "risk_vetoed_candidate_ids",
    "risk_veto_reasons",
)


@dataclass(frozen=True)
class FrozenLegacyComparatorResult:
    """The frozen legacy comparator's result for one panel.

    ``population_candidate_ids`` is the legacy Top-8 population, ``ranked_candidate_ids``
    is the profit-ranking order, and ``risk_admitted_candidate_ids`` /
    ``risk_vetoed_candidate_ids`` are the live risk-veto outcome.
    """

    panel_fingerprint: str
    population_candidate_ids: tuple[str, ...]
    ranked_candidate_ids: tuple[str, ...]
    risk_admitted_candidate_ids: tuple[str, ...]
    risk_vetoed_candidate_ids: tuple[str, ...]
    risk_veto_reasons: Mapping[str, str]
    comparator_version: str = PORTFOLIO_COMPARATOR_VERSION
    result_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "panel_fingerprint",
            require_portfolio_text(self.panel_fingerprint, field_name="panel_fingerprint"),
        )
        version = require_portfolio_text(
            self.comparator_version, field_name="comparator_version"
        )
        if version != PORTFOLIO_COMPARATOR_VERSION:
            raise PortfolioContractError(
                "comparator_version is not the ratified "
                f"{PORTFOLIO_COMPARATOR_VERSION}"
            )
        object.__setattr__(self, "comparator_version", version)
        for name in (
            "population_candidate_ids",
            "ranked_candidate_ids",
            "risk_admitted_candidate_ids",
            "risk_vetoed_candidate_ids",
        ):
            tokens = tuple(
                require_portfolio_text(item, field_name=name) for item in getattr(self, name)
            )
            object.__setattr__(self, name, tokens)
        reasons = _freeze_mapping(
            self.risk_veto_reasons, field_name="risk_veto_reasons", value_kind="text"
        )
        object.__setattr__(self, "risk_veto_reasons", reasons)
        expected = stable_hash(PORTFOLIO_COMPARATOR_ID_PREFIX, self._identity_payload())
        if self.result_id == "":
            object.__setattr__(self, "result_id", expected)
        elif self.result_id != expected:
            raise PortfolioContractError(
                "result_id does not match its content; build comparator results with the "
                "comparator"
            )

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "comparator_version": self.comparator_version,
            "panel_fingerprint": self.panel_fingerprint,
            "population_candidate_ids": list(self.population_candidate_ids),
            "ranked_candidate_ids": list(self.ranked_candidate_ids),
            "risk_admitted_candidate_ids": list(self.risk_admitted_candidate_ids),
            "risk_vetoed_candidate_ids": list(self.risk_vetoed_candidate_ids),
            "risk_veto_reasons": _mapping_to_dict(self.risk_veto_reasons),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["result_id"] = self.result_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "FrozenLegacyComparatorResult":
        body = _require_durable_mapping(
            raw, field_name="comparator_result", expected_keys=_RESULT_DURABLE_KEYS
        )
        return cls(
            panel_fingerprint=body["panel_fingerprint"],
            population_candidate_ids=tuple(body["population_candidate_ids"]),
            ranked_candidate_ids=tuple(body["ranked_candidate_ids"]),
            risk_admitted_candidate_ids=tuple(body["risk_admitted_candidate_ids"]),
            risk_vetoed_candidate_ids=tuple(body["risk_vetoed_candidate_ids"]),
            risk_veto_reasons=body["risk_veto_reasons"],
            comparator_version=body["comparator_version"],
            result_id=body["result_id"],
        )


def build_frozen_legacy_comparison(
    candidates: Sequence[PortfolioCandidate],
    *,
    capital_state: PortfolioCapitalState,
    window: PortfolioEvaluationWindow,
    active_positions: Sequence[LegacyActivePosition] = (),
) -> FrozenLegacyComparatorResult:
    """Run the frozen legacy pipeline over the shared panel.

    The pipeline is exactly the live one: the Top-8 population, the profit-ranking
    order and the live risk veto. No production state is mutated.
    """
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, (list, tuple)):
        raise PortfolioContractError("candidates must be a list or tuple")
    resolved = tuple(candidates)
    if not isinstance(capital_state, PortfolioCapitalState):
        raise PortfolioContractError("capital_state must be a PortfolioCapitalState")
    if not isinstance(window, PortfolioEvaluationWindow):
        raise PortfolioContractError("window must be a PortfolioEvaluationWindow")
    positions = tuple(active_positions)
    for position in positions:
        if not isinstance(position, LegacyActivePosition):
            raise PortfolioContractError("active_positions must be LegacyActivePosition values")

    panel_fingerprint = portfolio_panel_fingerprint(window, resolved)

    # The F7 panel is a set: canonicalize the input order once, then reproduce the
    # live stable sorts exactly, without inventing a tie-break the live code lacks.
    ordered = tuple(sorted(resolved, key=lambda candidate: candidate.candidate_id))
    qualified = [
        candidate
        for candidate in ordered
        if candidate.legacy_observables.technical_score >= LEGACY_MIN_TECHNICAL_SCORE
    ]
    qualified.sort(key=lambda candidate: -candidate.legacy_observables.technical_score)
    population = tuple(qualified[:LEGACY_MAX_CANDIDATES])
    ranked = tuple(sorted(population, key=_legacy_ranking_key))

    proposed_capital = LEGACY_PRODUCTION_CAPITAL_FRACTION * capital_state.available_capital
    if not math.isfinite(proposed_capital) or proposed_capital <= 0.0:
        raise PortfolioContractError("legacy proposed capital must be positive and finite")

    admitted: list[str] = []
    vetoed: list[str] = []
    reasons: dict[str, str] = {}
    live_positions: list[LegacyActivePosition] = list(positions)
    for candidate in ranked:
        decision = evaluate_portfolio_risk(
            active_trades=[
                types.SimpleNamespace(
                    symbol=position.symbol,
                    direction=position.direction.value,
                    capital=position.capital,
                    margin_leverage=position.margin_leverage,
                    status=position.status,
                )
                for position in live_positions
            ],
            proposed_symbol=candidate.symbol,
            proposed_direction=candidate.direction.value,
            proposed_capital=proposed_capital,
            account_capital=capital_state.available_capital,
        )
        if decision.allowed:
            admitted.append(candidate.candidate_id)
            live_positions.append(
                LegacyActivePosition(
                    symbol=candidate.symbol,
                    direction=candidate.direction,
                    capital=proposed_capital,
                )
            )
        else:
            vetoed.append(candidate.candidate_id)
            reasons[candidate.candidate_id] = require_portfolio_text(
                decision.reason, field_name="risk_veto_reason"
            )

    return FrozenLegacyComparatorResult(
        panel_fingerprint=panel_fingerprint,
        population_candidate_ids=tuple(
            candidate.candidate_id for candidate in population
        ),
        ranked_candidate_ids=tuple(candidate.candidate_id for candidate in ranked),
        risk_admitted_candidate_ids=tuple(admitted),
        risk_vetoed_candidate_ids=tuple(vetoed),
        risk_veto_reasons=reasons,
    )


_COMPARISON_DURABLE_KEYS: tuple[str, ...] = (
    "comparison_id",
    "evaluation_version",
    "panel_fingerprint",
    "f7_decision_id",
    "f7_status",
    "f7_expected_net_dollars",
    "legacy_result_id",
    "legacy_selected_count",
    "cash_expected_net_dollars",
    "declared_winner",
)


@dataclass(frozen=True)
class PortfolioComparison:
    """A side-by-side comparison of F7, cash and the frozen legacy comparator.

    The record declares ``declared_winner`` as ``None``: the canonical architecture
    defines no promotion threshold for the economic selector, so this record never
    pretends F7 already won.
    """

    panel_fingerprint: str
    f7_decision_id: str
    f7_status: PortfolioStatus
    legacy_result_id: str
    legacy_selected_count: int
    f7_expected_net_dollars: float | None = None
    cash_expected_net_dollars: float = 0.0
    evaluation_version: str = PORTFOLIO_EVALUATION_VERSION
    declared_winner: None = None
    comparison_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "panel_fingerprint",
            require_portfolio_text(self.panel_fingerprint, field_name="panel_fingerprint"),
        )
        object.__setattr__(
            self,
            "f7_decision_id",
            require_portfolio_text(self.f7_decision_id, field_name="f7_decision_id"),
        )
        object.__setattr__(
            self,
            "f7_status",
            require_portfolio_enum(PortfolioStatus, self.f7_status, field_name="f7_status"),
        )
        object.__setattr__(
            self,
            "legacy_result_id",
            require_portfolio_text(self.legacy_result_id, field_name="legacy_result_id"),
        )
        object.__setattr__(
            self,
            "legacy_selected_count",
            require_non_negative_int(
                self.legacy_selected_count, field_name="legacy_selected_count"
            ),
        )
        if self.f7_expected_net_dollars is not None:
            object.__setattr__(
                self,
                "f7_expected_net_dollars",
                require_finite(
                    self.f7_expected_net_dollars, field_name="f7_expected_net_dollars"
                ),
            )
        object.__setattr__(
            self,
            "cash_expected_net_dollars",
            require_finite(
                self.cash_expected_net_dollars, field_name="cash_expected_net_dollars"
            ),
        )
        version = require_portfolio_text(
            self.evaluation_version, field_name="evaluation_version"
        )
        if version != PORTFOLIO_EVALUATION_VERSION:
            raise PortfolioContractError(
                f"evaluation_version is not the ratified {PORTFOLIO_EVALUATION_VERSION}"
            )
        object.__setattr__(self, "evaluation_version", version)
        if self.declared_winner is not None:
            raise PortfolioContractError(
                "the economic selector has no promotion threshold; no winner may be declared"
            )
        expected = stable_hash(PORTFOLIO_COMPARISON_ID_PREFIX, self._identity_payload())
        if self.comparison_id == "":
            object.__setattr__(self, "comparison_id", expected)
        elif self.comparison_id != expected:
            raise PortfolioContractError("comparison_id does not match its content")

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "evaluation_version": self.evaluation_version,
            "panel_fingerprint": self.panel_fingerprint,
            "f7_decision_id": self.f7_decision_id,
            "f7_status": self.f7_status.value,
            "f7_expected_net_dollars": self.f7_expected_net_dollars,
            "legacy_result_id": self.legacy_result_id,
            "legacy_selected_count": self.legacy_selected_count,
            "cash_expected_net_dollars": self.cash_expected_net_dollars,
            "declared_winner": None,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["comparison_id"] = self.comparison_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioComparison":
        body = _require_durable_mapping(
            raw, field_name="comparison", expected_keys=_COMPARISON_DURABLE_KEYS
        )
        return cls(
            panel_fingerprint=body["panel_fingerprint"],
            f7_decision_id=body["f7_decision_id"],
            f7_status=body["f7_status"],
            f7_expected_net_dollars=body["f7_expected_net_dollars"],
            legacy_result_id=body["legacy_result_id"],
            legacy_selected_count=body["legacy_selected_count"],
            cash_expected_net_dollars=body["cash_expected_net_dollars"],
            evaluation_version=body["evaluation_version"],
            declared_winner=body["declared_winner"],
            comparison_id=body["comparison_id"],
        )


def build_portfolio_comparison(
    decision: PortfolioDecision,
    legacy: FrozenLegacyComparatorResult,
    *,
    panel_fingerprint: str,
) -> PortfolioComparison:
    """Compare F7, cash and the frozen legacy comparator on the SAME panel."""
    if not isinstance(decision, PortfolioDecision):
        raise PortfolioContractError("decision must be a PortfolioDecision")
    if not isinstance(legacy, FrozenLegacyComparatorResult):
        raise PortfolioContractError("legacy must be a FrozenLegacyComparatorResult")
    shared = require_portfolio_text(panel_fingerprint, field_name="panel_fingerprint")
    if legacy.panel_fingerprint != shared:
        raise PortfolioContractError(
            "the legacy comparator result was computed on a different panel"
        )
    if decision.panel_fingerprint is None:
        raise PortfolioContractError(
            "the F7 decision has no panel evaluation and cannot be compared"
        )
    if decision.panel_fingerprint != shared:
        raise PortfolioContractError(
            "the F7 decision was computed on a different panel"
        )
    return PortfolioComparison(
        panel_fingerprint=shared,
        f7_decision_id=decision.decision_id,
        f7_status=decision.status,
        f7_expected_net_dollars=decision.expected_net_dollars,
        legacy_result_id=legacy.result_id,
        legacy_selected_count=len(legacy.risk_admitted_candidate_ids),
        cash_expected_net_dollars=0.0,
    )


__all__ = [
    "LEGACY_MAX_CANDIDATES",
    "LEGACY_MIN_TECHNICAL_SCORE",
    "LEGACY_PRODUCTION_CAPITAL_FRACTION",
    "PORTFOLIO_COMPARISON_ID_PREFIX",
    "FrozenLegacyComparatorResult",
    "LegacyActivePosition",
    "PortfolioComparison",
    "build_frozen_legacy_comparison",
    "build_portfolio_comparison",
    "legacy_unrounded_total",
    "observed_legacy_policy",
]
