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


# R5-F prospective outcome integration (SC-10, SC-12)
#
# The learning half of the chain the R5-E slice opened: a frozen prospective
# expectation is joined to the committed canonical terminal paper outcome the
# canonical writer already owns, and the join is turned into economic truth,
# capital time, a named root cause, a named consumption disposition and a paired
# prospective champion/challenger comparison.
#
# It stays evidence and nothing else, exactly as ``SC-10`` freezes it: it holds no
# order, no clock of its own, no durable write, no storage handle, no venue
# client, no authority and no runtime consumer. The canonical outcome is supplied
# by the caller as the validated payload of one committed terminal outcome that
# was read back through the read-only reader, and the committed fill count is
# supplied by the caller's committed execution evidence rather than being
# inferred, so no figure here is guessed.
#
# Money is in quote-currency units, duration is in seconds and every share is
# dimensionless; the unit of each reported figure is declared on its own record.

#: The absolute window within which the committed net of a canonical outcome must
#: equal its own gross minus its fees. It matches the canonical contract's own
#: consistency window, so a cost deducted twice is detected rather than divided.
CANONICAL_ECONOMICS_TOLERANCE = 1e-6

#: The canonical terminal status whose outcome asserts realized economics. The
#: other terminal statuses assert nothing realised or nothing knowable, so they
#: yield no facts rather than zeros.
CANONICAL_CLOSED_STATUS = "CLOSED"

#: The exact evidence keys one ex-ante decision capture must carry.
DECISION_EVIDENCE_KEYS: tuple[str, ...] = (
    "grain_id",
    "entity",
    "policy_id",
    "horizon",
    "revision",
    "population",
    "decision_at_utc",
    "cutoff",
    "maturity_at",
    "recorded_at",
    "baseline_id",
    "baseline_description",
    "measure_kinds",
    "correction_of",
    "expected_net_return",
    "expected_duration_seconds",
    "expected_cost_dollars",
    "expected_horizon_seconds",
)


class LearningRootCause(str, Enum):
    """The closed structural causes of a realized shortfall or its absence."""

    NO_VERIFIED_OUTCOME = "NO_VERIFIED_OUTCOME"
    HORIZON_MISMATCH = "HORIZON_MISMATCH"
    COST_DRAG = "COST_DRAG"
    EXECUTION_TIMING = "EXECUTION_TIMING"
    MARKET_ADVERSE_MOVE = "MARKET_ADVERSE_MOVE"
    OUTCOME_MET_EXPECTATION = "OUTCOME_MET_EXPECTATION"


#: The frozen precedence of the root-cause classes. The first class that the
#: evidence supports is the classification, so a horizon breach is never reported
#: as a cost overrun and a met expectation is never reported as a shortfall.
ROOT_CAUSE_PRECEDENCE: tuple[str, ...] = tuple(
    cause.value for cause in LearningRootCause
)

ROOT_CAUSE_REASON_NO_FACTS = "NO_VERIFIED_OUTCOME_FACTS"
ROOT_CAUSE_REASON_HORIZON = "REALISED_DURATION_BEYOND_DECLARED_HORIZON"
ROOT_CAUSE_REASON_COST = "REALISED_COST_BEYOND_EXPECTED_COST"
ROOT_CAUSE_REASON_TIMING = "REALISED_DURATION_BEFORE_EXPECTED_DURATION"
ROOT_CAUSE_REASON_ADVERSE = "REALISED_NET_BELOW_EXPECTED_WITH_FULL_HORIZON"
ROOT_CAUSE_REASON_MET = "REALISED_NET_MET_EXPECTED"


class LearningDisposition(str, Enum):
    """The closed consumption dispositions one learning artifact can receive.

    A disposition says how an artifact may be consumed as evidence. It never
    authorizes a policy, a trade decision or a size: ``LIVE`` means the artifact
    is reachable from the live read path as evidence, not that it holds authority.
    """

    LIVE = "LIVE"
    ADVISORY = "ADVISORY"
    SHADOW = "SHADOW"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"
    SUPERSEDED = "SUPERSEDED"


#: The closed disposition vocabulary, in its declared order.
LEARNING_DISPOSITIONS: tuple[str, ...] = tuple(
    disposition.value for disposition in LearningDisposition
)

#: The frozen precedence of the dispositions. The first rule the artifact matches
#: is its disposition, so an unusable artifact is always quarantined and never
#: silently dropped, and a superseded revision is never consumed as current.
DISPOSITION_PRECEDENCE: tuple[str, ...] = (
    LearningDisposition.QUARANTINED.value,
    LearningDisposition.SUPERSEDED.value,
    LearningDisposition.REJECTED.value,
    LearningDisposition.SHADOW.value,
    LearningDisposition.LIVE.value,
    LearningDisposition.ADVISORY.value,
)

DISPOSITION_REASON_NO_REALIZED_KIND = "NO_REALIZED_MEASURE_KIND_DECLARED"
DISPOSITION_REASON_MATURED_TAKEN = "MATURED_REALIZED_OUTCOME_CONSUMED_AS_EVIDENCE"


@dataclass(frozen=True)
class AttachedOutcome:
    """One frozen expectation and the committed evidence resolved against it.

    It is one artifact: the frozen expectation, the closed outcome resolution, the
    committed attachment when one was verified, the committed capital the episode
    occupied when it was verified, and the integrity reason when the evidence
    could not be verified at all. An artifact is never dropped: an unusable one
    keeps its identity and carries the reason it could not be used.
    """

    expectation: ProspectiveExpectation
    resolution: OutcomeResolution
    attachment: ExpectationAttachment | None = None
    committed_capital: float | None = None
    integrity_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.expectation, ProspectiveExpectation):
            raise ExpectationError("expectation must be a ProspectiveExpectation")
        if not isinstance(self.resolution, OutcomeResolution):
            raise ExpectationError("resolution must be an OutcomeResolution")
        if self.attachment is not None:
            if not isinstance(self.attachment, ExpectationAttachment):
                raise ExpectationError("attachment must be an ExpectationAttachment")
            if self.attachment.expectation_id != self.expectation.expectation_id:
                raise ExpectationError("an attachment belongs to the expectation it measures")
        if self.committed_capital is not None:
            object.__setattr__(
                self,
                "committed_capital",
                _expectation_number(
                    self.committed_capital,
                    field_name="committed_capital",
                    minimum=0.0,
                ),
            )
        if self.integrity_reason is not None:
            object.__setattr__(
                self,
                "integrity_reason",
                _expectation_text(self.integrity_reason, field_name="integrity_reason"),
            )

    @property
    def expectation_id(self) -> str:
        return self.expectation.expectation_id

    @property
    def grain_id(self) -> str:
        return self.expectation.grain.grain_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "expectation_id": self.expectation.expectation_id,
            "grain_id": self.expectation.grain.grain_id,
            "population": self.expectation.population.value,
            "status": self.resolution.status.value,
            "reasons": [reason.value for reason in self.resolution.reasons],
            "expected_hash": self.expectation.expected_hash,
            "facts": self.attachment.facts.as_dict() if self.attachment else None,
            "committed_capital": self.committed_capital,
            "integrity_reason": self.integrity_reason,
            "measurement_only": True,
        }


def capture_expectation_from_decision(
    decision: Mapping[str, Any],
) -> ProspectiveExpectation:
    """Freeze one prospective expectation from ex-ante decision evidence.

    The evidence key set is exact, the decision's own clock must fall at or before
    the cutoff, and the record is then frozen by the existing capture seam, so a
    capture recorded after its own cutoff, built from evidence dated after it, or
    carrying an unexpected key is refused rather than recorded.
    """

    body = _expectation_mapping(
        decision, field_name="decision", expected_keys=DECISION_EVIDENCE_KEYS
    )
    decision_at = _expectation_instant_from_text(
        body["decision_at_utc"], field_name="decision_at_utc"
    )
    cutoff = _expectation_instant_from_text(body["cutoff"], field_name="cutoff")
    if decision_at > cutoff:
        raise ExpectationError(
            "a decision clock that falls after the cutoff is hindsight contamination"
        )

    raw_kinds = body["measure_kinds"]
    if isinstance(raw_kinds, (str, MeasureKind)) or not isinstance(raw_kinds, Sequence):
        raise ExpectationError("measure_kinds must be a sequence of measure kinds")

    return freeze_expectation(
        grain=ExpectationGrain(
            grain_id=body["grain_id"],
            entity=body["entity"],
            policy_id=body["policy_id"],
            horizon=body["horizon"],
            revision=body["revision"],
        ),
        population=body["population"],
        expected_payload=ExpectedOutcome(
            expected_net_return=body["expected_net_return"],
            expected_duration_seconds=body["expected_duration_seconds"],
            expected_cost_dollars=body["expected_cost_dollars"],
            expected_horizon_seconds=body["expected_horizon_seconds"],
        ),
        cutoff=cutoff,
        maturity_at=_expectation_instant_from_text(
            body["maturity_at"], field_name="maturity_at"
        ),
        recorded_at=_expectation_instant_from_text(
            body["recorded_at"], field_name="recorded_at"
        ),
        baseline=ExpectationBaseline(
            baseline_id=body["baseline_id"],
            description=body["baseline_description"],
        ),
        measure_kinds=tuple(raw_kinds),
        correction_of=body["correction_of"],
    )


def _coverage_share(value: Any) -> float | None:
    """A measured coverage share of the declared window, or nothing measured."""

    if value is None:
        return None
    share = _expectation_number(value, field_name="coverage", minimum=0.0)
    if share > 1.0:
        raise ExpectationError("coverage cannot exceed the declared window it measures")
    return share


def outcome_facts_from_canonical_outcome(
    payload: Mapping[str, Any],
    *,
    committed_fill_count: int,
) -> OutcomeFacts:
    """Derive attachment facts from one committed canonical terminal outcome.

    Only a closed outcome asserts realized economics: a cancelled outcome realised
    nothing and an unresolved one knows nothing, so both yield no facts rather than
    zeros. The committed fill count is supplied by the caller's committed
    execution evidence instead of being inferred. The committed net must equal the
    payload's own gross minus its fees, so a cost deducted twice is refused, and
    the duration is measured from the payload's own committed timestamps.
    """

    if not isinstance(payload, Mapping):
        raise ExpectationError("a canonical outcome must be a mapping")
    status = str(payload.get("terminal_status") or "").strip().upper()
    if status != CANONICAL_CLOSED_STATUS:
        raise ExpectationError(
            f"a {status or 'non-terminal'} outcome asserts no realised economics, so "
            "it yields no attachment facts and never zeros"
        )

    if isinstance(committed_fill_count, bool) or not isinstance(committed_fill_count, int):
        raise ExpectationError("committed_fill_count must be an integer")
    if committed_fill_count < 1:
        raise ExpectationError("an attachment requires at least one committed fill")

    gross = _expectation_number(payload.get("gross_pnl"), field_name="gross_pnl")
    fees = _expectation_number(
        payload.get("fees_paid"), field_name="fees_paid", minimum=0.0
    )
    net = _expectation_number(payload.get("net_pnl"), field_name="net_pnl")
    if abs(net - (gross - fees)) > CANONICAL_ECONOMICS_TOLERANCE:
        raise ExpectationError(
            "the committed net disagrees with its gross minus its fees, so a cost "
            "was not deducted exactly once"
        )

    entry = _expectation_instant_from_text(
        payload.get("entry_timestamp"), field_name="entry_timestamp"
    )
    exit_at = _expectation_instant_from_text(
        payload.get("exit_timestamp"), field_name="exit_timestamp"
    )
    if exit_at < entry:
        raise ExpectationError("a committed outcome cannot exit before it entered")

    return OutcomeFacts(
        fill_count=committed_fill_count,
        net_return=net,
        duration_seconds=(exit_at - entry).total_seconds(),
        cost_dollars=fees,
    )


def attach_canonical_outcome(
    expectation: ProspectiveExpectation,
    payload: Mapping[str, Any],
    *,
    committed_fill_count: int,
    recorded_at: datetime,
    coverage: float | None = None,
) -> AttachedOutcome:
    """Join one frozen expectation to one committed canonical outcome.

    The facts are derived from the committed payload, the attachment is legal only
    after the cutoff, at or after the maturity horizon and at or after the moment
    the episode itself exited, and the closed outcome status is then resolved with
    the measured coverage. The frozen expectation is never rewritten: a refused
    attachment leaves it exactly as it was.
    """

    if not isinstance(expectation, ProspectiveExpectation):
        raise ExpectationError("expectation must be a ProspectiveExpectation")
    facts = outcome_facts_from_canonical_outcome(
        payload, committed_fill_count=committed_fill_count
    )
    moment = _expectation_utc(recorded_at, field_name="recorded_at")
    exit_at = _expectation_instant_from_text(
        payload.get("exit_timestamp"), field_name="exit_timestamp"
    )
    if moment < exit_at:
        raise ExpectationError(
            "an outcome cannot be attached before the episode it describes has exited"
        )
    capital = _expectation_number(
        payload.get("capital_committed"), field_name="capital_committed", minimum=0.0
    )
    attachment = attach_outcome(expectation, facts, recorded_at=moment)
    resolution = resolve_outcome_status(
        expectation,
        evaluated_at=moment,
        attachment=attachment,
        coverage=_coverage_share(coverage),
    )
    return AttachedOutcome(
        expectation=expectation,
        resolution=resolution,
        attachment=attachment,
        committed_capital=capital,
    )


def assess_unattached_expectation(
    expectation: ProspectiveExpectation,
    *,
    evaluated_at: datetime,
    coverage: float | None = None,
    censored: OutcomeStatusReason | None = None,
) -> AttachedOutcome:
    """Resolve the outcome status of a frozen expectation with no attachment.

    No facts were verified, so nothing is claimed: the artifact keeps its identity
    and reports pending, censored or insufficient evidence with its own reasons
    rather than being measured as a result.
    """

    if not isinstance(expectation, ProspectiveExpectation):
        raise ExpectationError("expectation must be a ProspectiveExpectation")
    resolution = resolve_outcome_status(
        expectation,
        evaluated_at=evaluated_at,
        coverage=_coverage_share(coverage),
        censored=censored,
    )
    return AttachedOutcome(expectation=expectation, resolution=resolution)


def mark_unusable_expectation(
    expectation: ProspectiveExpectation,
    *,
    evaluated_at: datetime,
    reason: str,
) -> AttachedOutcome:
    """Keep one artifact whose committed evidence could not be verified.

    The reason is recorded with the artifact and the outcome stays insufficient
    evidence, so unusable evidence is quarantined with its cause instead of being
    dropped, defaulted to zero or reported as a break-even result.
    """

    if not isinstance(expectation, ProspectiveExpectation):
        raise ExpectationError("expectation must be a ProspectiveExpectation")
    text = _expectation_text(reason, field_name="reason")
    resolution = resolve_outcome_status(expectation, evaluated_at=evaluated_at)
    return AttachedOutcome(
        expectation=expectation, resolution=resolution, integrity_reason=text
    )


def _support_threshold(value: Any) -> int:
    """A declared minimum support, which is a caller parameter and no policy."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ExpectationError("minimum_support must be an integer")
    if value < 2:
        raise ExpectationError("minimum_support must be at least 2")
    return value


def _measured_rows(
    expectations: Iterable[AttachedOutcome], wanted: Population
) -> tuple[AttachedOutcome, ...]:
    """Validate one population's matured, verified artifacts without mixing them.

    An unusable artifact, an artifact of another population, an artifact whose
    window has not matured and a grain measured twice are each refused, so a
    combined or duplicated number can never be produced by accident.
    """

    rows = tuple(expectations)
    if not rows:
        raise ExpectationError("a population evaluation needs at least one artifact")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, AttachedOutcome):
            raise ExpectationError("artifacts must be AttachedOutcome records")
        if row.integrity_reason is not None:
            raise ExpectationError(
                "an artifact whose evidence is unusable cannot be measured"
            )
        if row.expectation.population is not wanted:
            raise ExpectationError(
                f"a {row.expectation.population.value} artifact cannot be measured as "
                f"{wanted.value}: populations are never mixed"
            )
        if row.resolution.status is not OutcomeStatus.MATURED or row.attachment is None:
            raise ExpectationError(
                "only a matured, verified outcome carries a realized measure"
            )
        grain_id = row.expectation.grain.grain_id
        if grain_id in seen:
            raise ExpectationError(
                "one grain is measured once: a duplicate artifact is refused"
            )
        seen.add(grain_id)
    return rows


def _committed_capital(rows: tuple[AttachedOutcome, ...]) -> float:
    """The committed capital of every episode, which an occupancy view needs."""

    if any(row.committed_capital is None for row in rows):
        raise ExpectationError(
            "an occupancy view needs the committed capital of every episode"
        )
    return math.fsum(float(row.committed_capital) for row in rows)


def realized_outcome_measures(
    attached: Iterable[AttachedOutcome],
    *,
    population: Population | None = None,
) -> tuple[MeasureRecord, ...]:
    """One realized measure per matured grain, and never another kind.

    The series is built from verified realized outcomes only, so a counterfactual
    or hindsight value can never enter it, and it is never mixed across
    populations or duplicated at one grain.
    """

    rows = tuple(attached)
    if not rows:
        raise ExpectationError("a realized measure series needs at least one artifact")
    if population is None:
        populations = {
            row.expectation.population
            for row in rows
            if isinstance(row, AttachedOutcome)
        }
        if not populations:
            raise ExpectationError("artifacts must be AttachedOutcome records")
        if len(populations) != 1:
            raise ExpectationError(
                "a realized measure series is never mixed across populations"
            )
        wanted = next(iter(populations))
    else:
        wanted = _expectation_enum(population, Population, field_name="population")
    validated = _measured_rows(rows, wanted)
    return tuple(
        MeasureRecord(
            kind=MeasureKind.REALIZED_PAPER_OUTCOME,
            grain_id=row.expectation.grain.grain_id,
            value=row.attachment.facts.net_return,
        )
        for row in validated
    )


@dataclass(frozen=True)
class PopulationEconomics:
    """The realized economic truth of one retained population.

    Every figure is in quote-currency units except the shares, which are
    dimensionless. ``cost_share`` is the ratio of the summed costs to the summed
    gross, and ``mean_episode_cost_share_diagnostic`` is the per-episode mean kept
    beside it as a diagnostic only; the two are reported separately because the
    ratio of sums is the population truth and the mean of ratios is not.
    """

    population: Population
    kind: MeasureKind
    support: int
    minimum_support: int
    support_sufficiency: EvaluationSupport
    committed_capital: float
    gross_dollars: float
    cost_dollars: float
    net_dollars: float
    cost_share: float | None
    mean_episode_cost_share_diagnostic: MetricEstimate
    net_return_on_capital: float | None
    net_expectancy: MetricEstimate
    measurement_only: bool = True
    authoritative: bool = False
    can_promote: bool = False
    affects_live_decisions: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "population": self.population.value,
            "kind": self.kind.value,
            "support": self.support,
            "minimum_support": self.minimum_support,
            "support_sufficiency": self.support_sufficiency.value,
            "committed_capital": self.committed_capital,
            "gross_dollars": self.gross_dollars,
            "cost_dollars": self.cost_dollars,
            "net_dollars": self.net_dollars,
            "cost_share": self.cost_share,
            "mean_episode_cost_share_diagnostic": (
                self.mean_episode_cost_share_diagnostic.as_dict()
            ),
            "net_return_on_capital": self.net_return_on_capital,
            "net_expectancy": self.net_expectancy.as_dict(),
            "measurement_only": True,
            "authoritative": False,
            "can_promote": False,
            "affects_live_decisions": False,
        }


def evaluate_population_economics(
    expectations: Iterable[AttachedOutcome],
    *,
    population: Population,
    minimum_support: int = 30,
) -> PopulationEconomics:
    """Evaluate the realized economic truth of exactly one population.

    Operating costs are deducted exactly once, because each committed net already
    accounts for its own fees and the population total is the sum of those nets;
    the reported cost share is the ratio of the summed costs to the summed gross,
    which is the population truth, and the per-episode mean is kept only as a
    diagnostic. Nothing promotes, nothing becomes authoritative and support is
    reported rather than assumed.
    """

    wanted = _expectation_enum(population, Population, field_name="population")
    threshold = _support_threshold(minimum_support)
    rows = _measured_rows(expectations, wanted)
    capital = _committed_capital(rows)
    nets = [row.attachment.facts.net_return for row in rows]
    costs = [row.attachment.facts.cost_dollars for row in rows]
    net_total = math.fsum(nets)
    cost_total = math.fsum(costs)
    gross_total = net_total + cost_total
    episode_shares = [
        cost / (cost + net) for net, cost in zip(nets, costs) if (cost + net) > 0.0
    ]
    measured_shares = (
        _metric(episode_shares, minimum_support=threshold)
        if episode_shares
        else MetricEstimate(
            n=0,
            mean=None,
            ci_low=None,
            ci_high=None,
            support=EvaluationSupport.INSUFFICIENT,
        )
    )
    return PopulationEconomics(
        population=wanted,
        kind=MeasureKind.REALIZED_PAPER_OUTCOME,
        support=len(rows),
        minimum_support=threshold,
        support_sufficiency=(
            EvaluationSupport.SUFFICIENT
            if len(rows) >= threshold
            else EvaluationSupport.INSUFFICIENT
        ),
        committed_capital=capital,
        gross_dollars=gross_total,
        cost_dollars=cost_total,
        net_dollars=net_total,
        cost_share=(cost_total / gross_total) if gross_total > 0.0 else None,
        mean_episode_cost_share_diagnostic=measured_shares,
        net_return_on_capital=(net_total / capital) if capital > 0.0 else None,
        net_expectancy=_metric(nets, minimum_support=threshold),
    )


@dataclass(frozen=True)
class CapitalTimeEvaluation:
    """The occupied capital time of one retained population.

    Occupied capital time is the committed capital multiplied by the realized
    duration, and it is compared with the capital time the frozen expectations
    themselves declared. A holding that outlived its own declared horizon is
    counted and shared as a breach rather than smoothed away.
    """

    population: Population
    support: int
    minimum_support: int
    support_sufficiency: EvaluationSupport
    committed_capital: float
    capital_time_seconds: float
    expected_capital_time_seconds: float
    capital_time_utilisation: float | None
    mean_holding_seconds: MetricEstimate
    horizon_breaches: int
    horizon_breach_share: float
    measurement_only: bool = True
    affects_live_decisions: bool = False
    authoritative: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "population": self.population.value,
            "support": self.support,
            "minimum_support": self.minimum_support,
            "support_sufficiency": self.support_sufficiency.value,
            "committed_capital": self.committed_capital,
            "capital_time_seconds": self.capital_time_seconds,
            "expected_capital_time_seconds": self.expected_capital_time_seconds,
            "capital_time_utilisation": self.capital_time_utilisation,
            "mean_holding_seconds": self.mean_holding_seconds.as_dict(),
            "horizon_breaches": self.horizon_breaches,
            "horizon_breach_share": self.horizon_breach_share,
            "measurement_only": True,
            "affects_live_decisions": False,
            "authoritative": False,
        }


def evaluate_capital_time(
    expectations: Iterable[AttachedOutcome],
    *,
    population: Population,
    minimum_support: int = 30,
) -> CapitalTimeEvaluation:
    """Evaluate occupied capital time against the declared horizons.

    The declared horizon is the frozen expectation's own figure, so nothing here
    invents a holding boundary: the view reports how much capital time was
    occupied, how much the expectations themselves declared and which episodes
    outlived the horizon they had declared.
    """

    wanted = _expectation_enum(population, Population, field_name="population")
    threshold = _support_threshold(minimum_support)
    rows = _measured_rows(expectations, wanted)
    capital = _committed_capital(rows)
    durations = [row.attachment.facts.duration_seconds for row in rows]
    occupied = math.fsum(
        float(row.committed_capital) * row.attachment.facts.duration_seconds
        for row in rows
    )
    declared = math.fsum(
        float(row.committed_capital)
        * row.expectation.expected_payload.expected_horizon_seconds
        for row in rows
    )
    breaches = sum(
        1
        for row in rows
        if row.attachment.facts.duration_seconds
        > row.expectation.expected_payload.expected_horizon_seconds
    )
    return CapitalTimeEvaluation(
        population=wanted,
        support=len(rows),
        minimum_support=threshold,
        support_sufficiency=(
            EvaluationSupport.SUFFICIENT
            if len(rows) >= threshold
            else EvaluationSupport.INSUFFICIENT
        ),
        committed_capital=capital,
        capital_time_seconds=occupied,
        expected_capital_time_seconds=declared,
        capital_time_utilisation=(occupied / declared) if declared > 0.0 else None,
        mean_holding_seconds=_metric(durations, minimum_support=threshold),
        horizon_breaches=breaches,
        horizon_breach_share=breaches / len(rows),
    )


@dataclass(frozen=True)
class RootCauseClassification:
    """One named structural cause for one artifact, with its own reasons."""

    expectation_id: str
    grain_id: str
    population: Population
    root_cause: LearningRootCause
    reasons: tuple[str, ...]
    measurement_only: bool = True
    affects_live_decisions: bool = False
    authoritative: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "expectation_id": self.expectation_id,
            "grain_id": self.grain_id,
            "population": self.population.value,
            "root_cause": self.root_cause.value,
            "reasons": list(self.reasons),
            "measurement_only": True,
            "affects_live_decisions": False,
            "authoritative": False,
        }


def classify_root_cause(attached: AttachedOutcome) -> RootCauseClassification:
    """Classify one artifact's structural root cause by the frozen precedence.

    The evidence is the artifact's own committed facts and the expectation's own
    declared figures: no external threshold and no hindsight measure takes part.
    An artifact with no verified outcome yields no cause at all, and the first
    supported class wins, so a holding that outlived its declared horizon is named
    a horizon mismatch before anything else is considered.
    """

    if not isinstance(attached, AttachedOutcome):
        raise ExpectationError("attached must be an AttachedOutcome")
    expectation = attached.expectation
    verified = (
        attached.integrity_reason is None
        and attached.attachment is not None
        and attached.resolution.status is OutcomeStatus.MATURED
    )
    if not verified:
        cause = LearningRootCause.NO_VERIFIED_OUTCOME
        reasons = (ROOT_CAUSE_REASON_NO_FACTS,)
    else:
        facts = attached.attachment.facts
        expected = expectation.expected_payload
        if facts.duration_seconds > expected.expected_horizon_seconds:
            cause = LearningRootCause.HORIZON_MISMATCH
            reasons = (ROOT_CAUSE_REASON_HORIZON,)
        elif (
            facts.net_return < expected.expected_net_return
            and facts.cost_dollars > expected.expected_cost_dollars
        ):
            cause = LearningRootCause.COST_DRAG
            reasons = (ROOT_CAUSE_REASON_COST,)
        elif facts.net_return < expected.expected_net_return:
            if facts.duration_seconds < expected.expected_duration_seconds:
                cause = LearningRootCause.EXECUTION_TIMING
                reasons = (ROOT_CAUSE_REASON_TIMING,)
            else:
                cause = LearningRootCause.MARKET_ADVERSE_MOVE
                reasons = (ROOT_CAUSE_REASON_ADVERSE,)
        else:
            cause = LearningRootCause.OUTCOME_MET_EXPECTATION
            reasons = (ROOT_CAUSE_REASON_MET,)
    return RootCauseClassification(
        expectation_id=expectation.expectation_id,
        grain_id=expectation.grain.grain_id,
        population=expectation.population,
        root_cause=cause,
        reasons=reasons,
    )


@dataclass(frozen=True)
class RepairLineage:
    """One expectation's place in its repair lineage, resolved to its root."""

    expectation_id: str
    root_expectation_id: str
    depth: int
    chain: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "expectation_id": self.expectation_id,
            "root_expectation_id": self.root_expectation_id,
            "depth": self.depth,
            "chain": list(self.chain),
        }


def resolve_repair_lineage(
    expectations: Iterable[ProspectiveExpectation],
) -> tuple[RepairLineage, ...]:
    """Resolve every correction to its root, and refuse an impossible lineage.

    A correction must name an expectation that is present, of its own grain, at a
    later revision, recorded at or after the expectation it corrects, and one
    revision of one grain is claimed only once. A dangling parent, a self-
    reference, a cycle, an inverted recording and an ambiguous revision therefore
    fail closed instead of being resolved by a guess.
    """

    rows = tuple(expectations)
    if not rows:
        raise ExpectationError("a repair lineage needs at least one expectation")
    by_id: dict[str, ProspectiveExpectation] = {}
    for row in rows:
        if not isinstance(row, ProspectiveExpectation):
            raise ExpectationError(
                "expectations must be ProspectiveExpectation records"
            )
        if row.expectation_id in by_id:
            raise ExpectationError("one identity cannot appear twice in one lineage")
        by_id[row.expectation_id] = row

    revisions: dict[tuple[str, str, str, str, int], str] = {}
    for row in rows:
        key = (
            row.grain.grain_id,
            row.grain.entity,
            row.grain.policy_id,
            row.grain.horizon,
            row.grain.revision,
        )
        prior = revisions.get(key)
        if prior is not None and prior != row.expectation_id:
            raise ExpectationError(
                "one grain revision is claimed by more than one expectation"
            )
        revisions[key] = row.expectation_id

    resolved: list[RepairLineage] = []
    for row in rows:
        chain = [row.expectation_id]
        current = row
        while current.correction_of is not None:
            parent = by_id.get(current.correction_of)
            if parent is None:
                raise ExpectationError(
                    "the repair lineage names an unknown expectation: "
                    f"{current.correction_of}"
                )
            if parent.expectation_id in chain:
                raise ExpectationError("the repair lineage is cyclic")
            if parent.recorded_at > current.recorded_at:
                raise ExpectationError(
                    "a correction cannot be recorded before the expectation it corrects"
                )
            if parent.grain.grain_id != current.grain.grain_id:
                raise ExpectationError(
                    "a correction must name an expectation of its own grain"
                )
            if parent.grain.revision >= current.grain.revision:
                raise ExpectationError(
                    "a correction must carry a later revision than the expectation "
                    "it corrects"
                )
            chain.append(parent.expectation_id)
            current = parent
        resolved.append(
            RepairLineage(
                expectation_id=row.expectation_id,
                root_expectation_id=chain[-1],
                depth=len(chain) - 1,
                chain=tuple(reversed(chain)),
            )
        )
    return tuple(resolved)


@dataclass(frozen=True)
class LearningConsumptionDisposition:
    """How one learning artifact may be consumed, and nothing more.

    The record names the disposition, the reason it was chosen and the artifact it
    belongs to. It declares itself evidence only and grants no authority: it can
    never edit a policy, place an order or change a trade decision, and it never
    reports the artifact as anything other than evidence.
    """

    artifact_id: str
    grain_id: str
    population: Population
    disposition: LearningDisposition
    reason: str
    consumed_as_evidence_only: bool = True
    authority_granted: bool = False
    trade_authority_changed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_id",
            _expectation_text(self.artifact_id, field_name="artifact_id"),
        )
        object.__setattr__(
            self, "grain_id", _expectation_text(self.grain_id, field_name="grain_id")
        )
        object.__setattr__(
            self,
            "population",
            _expectation_enum(self.population, Population, field_name="population"),
        )
        object.__setattr__(
            self,
            "disposition",
            _expectation_enum(
                self.disposition, LearningDisposition, field_name="disposition"
            ),
        )
        object.__setattr__(
            self, "reason", _expectation_text(self.reason, field_name="reason")
        )
        if self.consumed_as_evidence_only is not True:
            raise ExpectationError("a disposition is evidence only")
        if self.authority_granted is not False:
            raise ExpectationError("a disposition never grants authority")
        if self.trade_authority_changed is not False:
            raise ExpectationError("a disposition never changes trade authority")

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "grain_id": self.grain_id,
            "population": self.population.value,
            "disposition": self.disposition.value,
            "reason": self.reason,
            "consumed_as_evidence_only": True,
            "authority_granted": False,
            "trade_authority_changed": False,
        }


def disposition_for(
    attached: AttachedOutcome,
    *,
    superseded_by: str | None = None,
) -> LearningConsumptionDisposition:
    """Assign exactly one named consumption disposition to one artifact.

    The frozen precedence decides: unusable evidence is quarantined, a superseded
    revision is superseded, an artifact whose declared kinds could never carry
    realized truth is rejected, an unresolved artifact stays shadow, a matured
    taken outcome is consumed as live evidence and a matured outcome of another
    retained population is consumed as advisory evidence. Nothing is dropped and
    nothing is consumed twice.
    """

    if not isinstance(attached, AttachedOutcome):
        raise ExpectationError("attached must be an AttachedOutcome")
    expectation = attached.expectation
    replacement: str | None = None
    if superseded_by is not None:
        replacement = _expectation_text(superseded_by, field_name="superseded_by")
        if replacement == expectation.expectation_id:
            raise ExpectationError("an expectation cannot supersede itself")

    if attached.integrity_reason is not None:
        disposition = LearningDisposition.QUARANTINED
        reason = attached.integrity_reason
    elif replacement is not None:
        disposition = LearningDisposition.SUPERSEDED
        reason = f"SUPERSEDED_BY:{replacement}"
    elif not any(
        kind is MeasureKind.REALIZED_PAPER_OUTCOME for kind in expectation.measure_kinds
    ):
        disposition = LearningDisposition.REJECTED
        reason = DISPOSITION_REASON_NO_REALIZED_KIND
    elif attached.resolution.status is not OutcomeStatus.MATURED:
        disposition = LearningDisposition.SHADOW
        reason = attached.resolution.reasons[0].value
    elif expectation.population is Population.TAKEN:
        disposition = LearningDisposition.LIVE
        reason = DISPOSITION_REASON_MATURED_TAKEN
    else:
        disposition = LearningDisposition.ADVISORY
        reason = f"MATURED_{expectation.population.value}_CONSUMED_AS_ADVISORY"
    return LearningConsumptionDisposition(
        artifact_id=expectation.expectation_id,
        grain_id=expectation.grain.grain_id,
        population=expectation.population,
        disposition=disposition,
        reason=reason,
    )


def assert_every_artifact_has_one_disposition(
    dispositions: Iterable[LearningConsumptionDisposition],
) -> tuple[str, ...]:
    """Prove every artifact received exactly one disposition from the vocabulary.

    An empty retained set and a duplicated artifact are both refused, so the
    returned tuple is a complete, non-repeated coverage of the artifacts that were
    supplied rather than a partial view of them.
    """

    rows = tuple(dispositions)
    if not rows:
        raise ExpectationError("a retained set cannot hold zero dispositions")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, LearningConsumptionDisposition):
            raise ExpectationError(
                "dispositions must be LearningConsumptionDisposition records"
            )
        if row.artifact_id in seen:
            raise ExpectationError(
                "one artifact is consumed once: a duplicate disposition is refused"
            )
        seen.add(row.artifact_id)
        if row.disposition.value not in LEARNING_DISPOSITIONS:
            raise ExpectationError(
                f"the disposition {row.disposition.value} is not in the frozen vocabulary"
            )
    return tuple(row.disposition.value for row in rows)


def _arm_index(arm: Iterable[AttachedOutcome], name: str) -> dict[str, AttachedOutcome]:
    """Index one arm of a prospective comparison by deduplicated grain."""

    index: dict[str, AttachedOutcome] = {}
    for row in arm:
        if not isinstance(row, AttachedOutcome):
            raise ExpectationError("an arm holds AttachedOutcome records")
        grain_id = row.expectation.grain.grain_id
        if grain_id in index:
            raise ExpectationError(
                f"the {name} arm declares one grain twice: {grain_id}"
            )
        index[grain_id] = row
    return index


def _took_the_episode(row: AttachedOutcome) -> bool:
    """Whether one arm actually took the episode, on verified committed facts."""

    return (
        row.integrity_reason is None
        and row.attachment is not None
        and row.resolution.status is OutcomeStatus.MATURED
        and row.expectation.population is Population.TAKEN
    )


def compare_prospective_arms(
    champion: Iterable[AttachedOutcome],
    challenger: Iterable[AttachedOutcome],
    *,
    minimum_support: int = 30,
) -> ChampionChallengerEvaluation:
    """Compare two frozen arms prospectively against one frozen baseline.

    The comparison is paired: both arms must be captured at the same grains under
    the same frozen baseline and horizon and under two distinct policies. A grain
    present in only one arm, a grain both arms took and an imputed realized return
    are all refused, so a realized figure is only ever the committed outcome of the
    arm that actually took the episode. The paired evaluation, and with it every
    support, coverage and uncertainty figure, is then delegated to the existing
    measurement-only champion/challenger seam, whose promotion flags stay false.
    """

    champion_index = _arm_index(champion, "champion")
    challenger_index = _arm_index(challenger, "challenger")
    if not champion_index or not challenger_index:
        raise ExpectationError("a prospective comparison needs both arms")

    rows = tuple(champion_index.values()) + tuple(challenger_index.values())
    baselines = {row.expectation.baseline.baseline_id for row in rows}
    if len(baselines) != 1:
        raise ExpectationError(
            "a prospective comparison is measured against one frozen baseline"
        )
    horizons = {row.expectation.grain.horizon for row in rows}
    if len(horizons) != 1:
        raise ExpectationError(
            "a prospective comparison measures one horizon at a time"
        )
    champion_policies = {
        row.expectation.grain.policy_id for row in champion_index.values()
    }
    challenger_policies = {
        row.expectation.grain.policy_id for row in challenger_index.values()
    }
    if len(champion_policies) != 1 or len(challenger_policies) != 1:
        raise ExpectationError("each arm is captured under exactly one policy")
    if champion_policies == challenger_policies:
        raise ExpectationError("a prospective comparison needs two distinct policies")
    if set(champion_index) != set(challenger_index):
        raise ExpectationError(
            "a prospective comparison is paired: both arms must cover the same grains"
        )

    baseline_id = next(iter(baselines))
    horizon = next(iter(horizons))
    cohort = f"{baseline_id}:{horizon}"
    samples: list[PairedEvaluationSample] = []
    for grain_id in sorted(champion_index):
        champion_row = champion_index[grain_id]
        challenger_row = challenger_index[grain_id]
        champion_took = _took_the_episode(champion_row)
        challenger_took = _took_the_episode(challenger_row)
        if champion_took and challenger_took:
            raise ExpectationError(
                "one episode cannot be taken by both arms, and no realized return "
                "is imputed from a counterfactual measure"
            )
        realized: float | None = None
        if champion_took and champion_row.attachment is not None:
            realized = champion_row.attachment.facts.net_return
        elif challenger_took and challenger_row.attachment is not None:
            realized = challenger_row.attachment.facts.net_return
        samples.append(
            PairedEvaluationSample(
                sample_id=grain_id,
                cohort=cohort,
                champion_admitted=champion_took,
                challenger_admitted=challenger_took,
                realized_net_return=realized,
            )
        )
    return evaluate_champion_challenger(samples, minimum_support=minimum_support)
