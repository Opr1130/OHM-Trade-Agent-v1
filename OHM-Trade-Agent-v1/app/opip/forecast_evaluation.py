"""Proper scoring and reliability evaluation for the F6 Forecast Engine.

This is the separate evaluation framework for the fourth R3 slice. It implements
proper scoring rules separately for the two outcome families:

* (A) the entry-execution distribution over ``NO_FILL``/``PARTIAL_FILL``/
  ``FULL_FILL``;
* (B) the conditional post-fill path distribution over ``TARGET``/``STOP``/
  ``TIMEOUT``/``RISK_EXIT``.

The two families are never merged, and a ``P(fill)`` is never folded into a
``P(target)``. A ``FORECAST`` decision is scored only where its label is cleanly
resolved; an ``UNRESOLVED``, ``INCOMPLETE_COVERAGE`` or ``INSUFFICIENT_EVIDENCE``
label is never turned into a negative, and a ``NO_FILL`` example never fabricates
a ``STOP``/``TIMEOUT`` path label.

Nothing here is a promotion gate. No universal Brier, log-loss, ECE or sample
threshold is an architecture constant, and this module never emits a PASS/FAIL
promotion verdict from arbitrary metric thresholds. The evaluation population
must contain every eligible forecast attempt, including abstentions and
missingness: it must never be only filled trades, winners or successful episodes.

Sealed evaluation is enforced: a label whose availability is not strictly after
the prediction cutoff is a future-label leakage defect and is refused, and a
forecast record is immutable before a label is attached. The single numerical
epsilon below is strictly for log-loss evaluation stability; it is not a
trading, calibration or promotion threshold.

SHADOW / NON-AUTHORITATIVE. This module reads no clock, environment, filesystem,
database or network, and it allocates no capital.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from app.opip.contracts import (
    ENTRY_EXECUTION_OUTCOMES,
    FORECAST_EVALUATION_VERSION,
    POST_FILL_PATH_OUTCOMES,
    DistributionKind,
    EntryExecutionOutcome,
    ForecastContractError,
    ForecastHorizon,
    ForecastStatus,
    ForecastUncertainty,
    PostFillPathOutcome,
    ProbabilityDistribution,
    iso_z,
    require_finite_number,
    require_forecast_enum,
    require_forecast_text,
    require_forecast_utc,
    require_non_negative_int,
    stable_hash,
)

#: Semantic prefix of the deterministic ``FEVAL:`` evaluation-report identity.
FORECAST_EVALUATION_REPORT_ID_PREFIX = "FEVAL"

#: Numerical epsilon used *only* to keep log loss finite when an observed
#: outcome received probability zero. It exists for evaluation stability. It is
#: deliberately small and is never a trading, calibration or promotion threshold.
EVALUATION_LOG_EPSILON = 1e-12


class ForecastLabelState(str, Enum):
    """The label resolution state for one outcome family.

    Only ``RESOLVED`` is a clean label. ``UNRESOLVED`` and ``INCOMPLETE_COVERAGE``
    are never turned into negatives, and ``INSUFFICIENT_EVIDENCE`` covers the
    absent path evidence of a ``NO_FILL`` entry.
    """

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    INCOMPLETE_COVERAGE = "INCOMPLETE_COVERAGE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ForecastFidelityGrade(str, Enum):
    """The simulation-fidelity grade of one example (A exact, C material gap)."""

    A = "A"
    B = "B"
    C = "C"


_EXAMPLE_DURABLE_KEYS: tuple[str, ...] = (
    "example_id",
    "forecast_decision_id",
    "model_artifact_id",
    "prediction_cutoff",
    "input_fingerprint",
    "status",
    "entry_distribution",
    "post_fill_distribution",
    "expected_return_unconditional",
    "expected_return_conditional_on_fill",
    "uncertainty",
    "horizon",
    "entry_outcome_label",
    "entry_label_state",
    "post_fill_outcome_label",
    "post_fill_label_state",
    "realized_net_return",
    "realized_return_label_state",
    "label_available_at",
    "label_cutoff",
    "fidelity",
)


def _require_mapping(
    value: Any, *, field_name: str, expected_keys: tuple[str, ...]
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ForecastContractError(f"{field_name} must be a mapping")
    present = set(value.keys())
    expected = set(expected_keys)
    missing = sorted(expected - present)
    if missing:
        raise ForecastContractError(
            f"{field_name} is missing mandatory keys: " + ", ".join(missing)
        )
    unknown = sorted(str(key) for key in present - expected)
    if unknown:
        raise ForecastContractError(
            f"{field_name} carries unknown keys: " + ", ".join(unknown)
        )
    return value


def _parse_optional_utc(value: Any, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ForecastContractError(f"{field_name} must be an ISO-8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ForecastContractError(
            f"{field_name} must be an ISO-8601 UTC instant"
        ) from exc
    return require_forecast_utc(parsed, field_name=field_name)


def _is_resolved(state: ForecastLabelState) -> bool:
    return state is ForecastLabelState.RESOLVED


@dataclass(frozen=True)
class ForecastEvaluationExample:
    """One sealed evaluation example: a prediction and its matured label.

    The example is immutable. It carries the scored prediction (for a
    ``FORECAST`` decision) and the label state for each outcome family. A label
    that is not cleanly resolved never carries a fabricated outcome, a ``NO_FILL``
    entry never carries a path label, and a path label is only permitted where
    there was actual fill exposure.
    """

    example_id: str
    forecast_decision_id: str
    model_artifact_id: str | None
    prediction_cutoff: datetime
    input_fingerprint: str
    status: ForecastStatus
    entry_distribution: ProbabilityDistribution | None
    post_fill_distribution: ProbabilityDistribution | None
    expected_return_unconditional: float | None
    expected_return_conditional_on_fill: float | None
    uncertainty: ForecastUncertainty | None
    horizon: ForecastHorizon | None
    entry_outcome_label: EntryExecutionOutcome | None
    entry_label_state: ForecastLabelState
    post_fill_outcome_label: PostFillPathOutcome | None
    post_fill_label_state: ForecastLabelState
    realized_net_return: float | None
    realized_return_label_state: ForecastLabelState
    label_available_at: datetime | None
    label_cutoff: datetime | None
    fidelity: ForecastFidelityGrade

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "example_id",
            require_forecast_text(self.example_id, field_name="example_id"),
        )
        object.__setattr__(
            self,
            "forecast_decision_id",
            require_forecast_text(
                self.forecast_decision_id, field_name="forecast_decision_id"
            ),
        )
        object.__setattr__(
            self,
            "input_fingerprint",
            require_forecast_text(self.input_fingerprint, field_name="input_fingerprint"),
        )
        if self.model_artifact_id is not None:
            object.__setattr__(
                self,
                "model_artifact_id",
                require_forecast_text(self.model_artifact_id, field_name="model_artifact_id"),
            )
        object.__setattr__(
            self,
            "prediction_cutoff",
            require_forecast_utc(self.prediction_cutoff, field_name="prediction_cutoff"),
        )
        status = require_forecast_enum(ForecastStatus, self.status, field_name="status")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "fidelity",
            require_forecast_enum(
                ForecastFidelityGrade, self.fidelity, field_name="fidelity"
            ),
        )
        entry_state = require_forecast_enum(
            ForecastLabelState, self.entry_label_state, field_name="entry_label_state"
        )
        path_state = require_forecast_enum(
            ForecastLabelState, self.post_fill_label_state, field_name="post_fill_label_state"
        )
        return_state = require_forecast_enum(
            ForecastLabelState,
            self.realized_return_label_state,
            field_name="realized_return_label_state",
        )
        object.__setattr__(self, "entry_label_state", entry_state)
        object.__setattr__(self, "post_fill_label_state", path_state)
        object.__setattr__(self, "realized_return_label_state", return_state)

        if self.horizon is not None and not isinstance(self.horizon, ForecastHorizon):
            raise ForecastContractError("horizon must be a ForecastHorizon")
        if self.entry_distribution is not None and not isinstance(
            self.entry_distribution, ProbabilityDistribution
        ):
            raise ForecastContractError("entry_distribution must be a distribution")
        if self.post_fill_distribution is not None and not isinstance(
            self.post_fill_distribution, ProbabilityDistribution
        ):
            raise ForecastContractError("post_fill_distribution must be a distribution")
        if self.uncertainty is not None and not isinstance(
            self.uncertainty, ForecastUncertainty
        ):
            raise ForecastContractError("uncertainty must be a ForecastUncertainty")

        if status is ForecastStatus.FORECAST:
            if (
                self.entry_distribution is None
                or self.entry_distribution.kind is not DistributionKind.ENTRY_EXECUTION
            ):
                raise ForecastContractError(
                    "a scored FORECAST example requires an entry-execution distribution"
                )
            if (
                self.post_fill_distribution is None
                or self.post_fill_distribution.kind is not DistributionKind.POST_FILL_PATH
            ):
                raise ForecastContractError(
                    "a scored FORECAST example requires a post-fill path distribution"
                )
            if self.horizon is None:
                raise ForecastContractError("a scored FORECAST example requires a horizon")
        else:
            if any(
                value is not None
                for value in (
                    self.entry_distribution,
                    self.post_fill_distribution,
                    self.expected_return_unconditional,
                    self.expected_return_conditional_on_fill,
                    self.uncertainty,
                    self.horizon,
                )
            ):
                raise ForecastContractError(
                    "an abstention example carries no prediction payload"
                )

        entry_label = self.entry_outcome_label
        if _is_resolved(entry_state):
            if entry_label is None:
                raise ForecastContractError(
                    "a RESOLVED entry label requires an entry outcome"
                )
            entry_label = require_forecast_enum(
                EntryExecutionOutcome, entry_label, field_name="entry_outcome_label"
            )
        elif entry_label is not None:
            raise ForecastContractError(
                "a non-resolved entry label carries no fabricated outcome"
            )
        object.__setattr__(self, "entry_outcome_label", entry_label)

        path_label = self.post_fill_outcome_label
        if _is_resolved(path_state):
            if path_label is None:
                raise ForecastContractError(
                    "a RESOLVED path label requires a post-fill outcome"
                )
            if entry_label not in (
                EntryExecutionOutcome.PARTIAL_FILL,
                EntryExecutionOutcome.FULL_FILL,
            ):
                raise ForecastContractError(
                    "a path label requires actual fill exposure"
                )
            path_label = require_forecast_enum(
                PostFillPathOutcome, path_label, field_name="post_fill_outcome_label"
            )
        elif path_label is not None:
            raise ForecastContractError(
                "a non-resolved path label carries no fabricated outcome"
            )
        object.__setattr__(self, "post_fill_outcome_label", path_label)

        # A NO_FILL entry has no post-fill path at all: never fabricate a
        # STOP/TIMEOUT, and never claim a resolved path.
        if entry_label is EntryExecutionOutcome.NO_FILL:
            if (
                path_label is not None
                or path_state is ForecastLabelState.RESOLVED
            ):
                raise ForecastContractError(
                    "a NO_FILL entry has no post-fill path label"
                )

        realized = self.realized_net_return
        if _is_resolved(return_state):
            if realized is None:
                raise ForecastContractError(
                    "a RESOLVED realized-return label requires a value"
                )
            object.__setattr__(
                self,
                "realized_net_return",
                require_finite_number(realized, field_name="realized_net_return"),
            )
        elif realized is not None:
            raise ForecastContractError(
                "a non-resolved realized-return label carries no fabricated value"
            )
        if self.expected_return_unconditional is not None:
            object.__setattr__(
                self,
                "expected_return_unconditional",
                require_finite_number(
                    self.expected_return_unconditional,
                    field_name="expected_return_unconditional",
                ),
            )
        if self.expected_return_conditional_on_fill is not None:
            object.__setattr__(
                self,
                "expected_return_conditional_on_fill",
                require_finite_number(
                    self.expected_return_conditional_on_fill,
                    field_name="expected_return_conditional_on_fill",
                ),
            )

        any_resolved = (
            _is_resolved(entry_state)
            or _is_resolved(path_state)
            or _is_resolved(return_state)
        )
        available = self.label_available_at
        if any_resolved and available is None:
            raise ForecastContractError(
                "a resolved label requires an explicit label availability time"
            )
        if available is not None:
            object.__setattr__(
                self,
                "label_available_at",
                require_forecast_utc(available, field_name="label_available_at"),
            )
        if self.label_cutoff is not None:
            object.__setattr__(
                self,
                "label_cutoff",
                require_forecast_utc(self.label_cutoff, field_name="label_cutoff"),
            )

    @property
    def has_leakage(self) -> bool:
        """True when a resolved label was available at or before the prediction."""
        if not (
            _is_resolved(self.entry_label_state)
            or _is_resolved(self.post_fill_label_state)
            or _is_resolved(self.realized_return_label_state)
        ):
            return False
        available = self.label_available_at
        if available is None:
            return True
        return available <= self.prediction_cutoff

    def to_dict(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "forecast_decision_id": self.forecast_decision_id,
            "model_artifact_id": self.model_artifact_id,
            "prediction_cutoff": iso_z(
                self.prediction_cutoff, field_name="prediction_cutoff"
            ),
            "input_fingerprint": self.input_fingerprint,
            "status": self.status.value,
            "entry_distribution": (
                None if self.entry_distribution is None else self.entry_distribution.to_dict()
            ),
            "post_fill_distribution": (
                None
                if self.post_fill_distribution is None
                else self.post_fill_distribution.to_dict()
            ),
            "expected_return_unconditional": self.expected_return_unconditional,
            "expected_return_conditional_on_fill": self.expected_return_conditional_on_fill,
            "uncertainty": None if self.uncertainty is None else self.uncertainty.to_dict(),
            "horizon": None if self.horizon is None else self.horizon.to_dict(),
            "entry_outcome_label": (
                None if self.entry_outcome_label is None else self.entry_outcome_label.value
            ),
            "entry_label_state": self.entry_label_state.value,
            "post_fill_outcome_label": (
                None
                if self.post_fill_outcome_label is None
                else self.post_fill_outcome_label.value
            ),
            "post_fill_label_state": self.post_fill_label_state.value,
            "realized_net_return": self.realized_net_return,
            "realized_return_label_state": self.realized_return_label_state.value,
            "label_available_at": (
                None
                if self.label_available_at is None
                else iso_z(self.label_available_at, field_name="label_available_at")
            ),
            "label_cutoff": (
                None
                if self.label_cutoff is None
                else iso_z(self.label_cutoff, field_name="label_cutoff")
            ),
            "fidelity": self.fidelity.value,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ForecastEvaluationExample":
        body = _require_mapping(
            raw, field_name="example", expected_keys=_EXAMPLE_DURABLE_KEYS
        )
        return cls(
            example_id=body["example_id"],
            forecast_decision_id=body["forecast_decision_id"],
            model_artifact_id=body["model_artifact_id"],
            prediction_cutoff=require_forecast_utc(
                datetime.fromisoformat(
                    str(body["prediction_cutoff"]).replace("Z", "+00:00")
                ),
                field_name="prediction_cutoff",
            ),
            input_fingerprint=body["input_fingerprint"],
            status=body["status"],
            entry_distribution=(
                None
                if body["entry_distribution"] is None
                else ProbabilityDistribution.from_dict(body["entry_distribution"])
            ),
            post_fill_distribution=(
                None
                if body["post_fill_distribution"] is None
                else ProbabilityDistribution.from_dict(body["post_fill_distribution"])
            ),
            expected_return_unconditional=body["expected_return_unconditional"],
            expected_return_conditional_on_fill=body[
                "expected_return_conditional_on_fill"
            ],
            uncertainty=(
                None
                if body["uncertainty"] is None
                else ForecastUncertainty.from_dict(body["uncertainty"])
            ),
            horizon=(
                None if body["horizon"] is None else ForecastHorizon.from_dict(body["horizon"])
            ),
            entry_outcome_label=body["entry_outcome_label"],
            entry_label_state=body["entry_label_state"],
            post_fill_outcome_label=body["post_fill_outcome_label"],
            post_fill_label_state=body["post_fill_label_state"],
            realized_net_return=body["realized_net_return"],
            realized_return_label_state=body["realized_return_label_state"],
            label_available_at=_parse_optional_utc(
                body["label_available_at"], field_name="label_available_at"
            ),
            label_cutoff=_parse_optional_utc(
                body["label_cutoff"], field_name="label_cutoff"
            ),
            fidelity=body["fidelity"],
        )


# ---------------------------------------------------------------------------
# Proper scoring rules (per family; never merged)
# ---------------------------------------------------------------------------


def _distribution_family_outcomes(
    distribution: ProbabilityDistribution,
) -> tuple[str, ...]:
    if distribution.kind is DistributionKind.ENTRY_EXECUTION:
        return ENTRY_EXECUTION_OUTCOMES
    return POST_FILL_PATH_OUTCOMES


def _family_outcome_token(
    distribution: ProbabilityDistribution, observed_outcome: Any
) -> str:
    if distribution.kind is DistributionKind.ENTRY_EXECUTION:
        return require_forecast_enum(
            EntryExecutionOutcome, observed_outcome, field_name="observed_outcome"
        ).value
    return require_forecast_enum(
        PostFillPathOutcome, observed_outcome, field_name="observed_outcome"
    ).value


def brier_score(
    distribution: ProbabilityDistribution, observed_outcome: Any
) -> float:
    """The multi-class Brier score ``sum_i (p_i - o_i)**2`` for one distribution.

    ``o`` is the one-hot vector of the observed outcome. A perfect one-hot
    forecast scores ``0``; a uniform forecast over ``K`` outcomes scores
    ``(K - 1) / K``.
    """
    if not isinstance(distribution, ProbabilityDistribution):
        raise ForecastContractError("distribution must be a ProbabilityDistribution")
    observed = _family_outcome_token(distribution, observed_outcome)
    outcomes = _distribution_family_outcomes(distribution)
    total = 0.0
    for outcome in outcomes:
        target = 1.0 if outcome == observed else 0.0
        delta = distribution.probabilities[outcome] - target
        total += delta * delta
    return total


def log_loss(
    distribution: ProbabilityDistribution,
    observed_outcome: Any,
    *,
    epsilon: float = EVALUATION_LOG_EPSILON,
) -> float:
    """The multi-class log loss ``-log(p_observed)`` for one distribution.

    ``log(0)`` is never evaluated: the observed probability is floored at the
    explicit ``epsilon``, which exists only for evaluation stability. A perfect
    one-hot forecast scores ``0``; a uniform forecast over ``K`` outcomes scores
    ``log(K)``.
    """
    if not isinstance(distribution, ProbabilityDistribution):
        raise ForecastContractError("distribution must be a ProbabilityDistribution")
    bound = require_finite_number(epsilon, field_name="epsilon")
    if bound <= 0.0 or bound >= 1.0:
        raise ForecastContractError("epsilon must be within (0, 1)")
    observed = _family_outcome_token(distribution, observed_outcome)
    probability = distribution.probabilities[observed]
    safe = max(probability, bound)
    if safe > 1.0:
        safe = 1.0
    return -math.log(safe)


# ---------------------------------------------------------------------------
# Reliability diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastReliabilityBin:
    """One reliability bin for one family/outcome pair.

    ``predicted_mean`` and ``observed_frequency`` are ``None`` when the bin is
    empty (a zero denominator is reported as unavailable, never as zero).
    """

    family: str
    outcome: str
    bin_lower: float
    bin_upper: float
    count: int
    predicted_mean: float | None
    observed_frequency: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "outcome": self.outcome,
            "bin_lower": self.bin_lower,
            "bin_upper": self.bin_upper,
            "count": self.count,
            "predicted_mean": self.predicted_mean,
            "observed_frequency": self.observed_frequency,
        }


def _require_bin_edges(bin_edges: Any) -> tuple[float, ...]:
    if not isinstance(bin_edges, (list, tuple)) or len(bin_edges) < 2:
        raise ForecastContractError(
            "an explicit bin configuration of at least two edges is required"
        )
    edges = tuple(
        require_finite_number(edge, field_name="bin_edge") for edge in bin_edges
    )
    for index, edge in enumerate(edges):
        if edge < 0.0 or edge > 1.0:
            raise ForecastContractError("bin edges must lie within [0, 1]")
        if index and edge <= edges[index - 1]:
            raise ForecastContractError("bin edges must be strictly increasing")
    if edges[0] != 0.0 or edges[-1] != 1.0:
        raise ForecastContractError("bin edges must span [0, 1]")
    return edges


def _bin_index(value: float, edges: tuple[float, ...]) -> int:
    # Right-open bins, last bin closed, so the maximum maps into the final bin.
    for index in range(len(edges) - 1):
        if value < edges[index + 1]:
            return index
    return len(edges) - 2


def reliability_diagnostics(
    examples: Sequence[ForecastEvaluationExample],
    *,
    bin_edges: Any,
) -> tuple[ForecastReliabilityBin, ...]:
    """Deterministic reliability diagnostics for both families.

    An explicit ``bin_edges`` configuration is required; there is no hidden
    default. The result reports the bin count, the mean predicted probability and
    the observed frequency. It is a diagnostic, never a calibration-pass verdict.
    """
    edges = _require_bin_edges(bin_edges)

    def _collect(
        family: DistributionKind, outcome_tokens: tuple[str, ...]
    ) -> list[ForecastReliabilityBin]:
        rows: list[ForecastReliabilityBin] = []
        for outcome in outcome_tokens:
            buckets: list[list[tuple[float, float]]] = [[] for _ in range(len(edges) - 1)]
            for example in examples:
                if example.status is not ForecastStatus.FORECAST:
                    continue
                if family is DistributionKind.ENTRY_EXECUTION:
                    distribution = example.entry_distribution
                    state = example.entry_label_state
                    observed = (
                        None
                        if example.entry_outcome_label is None
                        else example.entry_outcome_label.value
                    )
                else:
                    distribution = example.post_fill_distribution
                    state = example.post_fill_label_state
                    observed = (
                        None
                        if example.post_fill_outcome_label is None
                        else example.post_fill_outcome_label.value
                    )
                if distribution is None or state is not ForecastLabelState.RESOLVED:
                    continue
                predicted = distribution.probabilities[outcome]
                indicator = 1.0 if observed == outcome else 0.0
                buckets[_bin_index(predicted, edges)].append((predicted, indicator))
            for index, bucket in enumerate(buckets):
                if bucket:
                    predicted_mean = sum(item[0] for item in bucket) / len(bucket)
                    observed_frequency = sum(item[1] for item in bucket) / len(bucket)
                else:
                    predicted_mean = None
                    observed_frequency = None
                rows.append(
                    ForecastReliabilityBin(
                        family=family.value,
                        outcome=outcome,
                        bin_lower=edges[index],
                        bin_upper=edges[index + 1],
                        count=len(bucket),
                        predicted_mean=predicted_mean,
                        observed_frequency=observed_frequency,
                    )
                )
        return rows

    return tuple(
        _collect(DistributionKind.ENTRY_EXECUTION, ENTRY_EXECUTION_OUTCOMES)
        + _collect(DistributionKind.POST_FILL_PATH, POST_FILL_PATH_OUTCOMES)
    )


# ---------------------------------------------------------------------------
# Expected-return diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastExpectedReturnDiagnostics:
    """Squared/absolute expected-return error and interval coverage.

    A zero denominator is reported as ``None``, never as zero. These are
    diagnostics only; they never promote a model.
    """

    count: int
    mean_squared_error: float | None
    mean_absolute_error: float | None
    interval_covered: int | None
    interval_total: int | None
    interval_coverage: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mean_squared_error": self.mean_squared_error,
            "mean_absolute_error": self.mean_absolute_error,
            "interval_covered": self.interval_covered,
            "interval_total": self.interval_total,
            "interval_coverage": self.interval_coverage,
        }


def expected_return_diagnostics(
    examples: Sequence[ForecastEvaluationExample],
) -> ForecastExpectedReturnDiagnostics:
    """Expected-return error and uncertainty-interval coverage.

    The realized net return is read from the label directly. A ``TIMEOUT`` path
    outcome is never equated with a negative return: the realized return is
    whatever the label records, which may be positive or negative.
    """
    squared = 0.0
    absolute = 0.0
    count = 0
    covered = 0
    interval_total = 0
    for example in examples:
        if example.status is not ForecastStatus.FORECAST:
            continue
        if example.realized_return_label_state is not ForecastLabelState.RESOLVED:
            continue
        if example.expected_return_unconditional is None or example.realized_net_return is None:
            continue
        error = example.expected_return_unconditional - example.realized_net_return
        squared += error * error
        absolute += abs(error)
        count += 1
        if example.uncertainty is not None:
            interval_total += 1
            if example.uncertainty.contains(example.realized_net_return):
                covered += 1
    return ForecastExpectedReturnDiagnostics(
        count=count,
        mean_squared_error=None if not count else squared / count,
        mean_absolute_error=None if not count else absolute / count,
        interval_covered=None if not interval_total else covered,
        interval_total=None if not interval_total else interval_total,
        interval_coverage=None if not interval_total else covered / interval_total,
    )


# ---------------------------------------------------------------------------
# Sealing, missingness, and the evaluation report
# ---------------------------------------------------------------------------


def count_leakage_violations(
    examples: Sequence[ForecastEvaluationExample],
) -> int:
    """Count examples whose label availability violates the prediction cutoff."""
    return sum(1 for example in examples if example.has_leakage)


def seal_forecast_population(
    examples: Sequence[ForecastEvaluationExample],
) -> tuple[ForecastEvaluationExample, ...]:
    """Validate a sealed population and refuse any future-label leakage.

    Every resolved label must have become available strictly after the prediction
    cutoff; otherwise the example is a leakage defect and the population is
    refused rather than quietly scored.
    """
    sealed = tuple(examples)
    for example in sealed:
        if not isinstance(example, ForecastEvaluationExample):
            raise ForecastContractError(
                "every population member must be a ForecastEvaluationExample"
            )
    violations = count_leakage_violations(sealed)
    if violations:
        raise ForecastContractError(
            f"sealed evaluation rejected {violations} future-label leakage violation(s)"
        )
    return sealed


def missingness_summary(
    examples: Sequence[ForecastEvaluationExample],
) -> dict[str, int]:
    """A deterministic missingness/coverage summary over the full population."""
    summary = {
        "prediction_count": 0,
        "forecast_count": 0,
        "abstention_count": 0,
        "entry_resolved": 0,
        "entry_unresolved": 0,
        "entry_incomplete_coverage": 0,
        "entry_insufficient_evidence": 0,
        "path_resolved": 0,
        "path_unresolved": 0,
        "path_incomplete_coverage": 0,
        "path_insufficient_evidence": 0,
        "realized_return_resolved": 0,
        "realized_return_unresolved": 0,
    }
    for example in examples:
        summary["prediction_count"] += 1
        if example.status is ForecastStatus.FORECAST:
            summary["forecast_count"] += 1
        else:
            summary["abstention_count"] += 1
        summary[f"entry_{example.entry_label_state.value.lower()}"] = (
            summary.get(f"entry_{example.entry_label_state.value.lower()}", 0) + 1
        )
        summary[f"path_{example.post_fill_label_state.value.lower()}"] = (
            summary.get(f"path_{example.post_fill_label_state.value.lower()}", 0) + 1
        )
        summary[f"realized_return_{example.realized_return_label_state.value.lower()}"] = (
            summary.get(
                f"realized_return_{example.realized_return_label_state.value.lower()}", 0
            )
            + 1
        )
    return summary


def _fidelity_breakdown(
    examples: Sequence[ForecastEvaluationExample],
) -> dict[str, int]:
    breakdown = {grade.value: 0 for grade in ForecastFidelityGrade}
    for example in examples:
        breakdown[example.fidelity.value] += 1
    return breakdown


_REPORT_DURABLE_KEYS: tuple[str, ...] = (
    "report_id",
    "report_version",
    "model_artifact_id",
    "population_id",
    "evaluation_start",
    "evaluation_end",
    "prediction_count",
    "forecast_count",
    "abstention_count",
    "resolved_label_count",
    "unresolved_count",
    "incomplete_coverage_count",
    "entry_brier",
    "entry_log_loss",
    "path_brier",
    "path_log_loss",
    "expected_return_mean_squared_error",
    "expected_return_mean_absolute_error",
    "interval_coverage",
    "interval_total",
    "horizon",
    "fidelity_breakdown",
    "missingness",
    "leakage_violations",
    "sealed_evaluation",
    "reliability",
)


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _report_identity_payload(
    *,
    report_version: str,
    model_artifact_id: str,
    population_id: str,
    evaluation_start: datetime,
    evaluation_end: datetime,
    prediction_count: int,
    forecast_count: int,
    abstention_count: int,
    resolved_label_count: int,
    unresolved_count: int,
    incomplete_coverage_count: int,
    entry_brier: float | None,
    entry_log_loss: float | None,
    path_brier: float | None,
    path_log_loss: float | None,
    expected_return_mean_squared_error: float | None,
    expected_return_mean_absolute_error: float | None,
    interval_coverage: float | None,
    interval_total: int | None,
    horizon: ForecastHorizon | None,
    fidelity_breakdown: Mapping[str, int],
    missingness: Mapping[str, int],
    leakage_violations: int,
    sealed_evaluation: bool,
    reliability: Sequence[ForecastReliabilityBin],
) -> dict[str, Any]:
    return {
        "report_version": report_version,
        "model_artifact_id": model_artifact_id,
        "population_id": population_id,
        "evaluation_start": iso_z(evaluation_start, field_name="evaluation_start"),
        "evaluation_end": iso_z(evaluation_end, field_name="evaluation_end"),
        "prediction_count": prediction_count,
        "forecast_count": forecast_count,
        "abstention_count": abstention_count,
        "resolved_label_count": resolved_label_count,
        "unresolved_count": unresolved_count,
        "incomplete_coverage_count": incomplete_coverage_count,
        "entry_brier": entry_brier,
        "entry_log_loss": entry_log_loss,
        "path_brier": path_brier,
        "path_log_loss": path_log_loss,
        "expected_return_mean_squared_error": expected_return_mean_squared_error,
        "expected_return_mean_absolute_error": expected_return_mean_absolute_error,
        "interval_coverage": interval_coverage,
        "interval_total": interval_total,
        "horizon": None if horizon is None else horizon.to_dict(),
        "fidelity_breakdown": dict(fidelity_breakdown),
        "missingness": dict(missingness),
        "leakage_violations": leakage_violations,
        "sealed_evaluation": sealed_evaluation,
        "reliability": [bin_.to_dict() for bin_ in reliability],
    }


@dataclass(frozen=True)
class ForecastEvaluationReport:
    """One immutable, deterministic evaluation report.

    The report binds the population, the artifact, the window, the two families'
    proper scores, the expected-return diagnostics, the reliability diagnostics,
    the fidelity breakdown and the missingness summary. It never emits a
    PASS/FAIL promotion verdict, and its ``FEVAL:`` identity is deterministic:
    no random UUID and no hidden clock takes part.
    """

    report_id: str
    report_version: str
    model_artifact_id: str
    population_id: str
    evaluation_start: datetime
    evaluation_end: datetime
    prediction_count: int
    forecast_count: int
    abstention_count: int
    resolved_label_count: int
    unresolved_count: int
    incomplete_coverage_count: int
    entry_brier: float | None
    entry_log_loss: float | None
    path_brier: float | None
    path_log_loss: float | None
    expected_return_mean_squared_error: float | None
    expected_return_mean_absolute_error: float | None
    interval_coverage: float | None
    interval_total: int | None
    horizon: ForecastHorizon | None
    fidelity_breakdown: Mapping[str, int]
    missingness: Mapping[str, int]
    leakage_violations: int
    sealed_evaluation: bool
    reliability: tuple[ForecastReliabilityBin, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "report_id",
            require_forecast_text(self.report_id, field_name="report_id"),
        )
        object.__setattr__(
            self,
            "report_version",
            require_forecast_text(self.report_version, field_name="report_version"),
        )
        if self.report_version != FORECAST_EVALUATION_VERSION:
            raise ForecastContractError(
                f"report_version is not the ratified {FORECAST_EVALUATION_VERSION}"
            )
        object.__setattr__(
            self,
            "model_artifact_id",
            require_forecast_text(self.model_artifact_id, field_name="model_artifact_id"),
        )
        object.__setattr__(
            self,
            "population_id",
            require_forecast_text(self.population_id, field_name="population_id"),
        )
        start = require_forecast_utc(self.evaluation_start, field_name="evaluation_start")
        end = require_forecast_utc(self.evaluation_end, field_name="evaluation_end")
        if end < start:
            raise ForecastContractError("evaluation_end cannot precede evaluation_start")
        object.__setattr__(self, "evaluation_start", start)
        object.__setattr__(self, "evaluation_end", end)
        for name in (
            "prediction_count",
            "forecast_count",
            "abstention_count",
            "resolved_label_count",
            "unresolved_count",
            "incomplete_coverage_count",
            "leakage_violations",
        ):
            object.__setattr__(
                self,
                name,
                require_non_negative_int(getattr(self, name), field_name=name),
            )
        if self.horizon is not None and not isinstance(self.horizon, ForecastHorizon):
            raise ForecastContractError("horizon must be a ForecastHorizon")
        if not isinstance(self.sealed_evaluation, bool):
            raise ForecastContractError("sealed_evaluation must be a bool")
        if not isinstance(self.fidelity_breakdown, Mapping):
            raise ForecastContractError("fidelity_breakdown must be a mapping")
        if not isinstance(self.missingness, Mapping):
            raise ForecastContractError("missingness must be a mapping")
        for name in (
            "entry_brier",
            "entry_log_loss",
            "path_brier",
            "path_log_loss",
            "expected_return_mean_squared_error",
            "expected_return_mean_absolute_error",
            "interval_coverage",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self, name, require_finite_number(value, field_name=name)
                )
        if self.interval_total is not None:
            object.__setattr__(
                self,
                "interval_total",
                require_non_negative_int(self.interval_total, field_name="interval_total"),
            )
        bins = tuple(self.reliability)
        for bin_ in bins:
            if not isinstance(bin_, ForecastReliabilityBin):
                raise ForecastContractError("reliability must contain reliability bins")
        object.__setattr__(self, "reliability", bins)
        object.__setattr__(
            self, "fidelity_breakdown", {str(k): int(v) for k, v in self.fidelity_breakdown.items()}
        )
        object.__setattr__(
            self, "missingness", {str(k): int(v) for k, v in self.missingness.items()}
        )
        expected = stable_hash(
            FORECAST_EVALUATION_REPORT_ID_PREFIX,
            _report_identity_payload(
                report_version=self.report_version,
                model_artifact_id=self.model_artifact_id,
                population_id=self.population_id,
                evaluation_start=self.evaluation_start,
                evaluation_end=self.evaluation_end,
                prediction_count=self.prediction_count,
                forecast_count=self.forecast_count,
                abstention_count=self.abstention_count,
                resolved_label_count=self.resolved_label_count,
                unresolved_count=self.unresolved_count,
                incomplete_coverage_count=self.incomplete_coverage_count,
                entry_brier=self.entry_brier,
                entry_log_loss=self.entry_log_loss,
                path_brier=self.path_brier,
                path_log_loss=self.path_log_loss,
                expected_return_mean_squared_error=(
                    self.expected_return_mean_squared_error
                ),
                expected_return_mean_absolute_error=(
                    self.expected_return_mean_absolute_error
                ),
                interval_coverage=self.interval_coverage,
                interval_total=self.interval_total,
                horizon=self.horizon,
                fidelity_breakdown=self.fidelity_breakdown,
                missingness=self.missingness,
                leakage_violations=self.leakage_violations,
                sealed_evaluation=self.sealed_evaluation,
                reliability=self.reliability,
            ),
        )
        if self.report_id != expected:
            raise ForecastContractError(
                "report_id does not match its binding; build reports with "
                "build_evaluation_report"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "report_version": self.report_version,
            "model_artifact_id": self.model_artifact_id,
            "population_id": self.population_id,
            "evaluation_start": iso_z(
                self.evaluation_start, field_name="evaluation_start"
            ),
            "evaluation_end": iso_z(self.evaluation_end, field_name="evaluation_end"),
            "prediction_count": self.prediction_count,
            "forecast_count": self.forecast_count,
            "abstention_count": self.abstention_count,
            "resolved_label_count": self.resolved_label_count,
            "unresolved_count": self.unresolved_count,
            "incomplete_coverage_count": self.incomplete_coverage_count,
            "entry_brier": self.entry_brier,
            "entry_log_loss": self.entry_log_loss,
            "path_brier": self.path_brier,
            "path_log_loss": self.path_log_loss,
            "expected_return_mean_squared_error": self.expected_return_mean_squared_error,
            "expected_return_mean_absolute_error": self.expected_return_mean_absolute_error,
            "interval_coverage": self.interval_coverage,
            "interval_total": self.interval_total,
            "horizon": None if self.horizon is None else self.horizon.to_dict(),
            "fidelity_breakdown": dict(self.fidelity_breakdown),
            "missingness": dict(self.missingness),
            "leakage_violations": self.leakage_violations,
            "sealed_evaluation": self.sealed_evaluation,
            "reliability": [bin_.to_dict() for bin_ in self.reliability],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ForecastEvaluationReport":
        """Reconstitute one durable report, re-verifying its deterministic identity."""
        body = _require_mapping(
            raw, field_name="report", expected_keys=_REPORT_DURABLE_KEYS
        )
        raw_bins = body["reliability"]
        if not isinstance(raw_bins, (list, tuple)):
            raise ForecastContractError("report.reliability must be a list")
        bins: list[ForecastReliabilityBin] = []
        for item in raw_bins:
            bin_body = _require_mapping(
                item,
                field_name="reliability_bin",
                expected_keys=(
                    "family",
                    "outcome",
                    "bin_lower",
                    "bin_upper",
                    "count",
                    "predicted_mean",
                    "observed_frequency",
                ),
            )
            bins.append(
                ForecastReliabilityBin(
                    family=bin_body["family"],
                    outcome=bin_body["outcome"],
                    bin_lower=bin_body["bin_lower"],
                    bin_upper=bin_body["bin_upper"],
                    count=bin_body["count"],
                    predicted_mean=bin_body["predicted_mean"],
                    observed_frequency=bin_body["observed_frequency"],
                )
            )
        return cls(
            report_id=body["report_id"],
            report_version=body["report_version"],
            model_artifact_id=body["model_artifact_id"],
            population_id=body["population_id"],
            evaluation_start=require_forecast_utc(
                datetime.fromisoformat(
                    str(body["evaluation_start"]).replace("Z", "+00:00")
                ),
                field_name="evaluation_start",
            ),
            evaluation_end=require_forecast_utc(
                datetime.fromisoformat(
                    str(body["evaluation_end"]).replace("Z", "+00:00")
                ),
                field_name="evaluation_end",
            ),
            prediction_count=body["prediction_count"],
            forecast_count=body["forecast_count"],
            abstention_count=body["abstention_count"],
            resolved_label_count=body["resolved_label_count"],
            unresolved_count=body["unresolved_count"],
            incomplete_coverage_count=body["incomplete_coverage_count"],
            entry_brier=body["entry_brier"],
            entry_log_loss=body["entry_log_loss"],
            path_brier=body["path_brier"],
            path_log_loss=body["path_log_loss"],
            expected_return_mean_squared_error=body[
                "expected_return_mean_squared_error"
            ],
            expected_return_mean_absolute_error=body[
                "expected_return_mean_absolute_error"
            ],
            interval_coverage=body["interval_coverage"],
            interval_total=body["interval_total"],
            horizon=(
                None if body["horizon"] is None else ForecastHorizon.from_dict(body["horizon"])
            ),
            fidelity_breakdown=dict(body["fidelity_breakdown"]),
            missingness=dict(body["missingness"]),
            leakage_violations=body["leakage_violations"],
            sealed_evaluation=body["sealed_evaluation"],
            reliability=tuple(bins),
        )


def build_evaluation_report(
    examples: Sequence[ForecastEvaluationExample],
    *,
    model_artifact_id: str,
    population_id: str,
    evaluation_start: datetime,
    evaluation_end: datetime,
    bin_edges: Any,
    primary_fidelity: Any,
) -> ForecastEvaluationReport:
    """Build one deterministic evaluation report over a sealed population.

    ``primary_fidelity`` is an explicit set of fidelity grades that define the
    primary calibration population; there is no hidden default, so Grade C cases
    can never be silently mixed in as exact. Low-fidelity cases remain visible in
    the counts and the fidelity breakdown. The full population is sealed first,
    so a future-label leakage defect is refused rather than scored.
    """
    if not isinstance(primary_fidelity, (set, frozenset, list, tuple)):
        raise ForecastContractError("primary_fidelity must be an explicit set of grades")
    grades = frozenset(
        require_forecast_enum(
            ForecastFidelityGrade, grade, field_name="primary_fidelity"
        )
        for grade in primary_fidelity
    )
    if not grades:
        raise ForecastContractError("primary_fidelity must not be empty")

    sealed = seal_forecast_population(examples)
    start = require_forecast_utc(evaluation_start, field_name="evaluation_start")
    end = require_forecast_utc(evaluation_end, field_name="evaluation_end")

    primary = tuple(
        example
        for example in sealed
        if example.fidelity in grades and example.status is ForecastStatus.FORECAST
    )

    horizons = {
        example.horizon for example in sealed if example.horizon is not None
    }
    if len(horizons) > 1:
        raise ForecastContractError(
            "a calibration population must share one horizon contract"
        )
    horizon = next(iter(horizons)) if horizons else None

    entry_brier: list[float] = []
    entry_loss: list[float] = []
    path_brier: list[float] = []
    path_loss: list[float] = []
    for example in primary:
        if (
            example.entry_distribution is not None
            and example.entry_label_state is ForecastLabelState.RESOLVED
            and example.entry_outcome_label is not None
        ):
            entry_brier.append(
                brier_score(example.entry_distribution, example.entry_outcome_label)
            )
            entry_loss.append(
                log_loss(example.entry_distribution, example.entry_outcome_label)
            )
        if (
            example.post_fill_distribution is not None
            and example.post_fill_label_state is ForecastLabelState.RESOLVED
            and example.post_fill_outcome_label is not None
        ):
            path_brier.append(
                brier_score(example.post_fill_distribution, example.post_fill_outcome_label)
            )
            path_loss.append(
                log_loss(example.post_fill_distribution, example.post_fill_outcome_label)
            )

    expected_return = expected_return_diagnostics(primary)
    reliability = reliability_diagnostics(primary, bin_edges=bin_edges)
    fidelity = _fidelity_breakdown(sealed)
    missingness = missingness_summary(sealed)

    resolved = sum(
        1 for example in sealed if example.entry_label_state is ForecastLabelState.RESOLVED
    )
    unresolved = sum(
        1 for example in sealed if example.entry_label_state is ForecastLabelState.UNRESOLVED
    )
    incomplete = sum(
        1
        for example in sealed
        if ForecastLabelState.INCOMPLETE_COVERAGE
        in (
            example.entry_label_state,
            example.post_fill_label_state,
        )
    )

    report_id = stable_hash(
        FORECAST_EVALUATION_REPORT_ID_PREFIX,
        _report_identity_payload(
            report_version=FORECAST_EVALUATION_VERSION,
            model_artifact_id=model_artifact_id,
            population_id=population_id,
            evaluation_start=start,
            evaluation_end=end,
            prediction_count=len(sealed),
            forecast_count=sum(
                1 for example in sealed if example.status is ForecastStatus.FORECAST
            ),
            abstention_count=sum(
                1
                for example in sealed
                if example.status is ForecastStatus.INSUFFICIENT_EVIDENCE
            ),
            resolved_label_count=resolved,
            unresolved_count=unresolved,
            incomplete_coverage_count=incomplete,
            entry_brier=_mean(entry_brier),
            entry_log_loss=_mean(entry_loss),
            path_brier=_mean(path_brier),
            path_log_loss=_mean(path_loss),
            expected_return_mean_squared_error=expected_return.mean_squared_error,
            expected_return_mean_absolute_error=expected_return.mean_absolute_error,
            interval_coverage=expected_return.interval_coverage,
            interval_total=expected_return.interval_total,
            horizon=horizon,
            fidelity_breakdown=fidelity,
            missingness=missingness,
            leakage_violations=count_leakage_violations(sealed),
            sealed_evaluation=True,
            reliability=reliability,
        ),
    )
    return ForecastEvaluationReport(
        report_id=report_id,
        report_version=FORECAST_EVALUATION_VERSION,
        model_artifact_id=model_artifact_id,
        population_id=population_id,
        evaluation_start=start,
        evaluation_end=end,
        prediction_count=len(sealed),
        forecast_count=sum(
            1 for example in sealed if example.status is ForecastStatus.FORECAST
        ),
        abstention_count=sum(
            1 for example in sealed if example.status is ForecastStatus.INSUFFICIENT_EVIDENCE
        ),
        resolved_label_count=resolved,
        unresolved_count=unresolved,
        incomplete_coverage_count=incomplete,
        entry_brier=_mean(entry_brier),
        entry_log_loss=_mean(entry_loss),
        path_brier=_mean(path_brier),
        path_log_loss=_mean(path_loss),
        expected_return_mean_squared_error=expected_return.mean_squared_error,
        expected_return_mean_absolute_error=expected_return.mean_absolute_error,
        interval_coverage=expected_return.interval_coverage,
        interval_total=expected_return.interval_total,
        horizon=horizon,
        fidelity_breakdown=fidelity,
        missingness=missingness,
        leakage_violations=count_leakage_violations(sealed),
        sealed_evaluation=True,
        reliability=reliability,
    )


__all__ = [
    "EVALUATION_LOG_EPSILON",
    "FORECAST_EVALUATION_REPORT_ID_PREFIX",
    "ForecastEvaluationExample",
    "ForecastEvaluationReport",
    "ForecastExpectedReturnDiagnostics",
    "ForecastFidelityGrade",
    "ForecastLabelState",
    "ForecastReliabilityBin",
    "brier_score",
    "build_evaluation_report",
    "count_leakage_violations",
    "expected_return_diagnostics",
    "log_loss",
    "missingness_summary",
    "reliability_diagnostics",
    "seal_forecast_population",
]
