"""Paired champion/challenger evidence evaluation for O'Pip Wave A2."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import math
import statistics
from types import MappingProxyType
from typing import Any, Iterable

from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import TemporalIntegrityError, require_utc


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
        """Return as dict."""
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
        """Return  post init  ."""
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
        """Return as dict."""
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
    cohort: str
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
        """Return as dict."""
        return {
            "support": self.support.value,
            "cohort": self.cohort,
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
    """Return metric."""
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
    """Return rate."""
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
    """Return tail losses."""
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
    """Return arm."""
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
    """Return delta."""
    if a.mean is None or b.mean is None:
        return None
    return b.mean - a.mean


def evaluate_champion_challenger(
    samples: Iterable[PairedEvaluationSample],
    *,
    minimum_support: int = 30,
) -> ChampionChallengerEvaluation:
    """Return evaluate champion challenger."""
    if minimum_support < 2:
        raise ValueError("minimum_support must be >= 2")
    rows = tuple(samples)
    ids = [row.sample_id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("paired evaluation sample ids must be unique")
    cohorts = {str(row.cohort or "").strip() for row in rows}
    if "" in cohorts:
        raise ValueError("paired evaluation cohort is required")
    if len(cohorts) > 1:
        raise ValueError("paired evaluation cannot aggregate mixed cohorts")
    cohort = next(iter(cohorts), "UNSPECIFIED")
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
        cohort=cohort,
        paired_samples=len(rows),
        minimum_support=minimum_support,
        champion=champion,
        challenger=challenger,
        expectancy_delta=_delta(champion.net_expectancy, challenger.net_expectancy),
        precision_delta=_delta(champion.precision, challenger.precision),
        recall_delta=_delta(champion.recall, challenger.recall),
    )


# R5-E prospective expectation capture (SC-10)
#
# The frozen SC-10 record this module did not yet carry: one immutable
# prospective expectation per entity, policy, horizon and revision, frozen at or
# before its cutoff and before any outcome exists for it. It is evidence and
# nothing else: it holds no order, no durable write, no clock of its own and no
# authority, and no runtime root consumes it.

#: Identity domain of one frozen prospective expectation.
EXPECTATION_ID_PREFIX = "PEXP"

#: Hash domain of an expectation's expected payload. Deliberately distinct from
#: the expectation identity, so a content hash can never be mistaken for it.
EXPECTED_PAYLOAD_HASH_PREFIX = "PEXPP"

EXPECTATION_SCHEMA_VERSION = 1

#: The frozen SC-10 expectation keys in their frozen order.
EXPECTATION_RECORD_KEYS: tuple[str, ...] = (
    "grain",
    "population",
    "expected_payload",
    "expected_hash",
    "cutoff",
    "maturity_at",
    "recorded_at",
    "baseline",
    "measure_kinds",
    "correction_of",
    "schema_version",
    "authoritative",
    "can_promote",
    "expectation_id",
)


class ExpectationError(ValueError):
    """A prospective expectation, its attachment or its measures broke a law."""


class Population(str, Enum):
    """The retained populations of ``LE-01`` at a deduplicated episode grain."""

    TAKEN = "TAKEN"
    REJECTED = "REJECTED"
    MISSED = "MISSED"
    EXPIRED = "EXPIRED"
    NO_FILL = "NO_FILL"
    NO_TRADE = "NO_TRADE"


#: The closed population vocabulary, in its declared order.
POPULATIONS: tuple[str, ...] = tuple(population.value for population in Population)


class MeasureKind(str, Enum):
    """The separate measures of ``LE-02``. They are never summed together."""

    REALIZED_PAPER_OUTCOME = "REALIZED_PAPER_OUTCOME"
    DECLARED_POLICY_COUNTERFACTUAL = "DECLARED_POLICY_COUNTERFACTUAL"
    HINDSIGHT_EXCURSION = "HINDSIGHT_EXCURSION"


#: The closed measure vocabulary, in its declared order.
MEASURE_KINDS: tuple[str, ...] = tuple(kind.value for kind in MeasureKind)


class OutcomeStatus(str, Enum):
    """The closed outcome status vocabulary of ``LE-01``."""

    PENDING = "PENDING"
    MATURED = "MATURED"
    CENSORED = "CENSORED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class OutcomeStatusReason(str, Enum):
    """The explicit reasons an outcome status can carry. Every status needs one."""

    HORIZON_NOT_REACHED = "HORIZON_NOT_REACHED"
    MATURED_WITH_FACTS = "MATURED_WITH_FACTS"
    CENSORED_BY_WINDOW_END = "CENSORED_BY_WINDOW_END"
    CENSORED_BY_DATA_GAP = "CENSORED_BY_DATA_GAP"
    CENSORED_BY_IMMATURE_WINDOW = "CENSORED_BY_IMMATURE_WINDOW"
    NO_OUTCOME_FACTS = "NO_OUTCOME_FACTS"
    COVERAGE_MISSING = "COVERAGE_MISSING"


#: Which reasons belong to which status. A reason is valid for exactly one
#: status, so a favourable status can never borrow a censoring reason.
OUTCOME_STATUS_REASONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "PENDING": (OutcomeStatusReason.HORIZON_NOT_REACHED.value,),
        "MATURED": (OutcomeStatusReason.MATURED_WITH_FACTS.value,),
        "CENSORED": (
            OutcomeStatusReason.CENSORED_BY_WINDOW_END.value,
            OutcomeStatusReason.CENSORED_BY_DATA_GAP.value,
            OutcomeStatusReason.CENSORED_BY_IMMATURE_WINDOW.value,
        ),
        "INSUFFICIENT_EVIDENCE": (
            OutcomeStatusReason.NO_OUTCOME_FACTS.value,
            OutcomeStatusReason.COVERAGE_MISSING.value,
        ),
    }
)


def _expectation_text(value: Any, *, field_name: str) -> str:
    """A required non-empty text field of an expectation record."""

    if not isinstance(value, str) or not value.strip():
        raise ExpectationError(f"{field_name} is required")
    return value.strip()


def _expectation_number(
    value: Any,
    *,
    field_name: str,
    minimum: float | None = None,
    strictly_greater: bool = False,
) -> float:
    """A required finite number, optionally above a declared lower bound."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExpectationError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ExpectationError(f"{field_name} must be finite")
    if minimum is not None:
        if strictly_greater:
            if number <= minimum:
                raise ExpectationError(f"{field_name} must be greater than {minimum}")
        elif number < minimum:
            raise ExpectationError(f"{field_name} must be at least {minimum}")
    return number


def _expectation_utc(value: Any, *, field_name: str) -> datetime:
    """A required instant on the UTC clock the canonical contracts use."""

    try:
        return require_utc(value, field_name=field_name)
    except TemporalIntegrityError as exc:
        raise ExpectationError(str(exc)) from exc


def _expectation_enum(value: Any, enum_type, *, field_name: str):
    """A required member of a closed vocabulary. A near-miss token is refused."""

    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            raise ExpectationError(
                f"{field_name} is not a known token: {value!r}"
            ) from exc
    raise ExpectationError(
        f"{field_name} must be one of "
        f"{tuple(member.value for member in enum_type)}"
    )


@dataclass(frozen=True)
class ExpectationGrain:
    """The deduplicated episode or intent grain one expectation belongs to."""

    grain_id: str
    entity: str
    policy_id: str
    horizon: str
    revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "grain_id", _expectation_text(self.grain_id, field_name="grain_id"))
        object.__setattr__(self, "entity", _expectation_text(self.entity, field_name="entity"))
        object.__setattr__(self, "policy_id", _expectation_text(self.policy_id, field_name="policy_id"))
        object.__setattr__(self, "horizon", _expectation_text(self.horizon, field_name="horizon"))
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ExpectationError("revision must be a positive integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "grain_id": self.grain_id,
            "entity": self.entity,
            "policy_id": self.policy_id,
            "horizon": self.horizon,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class ExpectationBaseline:
    """The frozen declared-policy baseline an expectation is measured against."""

    baseline_id: str
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "baseline_id", _expectation_text(self.baseline_id, field_name="baseline_id"))
        object.__setattr__(self, "description", _expectation_text(self.description, field_name="description"))

    def as_dict(self) -> dict[str, Any]:
        return {"baseline_id": self.baseline_id, "description": self.description}


@dataclass(frozen=True)
class ExpectedOutcome:
    """The immutable expected payload of one prospective expectation."""

    expected_net_return: float
    expected_duration_seconds: float
    expected_cost_dollars: float
    expected_horizon_seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expected_net_return",
            _expectation_number(self.expected_net_return, field_name="expected_net_return"),
        )
        object.__setattr__(
            self,
            "expected_duration_seconds",
            _expectation_number(
                self.expected_duration_seconds,
                field_name="expected_duration_seconds",
                minimum=0.0,
            ),
        )
        object.__setattr__(
            self,
            "expected_cost_dollars",
            _expectation_number(
                self.expected_cost_dollars, field_name="expected_cost_dollars", minimum=0.0
            ),
        )
        object.__setattr__(
            self,
            "expected_horizon_seconds",
            _expectation_number(
                self.expected_horizon_seconds,
                field_name="expected_horizon_seconds",
                minimum=0.0,
                strictly_greater=True,
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "expected_net_return": self.expected_net_return,
            "expected_duration_seconds": self.expected_duration_seconds,
            "expected_cost_dollars": self.expected_cost_dollars,
            "expected_horizon_seconds": self.expected_horizon_seconds,
        }


def expected_payload_hash(payload: ExpectedOutcome) -> str:
    """The content hash of an expected payload.

    It is a pure function of the payload, so the same expectation always carries
    the same hash and any mutation changes it.
    """

    if not isinstance(payload, ExpectedOutcome):
        raise ExpectationError("expected_payload must be an ExpectedOutcome")
    return stable_hash(EXPECTED_PAYLOAD_HASH_PREFIX, payload.as_dict())


@dataclass(frozen=True)
class OutcomeFacts:
    """The committed outcome facts of one matured expectation."""

    fill_count: int
    net_return: float
    duration_seconds: float
    cost_dollars: float

    def __post_init__(self) -> None:
        if isinstance(self.fill_count, bool) or not isinstance(self.fill_count, int):
            raise ExpectationError("fill_count must be an integer")
        if self.fill_count < 1:
            raise ExpectationError("an outcome attachment must carry at least one committed fill")
        object.__setattr__(
            self, "net_return", _expectation_number(self.net_return, field_name="net_return")
        )
        object.__setattr__(
            self,
            "duration_seconds",
            _expectation_number(
                self.duration_seconds, field_name="duration_seconds", minimum=0.0
            ),
        )
        object.__setattr__(
            self,
            "cost_dollars",
            _expectation_number(
                self.cost_dollars, field_name="cost_dollars", minimum=0.0
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "fill_count": self.fill_count,
            "net_return": self.net_return,
            "duration_seconds": self.duration_seconds,
            "cost_dollars": self.cost_dollars,
        }


@dataclass(frozen=True)
class MeasureRecord:
    """One measure of exactly one kind at exactly one grain.

    A record cannot carry two kinds: that is how the realized, counterfactual and
    hindsight populations would otherwise be summed into each other.
    """

    kind: MeasureKind
    grain_id: str
    value: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "kind", _expectation_enum(self.kind, MeasureKind, field_name="kind")
        )
        object.__setattr__(
            self, "grain_id", _expectation_text(self.grain_id, field_name="grain_id")
        )
        object.__setattr__(
            self, "value", _expectation_number(self.value, field_name="value")
        )

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "grain_id": self.grain_id, "value": self.value}


@dataclass(frozen=True)
class ExpectationAttachment:
    """The outcome facts attached to one frozen expectation.

    The attachment is a separate record: it never rewrites the expectation it
    measures, so the expected payload and its hash stay exactly as frozen.
    """

    expectation_id: str
    grain: ExpectationGrain
    facts: OutcomeFacts
    recorded_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expectation_id",
            _expectation_text(self.expectation_id, field_name="expectation_id"),
        )
        if not isinstance(self.grain, ExpectationGrain):
            raise ExpectationError("grain must be an ExpectationGrain")
        if not isinstance(self.facts, OutcomeFacts):
            raise ExpectationError("facts must be an OutcomeFacts")
        object.__setattr__(
            self,
            "recorded_at",
            _expectation_utc(self.recorded_at, field_name="recorded_at"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "expectation_id": self.expectation_id,
            "grain": self.grain.as_dict(),
            "facts": self.facts.as_dict(),
            "recorded_at": iso_z(self.recorded_at, field_name="recorded_at"),
        }


@dataclass(frozen=True)
class OutcomeResolution:
    """One resolved outcome status with its explicit reasons."""

    status: OutcomeStatus
    reasons: tuple[OutcomeStatusReason, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reasons": [reason.value for reason in self.reasons],
        }


def _expectation_mapping(
    raw: Any, *, field_name: str, expected_keys: Sequence[str]
) -> dict[str, Any]:
    """Read one durable mapping whose key set must be exactly the frozen one."""

    if not isinstance(raw, Mapping):
        raise ExpectationError(f"{field_name} must be a mapping")
    expected = set(expected_keys)
    if set(raw) != expected:
        missing = sorted(expected - set(raw))
        extra = sorted(set(raw) - expected)
        raise ExpectationError(
            f"{field_name} keys do not match the frozen record: "
            f"missing={missing} extra={extra}"
        )
    return dict(raw)


def _expectation_instant_from_text(value: Any, *, field_name: str) -> datetime:
    """Read back one canonical UTC instant from its durable text form."""

    text = _expectation_text(value, field_name=field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExpectationError(
            f"{field_name} is not a canonical instant: {text!r}"
        ) from exc
    return _expectation_utc(parsed, field_name=field_name)


@dataclass(frozen=True)
class ProspectiveExpectation:
    """One immutable prospective expectation (the frozen ``SC-10`` record).

    It is captured for one entity, policy, horizon and revision of one
    deduplicated episode grain, at or before its cutoff, before any outcome
    exists. It carries no authority: it cannot promote policy, and its expected
    payload and hash can never be rewritten, only superseded through the recorded
    correction lineage.
    """

    grain: ExpectationGrain
    population: Population
    expected_payload: ExpectedOutcome
    expected_hash: str
    cutoff: datetime
    maturity_at: datetime
    recorded_at: datetime
    baseline: ExpectationBaseline
    measure_kinds: tuple[MeasureKind, ...]
    correction_of: str | None = None
    schema_version: int = EXPECTATION_SCHEMA_VERSION
    authoritative: bool = False
    can_promote: bool = False
    expectation_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.grain, ExpectationGrain):
            raise ExpectationError("grain must be an ExpectationGrain")
        if not isinstance(self.baseline, ExpectationBaseline):
            raise ExpectationError("baseline must be an ExpectationBaseline")
        if not isinstance(self.expected_payload, ExpectedOutcome):
            raise ExpectationError("expected_payload must be an ExpectedOutcome")

        object.__setattr__(
            self,
            "population",
            _expectation_enum(self.population, Population, field_name="population"),
        )

        cutoff = _expectation_utc(self.cutoff, field_name="cutoff")
        maturity_at = _expectation_utc(self.maturity_at, field_name="maturity_at")
        recorded_at = _expectation_utc(self.recorded_at, field_name="recorded_at")
        if recorded_at > cutoff:
            raise ExpectationError(
                "an expectation cannot be frozen after its own cutoff"
            )
        if maturity_at <= cutoff:
            raise ExpectationError("the maturity horizon must follow the cutoff")
        object.__setattr__(self, "cutoff", cutoff)
        object.__setattr__(self, "maturity_at", maturity_at)
        object.__setattr__(self, "recorded_at", recorded_at)

        raw_kinds = self.measure_kinds
        if isinstance(raw_kinds, (str, MeasureKind)) or not isinstance(
            raw_kinds, Sequence
        ):
            raise ExpectationError("measure_kinds must be a sequence of measure kinds")
        kinds: list[MeasureKind] = []
        for item in raw_kinds:
            kind = _expectation_enum(item, MeasureKind, field_name="measure_kinds")
            if kind in kinds:
                raise ExpectationError("measure kinds cannot repeat")
            kinds.append(kind)
        if not kinds:
            raise ExpectationError("an expectation must declare at least one measure kind")
        object.__setattr__(self, "measure_kinds", tuple(kinds))

        expected_hash = _expectation_text(self.expected_hash, field_name="expected_hash")
        if expected_hash != expected_payload_hash(self.expected_payload):
            raise ExpectationError(
                "expected_hash does not match the expected payload it describes"
            )
        object.__setattr__(self, "expected_hash", expected_hash)

        if self.correction_of is not None:
            correction = _expectation_text(self.correction_of, field_name="correction_of")
            if not correction.startswith(f"{EXPECTATION_ID_PREFIX}:"):
                raise ExpectationError("correction_of must name an expectation")
            object.__setattr__(self, "correction_of", correction)

        if self.schema_version != EXPECTATION_SCHEMA_VERSION:
            raise ExpectationError(
                f"unsupported expectation schema version: {self.schema_version!r}"
            )
        if self.authoritative is not False:
            raise ExpectationError("a frozen expectation is never authoritative")
        if self.can_promote is not False:
            raise ExpectationError("a frozen expectation cannot promote a policy")

        expected_id = stable_hash(EXPECTATION_ID_PREFIX, self._identity_payload())
        if self.expectation_id == "":
            object.__setattr__(self, "expectation_id", expected_id)
        else:
            given = _expectation_text(self.expectation_id, field_name="expectation_id")
            if given != expected_id:
                raise ExpectationError(
                    "expectation_id does not match its content; a forged or stale "
                    "expectation fails closed"
                )
            object.__setattr__(self, "expectation_id", given)

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "grain": self.grain.as_dict(),
            "population": self.population.value,
            "expected_hash": self.expected_hash,
            "cutoff": iso_z(self.cutoff, field_name="cutoff"),
            "maturity_at": iso_z(self.maturity_at, field_name="maturity_at"),
            "baseline": self.baseline.as_dict(),
            "measure_kinds": [kind.value for kind in self.measure_kinds],
            "correction_of": self.correction_of,
            "schema_version": self.schema_version,
        }

    def as_dict(self) -> dict[str, Any]:
        """The durable payload: the frozen ``SC-10`` keys in their frozen order."""

        return {
            "grain": self.grain.as_dict(),
            "population": self.population.value,
            "expected_payload": self.expected_payload.as_dict(),
            "expected_hash": self.expected_hash,
            "cutoff": iso_z(self.cutoff, field_name="cutoff"),
            "maturity_at": iso_z(self.maturity_at, field_name="maturity_at"),
            "recorded_at": iso_z(self.recorded_at, field_name="recorded_at"),
            "baseline": self.baseline.as_dict(),
            "measure_kinds": [kind.value for kind in self.measure_kinds],
            "correction_of": self.correction_of,
            "schema_version": self.schema_version,
            "authoritative": self.authoritative,
            "can_promote": self.can_promote,
            "expectation_id": self.expectation_id,
        }

    def to_dict(self) -> dict[str, Any]:
        """``as_dict()``, so one payload shape serves capture and replay."""

        return self.as_dict()

    @classmethod
    def from_dict(cls, raw: Any) -> "ProspectiveExpectation":
        """Rebuild a frozen expectation from its durable payload.

        The frozen key set is checked exactly, every nested record revalidates its
        own invariants, and the identity and the content hash are recomputed, so a
        payload with a dropped key, an extra key, a mutated payload or a forged
        identity fails closed rather than being loaded and trusted.
        """

        body = _expectation_mapping(
            raw, field_name="expectation", expected_keys=EXPECTATION_RECORD_KEYS
        )
        grain = _expectation_mapping(
            body["grain"],
            field_name="grain",
            expected_keys=("grain_id", "entity", "policy_id", "horizon", "revision"),
        )
        baseline = _expectation_mapping(
            body["baseline"],
            field_name="baseline",
            expected_keys=("baseline_id", "description"),
        )
        payload = _expectation_mapping(
            body["expected_payload"],
            field_name="expected_payload",
            expected_keys=(
                "expected_net_return",
                "expected_duration_seconds",
                "expected_cost_dollars",
                "expected_horizon_seconds",
            ),
        )
        kinds = body["measure_kinds"]
        if not isinstance(kinds, Sequence) or isinstance(kinds, str):
            raise ExpectationError("measure_kinds must be a sequence of measure kinds")
        return cls(
            grain=ExpectationGrain(**grain),
            population=body["population"],
            expected_payload=ExpectedOutcome(**payload),
            expected_hash=body["expected_hash"],
            cutoff=_expectation_instant_from_text(body["cutoff"], field_name="cutoff"),
            maturity_at=_expectation_instant_from_text(
                body["maturity_at"], field_name="maturity_at"
            ),
            recorded_at=_expectation_instant_from_text(
                body["recorded_at"], field_name="recorded_at"
            ),
            baseline=ExpectationBaseline(**baseline),
            measure_kinds=tuple(kinds),
            correction_of=body["correction_of"],
            schema_version=body["schema_version"],
            authoritative=body["authoritative"],
            can_promote=body["can_promote"],
            expectation_id=body["expectation_id"],
        )


def freeze_expectation(
    *,
    grain: ExpectationGrain,
    expected_payload: ExpectedOutcome,
    cutoff: datetime,
    maturity_at: datetime,
    recorded_at: datetime,
    population: Population,
    baseline: ExpectationBaseline,
    measure_kinds: Sequence[MeasureKind],
    correction_of: str | None = None,
    expectation_id: str = "",
) -> ProspectiveExpectation:
    """Freeze one prospective expectation before any outcome exists for it.

    The expected payload is hashed here, the cutoff and the maturity horizon are
    recorded with it, and the record is built in one step, so a caller cannot
    capture an expectation whose payload and hash disagree or whose recording
    falls after its own cutoff.
    """

    if not isinstance(expected_payload, ExpectedOutcome):
        raise ExpectationError("expected_payload must be an ExpectedOutcome")
    return ProspectiveExpectation(
        grain=grain,
        population=population,
        expected_payload=expected_payload,
        expected_hash=expected_payload_hash(expected_payload),
        cutoff=cutoff,
        maturity_at=maturity_at,
        recorded_at=recorded_at,
        baseline=baseline,
        measure_kinds=measure_kinds,
        correction_of=correction_of,
        expectation_id=expectation_id,
    )


def attach_outcome(
    expectation: ProspectiveExpectation,
    facts: OutcomeFacts | None,
    *,
    recorded_at: datetime,
) -> ExpectationAttachment:
    """Attach committed outcome facts to a frozen expectation.

    The attachment is legal only after the expectation's cutoff and at or after
    its maturity horizon, and it carries at least one committed fill. It returns a
    separate record: the frozen expectation is never rewritten, so a refused
    attachment leaves it exactly as it was.
    """

    if not isinstance(expectation, ProspectiveExpectation):
        raise ExpectationError("expectation must be a ProspectiveExpectation")
    if not isinstance(facts, OutcomeFacts):
        raise ExpectationError("an outcome attachment requires committed outcome facts")
    moment = _expectation_utc(recorded_at, field_name="recorded_at")
    if moment <= expectation.cutoff:
        raise ExpectationError(
            "an outcome cannot be attached at or before the expectation's cutoff"
        )
    if moment < expectation.maturity_at:
        raise ExpectationError(
            "an outcome cannot be attached before the expectation's maturity horizon"
        )
    return ExpectationAttachment(
        expectation_id=expectation.expectation_id,
        grain=expectation.grain,
        facts=facts,
        recorded_at=moment,
    )


def _expectation_reasons(reasons: Any) -> tuple[OutcomeStatusReason, ...]:
    """A required sequence of explicit status reasons, without repeats."""

    if isinstance(reasons, (str, OutcomeStatusReason)) or not isinstance(
        reasons, Sequence
    ):
        raise ExpectationError("reasons must be a sequence of status reasons")
    found: list[OutcomeStatusReason] = []
    for item in reasons:
        reason = _expectation_enum(item, OutcomeStatusReason, field_name="reasons")
        if reason in found:
            raise ExpectationError("status reasons cannot repeat")
        found.append(reason)
    return tuple(found)


def _ordered_reasons(
    reasons: Iterable[OutcomeStatusReason],
) -> tuple[OutcomeStatusReason, ...]:
    """Order reasons by the frozen vocabulary, so two runs report the same tuple."""

    wanted = set(reasons)
    return tuple(reason for reason in OutcomeStatusReason if reason in wanted)


def resolve_outcome_status(
    expectation: ProspectiveExpectation,
    *,
    evaluated_at: datetime | None = None,
    attachment: ExpectationAttachment | None = None,
    coverage: float | None = None,
    reasons: Sequence[OutcomeStatusReason] = (),
    censored: OutcomeStatusReason | None = None,
) -> OutcomeResolution:
    """Resolve the closed outcome status of one frozen expectation.

    An immature horizon is never *matured*: it is pending before the horizon and
    censored once facts exist for a horizon that has not been reached. Maturity
    additionally requires a positive coverage figure, so a missing or empty
    measurement is reported as insufficient evidence rather than as a result. An
    explicit censoring reason censors the outcome outright, and every reason must
    belong to the status it is reported with.
    """

    if not isinstance(expectation, ProspectiveExpectation):
        raise ExpectationError("expectation must be a ProspectiveExpectation")
    if evaluated_at is None:
        raise ExpectationError("an outcome status needs the moment it is evaluated at")
    moment = _expectation_utc(evaluated_at, field_name="evaluated_at")
    if attachment is not None and not isinstance(attachment, ExpectationAttachment):
        raise ExpectationError("attachment must be an ExpectationAttachment")
    extras = _expectation_reasons(reasons)

    if censored is not None:
        censoring = _expectation_enum(
            censored, OutcomeStatusReason, field_name="censored"
        )
        if censoring.value not in OUTCOME_STATUS_REASONS[OutcomeStatus.CENSORED.value]:
            raise ExpectationError(
                "only an explicit censoring reason can censor an outcome"
            )
        return OutcomeResolution(
            status=OutcomeStatus.CENSORED,
            reasons=_ordered_reasons((censoring,)),
        )

    if attachment is None:
        if moment < expectation.maturity_at:
            status = OutcomeStatus.PENDING
            derived: tuple[OutcomeStatusReason, ...] = (
                OutcomeStatusReason.HORIZON_NOT_REACHED,
            )
        else:
            status = OutcomeStatus.INSUFFICIENT_EVIDENCE
            derived = (OutcomeStatusReason.NO_OUTCOME_FACTS,)
    elif moment < expectation.maturity_at:
        status = OutcomeStatus.CENSORED
        derived = (OutcomeStatusReason.CENSORED_BY_IMMATURE_WINDOW,)
    elif coverage is None:
        status = OutcomeStatus.INSUFFICIENT_EVIDENCE
        derived = (OutcomeStatusReason.COVERAGE_MISSING,)
    else:
        measured = _expectation_number(coverage, field_name="coverage", minimum=0.0)
        if measured <= 0.0:
            status = OutcomeStatus.INSUFFICIENT_EVIDENCE
            derived = (OutcomeStatusReason.COVERAGE_MISSING,)
        else:
            status = OutcomeStatus.MATURED
            derived = (OutcomeStatusReason.MATURED_WITH_FACTS,)

    allowed = OUTCOME_STATUS_REASONS[status.value]
    for reason in extras:
        if reason.value not in allowed:
            raise ExpectationError(
                f"the reason {reason.value} does not belong to {status.value}"
            )

    return OutcomeResolution(status=status, reasons=_ordered_reasons(derived + extras))


def assert_expectation_record_keys(expectation: ProspectiveExpectation) -> tuple[str, ...]:
    """Return the payload key order after proving it is exactly the frozen record."""

    if not isinstance(expectation, ProspectiveExpectation):
        raise ExpectationError("expectation must be a ProspectiveExpectation")
    keys = tuple(expectation.as_dict())
    if keys != EXPECTATION_RECORD_KEYS or len(keys) != len(set(keys)):
        raise ExpectationError("the expectation payload is not the frozen SC-10 record")
    return keys


def assert_expectation_populations_are_separate(
    expectations: Iterable[ProspectiveExpectation],
    *,
    required: Sequence[str] = POPULATIONS,
) -> tuple[str, ...]:
    """Prove one expectation per population and grain, with none dropped.

    ``required`` is the retention the span declares it covers. A population that
    the span declared and did not capture is a dropped population and fails
    closed; a population the span never declared is simply absent.
    """

    rows = tuple(expectations)
    if not rows:
        raise ExpectationError("a decision span must retain at least one expectation")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, ProspectiveExpectation):
            raise ExpectationError("expectations must be ProspectiveExpectation records")
        key = (row.grain.grain_id, row.population.value)
        if key in seen:
            raise ExpectationError(
                "one grain and population is captured once: a duplicate expectation "
                "is refused"
            )
        seen.add(key)

    present = {
        population.value
        for population in Population
        if any(row.population is population for row in rows)
    }
    declared = tuple(_expectation_text(item, field_name="required") for item in required)
    missing = [name for name in declared if name not in present]
    if missing:
        raise ExpectationError(f"a declared population was dropped: {missing}")
    return tuple(name for name in POPULATIONS if name in present)


def assert_measures_stay_separate(measures: Iterable[MeasureRecord]) -> tuple[str, ...]:
    """Prove every measure keeps its own kind in its own grain slot."""

    rows = tuple(measures)
    if not rows:
        raise ExpectationError("a measure set cannot be empty")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, MeasureRecord):
            raise ExpectationError("measures must be MeasureRecord rows")
        key = (row.kind.value, row.grain_id)
        if key in seen:
            raise ExpectationError(
                "one kind is measured once per grain: a duplicate measure is refused"
            )
        seen.add(key)
    present = {row.kind for row in rows}
    return tuple(kind.value for kind in MeasureKind if kind in present)


def total_for_kind(measures: Iterable[MeasureRecord], *, kind: MeasureKind) -> float:
    """Total exactly one measure kind.

    Realized paper outcomes, declared-policy counterfactuals and hindsight
    excursions are never summed into one number, so a request that mixes kinds is
    refused instead of being totalled.
    """

    wanted = _expectation_enum(kind, MeasureKind, field_name="kind")
    rows = tuple(measures)
    if not rows:
        raise ExpectationError("a measure total needs at least one measure")
    for row in rows:
        if not isinstance(row, MeasureRecord):
            raise ExpectationError("measures must be MeasureRecord rows")
        if row.kind is not wanted:
            raise ExpectationError(
                "measures of different kinds are never summed: request one kind at "
                "a time"
            )
    return math.fsum(row.value for row in rows)
