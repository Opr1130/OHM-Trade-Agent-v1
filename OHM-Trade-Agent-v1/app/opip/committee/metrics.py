"""Evaluation metrics with explicit applicability.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

A metric is only reported where it is mathematically valid for the case type.
Where it is not valid the metric is reported as *not applicable* with a reason,
never as zero and never as a fabricated number. That distinction is the whole
point of this module: an inapplicable metric that silently reads as ``0.0``
would make a model look better or worse than the evidence supports.

All arithmetic uses :class:`decimal.Decimal` and metric values are rendered as
decimal strings, matching the repository's numeric convention (binary floats are
not permitted in canonical O'Pip identity data).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Iterable, Sequence

#: Probability clipping for log loss so a confident miss cannot produce an
#: infinite value. Documented rather than hidden.
LOG_LOSS_EPSILON = Decimal("0.0001")

#: A metric is only meaningful on a minimum sample. Below it the metric is
#: reported as not applicable with reason INSUFFICIENT_SAMPLE.
MIN_METRIC_SAMPLE = 5

REASON_NO_LABELED_SAMPLES = "NO_LABELED_SAMPLES"
REASON_INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
REASON_NO_PROBABILISTIC_FORECAST = "NO_PROBABILISTIC_FORECAST"
REASON_NO_CLASSIFICATION_TARGET = "NO_CLASSIFICATION_TARGET"
REASON_NO_COMPARABLE_REPLAYS = "NO_COMPARABLE_REPLAYS"
REASON_NOT_DEFINED_FOR_CASE_TYPE = "NOT_DEFINED_FOR_CASE_TYPE"

#: Decimal places used when rendering a metric value. Sufficient for research
#: comparison without implying spurious precision.
METRIC_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class EvaluationMetric:
    """One metric value, or an explicit statement that it does not apply."""

    name: str
    value: str | None
    applicable: bool
    sample_size: int
    not_applicable_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("metric name is required")
        if type(self.sample_size) is not int or self.sample_size < 0:
            raise ValueError("sample_size must be a non-negative integer")
        if self.applicable:
            if self.value is None:
                raise ValueError("an applicable metric requires a value")
            if self.not_applicable_reason is not None:
                raise ValueError("an applicable metric cannot carry a reason")
        else:
            if self.value is not None:
                raise ValueError("an inapplicable metric must not carry a value")
            if not self.not_applicable_reason:
                raise ValueError("an inapplicable metric requires a reason")

    @property
    def decimal_value(self) -> Decimal | None:
        return None if self.value is None else Decimal(self.value)


def _render(value: Decimal) -> str:
    return str(value.quantize(METRIC_PLACES))


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        return (Decimal(numerator) / Decimal(denominator)).quantize(METRIC_PLACES)


def rate_metric(name: str, *, numerator: int, denominator: int) -> EvaluationMetric:
    """A count ratio. Reported as not applicable when nothing was observed."""
    value = _ratio(numerator, denominator)
    if value is None:
        return EvaluationMetric(
            name=name,
            value=None,
            applicable=False,
            sample_size=0,
            not_applicable_reason=REASON_NO_LABELED_SAMPLES,
        )
    return EvaluationMetric(
        name=name, value=str(value), applicable=True, sample_size=denominator
    )


@dataclass(frozen=True)
class ProbabilitySample:
    """One probabilistic forecast paired with a resolved binary target."""

    predicted_probability: Decimal
    observed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.predicted_probability, Decimal):
            raise ValueError("predicted_probability must be a Decimal")
        if not (Decimal(0) <= self.predicted_probability <= Decimal(1)):
            raise ValueError("predicted_probability must be within 0..1")
        if type(self.observed) is not bool:
            raise ValueError("observed must be a boolean")


def brier_score(samples: Sequence[ProbabilitySample]) -> EvaluationMetric:
    """Mean squared error of a probabilistic forecast (lower is better)."""
    if len(samples) < MIN_METRIC_SAMPLE:
        return EvaluationMetric(
            name="brier_score",
            value=None,
            applicable=False,
            sample_size=len(samples),
            not_applicable_reason=(
                REASON_NO_PROBABILISTIC_FORECAST
                if not samples
                else REASON_INSUFFICIENT_SAMPLE
            ),
        )
    total = Decimal(0)
    for sample in samples:
        target = Decimal(1) if sample.observed else Decimal(0)
        difference = sample.predicted_probability - target
        total += difference * difference
    return EvaluationMetric(
        name="brier_score",
        value=_render(total / Decimal(len(samples))),
        applicable=True,
        sample_size=len(samples),
    )


def log_loss(samples: Sequence[ProbabilitySample]) -> EvaluationMetric:
    """Mean negative log likelihood, with documented clipping."""
    if len(samples) < MIN_METRIC_SAMPLE:
        return EvaluationMetric(
            name="log_loss",
            value=None,
            applicable=False,
            sample_size=len(samples),
            not_applicable_reason=(
                REASON_NO_PROBABILISTIC_FORECAST
                if not samples
                else REASON_INSUFFICIENT_SAMPLE
            ),
        )
    floor = LOG_LOSS_EPSILON
    ceiling = Decimal(1) - LOG_LOSS_EPSILON
    total = Decimal(0)
    for sample in samples:
        probability = min(max(sample.predicted_probability, floor), ceiling)
        if sample.observed:
            total -= probability.ln()
        else:
            total -= (Decimal(1) - probability).ln()
    return EvaluationMetric(
        name="log_loss",
        value=_render(total / Decimal(len(samples))),
        applicable=True,
        sample_size=len(samples),
    )


@dataclass(frozen=True)
class CalibrationBin:
    """One reliability bin."""

    lower: str
    upper: str
    count: int
    mean_predicted: str
    observed_rate: str


@dataclass(frozen=True)
class CalibrationReport:
    """Reliability bins plus expected calibration error."""

    bins: tuple[CalibrationBin, ...]
    expected_calibration_error: EvaluationMetric


def calibration_report(
    samples: Sequence[ProbabilitySample], *, bin_count: int = 10
) -> CalibrationReport:
    """Reliability analysis. Empty bins are omitted, never interpolated."""
    if bin_count < 2:
        raise ValueError("bin_count must be at least 2")
    width = Decimal(1) / Decimal(bin_count)
    bins: list[CalibrationBin] = []
    weighted_gap = Decimal(0)
    for index in range(bin_count):
        lower = width * Decimal(index)
        upper = Decimal(1) if index == bin_count - 1 else width * Decimal(index + 1)
        members = [
            sample
            for sample in samples
            if (lower <= sample.predicted_probability < upper)
            or (index == bin_count - 1 and sample.predicted_probability == Decimal(1))
        ]
        if not members:
            continue
        predicted_mean = sum(
            (sample.predicted_probability for sample in members), Decimal(0)
        ) / Decimal(len(members))
        observed_mean = Decimal(sum(1 for s in members if s.observed)) / Decimal(
            len(members)
        )
        bins.append(
            CalibrationBin(
                lower=_render(lower),
                upper=_render(upper),
                count=len(members),
                mean_predicted=_render(predicted_mean),
                observed_rate=_render(observed_mean),
            )
        )
        weighted_gap += (
            abs(predicted_mean - observed_mean)
            * Decimal(len(members))
            / Decimal(len(samples))
        )
    if len(samples) < MIN_METRIC_SAMPLE:
        ece = EvaluationMetric(
            name="expected_calibration_error",
            value=None,
            applicable=False,
            sample_size=len(samples),
            not_applicable_reason=(
                REASON_NO_PROBABILISTIC_FORECAST
                if not samples
                else REASON_INSUFFICIENT_SAMPLE
            ),
        )
    else:
        ece = EvaluationMetric(
            name="expected_calibration_error",
            value=_render(weighted_gap),
            applicable=True,
            sample_size=len(samples),
        )
    return CalibrationReport(bins=tuple(bins), expected_calibration_error=ece)


@dataclass(frozen=True)
class ConfusionMatrix:
    """Binary classification counts."""

    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int

    def __post_init__(self) -> None:
        for field_name in (
            "true_positive",
            "false_positive",
            "true_negative",
            "false_negative",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")

    @property
    def total(self) -> int:
        return (
            self.true_positive
            + self.false_positive
            + self.true_negative
            + self.false_negative
        )


def confusion_matrix(
    pairs: Iterable[tuple[bool, bool]],
) -> ConfusionMatrix:
    """Build a confusion matrix from ``(predicted, observed)`` pairs."""
    tp = fp = tn = fn = 0
    for predicted, observed in pairs:
        if type(predicted) is not bool or type(observed) is not bool:
            raise ValueError("confusion pairs must be booleans")
        if predicted and observed:
            tp += 1
        elif predicted and not observed:
            fp += 1
        elif not predicted and observed:
            fn += 1
        else:
            tn += 1
    return ConfusionMatrix(
        true_positive=tp,
        false_positive=fp,
        true_negative=tn,
        false_negative=fn,
    )


@dataclass(frozen=True)
class ClassificationReport:
    """Precision, recall, F1 and accuracy over an explicit confusion matrix."""

    matrix: ConfusionMatrix
    precision: EvaluationMetric
    recall: EvaluationMetric
    f1: EvaluationMetric
    accuracy: EvaluationMetric


def classification_report(pairs: Sequence[tuple[bool, bool]]) -> ClassificationReport:
    """Classification metrics. No labeled pair means no metric, not a zero."""
    matrix = confusion_matrix(pairs)
    if matrix.total == 0:
        # Each metric keeps its own name even when nothing is applicable, so a
        # persisted report cannot mislabel four measurements as one and a public
        # ``metric("accuracy")`` lookup still resolves for an unevaluated arm.
        def _inapplicable(name: str) -> EvaluationMetric:
            return EvaluationMetric(
                name=name,
                value=None,
                applicable=False,
                sample_size=0,
                not_applicable_reason=REASON_NO_CLASSIFICATION_TARGET,
            )

        return ClassificationReport(
            matrix=matrix,
            precision=_inapplicable("precision"),
            recall=_inapplicable("recall"),
            f1=_inapplicable("f1"),
            accuracy=_inapplicable("accuracy"),
        )
    precision = rate_metric(
        "precision",
        numerator=matrix.true_positive,
        denominator=matrix.true_positive + matrix.false_positive,
    )
    recall = rate_metric(
        "recall",
        numerator=matrix.true_positive,
        denominator=matrix.true_positive + matrix.false_negative,
    )
    precision_value = precision.decimal_value if precision.applicable else None
    recall_value = recall.decimal_value if recall.applicable else None
    if precision_value is not None and recall_value is not None:
        combined = precision_value + recall_value
        f1_value = (
            Decimal(0)
            if combined == 0
            else (Decimal(2) * precision_value * recall_value) / combined
        )
        f1 = EvaluationMetric(
            name="f1",
            value=_render(f1_value),
            applicable=True,
            sample_size=matrix.total,
        )
    else:
        f1 = EvaluationMetric(
            name="f1",
            value=None,
            applicable=False,
            sample_size=matrix.total,
            not_applicable_reason=REASON_INSUFFICIENT_SAMPLE,
        )
    accuracy = rate_metric(
        "accuracy",
        numerator=matrix.true_positive + matrix.true_negative,
        denominator=matrix.total,
    )
    return ClassificationReport(
        matrix=matrix, precision=precision, recall=recall, f1=f1, accuracy=accuracy
    )


def repeatability_metric(
    *,
    agreed: int,
    compared: int,
) -> EvaluationMetric:
    """Rate at which a replayed seat reproduced its committed opinion."""
    if compared <= 0:
        return EvaluationMetric(
            name="repeatability",
            value=None,
            applicable=False,
            sample_size=0,
            not_applicable_reason=REASON_NO_COMPARABLE_REPLAYS,
        )
    return rate_metric("repeatability", numerator=agreed, denominator=compared)


@dataclass(frozen=True)
class LatencyDistribution:
    """Latency summary, in microseconds, over the calls that completed."""

    sample_size: int
    p50_micros: int | None
    p90_micros: int | None
    maximum_micros: int | None

    @property
    def applicable(self) -> bool:
        return self.p50_micros is not None


def latency_distribution(values: Sequence[int]) -> LatencyDistribution:
    """Nearest-rank percentiles. An empty sample reports no latency at all."""
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return LatencyDistribution(0, None, None, None)
    if any(type(value) is not int or value < 0 for value in ordered):
        raise ValueError("latency values must be non-negative integers")

    def _nearest_rank(percent: int) -> int:
        # Nearest-rank: ceil(percent/100 * n), clamped into range.
        index = (percent * len(ordered) + 99) // 100
        return ordered[min(max(index, 1), len(ordered)) - 1]

    return LatencyDistribution(
        sample_size=len(ordered),
        p50_micros=_nearest_rank(50),
        p90_micros=_nearest_rank(90),
        maximum_micros=ordered[-1],
    )


@dataclass(frozen=True)
class CostAggregate:
    """Cost summary in microunits, with an explicit completeness statement."""

    sample_size: int
    known_cost_microunits: int | None
    unknown_cost_samples: int

    @property
    def cost_is_known(self) -> bool:
        return self.unknown_cost_samples == 0 and self.sample_size > 0


def cost_aggregate(costs: Sequence[int | None]) -> CostAggregate:
    """Sum known costs and count the samples whose cost is genuinely unknown."""
    unknown = sum(1 for cost in costs if cost is None)
    known = [cost for cost in costs if cost is not None]
    if any(type(cost) is not int or cost < 0 for cost in known):
        raise ValueError("costs must be non-negative integers")
    return CostAggregate(
        sample_size=len(costs),
        known_cost_microunits=sum(known) if known else None,
        unknown_cost_samples=unknown,
    )


__all__ = [
    "LOG_LOSS_EPSILON",
    "METRIC_PLACES",
    "MIN_METRIC_SAMPLE",
    "REASON_INSUFFICIENT_SAMPLE",
    "REASON_NOT_DEFINED_FOR_CASE_TYPE",
    "REASON_NO_CLASSIFICATION_TARGET",
    "REASON_NO_COMPARABLE_REPLAYS",
    "REASON_NO_LABELED_SAMPLES",
    "REASON_NO_PROBABILISTIC_FORECAST",
    "CalibrationBin",
    "CalibrationReport",
    "ClassificationReport",
    "ConfusionMatrix",
    "CostAggregate",
    "EvaluationMetric",
    "LatencyDistribution",
    "ProbabilitySample",
    "brier_score",
    "calibration_report",
    "classification_report",
    "confusion_matrix",
    "cost_aggregate",
    "latency_distribution",
    "log_loss",
    "rate_metric",
    "repeatability_metric",
]
