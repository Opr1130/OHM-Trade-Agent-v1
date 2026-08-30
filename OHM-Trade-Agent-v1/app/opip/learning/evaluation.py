"""Paired champion/challenger evidence evaluation for O'Pip Wave A2."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import math
import statistics
from typing import Any, Iterable


class EvaluationSupport(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class MetricEstimate:
    n: int
    mean: float | None
    ci_low: float | None
    ci_high: float | None
    support: EvaluationSupport

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["support"] = self.support.value
        return row


@dataclass(frozen=True)
class PairedEvaluationSample:
    sample_id: str
    cohort: str
    champion_admitted: bool
    challenger_admitted: bool
    realized_net_return: float | None
    mfe: float | None = None
    mae: float | None = None
    asset: str | None = None
    regime: str | None = None
    lane: str | None = None
    direction: str | None = None

    def __post_init__(self) -> None:
        if not str(self.sample_id or "").strip():
            raise ValueError("sample_id is required")
        for name in ("realized_net_return", "mfe", "mae"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite when present")


@dataclass(frozen=True)
class ArmEvaluation:
    support: int
    admitted: int
    coverage: float
    true_positive: int
    false_positive: int
    false_negative: int
    precision: MetricEstimate
    recall: MetricEstimate
    net_expectancy: MetricEstimate
    mfe: MetricEstimate
    mae: MetricEstimate
    tail_loss_proxy: MetricEstimate
    opportunity_cost: MetricEstimate

    def as_dict(self) -> dict[str, Any]:
        return {
            "support": self.support,
            "admitted": self.admitted,
            "coverage": self.coverage,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "precision": self.precision.as_dict(),
            "recall": self.recall.as_dict(),
            "net_expectancy": self.net_expectancy.as_dict(),
            "mfe": self.mfe.as_dict(),
            "mae": self.mae.as_dict(),
            "tail_loss_proxy": self.tail_loss_proxy.as_dict(),
            "opportunity_cost": self.opportunity_cost.as_dict(),
        }


@dataclass(frozen=True)
class ChampionChallengerEvaluation:
    support: EvaluationSupport
    paired_samples: int
    minimum_support: int
    champion: ArmEvaluation
    challenger: ArmEvaluation
    expectancy_delta: float | None
    precision_delta: float | None
    recall_delta: float | None
    measurement_only: bool = True
    can_promote: bool = False
    automatic_promotion: bool = False
    trade_authority_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "support": self.support.value,
            "paired_samples": self.paired_samples,
            "minimum_support": self.minimum_support,
            "champion": self.champion.as_dict(),
            "challenger": self.challenger.as_dict(),
            "expectancy_delta": self.expectancy_delta,
            "precision_delta": self.precision_delta,
            "recall_delta": self.recall_delta,
            "measurement_only": True,
            "can_promote": False,
            "automatic_promotion": False,
            "trade_authority_changed": False,
        }


def _metric(values: Iterable[float], *, minimum_support: int) -> MetricEstimate:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    n = len(clean)
    if not clean:
        return MetricEstimate(
            n=0,
            mean=None,
            ci_low=None,
            ci_high=None,
            support=EvaluationSupport.INSUFFICIENT,
        )
    mean = statistics.fmean(clean)
    if n < minimum_support or n < 2:
        return MetricEstimate(
            n=n,
            mean=mean,
            ci_low=None,
            ci_high=None,
            support=EvaluationSupport.INSUFFICIENT,
        )
    stdev = statistics.stdev(clean)
    half = 1.96 * stdev / math.sqrt(n)
    return MetricEstimate(
        n=n,
        mean=mean,
        ci_low=mean - half,
        ci_high=mean + half,
        support=EvaluationSupport.SUFFICIENT,
    )


def _rate(
    numerator: int,
    denominator: int,
    *,
    minimum_support: int,
) -> MetricEstimate:
    if denominator <= 0:
        return MetricEstimate(
            n=0,
            mean=None,
            ci_low=None,
            ci_high=None,
            support=EvaluationSupport.INSUFFICIENT,
        )
    p = numerator / denominator
    if denominator < minimum_support:
        return MetricEstimate(
            n=denominator,
            mean=p,
            ci_low=None,
            ci_high=None,
            support=EvaluationSupport.INSUFFICIENT,
        )
    z = 1.96
    denom = 1.0 + z * z / denominator
    centre = (p + z * z / (2.0 * denominator)) / denom
    half = (
        z
        * math.sqrt(
            p * (1.0 - p) / denominator
            + z * z / (4.0 * denominator * denominator)
        )
        / denom
    )
    return MetricEstimate(
        n=denominator,
        mean=p,
        ci_low=max(0.0, centre - half),
        ci_high=min(1.0, centre + half),
        support=EvaluationSupport.SUFFICIENT,
    )


def _tail_losses(values: list[float]) -> list[float]:
    if not values:
        return []
    ordered = sorted(values)
    count = max(1, math.ceil(len(ordered) * 0.10))
    return ordered[:count]


def _arm(
    samples: tuple[PairedEvaluationSample, ...],
    *,
    admitted_attr: str,
    minimum_support: int,
) -> ArmEvaluation:
    admitted_rows = [row for row in samples if bool(getattr(row, admitted_attr))]
    labelled = [row for row in samples if row.realized_net_return is not None]
    positives = [row for row in labelled if float(row.realized_net_return) > 0.0]
    admitted_labelled = [
        row for row in admitted_rows if row.realized_net_return is not None
    ]
    true_positive = sum(
        1 for row in admitted_labelled if float(row.realized_net_return) > 0.0
    )
    false_positive = sum(
        1 for row in admitted_labelled if float(row.realized_net_return) <= 0.0
    )
    false_negative = sum(
        1
        for row in positives
        if not bool(getattr(row, admitted_attr))
    )
    admitted_returns = [
        float(row.realized_net_return)
        for row in admitted_labelled
    ]
    missed_positive_returns = [
        float(row.realized_net_return)
        for row in positives
        if not bool(getattr(row, admitted_attr))
    ]
    return ArmEvaluation(
        support=len(samples),
        admitted=len(admitted_rows),
        coverage=(len(admitted_rows) / len(samples) if samples else 0.0),
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        precision=_rate(
            true_positive,
            true_positive + false_positive,
            minimum_support=minimum_support,
        ),
        recall=_rate(
            true_positive,
            true_positive + false_negative,
            minimum_support=minimum_support,
        ),
        net_expectancy=_metric(
            admitted_returns,
            minimum_support=minimum_support,
        ),
        mfe=_metric(
            [
                float(row.mfe)
                for row in admitted_rows
                if row.mfe is not None
            ],
            minimum_support=minimum_support,
        ),
        mae=_metric(
            [
                float(row.mae)
                for row in admitted_rows
                if row.mae is not None
            ],
            minimum_support=minimum_support,
        ),
        tail_loss_proxy=_metric(
            _tail_losses(admitted_returns),
            minimum_support=max(2, min(minimum_support, len(_tail_losses(admitted_returns)))),
        ),
        opportunity_cost=_metric(
            missed_positive_returns,
            minimum_support=minimum_support,
        ),
    )


def _delta(a: MetricEstimate, b: MetricEstimate) -> float | None:
    if a.mean is None or b.mean is None:
        return None
    return b.mean - a.mean


def evaluate_champion_challenger(
    samples: Iterable[PairedEvaluationSample],
    *,
    minimum_support: int = 30,
) -> ChampionChallengerEvaluation:
    if minimum_support < 2:
        raise ValueError("minimum_support must be >= 2")
    rows = tuple(samples)
    ids = [row.sample_id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("paired evaluation sample ids must be unique")
    support = (
        EvaluationSupport.SUFFICIENT
        if len(rows) >= minimum_support
        else EvaluationSupport.INSUFFICIENT
    )
    champion = _arm(
        rows,
        admitted_attr="champion_admitted",
        minimum_support=minimum_support,
    )
    challenger = _arm(
        rows,
        admitted_attr="challenger_admitted",
        minimum_support=minimum_support,
    )
    return ChampionChallengerEvaluation(
        support=support,
        paired_samples=len(rows),
        minimum_support=minimum_support,
        champion=champion,
        challenger=challenger,
        expectancy_delta=_delta(champion.net_expectancy, challenger.net_expectancy),
        precision_delta=_delta(champion.precision, challenger.precision),
        recall_delta=_delta(champion.recall, challenger.recall),
    )
