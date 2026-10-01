"""The pure v1 constrained economic / portfolio selector (R3 F7).

One pure selector sits after the F6 Forecast Engine. It consumes the frozen
candidate panel (each member carrying its F4/F5/F6 lineage, its explicit economic
inputs, its data-quality/execution-evidence gates and, where it exists, a trusted
F6 ``FORECAST``), an explicit ``PortfolioCapitalState``, an explicit
``PortfolioExposureSnapshot``, one common ``PortfolioEvaluationWindow``, an
explicit UTC ``evaluation_time`` and an explicit ``PortfolioPolicy``; it returns
an immutable ``PortfolioDecision``.

Design boundaries enforced here:

* The primary objective is expected portfolio net dollars over the one declared
  window. Hit rate, profit factor and capital-hours are diagnostics only.
* ``CASH_NO_TRADE`` is a real competing decision. When no admissible constrained
  portfolio beats cash, the selector holds cash rather than forcing a trade.
* Missing evidence is never favorable evidence. A candidate without a usable
  trusted ``FORECAST``, or whose data-quality/execution-evidence gate is not
  ``VALID``, or whose forecast is stale, is never allocated capital. If no
  candidate is economically usable the selector abstains with
  ``INSUFFICIENT_EVIDENCE``; it never synthesizes a probability or a return.
* The selector is pure over its declared inputs: it reads no clock, environment,
  filesystem, database or network, holds no mutable global state, and derives the
  panel fingerprint from the explicit window and candidate ids.
* The allocation policy is explicit and frozen: each selected candidate receives
  its permitted allocation (the lesser of its requested fraction, the policy's
  per-candidate cap, and its liquidity/capacity notional). No leverage, no Kelly
  sizing, no covariance optimizer and no dynamic risk parity are used.
* A reservation is a deterministic *plan* bound to the explicit portfolio
  version. No production reservation writer is invoked, no Paper-v2 reservation
  is mutated and no live capital is touched.

SHADOW / NON-AUTHORITATIVE. This selector is a research artifact. It is not wired
into ``run_cycle`` or ``scan_opportunities``, it activates no Feature Bus, it
invokes no Committee, and it writes no canonical evidence.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from itertools import combinations

from app.opip.contracts.forecast import ForecastDecision, ForecastStatus
from app.opip.contracts.portfolio import (
    PORTFOLIO_DECISION_SCHEMA_VERSION,
    PORTFOLIO_SELECTOR_VERSION,
    PortfolioAbstentionReason,
    PortfolioAllocation,
    PortfolioCandidate,
    PortfolioCapitalState,
    PortfolioCashReason,
    PortfolioContractError,
    PortfolioDecision,
    PortfolioEvaluationWindow,
    PortfolioExposureSnapshot,
    PortfolioPolicy,
    PortfolioReservation,
    PortfolioReservationPlan,
    PortfolioStatus,
    expected_fill_probability,
    portfolio_decision_identity,
    portfolio_panel_fingerprint,
    require_portfolio_utc,
    require_positive,
)

#: A bounded panel keeps the exact subset search deterministic and cheap. A panel
#: larger than this fails closed rather than silently degrading to a heuristic.
MAX_SELECTOR_PANEL_SIZE = 12


def _require_candidates(candidates: Sequence[PortfolioCandidate]) -> tuple[PortfolioCandidate, ...]:
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, (list, tuple)):
        raise PortfolioContractError("candidates must be a list or tuple")
    resolved = tuple(candidates)
    if len(resolved) > MAX_SELECTOR_PANEL_SIZE:
        raise PortfolioContractError(
            "the candidate panel exceeds the deterministic enumeration bound of "
            f"{MAX_SELECTOR_PANEL_SIZE}"
        )
    for candidate in resolved:
        if not isinstance(candidate, PortfolioCandidate):
            raise PortfolioContractError("candidates must be PortfolioCandidate values")
    identifiers = [candidate.candidate_id for candidate in resolved]
    if len(identifiers) != len(set(identifiers)):
        raise PortfolioContractError(
            "the candidate panel carries a duplicate candidate identity; duplicate "
            "candidates cannot create duplicate capital"
        )
    keys = [(candidate.symbol, candidate.direction.value) for candidate in resolved]
    if len(keys) != len(set(keys)):
        raise PortfolioContractError(
            "the candidate panel carries a duplicate symbol/direction pair"
        )
    return resolved


def _require_policy(policy: object) -> PortfolioPolicy:
    if not isinstance(policy, PortfolioPolicy):
        raise PortfolioContractError("policy must be a PortfolioPolicy")
    return policy


def _require_capital_state(state: object) -> PortfolioCapitalState:
    if not isinstance(state, PortfolioCapitalState):
        raise PortfolioContractError("capital_state must be a PortfolioCapitalState")
    return state


def _require_exposure(exposure: object) -> PortfolioExposureSnapshot:
    if not isinstance(exposure, PortfolioExposureSnapshot):
        raise PortfolioContractError("exposure must be a PortfolioExposureSnapshot")
    return exposure


def _require_window(window: object) -> PortfolioEvaluationWindow:
    if not isinstance(window, PortfolioEvaluationWindow):
        raise PortfolioContractError("window must be a PortfolioEvaluationWindow")
    return window


def _require_valid_until(forecast: ForecastDecision | None) -> datetime:
    """Each reservation expires at its own candidate's forecast validity."""
    if forecast is None or forecast.valid_until is None:
        raise PortfolioContractError(
            "a selected candidate must carry a FORECAST with an explicit valid_until"
        )
    return forecast.valid_until


def _require_window_consistent(
    window: PortfolioEvaluationWindow,
    evaluation_time: datetime,
    candidates: tuple[PortfolioCandidate, ...],
) -> None:
    """Fail closed when the declared window and the panel's forecasts disagree.

    An inconsistent evaluation window is refused rather than silently narrowed, so
    a candidate can never be evaluated on a window it does not belong to.
    """
    if evaluation_time < window.start or evaluation_time >= window.end:
        raise PortfolioContractError(
            "evaluation_time must lie within [window.start, window.end)"
        )
    for candidate in candidates:
        forecast = candidate.forecast
        if forecast is None or forecast.status is not ForecastStatus.FORECAST:
            continue
        if forecast.valid_until is None:
            raise PortfolioContractError("a FORECAST candidate carries no valid_until")
        if forecast.valid_until > window.end:
            raise PortfolioContractError(
                "a candidate forecast outlives the declared evaluation window"
            )
        if forecast.episode_id != candidate.episode_id:
            raise PortfolioContractError(
                "the forecast episode lineage does not match the candidate"
            )
        if forecast.feasibility_decision_id != candidate.feasibility_decision_id:
            raise PortfolioContractError(
                "the forecast feasibility lineage does not match the candidate"
            )


def _candidate_is_economically_usable(
    candidate: PortfolioCandidate, evaluation_time: datetime
) -> bool:
    """True only when the candidate carries trustworthy, timely economic evidence."""
    if not candidate.evidence_complete:
        return False
    forecast = candidate.forecast
    if forecast is None or forecast.status is not ForecastStatus.FORECAST:
        return False
    if forecast.expected_return_unconditional is None:
        return False
    if forecast.uncertainty is None:
        return False
    if forecast.entry_distribution is None:
        return False
    if forecast.valid_until is None or forecast.valid_until <= evaluation_time:
        return False
    return True


def _permitted_allocation(
    candidate: PortfolioCandidate,
    capital_state: PortfolioCapitalState,
    policy: PortfolioPolicy,
) -> float:
    """The explicit allocation policy: requested, capped by policy and capacity."""
    fraction = min(
        candidate.requested_capital_fraction, policy.max_capital_fraction_per_candidate
    )
    capital = fraction * capital_state.available_capital
    capital = min(capital, candidate.liquidity_capacity_notional)
    capital = require_positive(capital, field_name="allocation_capital")
    if not math.isfinite(capital):
        raise PortfolioContractError("allocation capital must be finite")
    return capital


def _build_allocation(
    candidate: PortfolioCandidate,
    capital_state: PortfolioCapitalState,
    policy: PortfolioPolicy,
) -> PortfolioAllocation:
    forecast = candidate.forecast
    if forecast is None or forecast.expected_return_unconditional is None or forecast.uncertainty is None:
        raise PortfolioContractError("a usable candidate must carry a FORECAST")
    capital = _permitted_allocation(candidate, capital_state, policy)
    expected_return = float(forecast.expected_return_unconditional)
    lower_return = float(forecast.uncertainty.expected_return_lower_bound)
    upper_return = float(forecast.uncertainty.expected_return_upper_bound)
    fill_probability, no_fill_probability = expected_fill_probability(forecast)
    allocation = PortfolioAllocation(
        candidate_id=candidate.candidate_id,
        episode_id=candidate.episode_id,
        symbol=candidate.symbol,
        direction=candidate.direction,
        allocated_capital=capital,
        expected_net_dollars=capital * expected_return,
        expected_net_dollars_lower_bound=capital * lower_return,
        expected_net_dollars_upper_bound=capital * upper_return,
        expected_fill_probability=fill_probability,
        expected_no_fill_probability=no_fill_probability,
        loss_at_stop=capital * candidate.stop_loss_fraction,
    )
    return allocation.with_identity()


def _subset_is_feasible(
    subset: tuple[PortfolioCandidate, ...],
    allocations: dict[str, PortfolioAllocation],
    capital_state: PortfolioCapitalState,
    exposure: PortfolioExposureSnapshot,
    policy: PortfolioPolicy,
) -> bool:
    available = capital_state.available_capital
    selected = [allocations[candidate.candidate_id] for candidate in subset]

    if exposure.open_positions + len(selected) > policy.max_positions:
        return False

    gross_cap = policy.max_gross_exposure_fraction * available
    allocated = sum(allocation.allocated_capital for allocation in selected)
    if exposure.gross_exposure + allocated > gross_cap + 1e-9:
        return False

    direction_counts: dict[str, int] = {"LONG": 0, "SHORT": 0}
    for candidate in subset:
        direction_counts[candidate.direction.value] += 1
    for direction in ("LONG", "SHORT"):
        existing = int(exposure.same_direction_counts.get(direction, 0))
        if existing + direction_counts[direction] > policy.max_same_direction:
            return False

    group_counts: dict[str, int] = {}
    for candidate in subset:
        group_counts[candidate.common_shock_group] = (
            group_counts.get(candidate.common_shock_group, 0) + 1
        )
    for group, count in group_counts.items():
        existing = int(exposure.common_shock_group_counts.get(group, 0))
        if existing + count > policy.max_common_shock_group_positions:
            return False

    symbol_cap = policy.max_symbol_exposure_fraction * available
    symbol_totals: dict[str, float] = {}
    for allocation in selected:
        symbol_totals[allocation.symbol] = (
            symbol_totals.get(allocation.symbol, 0.0) + allocation.allocated_capital
        )
    for symbol, selected_exposure in symbol_totals.items():
        existing = float(exposure.symbol_exposure.get(symbol, 0.0))
        if existing + selected_exposure > symbol_cap + 1e-9:
            return False

    loss_cap = policy.max_portfolio_loss_fraction * available
    total_loss = exposure.loss_at_stop + sum(
        allocation.loss_at_stop for allocation in selected
    )
    if total_loss > loss_cap + 1e-9:
        return False

    return True


def _subset_key(
    subset: tuple[PortfolioCandidate, ...],
    allocations: dict[str, PortfolioAllocation],
) -> tuple[float, float, int]:
    net = sum(allocations[c.candidate_id].expected_net_dollars for c in subset)
    gross = sum(allocations[c.candidate_id].allocated_capital for c in subset)
    return (net, -gross, -len(subset))


def _choose_best_subset(
    usable: tuple[PortfolioCandidate, ...],
    allocations: dict[str, PortfolioAllocation],
    capital_state: PortfolioCapitalState,
    exposure: PortfolioExposureSnapshot,
    policy: PortfolioPolicy,
) -> tuple[tuple[PortfolioCandidate, ...] | None, int]:
    """Exhaustively search the bounded panel for the best feasible subset.

    The search is exact over the panel and deterministic: ties are broken toward
    the lower gross allocation, then the smaller position count, then the
    lexicographically smallest candidate-id tuple. A greedy per-row result can be
    strictly worse, so the search is not a greedy pass.
    """
    evaluated = 0
    best: tuple[PortfolioCandidate, ...] | None = None
    best_key: tuple[float, float, int] | None = None
    best_ids: tuple[str, ...] | None = None
    size = len(usable)
    for count in range(1, size + 1):
        for subset in combinations(usable, count):
            evaluated += 1
            if not _subset_is_feasible(subset, allocations, capital_state, exposure, policy):
                continue
            key = _subset_key(subset, allocations)
            identifiers = tuple(sorted(candidate.candidate_id for candidate in subset))
            if (
                best_key is None
                or key > best_key
                or (key == best_key and best_ids is not None and identifiers < best_ids)
            ):
                best, best_key, best_ids = subset, key, identifiers
    return best, evaluated


def _abstain(
    *,
    policy: PortfolioPolicy,
    evaluation_time: datetime,
    reason: PortfolioAbstentionReason,
) -> PortfolioDecision:
    identity = portfolio_decision_identity(
        decision_schema_version=PORTFOLIO_DECISION_SCHEMA_VERSION,
        selector_version=PORTFOLIO_SELECTOR_VERSION,
        policy_version=policy.policy_version,
        policy_identity=policy.policy_identity,
        status=PortfolioStatus.INSUFFICIENT_EVIDENCE,
        cash_reason=None,
        abstention_reason=reason,
        panel_fingerprint=None,
        capital_state_fingerprint=None,
        exposure_fingerprint=None,
        window_identity=None,
        evaluation_time=evaluation_time,
        allocations=(),
        reservation_plan_id=None,
        comparator_result_id=None,
        expected_net_dollars=None,
        expected_net_dollars_lower_bound=None,
        expected_net_dollars_upper_bound=None,
        loss_at_stop=None,
        unallocated_capital=None,
    )
    return PortfolioDecision(
        decision_id=identity,
        decision_schema_version=PORTFOLIO_DECISION_SCHEMA_VERSION,
        selector_version=PORTFOLIO_SELECTOR_VERSION,
        policy_version=policy.policy_version,
        policy_identity=policy.policy_identity,
        status=PortfolioStatus.INSUFFICIENT_EVIDENCE,
        abstention_reason=reason,
        evaluation_time=evaluation_time,
    )


def _cash(
    *,
    policy: PortfolioPolicy,
    evaluation_time: datetime,
    reason: PortfolioCashReason,
    panel_fingerprint: str,
    capital_state_fingerprint: str,
    exposure_fingerprint: str,
    window: PortfolioEvaluationWindow,
    unallocated_capital: float,
    diagnostics: dict[str, float],
) -> PortfolioDecision:
    identity = portfolio_decision_identity(
        decision_schema_version=PORTFOLIO_DECISION_SCHEMA_VERSION,
        selector_version=PORTFOLIO_SELECTOR_VERSION,
        policy_version=policy.policy_version,
        policy_identity=policy.policy_identity,
        status=PortfolioStatus.CASH_NO_TRADE,
        cash_reason=reason,
        abstention_reason=None,
        panel_fingerprint=panel_fingerprint,
        capital_state_fingerprint=capital_state_fingerprint,
        exposure_fingerprint=exposure_fingerprint,
        window_identity=window.window_identity,
        evaluation_time=evaluation_time,
        allocations=(),
        reservation_plan_id=None,
        comparator_result_id=None,
        expected_net_dollars=None,
        expected_net_dollars_lower_bound=None,
        expected_net_dollars_upper_bound=None,
        loss_at_stop=None,
        unallocated_capital=unallocated_capital,
    )
    return PortfolioDecision(
        decision_id=identity,
        decision_schema_version=PORTFOLIO_DECISION_SCHEMA_VERSION,
        selector_version=PORTFOLIO_SELECTOR_VERSION,
        policy_version=policy.policy_version,
        policy_identity=policy.policy_identity,
        status=PortfolioStatus.CASH_NO_TRADE,
        cash_reason=reason,
        evaluation_time=evaluation_time,
        panel_fingerprint=panel_fingerprint,
        capital_state_fingerprint=capital_state_fingerprint,
        exposure_fingerprint=exposure_fingerprint,
        window=window,
        unallocated_capital=unallocated_capital,
        attributable_operating_cost=policy.attributable_operating_cost,
        diagnostics=diagnostics,
    )


def select_portfolio(
    candidates: Sequence[PortfolioCandidate],
    *,
    capital_state: PortfolioCapitalState | None,
    exposure: PortfolioExposureSnapshot | None,
    window: PortfolioEvaluationWindow | None,
    evaluation_time: datetime,
    policy: PortfolioPolicy,
) -> PortfolioDecision:
    """Select the constrained portfolio that maximizes expected net dollars.

    Pure and deterministic: the same semantic inputs always produce a byte-identical
    decision, and an input reordering never changes the selected portfolio.
    """
    resolved_policy = _require_policy(policy)
    instant = require_portfolio_utc(evaluation_time, field_name="evaluation_time")
    resolved_candidates = _require_candidates(candidates)

    if capital_state is None:
        return _abstain(
            policy=resolved_policy,
            evaluation_time=instant,
            reason=PortfolioAbstentionReason.CAPITAL_STATE_UNAVAILABLE,
        )
    resolved_capital = _require_capital_state(capital_state)
    if exposure is None:
        return _abstain(
            policy=resolved_policy,
            evaluation_time=instant,
            reason=PortfolioAbstentionReason.EXPOSURE_STATE_UNAVAILABLE,
        )
    resolved_exposure = _require_exposure(exposure)
    if window is None:
        raise PortfolioContractError("window must be supplied for an economic selection")
    resolved_window = _require_window(window)

    if resolved_capital.portfolio_version != resolved_exposure.portfolio_version:
        raise PortfolioContractError(
            "capital state and exposure snapshot must share one portfolio version"
        )
    if resolved_capital.as_of > instant:
        raise PortfolioContractError(
            "the capital state was observed after the evaluation instant"
        )
    if resolved_exposure.as_of > instant:
        raise PortfolioContractError(
            "the exposure snapshot was observed after the evaluation instant"
        )

    _require_window_consistent(resolved_window, instant, resolved_candidates)
    panel_fingerprint = portfolio_panel_fingerprint(resolved_window, resolved_candidates)

    if not resolved_candidates:
        return _abstain(
            policy=resolved_policy,
            evaluation_time=instant,
            reason=PortfolioAbstentionReason.NO_ELIGIBLE_CANDIDATES,
        )

    ordered = tuple(sorted(resolved_candidates, key=lambda item: item.candidate_id))
    usable = tuple(
        candidate
        for candidate in ordered
        if _candidate_is_economically_usable(candidate, instant)
    )
    if not usable:
        return _abstain(
            policy=resolved_policy,
            evaluation_time=instant,
            reason=PortfolioAbstentionReason.NO_QUALIFIED_FORECAST,
        )

    allocations = {
        candidate.candidate_id: _build_allocation(candidate, resolved_capital, resolved_policy)
        for candidate in usable
    }

    best, evaluated = _choose_best_subset(
        usable, allocations, resolved_capital, resolved_exposure, resolved_policy
    )

    available = resolved_capital.available_capital
    if best is None:
        return _cash(
            policy=resolved_policy,
            evaluation_time=instant,
            reason=PortfolioCashReason.CONSTRAINTS_EXCLUDE_ALL_CANDIDATES,
            panel_fingerprint=panel_fingerprint,
            capital_state_fingerprint=resolved_capital.capital_state_fingerprint,
            exposure_fingerprint=resolved_exposure.exposure_fingerprint,
            window=resolved_window,
            unallocated_capital=available,
            diagnostics={
                "usable_candidate_count": float(len(usable)),
                "evaluated_subset_count": float(evaluated),
                "selected_position_count": 0.0,
                "gross_allocated_fraction": 0.0,
                "cash_fraction": 1.0,
            },
        )

    selected_allocations = tuple(
        sorted(
            (allocations[candidate.candidate_id] for candidate in best),
            key=lambda allocation: allocation.candidate_id,
        )
    )
    forecast_by_candidate = {
        candidate.candidate_id: candidate.forecast for candidate in best
    }
    expected_net_dollars = sum(
        allocation.expected_net_dollars for allocation in selected_allocations
    )
    if not math.isfinite(expected_net_dollars) or expected_net_dollars <= 0.0:
        return _cash(
            policy=resolved_policy,
            evaluation_time=instant,
            reason=PortfolioCashReason.NO_POSITIVE_EXPECTED_NET_DOLLARS,
            panel_fingerprint=panel_fingerprint,
            capital_state_fingerprint=resolved_capital.capital_state_fingerprint,
            exposure_fingerprint=resolved_exposure.exposure_fingerprint,
            window=resolved_window,
            unallocated_capital=available,
            diagnostics={
                "usable_candidate_count": float(len(usable)),
                "evaluated_subset_count": float(evaluated),
                "selected_position_count": 0.0,
                "gross_allocated_fraction": 0.0,
                "cash_fraction": 1.0,
            },
        )

    gross_allocated = sum(
        allocation.allocated_capital for allocation in selected_allocations
    )
    lower_bound = sum(
        allocation.expected_net_dollars_lower_bound for allocation in selected_allocations
    )
    upper_bound = sum(
        allocation.expected_net_dollars_upper_bound for allocation in selected_allocations
    )
    loss_at_stop = sum(allocation.loss_at_stop for allocation in selected_allocations)
    unallocated = available - gross_allocated

    reservations = tuple(
        PortfolioReservation(
            candidate_id=allocation.candidate_id,
            symbol=allocation.symbol,
            direction=allocation.direction,
            reserved_capital=allocation.allocated_capital,
            portfolio_version=resolved_capital.portfolio_version,
            created_at=instant,
            expires_at=_require_valid_until(
                forecast_by_candidate[allocation.candidate_id]
            ),
        )
        for allocation in selected_allocations
    )
    reservation_plan = PortfolioReservationPlan(
        portfolio_version=resolved_capital.portfolio_version,
        window_identity=resolved_window.window_identity,
        created_at=instant,
        reservations=reservations,
    )

    evidence_references = tuple(
        sorted(
            candidate.forecast.decision_id
            for candidate in best
            if candidate.forecast is not None
        )
    )

    diagnostics = {
        "usable_candidate_count": float(len(usable)),
        "evaluated_subset_count": float(evaluated),
        "selected_position_count": float(len(selected_allocations)),
        "gross_allocated_fraction": gross_allocated / available,
        "cash_fraction": unallocated / available,
        "objective_per_allocated_dollar": expected_net_dollars / gross_allocated,
    }

    identity = portfolio_decision_identity(
        decision_schema_version=PORTFOLIO_DECISION_SCHEMA_VERSION,
        selector_version=PORTFOLIO_SELECTOR_VERSION,
        policy_version=resolved_policy.policy_version,
        policy_identity=resolved_policy.policy_identity,
        status=PortfolioStatus.SELECTED,
        cash_reason=None,
        abstention_reason=None,
        panel_fingerprint=panel_fingerprint,
        capital_state_fingerprint=resolved_capital.capital_state_fingerprint,
        exposure_fingerprint=resolved_exposure.exposure_fingerprint,
        window_identity=resolved_window.window_identity,
        evaluation_time=instant,
        allocations=selected_allocations,
        reservation_plan_id=reservation_plan.plan_id,
        comparator_result_id=None,
        expected_net_dollars=expected_net_dollars,
        expected_net_dollars_lower_bound=lower_bound,
        expected_net_dollars_upper_bound=upper_bound,
        loss_at_stop=loss_at_stop,
        unallocated_capital=unallocated,
    )
    return PortfolioDecision(
        decision_id=identity,
        decision_schema_version=PORTFOLIO_DECISION_SCHEMA_VERSION,
        selector_version=PORTFOLIO_SELECTOR_VERSION,
        policy_version=resolved_policy.policy_version,
        policy_identity=resolved_policy.policy_identity,
        status=PortfolioStatus.SELECTED,
        evaluation_time=instant,
        panel_fingerprint=panel_fingerprint,
        capital_state_fingerprint=resolved_capital.capital_state_fingerprint,
        exposure_fingerprint=resolved_exposure.exposure_fingerprint,
        window=resolved_window,
        allocations=selected_allocations,
        reservation_plan=reservation_plan,
        expected_net_dollars=expected_net_dollars,
        expected_net_dollars_lower_bound=lower_bound,
        expected_net_dollars_upper_bound=upper_bound,
        loss_at_stop=loss_at_stop,
        unallocated_capital=unallocated,
        attributable_operating_cost=resolved_policy.attributable_operating_cost,
        diagnostics=diagnostics,
        evidence_references=evidence_references,
    )


__all__ = [
    "MAX_SELECTOR_PANEL_SIZE",
    "select_portfolio",
]
