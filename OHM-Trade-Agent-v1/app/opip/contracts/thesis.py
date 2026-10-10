"""Horizon thesis contract for the R5-A Continuous Opportunity Engine slice.

Adopted authority: `docs/architecture/v1.5.0/ARCHITECTURE.md` section 4
(`HZ-01`, `HZ-02`), section 10's `OpportunityThesis` record lineage and the
R5-0 structural contract `SC-04`. This module extends the existing
`app/opip/contracts/*` ownership family; it introduces no second detector, no
store, no scheduler and no lifecycle machine.

Two boundaries are deliberate:

* Horizon is a *versioned property of a thesis*, never an after-the-fact
  description of how long a position happened to stay open. The original thesis
  revision is immutable: an extension or reclassification produces a new linked
  revision with a new decision identity and a later evidence cutoff
  (`HZ-02`).
* Horizon boundary durations and expected holding ranges are OWNER inputs
  (`OQ-03`). This module invents no number. A policy whose durations are not
  supplied abstains to `UNKNOWN` with `POLICY_NOT_CONFIGURED` rather than
  defaulting, guessing or borrowing a value. `UNKNOWN` can never receive an
  allocation, and `IGNITION` alone is never sufficient structural evidence for
  a `POSITION` thesis.

Nothing here reads a clock, the environment, the filesystem, the network or a
database, and nothing here carries trading, admission, allocation, reservation
or execution authority: R5-A classifies in shadow/read-only evidence only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import TemporalIntegrityError, require_utc

#: Schema version of the durable/emitted horizon thesis record.
HORIZON_THESIS_SCHEMA_VERSION = "horizon-thesis-v1"

#: Applied classifier implementation version. A deterministic code artifact.
HORIZON_CLASSIFIER_VERSION = "horizon-classifier-v1"

#: The owner-gated policy token. Its numeric surface is `OQ-03` and is not
#: ratified, so a policy carrying this token with durations supplied is still
#: recorded as unratified engineering evidence.
HORIZON_POLICY_VERSION = "horizon-shadow-policy-v1"

#: Explicitly labelled engineering-scenario policy token, for measurements that
#: must be reproducible without pretending an owner ratified the numbers.
HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION = "horizon-engineering-scenario-v1"

DECLARED_POLICY_VERSIONS: tuple[str, ...] = (
    HORIZON_POLICY_VERSION,
    HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION,
)

#: Register row `OQ-03` remains open; this module never claims otherwise.
OWNER_INPUT_STATUS = "OWNER_DECISION_REQUIRED"

THESIS_ID_PREFIX = "OTHS"

class HorizonContractError(ValueError):
    """A structural contract violation. Always fails closed."""


class HorizonClass(str, Enum):
    """The adopted horizon classes plus the research-only abstention."""

    TACTICAL = "TACTICAL"
    SWING = "SWING"
    POSITION = "POSITION"
    UNKNOWN = "UNKNOWN"


#: Horizon classes that may carry a numeric boundary. `UNKNOWN` may not: it is
#: the abstention, not a fourth duration band.
BOUNDED_CLASSES: tuple[HorizonClass, ...] = (
    HorizonClass.TACTICAL,
    HorizonClass.SWING,
    HorizonClass.POSITION,
)


class AbstentionReason(str, Enum):
    """Why a thesis abstained. `NONE` means the class was supported."""

    NONE = "NONE"
    POLICY_NOT_CONFIGURED = "POLICY_NOT_CONFIGURED"
    MISSING_HOLDING_EVIDENCE = "MISSING_HOLDING_EVIDENCE"
    UNSUPPORTED_HORIZON = "UNSUPPORTED_HORIZON"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    INSUFFICIENT_STRUCTURAL_EVIDENCE = "INSUFFICIENT_STRUCTURAL_EVIDENCE"
    MISSING_LIQUIDITY_SUPPORT = "MISSING_LIQUIDITY_SUPPORT"
    MISSING_TIME_INVALIDATION = "MISSING_TIME_INVALIDATION"
    MISSING_ENTRY_WINDOW = "MISSING_ENTRY_WINDOW"
    RELABEL_REFUSED_PENDING_STRUCTURAL_EVIDENCE = (
        "RELABEL_REFUSED_PENDING_STRUCTURAL_EVIDENCE"
    )


class ClassificationUncertainty(str, Enum):
    """How well supported a classification is. Never a probability."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class ThesisLifecycleState(str, Enum):
    """R5-A observation of a thesis revision. Not an F8 position lifecycle."""

    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value == "" or value != value.strip():
        raise HorizonContractError(
            f"{field_name} must be a non-empty, whitespace-free token"
        )
    return value


def _require_utc(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise HorizonContractError(f"{field_name} must be an explicit datetime")
    try:
        return require_utc(value, field_name=field_name)
    except TemporalIntegrityError as exc:
        raise HorizonContractError(str(exc)) from exc


def _optional_utc(value: Any, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _require_utc(value, field_name=field_name)


def require_horizon_class(value: Any, *, field_name: str) -> HorizonClass:
    if isinstance(value, HorizonClass):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise HorizonContractError(f"{field_name} must be a HorizonClass token")
    if isinstance(value, str):
        try:
            return HorizonClass(value)
        except ValueError as exc:
            raise HorizonContractError(
                f"{field_name} has an unsupported token: {value!r}"
            ) from exc
    raise HorizonContractError(f"{field_name} must be a HorizonClass token")


@dataclass(frozen=True)
class HorizonPolicy:
    """The applied horizon policy.

    `holding_bounds_seconds` are the owner-gated horizon boundaries: a mapping
    from a bounded class to an inclusive `(min_seconds, max_seconds)` band. This
    module ships no band, and a policy without bands abstains.
    """

    policy_version: str = HORIZON_POLICY_VERSION
    holding_bounds_seconds: Mapping[HorizonClass, tuple[int, int]] | None = None
    #: `None` resolves to the unratified default requirement set in
    #: `__post_init__`, so declaration order inside this module creates no
    #: import-order dependency and no mutable shared default.
    requirements: HorizonRequirements | None = None

    def __post_init__(self) -> None:
        version = _require_text(self.policy_version, field_name="policy_version")
        if version not in DECLARED_POLICY_VERSIONS:
            raise HorizonContractError(
                f"policy_version is not a declared token: {version!r}"
            )
        object.__setattr__(self, "policy_version", version)
        if self.requirements is not None and not isinstance(
            self.requirements, HorizonRequirements
        ):
            raise HorizonContractError("requirements must be HorizonRequirements")
        object.__setattr__(
            self,
            "requirements",
            HorizonRequirements() if self.requirements is None else self.requirements,
        )
        bounds = self.holding_bounds_seconds
        if bounds is None:
            return
        if not isinstance(bounds, Mapping) or not bounds:
            raise HorizonContractError(
                "holding_bounds_seconds must be a non-empty mapping or None"
            )
        cleaned: dict[HorizonClass, tuple[int, int]] = {}
        for key, value in bounds.items():
            horizon = require_horizon_class(key, field_name="holding_bounds_seconds")
            if horizon not in BOUNDED_CLASSES:
                raise HorizonContractError(
                    "holding_bounds_seconds may only bound TACTICAL, SWING or POSITION"
                )
            if horizon in cleaned:
                raise HorizonContractError(f"duplicate horizon band: {horizon.value}")
            if not isinstance(value, tuple) or len(value) != 2:
                raise HorizonContractError(
                    "each holding band must be an inclusive (min, max) tuple"
                )
            low, high = value
            for name, number in (("min", low), ("max", high)):
                if isinstance(number, bool) or not isinstance(number, int):
                    raise HorizonContractError(f"holding band {name} must be an int")
                if number <= 0:
                    raise HorizonContractError(f"holding band {name} must be positive")
            if low > high:
                raise HorizonContractError("holding band min must not exceed max")
            cleaned[horizon] = (int(low), int(high))
        ordered = sorted(
            cleaned.items(), key=lambda item: BOUNDED_CLASSES.index(item[0])
        )
        for index in range(1, len(ordered)):
            if ordered[index - 1][1][1] >= ordered[index][1][0]:
                raise HorizonContractError(
                    "horizon bands must be ordered and non-overlapping"
                )
        object.__setattr__(self, "holding_bounds_seconds", MappingProxyType(dict(ordered)))

    @property
    def configured(self) -> bool:
        """True only when the owner-gated numeric surface is supplied."""

        return self.holding_bounds_seconds is not None

    @property
    def ratified(self) -> bool:
        """A policy can never be ratified by this module.

        `OQ-03` is an owner decision. Even a fully supplied policy is recorded
        as unratified engineering evidence until the OWNER ratifies one.
        """

        return False

    @property
    def owner_input_status(self) -> str:
        return OWNER_INPUT_STATUS

    def band_for(self, horizon: HorizonClass) -> tuple[int, int] | None:
        bounds = self.holding_bounds_seconds
        if bounds is None:
            return None
        return bounds.get(horizon)

    def band_for_holding(self, seconds: int) -> HorizonClass | None:
        """Return the declared class whose band contains `seconds`."""

        bounds = self.holding_bounds_seconds
        if bounds is None:
            return None
        for horizon, (low, high) in bounds.items():
            if horizon in BOUNDED_CLASSES and low <= seconds <= high:
                return horizon
        return None


@dataclass(frozen=True)
class HorizonRequirements:
    """Owner-gated structural gates applied by the classifier.

    Every numeric field is `None` in the unratified default state (`OQ-03`),
    which makes the classifier abstain instead of applying an agent-chosen rule.
    """

    position_requires_structural_evidence: bool = True
    position_min_structural_milestones: int | None = None
    position_requires_liquidity_support: bool = True
    requires_time_invalidation: bool = True
    requires_entry_window: bool = False

    def __post_init__(self) -> None:
        milestones = self.position_min_structural_milestones
        if milestones is not None:
            if isinstance(milestones, bool) or not isinstance(milestones, int):
                raise HorizonContractError(
                    "position_min_structural_milestones must be an integer or None"
                )
            if milestones < 1:
                raise HorizonContractError(
                    "position_min_structural_milestones must be >= 1"
                )
        for name in (
            "position_requires_structural_evidence",
            "position_requires_liquidity_support",
            "requires_time_invalidation",
            "requires_entry_window",
        ):
            if not isinstance(getattr(self, name), bool):
                raise HorizonContractError(f"{name} must be a bool")

    @property
    def numerics_supplied(self) -> bool:
        return self.position_min_structural_milestones is not None



@dataclass(frozen=True)
class HorizonEvidence:
    """Point-in-time evidence a classification may rely on.

    Every field is explicit and caller-supplied. `detector_support` records that
    the inherited IGNITION family claimed an episode; `structural_support` is
    the separate, stronger evidence a POSITION thesis needs, because the adopted
    clauses state that IGNITION alone cannot establish a structural thesis.
    """

    episode_id: str
    claim_id: str
    decision_id: str
    evidence_cutoff: datetime
    classified_at: datetime
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    detector_support: bool = False
    structural_support: bool = False
    structural_milestones: int = 0
    liquidity_support: bool = False
    expected_holding_seconds: int | None = None
    entry_window_start: datetime | None = None
    entry_window_end: datetime | None = None
    thesis_horizon: datetime | None = None
    price_invalidation: float | None = None
    time_invalidation: datetime | None = None
    profit_taking_policy_reference: str | None = None
    expected_capital_occupancy_seconds: int | None = None
    forecast_reference: str | None = None

    def __post_init__(self) -> None:
        for name in ("episode_id", "claim_id", "decision_id"):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), field_name=name)
            )
        cutoff = _require_utc(self.evidence_cutoff, field_name="evidence_cutoff")
        classified = _require_utc(self.classified_at, field_name="classified_at")
        if classified < cutoff:
            raise HorizonContractError(
                "classified_at must not precede the evidence cutoff"
            )
        object.__setattr__(self, "evidence_cutoff", cutoff)
        object.__setattr__(self, "classified_at", classified)
        for name in ("detector_support", "structural_support", "liquidity_support"):
            if not isinstance(getattr(self, name), bool):
                raise HorizonContractError(f"{name} must be a bool")
        milestones = self.structural_milestones
        if isinstance(milestones, bool) or not isinstance(milestones, int):
            raise HorizonContractError("structural_milestones must be an int")
        if milestones < 0:
            raise HorizonContractError("structural_milestones must be >= 0")
        object.__setattr__(self, "structural_milestones", int(milestones))
        holding = self.expected_holding_seconds
        if holding is not None:
            if isinstance(holding, bool) or not isinstance(holding, int):
                raise HorizonContractError(
                    "expected_holding_seconds must be an integer or None"
                )
            if holding <= 0:
                raise HorizonContractError("expected_holding_seconds must be positive")
        occupancy = self.expected_capital_occupancy_seconds
        if occupancy is not None:
            if isinstance(occupancy, bool) or not isinstance(occupancy, int):
                raise HorizonContractError(
                    "expected_capital_occupancy_seconds must be an integer or None"
                )
            if occupancy <= 0:
                raise HorizonContractError(
                    "expected_capital_occupancy_seconds must be positive"
                )


        start = _optional_utc(self.entry_window_start, field_name="entry_window_start")
        end = _optional_utc(self.entry_window_end, field_name="entry_window_end")
        if (start is None) != (end is None):
            raise HorizonContractError(
                "entry_window_start and entry_window_end must both be set or both unset"
            )
        if start is not None and end is not None and end <= start:
            raise HorizonContractError("entry window must end after it starts")
        object.__setattr__(self, "entry_window_start", start)
        object.__setattr__(self, "entry_window_end", end)
        object.__setattr__(
            self,
            "thesis_horizon",
            _optional_utc(self.thesis_horizon, field_name="thesis_horizon"),
        )
        time_invalidation = _optional_utc(
            self.time_invalidation, field_name="time_invalidation"
        )
        if time_invalidation is not None and time_invalidation <= cutoff:
            raise HorizonContractError(
                "time_invalidation must be later than the evidence cutoff"
            )
        object.__setattr__(self, "time_invalidation", time_invalidation)
        price = self.price_invalidation
        if price is not None:
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise HorizonContractError("price_invalidation must be a number or None")
            if price <= 0:
                raise HorizonContractError("price_invalidation must be positive")
            object.__setattr__(self, "price_invalidation", float(price))
        for name in ("profit_taking_policy_reference", "forecast_reference"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_text(value, field_name=name))
        object.__setattr__(
            self, "supporting_evidence", tuple(str(item) for item in self.supporting_evidence)
        )
        object.__setattr__(
            self,
            "contradicting_evidence",
            tuple(str(item) for item in self.contradicting_evidence),
        )

    @property
    def independent_support_families(self) -> int:
        """How many independent evidence families support the observation."""

        return sum(
            1
            for flag in (
                self.detector_support,
                self.structural_support,
                self.liquidity_support,
            )
            if flag
        )


def horizon_thesis_identity(
    *,
    episode_id: str,
    claim_id: str,
    decision_id: str,
    horizon_class: HorizonClass | str,
    thesis_version: int,
    classifier_version: str = HORIZON_CLASSIFIER_VERSION,
    thesis_schema_version: str = HORIZON_THESIS_SCHEMA_VERSION,
) -> str:
    """The deterministic ``OTHS:`` identity of one thesis revision.

    Identity binds the episode, the claim, the linked decision, the class, the
    revision number and the classifier version. No wall clock, UUID, process
    identity, invocation order or retry count takes part, so an unchanged
    classification reproduces the same identity and a reclassification produces
    a different one.
    """

    schema = _require_text(thesis_schema_version, field_name="thesis_schema_version")
    classifier = _require_text(classifier_version, field_name="classifier_version")
    horizon = require_horizon_class(horizon_class, field_name="horizon_class")
    if isinstance(thesis_version, bool) or not isinstance(thesis_version, int):
        raise HorizonContractError("thesis_version must be an integer")
    if thesis_version < 1:
        raise HorizonContractError("thesis_version must be >= 1")
    return stable_hash(
        THESIS_ID_PREFIX,
        {
            "thesis_schema_version": schema,
            "classifier_version": classifier,
            "episode_id": _require_text(episode_id, field_name="episode_id"),
            "claim_id": _require_text(claim_id, field_name="claim_id"),
            "decision_id": _require_text(decision_id, field_name="decision_id"),
            "horizon_class": horizon.value,
            "thesis_version": int(thesis_version),
        },
    )


@dataclass(frozen=True)
class HorizonThesis:
    """One immutable thesis revision and its point-in-time evidence.

    A revision is never rewritten in place: `reclassify` mints a new revision
    with `supersedes` naming the previous identity (`HZ-02`). `UNKNOWN` may not
    carry a holding range, and no revision carries allocation authority here.
    """

    thesis_id: str
    thesis_version: int
    thesis_schema_version: str
    classifier_version: str
    classifier_ratified: bool
    policy_version: str
    episode_id: str
    claim_id: str
    decision_id: str
    horizon_class: HorizonClass
    uncertainty: ClassificationUncertainty
    abstention_reason: AbstentionReason
    lifecycle_state: ThesisLifecycleState
    evidence_cutoff: datetime
    classified_at: datetime
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    entry_window_start: datetime | None = None
    entry_window_end: datetime | None = None
    expected_holding_seconds_min: int | None = None
    expected_holding_seconds_max: int | None = None
    thesis_horizon: datetime | None = None
    price_invalidation: float | None = None
    time_invalidation: datetime | None = None
    profit_taking_policy_reference: str | None = None
    expected_capital_occupancy_seconds: int | None = None
    forecast_reference: str | None = None
    supersedes: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "thesis_id",
            "thesis_schema_version",
            "classifier_version",
            "policy_version",
            "episode_id",
            "claim_id",
            "decision_id",
        ):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), field_name=name)
            )
        if self.thesis_schema_version != HORIZON_THESIS_SCHEMA_VERSION:
            raise HorizonContractError(
                "thesis_schema_version is not the declared "
                f"{HORIZON_THESIS_SCHEMA_VERSION}"
            )
        if not isinstance(self.thesis_version, bool) and isinstance(
            self.thesis_version, int
        ):
            if self.thesis_version < 1:
                raise HorizonContractError("thesis_version must be >= 1")
        else:
            raise HorizonContractError("thesis_version must be an integer")
        horizon = require_horizon_class(self.horizon_class, field_name="horizon_class")
        object.__setattr__(self, "horizon_class", horizon)
        if not isinstance(self.uncertainty, ClassificationUncertainty):
            raise HorizonContractError("uncertainty must be ClassificationUncertainty")
        if not isinstance(self.abstention_reason, AbstentionReason):
            raise HorizonContractError("abstention_reason must be AbstentionReason")
        if not isinstance(self.lifecycle_state, ThesisLifecycleState):
            raise HorizonContractError("lifecycle_state must be ThesisLifecycleState")
        if not isinstance(self.classifier_ratified, bool):
            raise HorizonContractError("classifier_ratified must be a bool")
        cutoff = _require_utc(self.evidence_cutoff, field_name="evidence_cutoff")
        classified = _require_utc(self.classified_at, field_name="classified_at")
        if classified < cutoff:
            raise HorizonContractError(
                "classified_at must not precede the evidence cutoff"
            )
        object.__setattr__(self, "evidence_cutoff", cutoff)
        object.__setattr__(self, "classified_at", classified)

        bounds = self.expected_holding_seconds_min, self.expected_holding_seconds_max
        for name, value in zip(
            ("expected_holding_seconds_min", "expected_holding_seconds_max"), bounds
        ):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise HorizonContractError(f"{name} must be an integer or None")
            if value <= 0:
                raise HorizonContractError(f"{name} must be positive")
        if bounds[0] is not None and bounds[1] is not None and bounds[0] > bounds[1]:
            raise HorizonContractError(
                "expected_holding_seconds_min must not exceed its max"
            )
        if horizon is HorizonClass.UNKNOWN and bounds != (None, None):
            raise HorizonContractError(
                "an UNKNOWN thesis may not carry an expected holding range"
            )
        if horizon is not HorizonClass.UNKNOWN and self.abstention_reason is not (
            AbstentionReason.NONE
        ):
            raise HorizonContractError(
                "a supported horizon class may not carry an abstention reason"
            )
        if horizon is HorizonClass.UNKNOWN and self.abstention_reason is (
            AbstentionReason.NONE
        ):
            raise HorizonContractError("UNKNOWN requires an abstention reason")
        start = _optional_utc(self.entry_window_start, field_name="entry_window_start")
        end = _optional_utc(self.entry_window_end, field_name="entry_window_end")
        if (start is None) != (end is None):
            raise HorizonContractError(
                "entry_window_start and entry_window_end must both be set or both unset"
            )
        if start is not None and end is not None and end <= start:
            raise HorizonContractError("entry window must end after it starts")
        object.__setattr__(self, "entry_window_start", start)
        object.__setattr__(self, "entry_window_end", end)
        object.__setattr__(
            self,
            "thesis_horizon",
            _optional_utc(self.thesis_horizon, field_name="thesis_horizon"),
        )
        object.__setattr__(
            self,
            "time_invalidation",
            _optional_utc(self.time_invalidation, field_name="time_invalidation"),
        )
        price = self.price_invalidation
        if price is not None:
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise HorizonContractError("price_invalidation must be a number or None")
            if price <= 0:
                raise HorizonContractError("price_invalidation must be positive")
            object.__setattr__(self, "price_invalidation", float(price))

        occupancy = self.expected_capital_occupancy_seconds
        if occupancy is not None:
            if isinstance(occupancy, bool) or not isinstance(occupancy, int):
                raise HorizonContractError(
                    "expected_capital_occupancy_seconds must be an integer or None"
                )
            if occupancy <= 0:
                raise HorizonContractError(
                    "expected_capital_occupancy_seconds must be positive"
                )
        for name in ("profit_taking_policy_reference", "forecast_reference"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_text(value, field_name=name))
        if self.supersedes is not None:
            previous = _require_text(self.supersedes, field_name="supersedes")
            if previous == self.thesis_id:
                raise HorizonContractError(
                    "a revision may not supersede its own identity"
                )
            if self.thesis_version < 2:
                raise HorizonContractError(
                    "a superseding revision must carry thesis_version >= 2"
                )
            object.__setattr__(self, "supersedes", previous)
        object.__setattr__(
            self,
            "supporting_evidence",
            tuple(str(item) for item in self.supporting_evidence),
        )
        object.__setattr__(
            self,
            "contradicting_evidence",
            tuple(str(item) for item in self.contradicting_evidence),
        )
        expected = horizon_thesis_identity(
            episode_id=self.episode_id,
            claim_id=self.claim_id,
            decision_id=self.decision_id,
            horizon_class=horizon,
            thesis_version=self.thesis_version,
            classifier_version=self.classifier_version,
            thesis_schema_version=self.thesis_schema_version,
        )
        if self.thesis_id != expected:
            raise HorizonContractError(
                "thesis_id does not match its own lineage and revision; build the "
                "thesis with classify_horizon() or reclassify()"
            )

    @property
    def research_only(self) -> bool:
        """True for `UNKNOWN`, which can never receive an allocation."""

        return self.horizon_class is HorizonClass.UNKNOWN

    @property
    def allocation_authority(self) -> str:
        """R5-A grants none. Allocation belongs to a later governed increment."""

        return "NONE"

    def expires_at(self, *, now: datetime) -> bool:
        """Whether the thesis time invalidation has passed at an explicit instant."""

        instant = _require_utc(now, field_name="now")
        return self.time_invalidation is not None and instant >= self.time_invalidation


    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "HorizonThesis",
            "thesis_id": self.thesis_id,
            "thesis_version": self.thesis_version,
            "thesis_schema_version": self.thesis_schema_version,
            "classifier_version": self.classifier_version,
            "classifier_ratified": self.classifier_ratified,
            "policy_version": self.policy_version,
            "episode_id": self.episode_id,
            "claim_id": self.claim_id,
            "decision_id": self.decision_id,
            "horizon_class": self.horizon_class.value,
            "uncertainty": self.uncertainty.value,
            "abstention_reason": self.abstention_reason.value,
            "lifecycle_state": self.lifecycle_state.value,
            "evidence_cutoff": iso_z(self.evidence_cutoff, field_name="evidence_cutoff"),
            "classified_at": iso_z(self.classified_at, field_name="classified_at"),
            "supporting_evidence": list(self.supporting_evidence),
            "contradicting_evidence": list(self.contradicting_evidence),
            "entry_window_start": (
                iso_z(self.entry_window_start, field_name="entry_window_start")
                if self.entry_window_start is not None
                else None
            ),
            "entry_window_end": (
                iso_z(self.entry_window_end, field_name="entry_window_end")
                if self.entry_window_end is not None
                else None
            ),
            "expected_holding_seconds_min": self.expected_holding_seconds_min,
            "expected_holding_seconds_max": self.expected_holding_seconds_max,
            "thesis_horizon": (
                iso_z(self.thesis_horizon, field_name="thesis_horizon")
                if self.thesis_horizon is not None
                else None
            ),
            "price_invalidation": self.price_invalidation,
            "time_invalidation": (
                iso_z(self.time_invalidation, field_name="time_invalidation")
                if self.time_invalidation is not None
                else None
            ),
            "profit_taking_policy_reference": self.profit_taking_policy_reference,
            "expected_capital_occupancy_seconds": (
                self.expected_capital_occupancy_seconds
            ),
            "forecast_reference": self.forecast_reference,
            "supersedes": self.supersedes,
            "allocation_authority": self.allocation_authority,
        }



def _uncertainty_for(evidence: HorizonEvidence) -> ClassificationUncertainty:
    if evidence.contradicting_evidence:
        return ClassificationUncertainty.HIGH
    families = evidence.independent_support_families
    if families >= 2:
        return ClassificationUncertainty.LOW
    if families == 1:
        return ClassificationUncertainty.MEDIUM
    return ClassificationUncertainty.UNKNOWN


def _build_thesis(
    evidence: HorizonEvidence,
    policy: HorizonPolicy,
    *,
    horizon_class: HorizonClass,
    abstention_reason: AbstentionReason,
    thesis_version: int = 1,
    supersedes: str | None = None,
) -> HorizonThesis:
    supported = horizon_class is not HorizonClass.UNKNOWN
    band = policy.band_for(horizon_class) if supported else None
    if supported and band is None:
        # A supported class must be bound by the owner-gated band it was
        # classified into; otherwise the classification is not reproducible.
        raise HorizonContractError(
            "a supported class must be bound by a declared holding band"
        )
    if supported:
        uncertainty = _uncertainty_for(evidence)
    else:
        # An abstention still records how well supported the *evidence* is: a
        # contradiction is HIGH, partial support is MEDIUM, and no support at all
        # is UNKNOWN. Abstention itself is never favourable evidence.
        derived = _uncertainty_for(evidence)
        uncertainty = (
            derived
            if derived in (ClassificationUncertainty.HIGH, ClassificationUncertainty.MEDIUM)
            else ClassificationUncertainty.UNKNOWN
        )
    return HorizonThesis(
        thesis_id=horizon_thesis_identity(
            episode_id=evidence.episode_id,
            claim_id=evidence.claim_id,
            decision_id=evidence.decision_id,
            horizon_class=horizon_class,
            thesis_version=thesis_version,
            classifier_version=HORIZON_CLASSIFIER_VERSION,
        ),
        thesis_version=thesis_version,
        thesis_schema_version=HORIZON_THESIS_SCHEMA_VERSION,
        classifier_version=HORIZON_CLASSIFIER_VERSION,
        classifier_ratified=policy.ratified,
        policy_version=policy.policy_version,
        episode_id=evidence.episode_id,
        claim_id=evidence.claim_id,
        decision_id=evidence.decision_id,
        horizon_class=horizon_class,
        uncertainty=uncertainty,
        abstention_reason=abstention_reason,
        lifecycle_state=ThesisLifecycleState.ACTIVE,
        evidence_cutoff=evidence.evidence_cutoff,
        classified_at=evidence.classified_at,
        supporting_evidence=evidence.supporting_evidence,
        contradicting_evidence=evidence.contradicting_evidence,
        entry_window_start=evidence.entry_window_start,
        entry_window_end=evidence.entry_window_end,
        expected_holding_seconds_min=band[0] if band is not None else None,
        expected_holding_seconds_max=band[1] if band is not None else None,
        thesis_horizon=evidence.thesis_horizon,
        price_invalidation=evidence.price_invalidation,
        time_invalidation=evidence.time_invalidation,
        profit_taking_policy_reference=evidence.profit_taking_policy_reference,
        expected_capital_occupancy_seconds=evidence.expected_capital_occupancy_seconds,
        forecast_reference=evidence.forecast_reference,
        supersedes=supersedes,
    )



def classify_horizon(
    evidence: HorizonEvidence, policy: HorizonPolicy
) -> HorizonThesis:
    """Classify one supported horizon, or abstain, from explicit evidence.

    Abstention is the default, not an error path: an unconfigured policy, an
    unbanded expected holding, contradictory evidence, missing structural
    support for a `POSITION` thesis, missing liquidity support or missing
    invalidation evidence all produce a research-only `UNKNOWN` thesis rather
    than a favourable label.
    """

    if not isinstance(evidence, HorizonEvidence):
        raise HorizonContractError("evidence must be a HorizonEvidence")
    if not isinstance(policy, HorizonPolicy):
        raise HorizonContractError("policy must be a HorizonPolicy")

    def _abstain(reason: AbstentionReason) -> HorizonThesis:
        return _build_thesis(
            evidence,
            policy,
            horizon_class=HorizonClass.UNKNOWN,
            abstention_reason=reason,
        )

    if not policy.configured:
        return _abstain(AbstentionReason.POLICY_NOT_CONFIGURED)
    if evidence.contradicting_evidence:
        return _abstain(AbstentionReason.CONTRADICTORY_EVIDENCE)
    if evidence.expected_holding_seconds is None:
        return _abstain(AbstentionReason.MISSING_HOLDING_EVIDENCE)
    if policy.requirements.requires_time_invalidation and evidence.time_invalidation is None:
        return _abstain(AbstentionReason.MISSING_TIME_INVALIDATION)
    if policy.requirements.requires_entry_window and evidence.entry_window_start is None:
        return _abstain(AbstentionReason.MISSING_ENTRY_WINDOW)

    horizon = policy.band_for_holding(int(evidence.expected_holding_seconds))
    if horizon is None:
        return _abstain(AbstentionReason.UNSUPPORTED_HORIZON)

    if horizon is HorizonClass.POSITION:
        requirements = policy.requirements
        if requirements.position_requires_structural_evidence and (
            not evidence.structural_support
        ):
            return _abstain(AbstentionReason.INSUFFICIENT_STRUCTURAL_EVIDENCE)
        if requirements.position_requires_liquidity_support and (
            not evidence.liquidity_support
        ):
            return _abstain(AbstentionReason.MISSING_LIQUIDITY_SUPPORT)
        minimum = requirements.position_min_structural_milestones
        if minimum is None:
            return _abstain(AbstentionReason.POLICY_NOT_CONFIGURED)
        if evidence.structural_milestones < minimum:
            return _abstain(AbstentionReason.INSUFFICIENT_STRUCTURAL_EVIDENCE)

    return _build_thesis(
        evidence,
        policy,
        horizon_class=horizon,
        abstention_reason=AbstentionReason.NONE,
    )



def expire_thesis(thesis: HorizonThesis, *, now: datetime) -> HorizonThesis:
    """Return the same revision observed as expired. Never mutates in place."""

    if not isinstance(thesis, HorizonThesis):
        raise HorizonContractError("thesis must be a HorizonThesis")
    instant = _require_utc(now, field_name="now")
    if not thesis.expires_at(now=instant):
        raise HorizonContractError(
            "thesis is not expired at the supplied instant; expiry must be observed"
        )
    return replace(thesis, lifecycle_state=ThesisLifecycleState.EXPIRED)


def reclassify(
    previous: HorizonThesis,
    evidence: HorizonEvidence,
    policy: HorizonPolicy,
    *,
    new_decision_id: str,
) -> HorizonThesis:
    """Mint a new linked revision. The original revision is never rewritten.

    The adopted clauses require a new linked decision, new evidence and the same
    gates (`HZ-02`). A TACTICAL revision that has expired may therefore not be
    relabelled as a POSITION thesis without new structural evidence: that path
    abstains to `UNKNOWN` with `RELABEL_REFUSED_PENDING_STRUCTURAL_EVIDENCE`.
    """

    if not isinstance(previous, HorizonThesis):
        raise HorizonContractError("previous must be a HorizonThesis")
    if not isinstance(evidence, HorizonEvidence):
        raise HorizonContractError("evidence must be a HorizonEvidence")
    decision = _require_text(new_decision_id, field_name="new_decision_id")
    if decision == previous.decision_id:
        raise HorizonContractError(
            "a reclassification requires a new linked decision identity"
        )
    if evidence.evidence_cutoff < previous.evidence_cutoff:
        raise HorizonContractError(
            "a reclassification requires evidence at or after the previous cutoff"
        )
    if evidence.classified_at < previous.classified_at:
        raise HorizonContractError(
            "a reclassification requires a later assessment instant"
        )
    revision_evidence = replace(
        evidence,
        episode_id=previous.episode_id,
        claim_id=previous.claim_id,
        decision_id=decision,
    )
    version = previous.thesis_version + 1
    expired_tactical = (
        previous.horizon_class is HorizonClass.TACTICAL
        and previous.expires_at(now=evidence.classified_at)
    )
    if expired_tactical and not evidence.structural_support:
        return _build_thesis(
            revision_evidence,
            policy,
            horizon_class=HorizonClass.UNKNOWN,
            abstention_reason=(
                AbstentionReason.RELABEL_REFUSED_PENDING_STRUCTURAL_EVIDENCE
            ),
            thesis_version=version,
            supersedes=previous.thesis_id,
        )
    draft = classify_horizon(revision_evidence, policy)
    return _build_thesis(
        revision_evidence,
        policy,
        horizon_class=draft.horizon_class,
        abstention_reason=draft.abstention_reason,
        thesis_version=version,
        supersedes=previous.thesis_id,
    )


__all__ = [
    "AbstentionReason",
    "BOUNDED_CLASSES",
    "ClassificationUncertainty",
    "DECLARED_POLICY_VERSIONS",
    "HORIZON_CLASSIFIER_VERSION",
    "HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION",
    "HORIZON_POLICY_VERSION",
    "HORIZON_THESIS_SCHEMA_VERSION",
    "HorizonClass",
    "HorizonContractError",
    "HorizonEvidence",
    "HorizonPolicy",
    "HorizonRequirements",
    "HorizonThesis",
    "OWNER_INPUT_STATUS",
    "ThesisLifecycleState",
    "classify_horizon",
    "expire_thesis",
    "horizon_thesis_identity",
    "reclassify",
    "require_horizon_class",
]

