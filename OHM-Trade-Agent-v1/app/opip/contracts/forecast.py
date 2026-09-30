"""Forecast Engine vocabulary and typed contracts (R3 F6).

This is the shared, pure vocabulary for the fourth R3 slice: the F6 Forecast
Engine that sits after the F5 Feasibility & Safety seam. It owns the forecast
status tokens, the entry-execution outcome vocabulary, the conditional post-fill
path outcome vocabulary, the model-status vocabulary, the typed probability
distribution, the explicit horizon contract, the uncertainty contract, the
model-artifact contract, the input-vector contract and the immutable
``ForecastDecision`` record with its deterministic ``FCST:`` identity.

Boundaries that are deliberate and enforced here:

* A forecast status is exactly ``FORECAST`` or ``INSUFFICIENT_EVIDENCE``. There
  is no third token, and PASS/FAIL/VETO/WATCH/APPROVE/REJECT are never statuses.
* Entry execution outcomes (``NO_FILL``/``PARTIAL_FILL``/``FULL_FILL``) and
  post-fill path outcomes (``TARGET``/``STOP``/``TIMEOUT``/``RISK_EXIT``) are
  separate families. A ``P(fill)`` is never collapsed into a ``P(target)``.
* Model status is exactly ``RESEARCH_ONLY`` or ``CALIBRATED_SHADOW``. There is no
  production or funded status in R3.
* No ordinal score is a probability. Nothing here divides a confidence, an
  opportunity score, a technical score, an explosion score, a tradeability
  score, an economic-quality score, a target-attainability score, a ranking
  score or a Committee rubric by 100, or maps any such value into a probability.
* Missing evidence is never favorable evidence: an eligible model that cannot
  support a required output is ``INSUFFICIENT_EVIDENCE``, never ``FORECAST``.
* The decision identity is a deterministic function of the decision schema
  version, the preserved F4/F5 lineage, the input fingerprint, the explicit
  evaluation time, the model artifact id, the horizon identity, the engine
  version, the policy version and the decision's own semantic payload. No UUID,
  receipt timestamp, retry, pid or database sequence takes part, and a forged
  decision identity fails closed.

Nothing here reads a clock, the environment, the filesystem, the network or any
model file, and nothing here allocates capital, ranks candidates, reserves cash,
sizes positions or places any paper or funded order.

SHADOW / NON-AUTHORITATIVE. This vocabulary is a research artifact. It is not
wired into ``run_cycle`` or ``scan_opportunities``, it activates no Feature Bus,
and it writes no canonical evidence.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any

from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import TemporalIntegrityError, require_utc

#: Schema version of the durable ``ForecastDecision`` record.
FORECAST_DECISION_SCHEMA_VERSION = "forecast-decision-v1"

#: Applied forecast-engine implementation version. A deterministic code artifact.
FORECAST_ENGINE_VERSION = "forecast-engine-v1"

#: Applied forecast policy version. A deterministic, replayable code artifact
#: tagged by this token; it is not a caller-selected input.
FORECAST_POLICY_VERSION = "forecast-shadow-policy-v1"

#: Schema version of the ``ForecastModelArtifact`` record.
FORECAST_MODEL_ARTIFACT_SCHEMA_VERSION = "forecast-model-artifact-v1"

#: Version of the proper-scoring / reliability evaluation framework.
FORECAST_EVALUATION_VERSION = "forecast-evaluation-v1"

#: Semantic prefixes of the deterministic identities.
FORECAST_DECISION_ID_PREFIX = "FCST"
FORECAST_MODEL_ARTIFACT_ID_PREFIX = "FMOD"
FORECAST_INPUT_FINGERPRINT_PREFIX = "FIN"
FORECAST_HORIZON_ID_PREFIX = "FHOR"
FORECAST_INPUT_SCHEMA_FINGERPRINT_PREFIX = "FISCH"

#: Tight serialization tolerance for a probability mass summing to one. It is a
#: serialization tolerance, not a trading, calibration or promotion threshold.
PROBABILITY_SUM_TOLERANCE = 1e-9


class ForecastContractError(ValueError):
    """A structural contract violation. Always fails closed."""


class ForecastStatus(str, Enum):
    """The F6 forecast status. There is no third token."""

    FORECAST = "FORECAST"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EntryExecutionOutcome(str, Enum):
    """The entry-execution outcome family. Modeled separately from the path."""

    NO_FILL = "NO_FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"


class PostFillPathOutcome(str, Enum):
    """The conditional post-fill path outcome family.

    These outcomes are conditional on fill exposure. They are never collapsed
    with the entry-execution family into a single binary win probability, and
    ``P(TARGET)`` is never equated with ``P(positive return)``.
    """

    TARGET = "TARGET"
    STOP = "STOP"
    TIMEOUT = "TIMEOUT"
    RISK_EXIT = "RISK_EXIT"


class ForecastModelStatus(str, Enum):
    """The R3 model status. There is no production or funded status."""

    RESEARCH_ONLY = "RESEARCH_ONLY"
    CALIBRATED_SHADOW = "CALIBRATED_SHADOW"


class ForecastModelKind(str, Enum):
    """The explicitly implemented model kinds.

    Only a kind listed here may ever execute. ``SYNTHETIC_TEST_ONLY`` is an
    unmistakable test-only kind: the trusted production registry refuses to hold
    an adapter for it, so a synthetic artifact can never produce a production
    forecast.
    """

    SYNTHETIC_TEST_ONLY = "SYNTHETIC_TEST_ONLY"


class ForecastHorizonAnchor(str, Enum):
    """The declared anchor for the post-fill outcome horizon.

    The v1 policy horizon is anchored to ``FIRST_FILL``; later fills do not
    restart it. No other anchor is implemented.
    """

    FIRST_FILL = "FIRST_FILL"


class DistributionKind(str, Enum):
    """Which outcome family a probability distribution describes."""

    ENTRY_EXECUTION = "ENTRY_EXECUTION"
    POST_FILL_PATH = "POST_FILL_PATH"


class ForecastAbstentionReason(str, Enum):
    """The bounded, machine-readable abstention vocabulary.

    An abstention is a governed evidence disposition, not a veto. It carries one
    of these reasons so the gap is auditable.
    """

    NO_CALIBRATED_MODEL = "NO_CALIBRATED_MODEL"
    MODEL_NOT_CALIBRATED_FOR_SHADOW_AUTHORITY = "MODEL_NOT_CALIBRATED_FOR_SHADOW_AUTHORITY"
    UNSUPPORTED_MODEL_KIND = "UNSUPPORTED_MODEL_KIND"
    CALIBRATION_REPORT_UNTRUSTED = "CALIBRATION_REPORT_UNTRUSTED"
    UNCERTAINTY_UNAVAILABLE = "UNCERTAINTY_UNAVAILABLE"


#: The exact entry-execution outcome tokens, in canonical order.
ENTRY_EXECUTION_OUTCOMES: tuple[str, ...] = tuple(
    outcome.value for outcome in EntryExecutionOutcome
)

#: The exact conditional post-fill outcome tokens, in canonical order.
POST_FILL_PATH_OUTCOMES: tuple[str, ...] = tuple(
    outcome.value for outcome in PostFillPathOutcome
)


def require_forecast_enum(
    enum_type: type[Enum], value: Any, *, field_name: str
) -> Any:
    """Coerce one enum token strictly; reject malformed or unsupported tokens.

    Accepts an existing member or its exact string token. Rejects bools, numbers
    and unknown strings, so a mistyped durable token is refused rather than
    silently substituted.
    """
    if isinstance(value, enum_type):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise ForecastContractError(f"{field_name} must be a {enum_type.__name__} token")
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            raise ForecastContractError(
                f"{field_name} has an unsupported token: {value!r}"
            ) from exc
    raise ForecastContractError(f"{field_name} must be a {enum_type.__name__} token")


def require_forecast_text(value: Any, *, field_name: str) -> str:
    """Validate one required canonical text token.

    A number or bool is never coerced into text, and a token with surrounding
    whitespace is refused so it cannot silently diverge from its digest.
    """
    if not isinstance(value, str):
        raise ForecastContractError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise ForecastContractError(
            f"{field_name} must be a non-empty, whitespace-free token"
        )
    return value


def require_forecast_utc(value: Any, *, field_name: str) -> datetime:
    """Validate one required explicit UTC instant (naive/malformed fails closed)."""
    if not isinstance(value, datetime):
        raise ForecastContractError(f"{field_name} must be an explicit datetime")
    try:
        return require_utc(value, field_name=field_name)
    except TemporalIntegrityError as exc:
        raise ForecastContractError(str(exc)) from exc


def require_finite_number(value: Any, *, field_name: str) -> float:
    """Validate one finite decimal number; bool, non-numeric or non-finite fails closed."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForecastContractError(f"{field_name} must be a finite decimal number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ForecastContractError(f"{field_name} must be finite") from exc
    if not math.isfinite(number):
        raise ForecastContractError(f"{field_name} must be finite")
    return number


def require_probability(value: Any, *, field_name: str) -> float:
    """Validate one probability: finite, not bool, and within ``[0, 1]``.

    The value is never clipped into range; an out-of-range or non-finite
    probability is refused rather than silently repaired.
    """
    number = require_finite_number(value, field_name=field_name)
    if number < 0.0 or number > 1.0:
        raise ForecastContractError(f"{field_name} must be within [0, 1]")
    return number


def require_non_negative_int(value: Any, *, field_name: str) -> int:
    """Validate one non-negative integer; a bool or non-integer fails closed."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ForecastContractError(f"{field_name} must be an integer")
    if value < 0:
        raise ForecastContractError(f"{field_name} must not be negative")
    return value


def require_positive_int(value: Any, *, field_name: str) -> int:
    """Validate one strictly positive integer; a bool or non-integer fails closed."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ForecastContractError(f"{field_name} must be an integer")
    if value <= 0:
        raise ForecastContractError(f"{field_name} must be positive")
    return value


def _require_json_safe(value: Any, *, field_name: str) -> Any:
    """Return a canonical JSON-safe primitive, refusing anything executable.

    Only ``None``, ``str``, ``bool``, finite ``int``/``float`` and sequences or
    mappings of such primitives are accepted. A callable, a bytes object, an
    arbitrary object or a non-finite number is refused, so a model artifact can
    never smuggle executable code, a pickled payload or object identity into the
    pure engine.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ForecastContractError(f"{field_name} must be finite")
        return value
    if isinstance(value, (list, tuple)):
        return [
            _require_json_safe(item, field_name=f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ForecastContractError(f"{field_name} keys must be strings")
            frozen[key] = _require_json_safe(item, field_name=f"{field_name}[{key}]")
        return frozen
    raise ForecastContractError(
        f"{field_name} must be a JSON-safe primitive, not {type(value).__name__}"
    )


def _freeze_json_safe(value: Any, *, field_name: str) -> Any:
    """Deep-freeze a JSON-safe value into an immutable, deterministic structure."""
    safe = _require_json_safe(value, field_name=field_name)
    if isinstance(safe, dict):
        return MappingProxyType(
            {key: _freeze_json_safe(item, field_name=field_name) for key, item in safe.items()}
        )
    if isinstance(safe, list):
        return tuple(_freeze_json_safe(item, field_name=field_name) for item in safe)
    return safe


def _thaw_json_safe(value: Any) -> Any:
    """Convert a frozen JSON-safe structure back into plain serializable values."""
    if isinstance(value, Mapping):
        return {key: _thaw_json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_json_safe(item) for item in value]
    return value


def _parse_persisted_utc(value: Any, *, field_name: str) -> datetime:
    """Strictly parse one durable UTC instant at a durability trust boundary."""
    if not isinstance(value, str):
        raise ForecastContractError(f"{field_name} must be an ISO-8601 UTC string")
    if value == "" or value != value.strip():
        raise ForecastContractError(
            f"{field_name} must be a non-empty, whitespace-free ISO-8601 UTC string"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ForecastContractError(
            f"{field_name} must be an ISO-8601 UTC instant"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForecastContractError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise ForecastContractError(f"{field_name} must be a UTC instant")
    return parsed.astimezone(timezone.utc)


def _parse_optional_persisted_utc(value: Any, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _parse_persisted_utc(value, field_name=field_name)


def _require_durable_mapping(
    value: Any, *, field_name: str, expected_keys: tuple[str, ...]
) -> Mapping[str, Any]:
    """Refuse a non-mapping, or one whose key set is not exactly ``expected_keys``."""
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


def _require_text_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ForecastContractError(f"{field_name} must be a list of text tokens")
    tokens = tuple(
        require_forecast_text(item, field_name=f"{field_name}[{index}]")
        for index, item in enumerate(value)
    )
    return tokens


# ---------------------------------------------------------------------------
# Explicit horizon contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastHorizon:
    """The explicit forecast horizon contract.

    Three durations are kept deliberately distinct and none has a default:

    * ``entry_deadline_seconds`` - the entry / execution deadline semantics.
    * ``path_horizon_seconds`` - the post-first-fill outcome horizon.
    * ``validity_seconds`` - the forecast validity window.

    The post-fill outcome horizon is anchored to ``FIRST_FILL``; additional fills
    do not restart it. No numeric duration is invented here: the caller and the
    model artifact must supply them explicitly.
    """

    entry_deadline_seconds: int
    path_horizon_seconds: int
    validity_seconds: int
    anchor: ForecastHorizonAnchor = ForecastHorizonAnchor.FIRST_FILL

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entry_deadline_seconds",
            require_positive_int(
                self.entry_deadline_seconds, field_name="entry_deadline_seconds"
            ),
        )
        object.__setattr__(
            self,
            "path_horizon_seconds",
            require_positive_int(
                self.path_horizon_seconds, field_name="path_horizon_seconds"
            ),
        )
        object.__setattr__(
            self,
            "validity_seconds",
            require_positive_int(self.validity_seconds, field_name="validity_seconds"),
        )
        anchor = require_forecast_enum(
            ForecastHorizonAnchor, self.anchor, field_name="anchor"
        )
        if anchor is not ForecastHorizonAnchor.FIRST_FILL:
            raise ForecastContractError("the only v1 path-horizon anchor is FIRST_FILL")
        object.__setattr__(self, "anchor", anchor)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_deadline_seconds": self.entry_deadline_seconds,
            "path_horizon_seconds": self.path_horizon_seconds,
            "validity_seconds": self.validity_seconds,
            "anchor": self.anchor.value,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ForecastHorizon":
        body = _require_durable_mapping(
            raw,
            field_name="horizon",
            expected_keys=(
                "entry_deadline_seconds",
                "path_horizon_seconds",
                "validity_seconds",
                "anchor",
            ),
        )
        return cls(
            entry_deadline_seconds=body["entry_deadline_seconds"],
            path_horizon_seconds=body["path_horizon_seconds"],
            validity_seconds=body["validity_seconds"],
            anchor=body["anchor"],
        )


def forecast_horizon_identity(horizon: ForecastHorizon) -> str:
    """The deterministic ``FHOR:<digest>`` identity of one horizon contract."""
    if not isinstance(horizon, ForecastHorizon):
        raise ForecastContractError("horizon must be a ForecastHorizon")
    return stable_hash(FORECAST_HORIZON_ID_PREFIX, horizon.to_dict())


# ---------------------------------------------------------------------------
# Probability distributions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbabilityDistribution:
    """One validated probability distribution over a declared outcome family.

    The outcome family is fixed by ``kind``. Every probability is finite, not a
    bool and within ``[0, 1]``, and the mass must sum to one within a tight
    serialization tolerance. Invalid mass is refused; it is never silently
    normalized, and no arbitrary output is ever clipped into range.
    """

    kind: DistributionKind
    probabilities: Mapping[str, float]

    def __post_init__(self) -> None:
        kind = require_forecast_enum(DistributionKind, self.kind, field_name="kind")
        expected = (
            ENTRY_EXECUTION_OUTCOMES
            if kind is DistributionKind.ENTRY_EXECUTION
            else POST_FILL_PATH_OUTCOMES
        )
        if not isinstance(self.probabilities, Mapping):
            raise ForecastContractError("probabilities must be a mapping")
        present = set(self.probabilities.keys())
        if present != set(expected):
            raise ForecastContractError(
                "probabilities must carry exactly the "
                f"{kind.value} outcomes: " + ", ".join(expected)
            )
        normalized: dict[str, float] = {}
        total = 0.0
        for token in expected:
            number = require_probability(
                self.probabilities[token], field_name=f"probabilities[{token}]"
            )
            normalized[token] = number
            total += number
        if abs(total - 1.0) > PROBABILITY_SUM_TOLERANCE:
            raise ForecastContractError(
                f"{kind.value} probabilities must sum to 1 (sum={total!r})"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "probabilities", MappingProxyType(normalized))

    def probability(self, outcome: str) -> float:
        return self.probabilities[outcome]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "probabilities": {
                token: self.probabilities[token]
                for token in (
                    ENTRY_EXECUTION_OUTCOMES
                    if self.kind is DistributionKind.ENTRY_EXECUTION
                    else POST_FILL_PATH_OUTCOMES
                )
            },
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ProbabilityDistribution":
        body = _require_durable_mapping(
            raw, field_name="distribution", expected_keys=("kind", "probabilities")
        )
        probabilities = body["probabilities"]
        if not isinstance(probabilities, Mapping):
            raise ForecastContractError("distribution.probabilities must be a mapping")
        return cls(kind=body["kind"], probabilities=dict(probabilities))


# ---------------------------------------------------------------------------
# Uncertainty
# ---------------------------------------------------------------------------


_UNCERTAINTY_DURABLE_KEYS: tuple[str, ...] = (
    "expected_return_lower_bound",
    "expected_return_upper_bound",
    "coverage_level",
    "interval_semantics",
    "evidence_reference",
    "sample_count",
    "support_count",
)


@dataclass(frozen=True)
class ForecastUncertainty:
    """The explicit forecast uncertainty contract.

    The expected-return interval, its coverage/level semantics and the evidence
    reference are all mandatory and have no default: an unknown interval is never
    defaulted to zero width, and uncertainty is never fabricated. Sample and
    support counts default to zero only because they are counts.
    """

    expected_return_lower_bound: float
    expected_return_upper_bound: float
    coverage_level: float
    interval_semantics: str
    evidence_reference: str
    sample_count: int = 0
    support_count: int = 0

    def __post_init__(self) -> None:
        lower = require_finite_number(
            self.expected_return_lower_bound,
            field_name="expected_return_lower_bound",
        )
        upper = require_finite_number(
            self.expected_return_upper_bound,
            field_name="expected_return_upper_bound",
        )
        if lower > upper:
            raise ForecastContractError(
                "expected_return_lower_bound must not exceed the upper bound"
            )
        coverage = require_finite_number(self.coverage_level, field_name="coverage_level")
        if coverage <= 0.0 or coverage > 1.0:
            raise ForecastContractError("coverage_level must be within (0, 1]")
        object.__setattr__(self, "expected_return_lower_bound", lower)
        object.__setattr__(self, "expected_return_upper_bound", upper)
        object.__setattr__(self, "coverage_level", coverage)
        object.__setattr__(
            self,
            "interval_semantics",
            require_forecast_text(self.interval_semantics, field_name="interval_semantics"),
        )
        object.__setattr__(
            self,
            "evidence_reference",
            require_forecast_text(self.evidence_reference, field_name="evidence_reference"),
        )
        object.__setattr__(
            self,
            "sample_count",
            require_non_negative_int(self.sample_count, field_name="sample_count"),
        )
        object.__setattr__(
            self,
            "support_count",
            require_non_negative_int(self.support_count, field_name="support_count"),
        )

    def contains(self, value: float) -> bool:
        return self.expected_return_lower_bound <= value <= self.expected_return_upper_bound

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_return_lower_bound": self.expected_return_lower_bound,
            "expected_return_upper_bound": self.expected_return_upper_bound,
            "coverage_level": self.coverage_level,
            "interval_semantics": self.interval_semantics,
            "evidence_reference": self.evidence_reference,
            "sample_count": self.sample_count,
            "support_count": self.support_count,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ForecastUncertainty":
        body = _require_durable_mapping(
            raw, field_name="uncertainty", expected_keys=_UNCERTAINTY_DURABLE_KEYS
        )
        return cls(
            expected_return_lower_bound=body["expected_return_lower_bound"],
            expected_return_upper_bound=body["expected_return_upper_bound"],
            coverage_level=body["coverage_level"],
            interval_semantics=body["interval_semantics"],
            evidence_reference=body["evidence_reference"],
            sample_count=body["sample_count"],
            support_count=body["support_count"],
        )


# ---------------------------------------------------------------------------
# Input vector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastFeatureValue:
    """One point-in-time feature value supplied to the pure forecast engine."""

    name: str
    value: Any
    available_at_utc: datetime
    missing: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_forecast_text(self.name, field_name="name"))
        object.__setattr__(
            self,
            "value",
            None if self.missing else _freeze_json_safe(self.value, field_name=self.name),
        )
        object.__setattr__(
            self,
            "available_at_utc",
            require_forecast_utc(self.available_at_utc, field_name="available_at_utc"),
        )
        if not isinstance(self.missing, bool):
            raise ForecastContractError("missing must be a bool")
        if self.missing and self.value is not None:
            raise ForecastContractError("a missing feature value must be None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": None if self.missing else _thaw_json_safe(self.value),
            "available_at_utc": iso_z(self.available_at_utc, field_name="available_at_utc"),
            "missing": self.missing,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ForecastFeatureValue":
        body = _require_durable_mapping(
            raw,
            field_name="feature",
            expected_keys=("name", "value", "available_at_utc", "missing"),
        )
        return cls(
            name=body["name"],
            value=body["value"],
            available_at_utc=_parse_persisted_utc(
                body["available_at_utc"], field_name="available_at_utc"
            ),
            missing=body["missing"],
        )


def _input_schema_fingerprint(
    input_schema_id: str, input_schema_version: str, feature_names: tuple[str, ...]
) -> str:
    return stable_hash(
        FORECAST_INPUT_SCHEMA_FINGERPRINT_PREFIX,
        {
            "input_schema_id": input_schema_id,
            "input_schema_version": input_schema_version,
            "feature_names": sorted(feature_names),
        },
    )


_INPUT_VECTOR_DURABLE_KEYS: tuple[str, ...] = (
    "input_schema_id",
    "input_schema_version",
    "source_snapshot_id",
    "source_cutoff",
    "horizon",
    "features",
    "input_fingerprint",
)


@dataclass(frozen=True)
class ForecastInputVector:
    """The deterministic, point-in-time forecast input vector.

    It carries the declared input schema identity, the source snapshot and its
    cutoff, the explicit horizon contract, the normalized feature values and a
    deterministic ``FIN:`` fingerprint. The schema fingerprint is derived from
    the schema identity and the sorted feature names, so a model artifact that
    declares a different schema can never be applied to these inputs.
    """

    input_schema_id: str
    input_schema_version: str
    source_snapshot_id: str
    source_cutoff: datetime
    horizon: ForecastHorizon
    features: tuple[ForecastFeatureValue, ...]
    input_fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_schema_id",
            require_forecast_text(self.input_schema_id, field_name="input_schema_id"),
        )
        object.__setattr__(
            self,
            "input_schema_version",
            require_forecast_text(
                self.input_schema_version, field_name="input_schema_version"
            ),
        )
        object.__setattr__(
            self,
            "source_snapshot_id",
            require_forecast_text(self.source_snapshot_id, field_name="source_snapshot_id"),
        )
        object.__setattr__(
            self,
            "source_cutoff",
            require_forecast_utc(self.source_cutoff, field_name="source_cutoff"),
        )
        if not isinstance(self.horizon, ForecastHorizon):
            raise ForecastContractError("horizon must be a ForecastHorizon")
        if not isinstance(self.features, (list, tuple)):
            raise ForecastContractError("features must be a list of feature values")
        features = tuple(self.features)
        for feature in features:
            if not isinstance(feature, ForecastFeatureValue):
                raise ForecastContractError("features must be ForecastFeatureValue values")
        names = [feature.name for feature in features]
        if len(names) != len(set(names)):
            raise ForecastContractError("feature names must be unique")
        object.__setattr__(self, "features", features)

        expected = stable_hash(
            FORECAST_INPUT_FINGERPRINT_PREFIX,
            self._fingerprint_payload(),
        )
        if self.input_fingerprint == "":
            object.__setattr__(self, "input_fingerprint", expected)
        elif self.input_fingerprint != expected:
            raise ForecastContractError(
                "input_fingerprint does not match its inputs; build input vectors "
                "with the forecast engine"
            )
        else:
            object.__setattr__(self, "input_fingerprint", expected)

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(feature.name for feature in self.features)

    @property
    def input_schema_fingerprint(self) -> str:
        return _input_schema_fingerprint(
            self.input_schema_id, self.input_schema_version, self.feature_names
        )

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "input_schema_id": self.input_schema_id,
            "input_schema_version": self.input_schema_version,
            "source_snapshot_id": self.source_snapshot_id,
            "source_cutoff": iso_z(self.source_cutoff, field_name="source_cutoff"),
            "horizon": self.horizon.to_dict(),
            "features": [
                feature.to_dict()
                for feature in sorted(self.features, key=lambda item: item.name)
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_schema_id": self.input_schema_id,
            "input_schema_version": self.input_schema_version,
            "source_snapshot_id": self.source_snapshot_id,
            "source_cutoff": iso_z(self.source_cutoff, field_name="source_cutoff"),
            "horizon": self.horizon.to_dict(),
            "features": [feature.to_dict() for feature in self.features],
            "input_fingerprint": self.input_fingerprint,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ForecastInputVector":
        body = _require_durable_mapping(
            raw, field_name="input_vector", expected_keys=_INPUT_VECTOR_DURABLE_KEYS
        )
        raw_features = body["features"]
        if not isinstance(raw_features, (list, tuple)):
            raise ForecastContractError("input_vector.features must be a list")
        return cls(
            input_schema_id=body["input_schema_id"],
            input_schema_version=body["input_schema_version"],
            source_snapshot_id=body["source_snapshot_id"],
            source_cutoff=_parse_persisted_utc(
                body["source_cutoff"], field_name="source_cutoff"
            ),
            horizon=ForecastHorizon.from_dict(body["horizon"]),
            features=tuple(ForecastFeatureValue.from_dict(item) for item in raw_features),
            input_fingerprint=body["input_fingerprint"],
        )


# ---------------------------------------------------------------------------
# Model artifact contract
# ---------------------------------------------------------------------------


_ARTIFACT_DURABLE_KEYS: tuple[str, ...] = (
    "artifact_id",
    "artifact_schema_version",
    "model_kind",
    "model_family",
    "model_version",
    "model_status",
    "training_cutoff",
    "input_schema_id",
    "input_schema_version",
    "input_feature_names",
    "input_schema_fingerprint",
    "horizon_contract",
    "training_population_id",
    "evaluation_population_id",
    "calibration_report_id",
    "model_payload_ref",
    "parameters",
    "created_at",
    "released_at",
    "expires_at",
)


def _artifact_identity_payload(
    *,
    artifact_schema_version: str,
    model_kind: ForecastModelKind,
    model_family: str,
    model_version: str,
    model_status: ForecastModelStatus,
    training_cutoff: datetime,
    input_schema_id: str,
    input_schema_version: str,
    input_feature_names: tuple[str, ...],
    input_schema_fingerprint: str,
    horizon_contract: ForecastHorizon,
    training_population_id: str,
    evaluation_population_id: str,
    calibration_report_id: str,
    model_payload_ref: str,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "artifact_schema_version": artifact_schema_version,
        "model_kind": model_kind.value,
        "model_family": model_family,
        "model_version": model_version,
        "model_status": model_status.value,
        "training_cutoff": iso_z(training_cutoff, field_name="training_cutoff"),
        "input_schema_id": input_schema_id,
        "input_schema_version": input_schema_version,
        "input_feature_names": sorted(input_feature_names),
        "input_schema_fingerprint": input_schema_fingerprint,
        "horizon_contract": horizon_contract.to_dict(),
        "training_population_id": training_population_id,
        "evaluation_population_id": evaluation_population_id,
        "calibration_report_id": calibration_report_id,
        "model_payload_ref": model_payload_ref,
        "parameters": _require_json_safe(parameters, field_name="parameters"),
    }


@dataclass(frozen=True)
class ForecastModelArtifact:
    """One immutable, provenance-bearing forecast model artifact.

    The artifact carries the provenance needed to decide whether it may be
    applied at all: its schema/manifest version, family, version, status, the
    training cutoff, the input schema it expects, the horizon contract it was
    trained under, its training/evaluation populations and the calibration /
    evaluation report it names. ``created_at`` and ``released_at`` are
    informational metadata and never take part in the ``FMOD:`` identity, so they
    cannot influence forecast semantics.

    ``parameters`` are deterministic JSON-safe primitives only. No arbitrary
    executable Python, pickle, ``eval``/``exec`` or dynamic import is accepted,
    and the engine never fetches an external model.
    """

    artifact_id: str
    artifact_schema_version: str
    model_kind: ForecastModelKind
    model_family: str
    model_version: str
    model_status: ForecastModelStatus
    training_cutoff: datetime
    input_schema_id: str
    input_schema_version: str
    input_feature_names: tuple[str, ...]
    input_schema_fingerprint: str
    horizon_contract: ForecastHorizon
    training_population_id: str
    evaluation_population_id: str
    calibration_report_id: str
    model_payload_ref: str
    parameters: Mapping[str, Any]
    created_at: datetime
    released_at: datetime
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_schema_version",
            require_forecast_text(
                self.artifact_schema_version, field_name="artifact_schema_version"
            ),
        )
        if self.artifact_schema_version != FORECAST_MODEL_ARTIFACT_SCHEMA_VERSION:
            raise ForecastContractError(
                "artifact_schema_version is not the ratified "
                f"{FORECAST_MODEL_ARTIFACT_SCHEMA_VERSION}"
            )
        kind = require_forecast_enum(
            ForecastModelKind, self.model_kind, field_name="model_kind"
        )
        object.__setattr__(self, "model_kind", kind)
        status = require_forecast_enum(
            ForecastModelStatus, self.model_status, field_name="model_status"
        )
        object.__setattr__(self, "model_status", status)
        for name in (
            "model_family",
            "model_version",
            "input_schema_id",
            "input_schema_version",
            "model_payload_ref",
            "training_population_id",
            "evaluation_population_id",
        ):
            object.__setattr__(
                self, name, require_forecast_text(getattr(self, name), field_name=name)
            )
        object.__setattr__(
            self,
            "training_cutoff",
            require_forecast_utc(self.training_cutoff, field_name="training_cutoff"),
        )
        names = _require_text_tuple(
            self.input_feature_names, field_name="input_feature_names"
        )
        if len(names) != len(set(names)):
            raise ForecastContractError("input_feature_names must be unique")
        object.__setattr__(self, "input_feature_names", tuple(sorted(names)))
        expected_schema = _input_schema_fingerprint(
            self.input_schema_id, self.input_schema_version, self.input_feature_names
        )
        object.__setattr__(
            self,
            "input_schema_fingerprint",
            require_forecast_text(
                self.input_schema_fingerprint, field_name="input_schema_fingerprint"
            ),
        )
        if self.input_schema_fingerprint != expected_schema:
            raise ForecastContractError(
                "input_schema_fingerprint does not match the declared input schema"
            )
        if not isinstance(self.horizon_contract, ForecastHorizon):
            raise ForecastContractError("horizon_contract must be a ForecastHorizon")
        object.__setattr__(
            self,
            "parameters",
            _freeze_json_safe(self.parameters, field_name="parameters"),
        )

        report = self.calibration_report_id
        if not isinstance(report, str):
            raise ForecastContractError("calibration_report_id must be a string")
        if status is ForecastModelStatus.CALIBRATED_SHADOW:
            if not report.startswith("FEVAL:"):
                raise ForecastContractError(
                    "a CALIBRATED_SHADOW artifact must name a FEVAL: evaluation report"
                )
        elif report != "" and (report != report.strip()):
            raise ForecastContractError(
                "calibration_report_id must be whitespace-free when present"
            )
        object.__setattr__(self, "calibration_report_id", report)

        created = require_forecast_utc(self.created_at, field_name="created_at")
        released = require_forecast_utc(self.released_at, field_name="released_at")
        if released < created:
            raise ForecastContractError("released_at cannot precede created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "released_at", released)
        object.__setattr__(
            self,
            "expires_at",
            None
            if self.expires_at is None
            else require_forecast_utc(self.expires_at, field_name="expires_at"),
        )

        expected_id = stable_hash(
            FORECAST_MODEL_ARTIFACT_ID_PREFIX, self._identity_payload()
        )
        object.__setattr__(
            self,
            "artifact_id",
            require_forecast_text(self.artifact_id, field_name="artifact_id"),
        )
        if self.artifact_id != expected_id:
            raise ForecastContractError(
                "artifact_id does not match its provenance; build artifacts with the "
                "forecast model artifact builder"
            )

    def _identity_payload(self) -> dict[str, Any]:
        return _artifact_identity_payload(
            artifact_schema_version=self.artifact_schema_version,
            model_kind=self.model_kind,
            model_family=self.model_family,
            model_version=self.model_version,
            model_status=self.model_status,
            training_cutoff=self.training_cutoff,
            input_schema_id=self.input_schema_id,
            input_schema_version=self.input_schema_version,
            input_feature_names=self.input_feature_names,
            input_schema_fingerprint=self.input_schema_fingerprint,
            horizon_contract=self.horizon_contract,
            training_population_id=self.training_population_id,
            evaluation_population_id=self.evaluation_population_id,
            calibration_report_id=self.calibration_report_id,
            model_payload_ref=self.model_payload_ref,
            parameters=self.parameters,
        )

    @classmethod
    def build(cls, **kwargs: Any) -> "ForecastModelArtifact":
        """Build an artifact, deriving the deterministic ``FMOD:`` identity."""
        payload = dict(kwargs)
        payload.setdefault(
            "artifact_schema_version", FORECAST_MODEL_ARTIFACT_SCHEMA_VERSION
        )
        payload.setdefault("expires_at", None)
        payload["input_feature_names"] = tuple(payload.get("input_feature_names") or ())
        payload["parameters"] = payload.get("parameters") or {}
        required = (
            "model_kind",
            "model_family",
            "model_version",
            "model_status",
            "training_cutoff",
            "input_schema_id",
            "input_schema_version",
            "input_feature_names",
            "horizon_contract",
            "training_population_id",
            "evaluation_population_id",
            "calibration_report_id",
            "model_payload_ref",
            "parameters",
            "created_at",
            "released_at",
        )
        missing = [name for name in required if name not in payload]
        if missing:
            raise ForecastContractError(
                "missing ForecastModelArtifact fields: " + ", ".join(missing)
            )
        # Validate the typed and JSON-safe fields before deriving the identity, so
        # a malformed artifact raises a contract error rather than leaking a raw
        # AttributeError/TypeError from identity derivation.
        payload["model_kind"] = require_forecast_enum(
            ForecastModelKind, payload["model_kind"], field_name="model_kind"
        )
        payload["model_status"] = require_forecast_enum(
            ForecastModelStatus, payload["model_status"], field_name="model_status"
        )
        if not isinstance(payload["horizon_contract"], ForecastHorizon):
            raise ForecastContractError("horizon_contract must be a ForecastHorizon")
        for name in ("training_cutoff", "created_at", "released_at"):
            payload[name] = require_forecast_utc(payload[name], field_name=name)
        if payload["expires_at"] is not None:
            payload["expires_at"] = require_forecast_utc(
                payload["expires_at"], field_name="expires_at"
            )
        payload["parameters"] = _require_json_safe(
            payload["parameters"], field_name="parameters"
        )
        payload["input_schema_fingerprint"] = _input_schema_fingerprint(
            payload["input_schema_id"],
            payload["input_schema_version"],
            payload["input_feature_names"],
        )
        payload["artifact_id"] = stable_hash(
            FORECAST_MODEL_ARTIFACT_ID_PREFIX,
            _artifact_identity_payload(
                artifact_schema_version=payload["artifact_schema_version"],
                model_kind=payload["model_kind"],
                model_family=payload["model_family"],
                model_version=payload["model_version"],
                model_status=payload["model_status"],
                training_cutoff=payload["training_cutoff"],
                input_schema_id=payload["input_schema_id"],
                input_schema_version=payload["input_schema_version"],
                input_feature_names=payload["input_feature_names"],
                input_schema_fingerprint=payload["input_schema_fingerprint"],
                horizon_contract=payload["horizon_contract"],
                training_population_id=payload["training_population_id"],
                evaluation_population_id=payload["evaluation_population_id"],
                calibration_report_id=payload["calibration_report_id"],
                model_payload_ref=payload["model_payload_ref"],
                parameters=payload["parameters"],
            ),
        )
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_schema_version": self.artifact_schema_version,
            "model_kind": self.model_kind.value,
            "model_family": self.model_family,
            "model_version": self.model_version,
            "model_status": self.model_status.value,
            "training_cutoff": iso_z(self.training_cutoff, field_name="training_cutoff"),
            "input_schema_id": self.input_schema_id,
            "input_schema_version": self.input_schema_version,
            "input_feature_names": list(self.input_feature_names),
            "input_schema_fingerprint": self.input_schema_fingerprint,
            "horizon_contract": self.horizon_contract.to_dict(),
            "training_population_id": self.training_population_id,
            "evaluation_population_id": self.evaluation_population_id,
            "calibration_report_id": self.calibration_report_id,
            "model_payload_ref": self.model_payload_ref,
            "parameters": _thaw_json_safe(self.parameters),
            "created_at": iso_z(self.created_at, field_name="created_at"),
            "released_at": iso_z(self.released_at, field_name="released_at"),
            "expires_at": (
                None
                if self.expires_at is None
                else iso_z(self.expires_at, field_name="expires_at")
            ),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ForecastModelArtifact":
        body = _require_durable_mapping(
            raw, field_name="artifact", expected_keys=_ARTIFACT_DURABLE_KEYS
        )
        raw_names = body["input_feature_names"]
        if not isinstance(raw_names, (list, tuple)):
            raise ForecastContractError("artifact.input_feature_names must be a list")
        return cls(
            artifact_id=body["artifact_id"],
            artifact_schema_version=body["artifact_schema_version"],
            model_kind=body["model_kind"],
            model_family=body["model_family"],
            model_version=body["model_version"],
            model_status=body["model_status"],
            training_cutoff=_parse_persisted_utc(
                body["training_cutoff"], field_name="training_cutoff"
            ),
            input_schema_id=body["input_schema_id"],
            input_schema_version=body["input_schema_version"],
            input_feature_names=tuple(raw_names),
            input_schema_fingerprint=body["input_schema_fingerprint"],
            horizon_contract=ForecastHorizon.from_dict(body["horizon_contract"]),
            training_population_id=body["training_population_id"],
            evaluation_population_id=body["evaluation_population_id"],
            calibration_report_id=body["calibration_report_id"],
            model_payload_ref=body["model_payload_ref"],
            parameters=body["parameters"],
            created_at=_parse_persisted_utc(body["created_at"], field_name="created_at"),
            released_at=_parse_persisted_utc(body["released_at"], field_name="released_at"),
            expires_at=_parse_optional_persisted_utc(
                body["expires_at"], field_name="expires_at"
            ),
        )


@dataclass(frozen=True)
class ForecastModelOutput:
    """One trusted adapter's raw output, before the engine validates it.

    The engine validates every field and refuses to use an output whose
    distributions are invalid or whose uncertainty is absent when the contract
    requires it.
    """

    entry_distribution: ProbabilityDistribution
    post_fill_distribution: ProbabilityDistribution
    expected_return_unconditional: float
    expected_return_conditional_on_fill: float | None = None
    uncertainty: ForecastUncertainty | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.entry_distribution, ProbabilityDistribution):
            raise ForecastContractError("entry_distribution must be a distribution")
        if self.entry_distribution.kind is not DistributionKind.ENTRY_EXECUTION:
            raise ForecastContractError(
                "entry_distribution must describe the entry-execution outcomes"
            )
        if not isinstance(self.post_fill_distribution, ProbabilityDistribution):
            raise ForecastContractError("post_fill_distribution must be a distribution")
        if self.post_fill_distribution.kind is not DistributionKind.POST_FILL_PATH:
            raise ForecastContractError(
                "post_fill_distribution must describe the conditional post-fill outcomes"
            )
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
        if self.uncertainty is not None and not isinstance(
            self.uncertainty, ForecastUncertainty
        ):
            raise ForecastContractError("uncertainty must be a ForecastUncertainty")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastPolicy:
    """The applied, versioned forecast policy.

    The policy is a deterministic code artifact. A policy that does not carry the
    ratified version tokens is refused rather than silently substituted, so a
    caller cannot select an unratified policy.
    """

    decision_schema_version: str = FORECAST_DECISION_SCHEMA_VERSION
    engine_version: str = FORECAST_ENGINE_VERSION
    policy_version: str = FORECAST_POLICY_VERSION
    model_artifact_schema_version: str = FORECAST_MODEL_ARTIFACT_SCHEMA_VERSION
    evaluation_version: str = FORECAST_EVALUATION_VERSION

    def __post_init__(self) -> None:
        for name, expected in (
            ("decision_schema_version", FORECAST_DECISION_SCHEMA_VERSION),
            ("engine_version", FORECAST_ENGINE_VERSION),
            ("policy_version", FORECAST_POLICY_VERSION),
            ("model_artifact_schema_version", FORECAST_MODEL_ARTIFACT_SCHEMA_VERSION),
            ("evaluation_version", FORECAST_EVALUATION_VERSION),
        ):
            value = require_forecast_text(getattr(self, name), field_name=name)
            if value != expected:
                raise ForecastContractError(f"{name} is not the ratified {expected}")
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_schema_version": self.decision_schema_version,
            "engine_version": self.engine_version,
            "policy_version": self.policy_version,
            "model_artifact_schema_version": self.model_artifact_schema_version,
            "evaluation_version": self.evaluation_version,
        }


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastRequest:
    """The immutable, validated bundle of one pure forecast request.

    The request binds the preserved F4/F5 lineage ids, the input vector (which
    carries the explicit horizon), the explicit evaluation time, the applied
    policy and the optional model artifact. It is the engine's declared input and
    is serialization-ready.
    """

    episode_id: str
    feasibility_decision_id: str
    input_vector: ForecastInputVector
    evaluation_time: datetime
    policy: ForecastPolicy
    model_artifact: ForecastModelArtifact | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "episode_id",
            require_forecast_text(self.episode_id, field_name="episode_id"),
        )
        object.__setattr__(
            self,
            "feasibility_decision_id",
            require_forecast_text(
                self.feasibility_decision_id, field_name="feasibility_decision_id"
            ),
        )
        if not isinstance(self.input_vector, ForecastInputVector):
            raise ForecastContractError("input_vector must be a ForecastInputVector")
        object.__setattr__(
            self,
            "evaluation_time",
            require_forecast_utc(self.evaluation_time, field_name="evaluation_time"),
        )
        if not isinstance(self.policy, ForecastPolicy):
            raise ForecastContractError("policy must be a ForecastPolicy")
        if self.model_artifact is not None and not isinstance(
            self.model_artifact, ForecastModelArtifact
        ):
            raise ForecastContractError("model_artifact must be a ForecastModelArtifact")

    @property
    def horizon(self) -> ForecastHorizon:
        return self.input_vector.horizon

    @property
    def input_fingerprint(self) -> str:
        return self.input_vector.input_fingerprint

    @property
    def model_artifact_id(self) -> str | None:
        return None if self.model_artifact is None else self.model_artifact.artifact_id


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


_DECISION_DURABLE_KEYS: tuple[str, ...] = (
    "decision_id",
    "decision_schema_version",
    "engine_version",
    "policy_version",
    "status",
    "abstention_reason",
    "episode_id",
    "feasibility_decision_id",
    "input_fingerprint",
    "evaluation_time",
    "horizon",
    "valid_until",
    "model_artifact_id",
    "entry_distribution",
    "post_fill_distribution",
    "expected_return_unconditional",
    "expected_return_conditional_on_fill",
    "uncertainty",
    "evidence_references",
)


def forecast_decision_identity(
    *,
    decision_schema_version: str,
    status: ForecastStatus | str,
    abstention_reason: ForecastAbstentionReason | str | None,
    episode_id: str,
    feasibility_decision_id: str,
    input_fingerprint: str,
    evaluation_time: datetime,
    horizon_identity: str | None,
    model_artifact_id: str | None,
    engine_version: str,
    policy_version: str,
    valid_until: datetime | None,
    entry_distribution: ProbabilityDistribution | None,
    post_fill_distribution: ProbabilityDistribution | None,
    expected_return_unconditional: float | None,
    expected_return_conditional_on_fill: float | None,
    uncertainty: ForecastUncertainty | None,
    evidence_references: tuple[str, ...],
) -> str:
    """The deterministic ``FCST:<digest>`` identity of one forecast decision.

    Identity binds the decision schema version, the preserved F4/F5 lineage, the
    input fingerprint, the explicit evaluation time, the horizon identity, the
    model artifact id, the engine version, the policy version and the decision's
    own semantic payload (status, abstention reason and any forecast outputs).
    Binding the status and payload makes a durable record tamper-evident: a
    changed disposition, output or copied lineage field changes the identity, so
    a forged decision fails closed. Abstentions are equally deterministic: the
    same semantic inputs and the same reason yield the same identity, while a
    different reason changes it. No UUID, receipt timestamp, retry count, process
    identity or database sequence takes part.
    """
    status_token = require_forecast_enum(ForecastStatus, status, field_name="status")
    if abstention_reason is None:
        reason_token: str | None = None
    else:
        reason_token = require_forecast_enum(
            ForecastAbstentionReason, abstention_reason, field_name="abstention_reason"
        ).value
    instant = require_forecast_utc(evaluation_time, field_name="evaluation_time")
    payload: dict[str, Any] = {
        "decision_schema_version": require_forecast_text(
            decision_schema_version, field_name="decision_schema_version"
        ),
        "status": status_token.value,
        "abstention_reason": reason_token,
        "episode_id": require_forecast_text(episode_id, field_name="episode_id"),
        "feasibility_decision_id": require_forecast_text(
            feasibility_decision_id, field_name="feasibility_decision_id"
        ),
        "input_fingerprint": require_forecast_text(
            input_fingerprint, field_name="input_fingerprint"
        ),
        "evaluation_time": iso_z(instant, field_name="evaluation_time"),
        "horizon_identity": (
            None
            if horizon_identity is None
            else require_forecast_text(horizon_identity, field_name="horizon_identity")
        ),
        "model_artifact_id": (
            None
            if model_artifact_id is None
            else require_forecast_text(model_artifact_id, field_name="model_artifact_id")
        ),
        "engine_version": require_forecast_text(engine_version, field_name="engine_version"),
        "policy_version": require_forecast_text(policy_version, field_name="policy_version"),
        "valid_until": (
            None
            if valid_until is None
            else iso_z(require_forecast_utc(valid_until, field_name="valid_until"), field_name="valid_until")
        ),
        "entry_distribution": (
            None if entry_distribution is None else entry_distribution.to_dict()
        ),
        "post_fill_distribution": (
            None if post_fill_distribution is None else post_fill_distribution.to_dict()
        ),
        "expected_return_unconditional": expected_return_unconditional,
        "expected_return_conditional_on_fill": expected_return_conditional_on_fill,
        "uncertainty": None if uncertainty is None else uncertainty.to_dict(),
        "evidence_references": sorted(
            require_forecast_text(item, field_name="evidence_reference")
            for item in evidence_references
        ),
    }
    return stable_hash(FORECAST_DECISION_ID_PREFIX, payload)


@dataclass(frozen=True)
class ForecastDecision:
    """One immutable forecast decision and its preserved F4/F5 lineage.

    A ``FORECAST`` decision carries the execution-aware entry distribution, the
    explicitly conditional post-fill path distribution, the unconditional (and
    optionally conditional) expected net return, the explicit uncertainty and the
    explicit horizon with a derived ``valid_until``. An ``INSUFFICIENT_EVIDENCE``
    decision carries a machine-readable abstention reason and no fabricated
    outputs. The decision is immutable and its ``FCST:`` identity is re-derived on
    construction, so a forged identity fails closed.
    """

    decision_id: str
    decision_schema_version: str
    engine_version: str
    policy_version: str
    status: ForecastStatus
    abstention_reason: ForecastAbstentionReason | None
    episode_id: str
    feasibility_decision_id: str
    input_fingerprint: str
    evaluation_time: datetime
    horizon: ForecastHorizon | None
    valid_until: datetime | None
    model_artifact_id: str | None
    entry_distribution: ProbabilityDistribution | None
    post_fill_distribution: ProbabilityDistribution | None
    expected_return_unconditional: float | None
    expected_return_conditional_on_fill: float | None = None
    uncertainty: ForecastUncertainty | None = None
    evidence_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, expected in (
            ("decision_schema_version", FORECAST_DECISION_SCHEMA_VERSION),
            ("engine_version", FORECAST_ENGINE_VERSION),
            ("policy_version", FORECAST_POLICY_VERSION),
        ):
            if getattr(self, name) != expected:
                raise ForecastContractError(f"{name} is not the ratified {expected}")
        object.__setattr__(
            self,
            "decision_id",
            require_forecast_text(self.decision_id, field_name="decision_id"),
        )
        for name in (
            "episode_id",
            "feasibility_decision_id",
            "input_fingerprint",
        ):
            object.__setattr__(
                self, name, require_forecast_text(getattr(self, name), field_name=name)
            )
        if self.model_artifact_id is not None:
            object.__setattr__(
                self,
                "model_artifact_id",
                require_forecast_text(self.model_artifact_id, field_name="model_artifact_id"),
            )
        status = require_forecast_enum(ForecastStatus, self.status, field_name="status")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "evaluation_time",
            require_forecast_utc(self.evaluation_time, field_name="evaluation_time"),
        )
        if self.horizon is not None and not isinstance(self.horizon, ForecastHorizon):
            raise ForecastContractError("horizon must be a ForecastHorizon")
        if self.valid_until is not None:
            object.__setattr__(
                self,
                "valid_until",
                require_forecast_utc(self.valid_until, field_name="valid_until"),
            )
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
        references = tuple(
            require_forecast_text(item, field_name="evidence_reference")
            for item in self.evidence_references
        )
        object.__setattr__(self, "evidence_references", references)

        reason = self.abstention_reason
        if reason is not None:
            reason = require_forecast_enum(
                ForecastAbstentionReason, reason, field_name="abstention_reason"
            )
        object.__setattr__(self, "abstention_reason", reason)

        self._validate_status_payload()
        self._validate_identity()

    def _validate_status_payload(self) -> None:
        if self.status is ForecastStatus.FORECAST:
            if self.abstention_reason is not None:
                raise ForecastContractError(
                    "a FORECAST decision carries no abstention reason"
                )
            if self.model_artifact_id is None:
                raise ForecastContractError(
                    "a FORECAST decision must name its model artifact"
                )
            if self.horizon is None or self.valid_until is None:
                raise ForecastContractError(
                    "a FORECAST decision requires an explicit horizon and valid_until"
                )
            if self.valid_until <= self.evaluation_time:
                raise ForecastContractError(
                    "valid_until must be after evaluation_time"
                )
            if self.entry_distribution is None or (
                self.entry_distribution.kind is not DistributionKind.ENTRY_EXECUTION
            ):
                raise ForecastContractError(
                    "a FORECAST decision requires an entry-execution distribution"
                )
            if self.post_fill_distribution is None or (
                self.post_fill_distribution.kind is not DistributionKind.POST_FILL_PATH
            ):
                raise ForecastContractError(
                    "a FORECAST decision requires a conditional post-fill distribution"
                )
            if self.expected_return_unconditional is None:
                raise ForecastContractError(
                    "a FORECAST decision requires an unconditional expected net return"
                )
            expected_return = require_finite_number(
                self.expected_return_unconditional,
                field_name="expected_return_unconditional",
            )
            object.__setattr__(self, "expected_return_unconditional", expected_return)
            if self.uncertainty is None:
                raise ForecastContractError(
                    "a FORECAST decision requires an explicit uncertainty"
                )
            if not self.uncertainty.contains(expected_return):
                raise ForecastContractError(
                    "the expected return must lie within the uncertainty interval"
                )
            return

        # INSUFFICIENT_EVIDENCE
        if self.abstention_reason is None:
            raise ForecastContractError(
                "an INSUFFICIENT_EVIDENCE decision requires an abstention reason"
            )
        if any(
            value is not None
            for value in (
                self.horizon,
                self.valid_until,
                self.entry_distribution,
                self.post_fill_distribution,
                self.expected_return_unconditional,
                self.expected_return_conditional_on_fill,
                self.uncertainty,
            )
        ):
            raise ForecastContractError(
                "an INSUFFICIENT_EVIDENCE decision carries no fabricated outputs"
            )

    def _validate_identity(self) -> None:
        expected = forecast_decision_identity(
            decision_schema_version=self.decision_schema_version,
            status=self.status,
            abstention_reason=self.abstention_reason,
            episode_id=self.episode_id,
            feasibility_decision_id=self.feasibility_decision_id,
            input_fingerprint=self.input_fingerprint,
            evaluation_time=self.evaluation_time,
            horizon_identity=(
                None if self.horizon is None else forecast_horizon_identity(self.horizon)
            ),
            model_artifact_id=self.model_artifact_id,
            engine_version=self.engine_version,
            policy_version=self.policy_version,
            valid_until=self.valid_until,
            entry_distribution=self.entry_distribution,
            post_fill_distribution=self.post_fill_distribution,
            expected_return_unconditional=self.expected_return_unconditional,
            expected_return_conditional_on_fill=self.expected_return_conditional_on_fill,
            uncertainty=self.uncertainty,
            evidence_references=self.evidence_references,
        )
        if self.decision_id != expected:
            raise ForecastContractError(
                "decision_id does not match its binding; build decisions with the "
                "forecast engine"
            )

    @property
    def horizon_identity(self) -> str | None:
        return None if self.horizon is None else forecast_horizon_identity(self.horizon)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "decision_schema_version": self.decision_schema_version,
            "engine_version": self.engine_version,
            "policy_version": self.policy_version,
            "status": self.status.value,
            "abstention_reason": (
                None if self.abstention_reason is None else self.abstention_reason.value
            ),
            "episode_id": self.episode_id,
            "feasibility_decision_id": self.feasibility_decision_id,
            "input_fingerprint": self.input_fingerprint,
            "evaluation_time": iso_z(self.evaluation_time, field_name="evaluation_time"),
            "horizon": None if self.horizon is None else self.horizon.to_dict(),
            "valid_until": (
                None
                if self.valid_until is None
                else iso_z(self.valid_until, field_name="valid_until")
            ),
            "model_artifact_id": self.model_artifact_id,
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
            "evidence_references": list(self.evidence_references),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ForecastDecision":
        """Reconstitute one durable decision, re-running every constructor invariant.

        This is a durability trust boundary: the mapping must carry exactly the
        canonical key set, tokens are never coerced, the evaluation instant must
        be an explicit UTC ISO-8601 string, and the reconstructed decision is put
        back through ``__post_init__`` so its identity and status payload are
        re-verified. A malformed, drifted or forged durable record is refused.
        """
        body = _require_durable_mapping(
            raw, field_name="decision", expected_keys=_DECISION_DURABLE_KEYS
        )
        references = body["evidence_references"]
        if not isinstance(references, (list, tuple)):
            raise ForecastContractError("decision.evidence_references must be a list")
        return cls(
            decision_id=body["decision_id"],
            decision_schema_version=body["decision_schema_version"],
            engine_version=body["engine_version"],
            policy_version=body["policy_version"],
            status=body["status"],
            abstention_reason=body["abstention_reason"],
            episode_id=body["episode_id"],
            feasibility_decision_id=body["feasibility_decision_id"],
            input_fingerprint=body["input_fingerprint"],
            evaluation_time=_parse_persisted_utc(
                body["evaluation_time"], field_name="evaluation_time"
            ),
            horizon=(
                None
                if body["horizon"] is None
                else ForecastHorizon.from_dict(body["horizon"])
            ),
            valid_until=_parse_optional_persisted_utc(
                body["valid_until"], field_name="valid_until"
            ),
            model_artifact_id=body["model_artifact_id"],
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
            evidence_references=tuple(references),
        )


__all__ = [
    "DistributionKind",
    "ENTRY_EXECUTION_OUTCOMES",
    "EntryExecutionOutcome",
    "FORECAST_DECISION_ID_PREFIX",
    "FORECAST_DECISION_SCHEMA_VERSION",
    "FORECAST_ENGINE_VERSION",
    "FORECAST_EVALUATION_VERSION",
    "FORECAST_HORIZON_ID_PREFIX",
    "FORECAST_INPUT_FINGERPRINT_PREFIX",
    "FORECAST_INPUT_SCHEMA_FINGERPRINT_PREFIX",
    "FORECAST_MODEL_ARTIFACT_ID_PREFIX",
    "FORECAST_MODEL_ARTIFACT_SCHEMA_VERSION",
    "FORECAST_POLICY_VERSION",
    "ForecastAbstentionReason",
    "ForecastContractError",
    "ForecastDecision",
    "ForecastFeatureValue",
    "ForecastHorizon",
    "ForecastHorizonAnchor",
    "ForecastInputVector",
    "ForecastModelArtifact",
    "ForecastModelKind",
    "ForecastModelOutput",
    "ForecastModelStatus",
    "ForecastPolicy",
    "ForecastRequest",
    "ForecastStatus",
    "ForecastUncertainty",
    "POST_FILL_PATH_OUTCOMES",
    "PostFillPathOutcome",
    "PROBABILITY_SUM_TOLERANCE",
    "ProbabilityDistribution",
    "forecast_decision_identity",
    "forecast_horizon_identity",
    "require_forecast_enum",
    "require_forecast_text",
    "require_forecast_utc",
    "require_finite_number",
    "require_non_negative_int",
    "require_positive_int",
    "require_probability",
]
