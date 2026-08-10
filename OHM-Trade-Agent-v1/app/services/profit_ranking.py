from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.scanner.models import MarketSnapshot
from app.services.economic_quality_gate import EconomicGateResult
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.target_attainability import TargetAttainabilityResult


ECONOMIC_WEIGHT = 35.0
TARGET_QUALITY_WEIGHT = 25.0
EXECUTION_QUALITY_WEIGHT = 20.0
TECHNICAL_QUALITY_WEIGHT = 10.0
EVIDENCE_QUALITY_WEIGHT = 10.0
ECONOMIC_FULL_CREDIT_MOVE_PCT = 10.0
EXECUTION_DRAG_FULL_PENALTY_PCT = 1.0
MISSING_EXECUTION_DRAG_POINTS = 3.0

BOOK_COVERAGE_POINTS = {
    "COMPLETE": 5.0,
    "PARTIAL": 3.0,
    "INSUFFICIENT": 1.0,
}
RECENT_TRADE_POINTS = {
    "FRESH": 3.0,
    "WARN": 1.5,
    "UNAVAILABLE": 0.0,
}
REFERENCE_POINTS = {
    "CONFIRMED": 6.0,
    "WARN": 4.0,
    "STALE": 2.0,
    "MATERIAL_DIVERGENCE": 1.0,
}
CROSS_PAIR_POINTS = {
    "CONFIRMED": 4.0,
    "MIXED": 2.0,
    "SINGLE_MARKET": 1.0,
}


@dataclass(frozen=True)
class ProfitRankingResult:
    """Deterministic comparison score for already-qualified opportunities.

    This score is not a probability, expected return, guarantee, AI confidence,
    or position-sizing recommendation. It cannot rescue a rejected candidate.
    """

    symbol: str
    total_score: float
    economic_opportunity_score: float
    target_quality_score: float
    execution_quality_score: float
    technical_quality_score: float
    evidence_quality_score: float
    strengths: list[str]
    warnings: list[str]
    measured_execution_drag_pct: float | None = None
    _unrounded_total_score: float = field(
        default=0.0,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class QualifiedOpportunity:
    alert: dict[str, Any]
    snapshot: MarketSnapshot
    plan: EntryExitPlan
    target_quality: TargetAttainabilityResult
    economic_quality: EconomicGateResult


@dataclass(frozen=True)
class RankedOpportunity:
    rank: int
    opportunity: QualifiedOpportunity
    profit_ranking: ProfitRankingResult


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _economic_score(economic: EconomicGateResult) -> float:
    return (
        _clamp(economic.target_2_move_pct / ECONOMIC_FULL_CREDIT_MOVE_PCT)
        * ECONOMIC_WEIGHT
    )


def _target_score(target: TargetAttainabilityResult) -> float:
    return _clamp(target.attainability_score / 100.0) * TARGET_QUALITY_WEIGHT


def _execution_score(snapshot: MarketSnapshot) -> tuple[float, list[str], list[str]]:
    execution = snapshot.execution_validation
    strengths: list[str] = []
    warnings: list[str] = []
    if execution is None:
        return MISSING_EXECUTION_DRAG_POINTS, strengths, [
            "Execution validation is unavailable; only missing-drag fallback points awarded"
        ]

    drag = execution.estimated_visible_round_trip_market_drag_pct
    if drag is None:
        drag_points = MISSING_EXECUTION_DRAG_POINTS
        warnings.append("Measured round-trip execution drag is unavailable")
    else:
        drag_points = _clamp(
            1.0 - drag / EXECUTION_DRAG_FULL_PENALTY_PCT
        ) * 12.0
        strengths.append("Measured round-trip execution drag is available")
        if drag >= EXECUTION_DRAG_FULL_PENALTY_PCT:
            warnings.append("Measured round-trip execution drag earns no points")

    coverage_points = BOOK_COVERAGE_POINTS.get(
        execution.book_coverage_status, 0.0
    )
    if execution.book_coverage_status == "COMPLETE":
        strengths.append("Visible book coverage is complete")
    elif execution.book_coverage_status != "PARTIAL":
        warnings.append("Visible book coverage provides limited corroboration")

    trade_points = RECENT_TRADE_POINTS.get(execution.recent_trade_status, 0.0)
    if execution.recent_trade_status == "FRESH":
        strengths.append("Recent public trade evidence is fresh")
    elif execution.recent_trade_status != "WARN":
        warnings.append("Fresh public trade evidence is unavailable")

    score = _clamp(
        drag_points + coverage_points + trade_points,
        minimum=0.0,
        maximum=EXECUTION_QUALITY_WEIGHT,
    )
    return score, strengths, warnings


def _technical_score(snapshot: MarketSnapshot) -> float:
    return _clamp(snapshot.technical_score / 100.0) * TECHNICAL_QUALITY_WEIGHT


def _evidence_score(snapshot: MarketSnapshot) -> tuple[float, list[str], list[str]]:
    strengths: list[str] = []
    warnings: list[str] = []
    reference_points = 0.0
    reference = snapshot.independent_market_reference
    if reference is not None and reference.mapping_status == "UNIQUE":
        reference_points = REFERENCE_POINTS.get(reference.status, 0.0)
        if reference.status == "CONFIRMED":
            strengths.append("Independent CoinGecko reference is confirmed")
        elif reference_points < 6.0:
            warnings.append("Independent CoinGecko reference has reduced quality")
    else:
        warnings.append("Independent CoinGecko identity is not uniquely corroborated")

    cross_pair_points = CROSS_PAIR_POINTS.get(
        snapshot.cross_pair_confirmation_status, 0.0
    )
    if snapshot.cross_pair_confirmation_status == "CONFIRMED":
        strengths.append("Kraken cross-pair evidence is confirmed")
    elif snapshot.cross_pair_confirmation_status == "SINGLE_MARKET":
        warnings.append("Only one Kraken analysis market is available")
    elif cross_pair_points == 0.0:
        warnings.append("Kraken cross-pair corroboration earns no points")

    return reference_points + cross_pair_points, strengths, warnings


def evaluate_profit_ranking(
    snapshot: MarketSnapshot,
    target_quality: TargetAttainabilityResult,
    economic_quality: EconomicGateResult,
) -> ProfitRankingResult:
    """Score one survivor without recalculating or overriding existing gates."""
    economic_score = _economic_score(economic_quality)
    target_score = _target_score(target_quality)
    execution_score, execution_strengths, execution_warnings = _execution_score(
        snapshot
    )
    technical_score = _technical_score(snapshot)
    evidence_score, evidence_strengths, evidence_warnings = _evidence_score(snapshot)
    unrounded_total = _clamp(
        economic_score
        + target_score
        + execution_score
        + technical_score
        + evidence_score,
        minimum=0.0,
        maximum=100.0,
    )
    strengths = execution_strengths + evidence_strengths
    warnings = execution_warnings + evidence_warnings
    if economic_score == ECONOMIC_WEIGHT:
        strengths.append("Target 2 move earns maximum economic-opportunity points")

    execution = snapshot.execution_validation
    drag = (
        execution.estimated_visible_round_trip_market_drag_pct
        if execution is not None
        else None
    )
    return ProfitRankingResult(
        symbol=snapshot.symbol,
        total_score=round(unrounded_total, 2),
        economic_opportunity_score=round(economic_score, 2),
        target_quality_score=round(target_score, 2),
        execution_quality_score=round(execution_score, 2),
        technical_quality_score=round(technical_score, 2),
        evidence_quality_score=round(evidence_score, 2),
        strengths=strengths,
        warnings=warnings,
        measured_execution_drag_pct=drag,
        _unrounded_total_score=unrounded_total,
    )


def _ranking_key(
    opportunity: QualifiedOpportunity,
    result: ProfitRankingResult,
) -> tuple[Any, ...]:
    drag = result.measured_execution_drag_pct
    return (
        -result._unrounded_total_score,
        -opportunity.target_quality.attainability_score,
        drag is None,
        drag if drag is not None else float("inf"),
        -opportunity.snapshot.technical_score,
        result.symbol,
    )


def _attach_outcome_metadata(
    opportunity: QualifiedOpportunity,
) -> None:
    """Expose already-computed facts for later outcome capture; no new scoring."""
    opportunity.alert.setdefault(
        "technical_score", opportunity.snapshot.technical_score
    )
    opportunity.alert.setdefault(
        "target_attainability_score",
        opportunity.target_quality.attainability_score,
    )
    opportunity.alert.setdefault(
        "target_quality_qualified", opportunity.target_quality.qualified
    )
    opportunity.alert.setdefault(
        "economic_qualified", opportunity.economic_quality.qualified
    )
    opportunity.alert.setdefault(
        "economic_target_2_move_pct",
        opportunity.economic_quality.target_2_move_pct,
    )
    opportunity.alert.setdefault(
        "economic_validation_net_t2",
        opportunity.economic_quality.target_2_net_profit,
    )


def rank_profit_opportunities(
    opportunities: list[QualifiedOpportunity],
) -> list[RankedOpportunity]:
    scored = []
    for opportunity in opportunities:
        _attach_outcome_metadata(opportunity)
        result = evaluate_profit_ranking(
            opportunity.snapshot,
            opportunity.target_quality,
            opportunity.economic_quality,
        )
        scored.append((opportunity, result))
    scored.sort(key=lambda item: _ranking_key(item[0], item[1]))
    return [
        RankedOpportunity(rank=index, opportunity=opportunity, profit_ranking=result)
        for index, (opportunity, result) in enumerate(scored, start=1)
    ]
