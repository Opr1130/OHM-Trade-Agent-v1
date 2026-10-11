"""Thesis lifecycle contract for the R5-B Horizon Intelligence slice.

Adopted authority: `docs/architecture/v1.5.0/ARCHITECTURE.md` section 4
(`HZ-01`, `HZ-02`) and section 10's `OpportunityThesis` lineage, extended by the
R5-B scope contract `docs/atdd/scope-contracts/ATDD-R5-B-horizon-intelligence.md`.

This module closes the one structural gap R5-A deliberately left open: R5-A
classifies a horizon and records entry/exit *intent* (an entry window, a holding
band, a time invalidation) but it ships no **first-fill anchor** and no
**lifecycle clock**. Without them an "expected holding range" cannot be measured
point-in-time, because there is no recorded instant the holding period is
anchored to.

Three boundaries are deliberate:

* The anchor is the single source of the holding clock. `HZ-02` says horizon is
  a versioned property of a thesis, so the anchor is bound to the thesis
  revision identity and the *first* fill instant is recorded once. Later fills
  never restart the clock (`ForecastHorizonAnchor.FIRST_FILL` already declares
  exactly this vocabulary in the frozen F6 contract, which this module reuses
  rather than duplicating).
* A clock is arithmetic on explicit instants. Nothing here reads a clock, the
  environment, the filesystem, the network or a database; every instant is
  supplied by the caller and validated as an explicit UTC value. `started_at`
  comes from preserved evidence (`classified_at` or the anchor's `first_fill_at`)
  and `expires_at` is *derived* from `started_at + expected_seconds`, so there is
  one authority per deadline.
* Expected durations are OWNER inputs (`OQ-03`, `OQ-08`). This module invents no
  number. A policy whose budgets are not supplied abstains with
  `POLICY_NOT_CONFIGURED` (or `MISSING_ENTRY_WINDOW` when only the qualification
  budget is absent) rather than defaulting, guessing or borrowing a value.

This module carries no trading, admission, allocation, reservation or execution
authority: a lifecycle observation is research-only evidence, and
`HorizonThesisLifecycle.authoritative` is hard-coded `False`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Any

from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import TemporalIntegrityError, require_utc
from app.opip.contracts.thesis import (
    BOUNDED_CLASSES,
    HorizonClass,
    HorizonContractError,
    HorizonThesis,
    ThesisLifecycleState,
    require_horizon_class,
)

#: Schema version of the durable/emitted lifecycle evidence record.
HORIZON_LIFECYCLE_SCHEMA_VERSION = "horizon-lifecycle-v1"

#: Schema version of the durable/emitted first-fill anchor record.
FIRST_FILL_ANCHOR_SCHEMA_VERSION = "first-fill-anchor-v1"

#: The owner-gated lifecycle policy token. Its numeric surface is the open owner
#: input (`OQ-03`/`OQ-08`) and is not ratified, so a policy carrying this token
#: with budgets supplied is still recorded as unratified engineering evidence.
HORIZON_LIFECYCLE_POLICY_VERSION = "horizon-lifecycle-shadow-policy-v1"

#: Explicitly labelled engineering-scenario policy token, for measurements that
#: must be reproducible without pretending an owner ratified the numbers.
HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION = (
    "horizon-lifecycle-engineering-scenario-v1"
)

DECLARED_LIFECYCLE_POLICY_VERSIONS: tuple[str, ...] = (
    HORIZON_LIFECYCLE_POLICY_VERSION,
    HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION,
)

#: Register row `OQ-03` remains open; this module never claims otherwise.
OWNER_INPUT_STATUS = "OWNER_DECISION_REQUIRED"

FIRST_FILL_ANCHOR_ID_PREFIX = "FFAN"
THESIS_CLOCK_ID_PREFIX = "TCLK"
HORIZON_LIFECYCLE_ID_PREFIX = "HLFC"

#: Schema version of the durable/emitted lifecycle clock record.
THESIS_CLOCK_SCHEMA_VERSION = "thesis-clock-v1"



class HorizonLifecycleContractError(HorizonContractError):
    """A lifecycle/clock structural violation. Always fails closed."""


class ThesisClockName(str, Enum):
    """The three independent lifecycle clocks of one thesis revision."""

    QUALIFICATION = "QUALIFICATION"
    HOLDING = "HOLDING"
    INVALIDATION = "INVALIDATION"


#: The deterministic clock order. It is the order in which clocks are started,
#: evaluated and serialized, so evidence ordering can never change the outcome.
CLOCK_ORDER: tuple[ThesisClockName, ...] = (
    ThesisClockName.QUALIFICATION,
    ThesisClockName.HOLDING,
    ThesisClockName.INVALIDATION,
)


class ThesisClockState(str, Enum):
    """A clock is running or terminally expired. There is no pause or resume."""

    RUNNING = "RUNNING"
    EXPIRED = "EXPIRED"


class LifecycleAbstentionReason(str, Enum):
    """Why a lifecycle observation abstained. `NONE` means it was supported.

    An abstention is a governed evidence disposition, not a veto and not an
    error: it records that the lifecycle could not be observed from the
    available point-in-time evidence.
    """

    NONE = "NONE"
    POLICY_NOT_CONFIGURED = "POLICY_NOT_CONFIGURED"
    UNKNOWN_HORIZON_CLASS = "UNKNOWN_HORIZON_CLASS"
    MISSING_FIRST_FILL_ANCHOR = "MISSING_FIRST_FILL_ANCHOR"
    UNSUPPORTED_LIFECYCLE_BAND = "UNSUPPORTED_LIFECYCLE_BAND"
    POLICY_BAND_MISMATCH = "POLICY_BAND_MISMATCH"
    MISSING_ENTRY_WINDOW = "MISSING_ENTRY_WINDOW"
    MISSING_TIME_INVALIDATION = "MISSING_TIME_INVALIDATION"


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value == "" or value != value.strip():
        raise HorizonLifecycleContractError(
            f"{field_name} must be a non-empty, whitespace-free token"
        )
    return value


def _require_utc(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise HorizonLifecycleContractError(
            f"{field_name} must be an explicit datetime"
        )
    try:
        return require_utc(value, field_name=field_name)
    except TemporalIntegrityError as exc:
        raise HorizonLifecycleContractError(str(exc)) from exc


def _require_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HorizonLifecycleContractError(f"{field_name} must be an integer")
    if value <= 0:
        raise HorizonLifecycleContractError(f"{field_name} must be positive")
    return int(value)


def _require_positive_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HorizonLifecycleContractError(f"{field_name} must be a number")
    if value <= 0:
        raise HorizonLifecycleContractError(f"{field_name} must be positive")
    return float(value)


def require_clock_name(value: Any, *, field_name: str) -> ThesisClockName:
    if isinstance(value, ThesisClockName):
        return value
    if isinstance(value, str):
        try:
            return ThesisClockName(value)
        except ValueError as exc:
            raise HorizonLifecycleContractError(
                f"{field_name} has an unsupported token: {value!r}"
            ) from exc
    raise HorizonLifecycleContractError(
        f"{field_name} must be a ThesisClockName token"
    )


# ---------------------------------------------------------------------------
# First-fill anchor (`HZ-02`)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FirstFillAnchor:
    """The immutable first-fill anchor of one thesis revision.

    ``first_fill_at`` is the *only* holding-period anchor and it is recorded
    once. Additional fills accumulate on a new record that carries the same
    ``anchor_id`` and the same ``first_fill_at``, so a later fill can never
    restart the holding clock. The identity is re-derived on construction, so a
    forged or drifted ``FFAN:`` id fails closed instead of being accepted.
    """

    anchor_id: str
    anchor_schema_version: str
    thesis_id: str
    thesis_version: int
    episode_id: str
    decision_id: str
    first_fill_at: datetime
    receipt_at: datetime
    filled_quantity: float
    average_fill_price: float
    fill_count: int
    source_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("anchor_id", "thesis_id", "episode_id", "decision_id"):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), field_name=name)
            )
        schema = _require_text(
            self.anchor_schema_version, field_name="anchor_schema_version"
        )
        if schema != FIRST_FILL_ANCHOR_SCHEMA_VERSION:
            raise HorizonLifecycleContractError(
                "anchor_schema_version is not the ratified "
                f"{FIRST_FILL_ANCHOR_SCHEMA_VERSION}"
            )
        object.__setattr__(self, "anchor_schema_version", schema)
        if isinstance(self.thesis_version, bool) or not isinstance(
            self.thesis_version, int
        ):
            raise HorizonLifecycleContractError("thesis_version must be an integer")
        if self.thesis_version < 1:
            raise HorizonLifecycleContractError("thesis_version must be >= 1")
        object.__setattr__(self, "thesis_version", int(self.thesis_version))
        first_fill = _require_utc(self.first_fill_at, field_name="first_fill_at")
        receipt = _require_utc(self.receipt_at, field_name="receipt_at")
        if receipt < first_fill:
            raise HorizonLifecycleContractError(
                "an anchor cannot be received before its first fill"
            )
        object.__setattr__(self, "first_fill_at", first_fill)
        object.__setattr__(self, "receipt_at", receipt)
        object.__setattr__(
            self,
            "filled_quantity",
            _require_positive_number(
                self.filled_quantity, field_name="filled_quantity"
            ),
        )
        object.__setattr__(
            self,
            "average_fill_price",
            _require_positive_number(
                self.average_fill_price, field_name="average_fill_price"
            ),
        )
        count = _require_positive_int(self.fill_count, field_name="fill_count")
        object.__setattr__(self, "fill_count", count)
        references = tuple(
            _require_text(reference, field_name="source_references")
            for reference in self.source_references
        )
        if not references:
            raise HorizonLifecycleContractError(
                "source_references must name at least the anchor observation"
            )
        object.__setattr__(self, "source_references", references)
        expected = first_fill_anchor_identity(
            thesis_id=self.thesis_id,
            thesis_version=self.thesis_version,
            episode_id=self.episode_id,
            decision_id=self.decision_id,
            first_fill_at=self.first_fill_at,
            anchor_schema_version=self.anchor_schema_version,
        )
        if self.anchor_id != expected:
            raise HorizonLifecycleContractError(
                "anchor_id is not the identity of the anchor it describes"
            )

    @property
    def authoritative(self) -> bool:
        """An anchor is preserved evidence. It never carries authority."""

        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "FirstFillAnchor",
            "anchor_id": self.anchor_id,
            "anchor_schema_version": self.anchor_schema_version,
            "thesis_id": self.thesis_id,
            "thesis_version": self.thesis_version,
            "episode_id": self.episode_id,
            "decision_id": self.decision_id,
            "first_fill_at": iso_z(self.first_fill_at, field_name="first_fill_at"),
            "receipt_at": iso_z(self.receipt_at, field_name="receipt_at"),
            "filled_quantity": self.filled_quantity,
            "average_fill_price": self.average_fill_price,
            "fill_count": self.fill_count,
            "source_references": list(self.source_references),
        }



def first_fill_anchor_identity(
    *,
    thesis_id: str,
    thesis_version: int,
    episode_id: str,
    decision_id: str,
    first_fill_at: datetime,
    anchor_schema_version: str = FIRST_FILL_ANCHOR_SCHEMA_VERSION,
) -> str:
    """The deterministic ``FFAN:`` identity of one thesis revision's anchor.

    Identity binds the thesis revision lineage and the *first* fill instant. It
    deliberately excludes the accumulated quantity, the average price, the fill
    count and the observation references, so additional fills and re-reads
    reproduce the same identity instead of minting a competing anchor. No wall
    clock, UUID, process identity or invocation order takes part.
    """

    schema = _require_text(anchor_schema_version, field_name="anchor_schema_version")
    if isinstance(thesis_version, bool) or not isinstance(thesis_version, int):
        raise HorizonLifecycleContractError("thesis_version must be an integer")
    if thesis_version < 1:
        raise HorizonLifecycleContractError("thesis_version must be >= 1")
    return stable_hash(
        FIRST_FILL_ANCHOR_ID_PREFIX,
        {
            "anchor_schema_version": schema,
            "thesis_id": _require_text(thesis_id, field_name="thesis_id"),
            "thesis_version": int(thesis_version),
            "episode_id": _require_text(episode_id, field_name="episode_id"),
            "decision_id": _require_text(decision_id, field_name="decision_id"),
            "first_fill_at": iso_z(first_fill_at, field_name="first_fill_at"),
        },
    )


def build_first_fill_anchor(
    thesis: HorizonThesis,
    *,
    first_fill_at: datetime,
    receipt_at: datetime,
    filled_quantity: Any,
    average_fill_price: Any,
    source_reference: str,
) -> FirstFillAnchor:
    """Record the first-fill anchor of one thesis revision.

    The instant is preserved evidence, never a clock read: `first_fill_at` and
    `receipt_at` are supplied by the caller. The anchor cannot precede the
    revision that declares the horizon (`classified_at`), cannot be received
    before it happened, and cannot anchor an already expired revision, so a
    back-filled or time-travelled anchor fails closed.
    """

    if not isinstance(thesis, HorizonThesis):
        raise HorizonLifecycleContractError("thesis must be a HorizonThesis")
    if thesis.lifecycle_state is not ThesisLifecycleState.ACTIVE:
        raise HorizonLifecycleContractError(
            "an expired thesis revision is never anchored"
        )
    first_fill = _require_utc(first_fill_at, field_name="first_fill_at")
    receipt = _require_utc(receipt_at, field_name="receipt_at")
    if first_fill < thesis.classified_at:
        raise HorizonLifecycleContractError(
            "the anchor cannot precede the thesis classification instant"
        )
    if receipt < first_fill:
        raise HorizonLifecycleContractError(
            "an anchor cannot be received before its first fill"
        )
    reference = _require_text(source_reference, field_name="source_reference")
    anchor_id = first_fill_anchor_identity(
        thesis_id=thesis.thesis_id,
        thesis_version=thesis.thesis_version,
        episode_id=thesis.episode_id,
        decision_id=thesis.decision_id,
        first_fill_at=first_fill,
    )
    return FirstFillAnchor(
        anchor_id=anchor_id,
        anchor_schema_version=FIRST_FILL_ANCHOR_SCHEMA_VERSION,
        thesis_id=thesis.thesis_id,
        thesis_version=thesis.thesis_version,
        episode_id=thesis.episode_id,
        decision_id=thesis.decision_id,
        first_fill_at=first_fill,
        receipt_at=receipt,
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        fill_count=1,
        source_references=(reference,),
    )


def record_additional_fill(
    anchor: FirstFillAnchor,
    *,
    filled_quantity_added: Any,
    average_fill_price: Any,
    fill_at: datetime,
    receipt_at: datetime,
    source_reference: str,
) -> FirstFillAnchor:
    """Return a new anchor record for a later fill. Never restarts the clock.

    The returned record preserves ``anchor_id`` and ``first_fill_at`` exactly,
    so the holding deadline is unchanged by a later fill. A fill observed before
    the anchor instant, or received out of order, is refused rather than merged.
    """

    if not isinstance(anchor, FirstFillAnchor):
        raise HorizonLifecycleContractError("anchor must be a FirstFillAnchor")
    added = _require_positive_number(
        filled_quantity_added, field_name="filled_quantity_added"
    )
    price = _require_positive_number(average_fill_price, field_name="average_fill_price")
    instant = _require_utc(fill_at, field_name="fill_at")
    receipt = _require_utc(receipt_at, field_name="receipt_at")
    if instant < anchor.first_fill_at:
        raise HorizonLifecycleContractError(
            "a later fill cannot precede the anchor instant"
        )
    if receipt < anchor.receipt_at:
        raise HorizonLifecycleContractError(
            "lifecycle evidence cannot be received out of order"
        )
    reference = _require_text(source_reference, field_name="source_reference")
    total_quantity = anchor.filled_quantity + added
    weighted_price = (
        anchor.filled_quantity * anchor.average_fill_price + added * price
    ) / total_quantity
    return FirstFillAnchor(
        anchor_id=anchor.anchor_id,
        anchor_schema_version=anchor.anchor_schema_version,
        thesis_id=anchor.thesis_id,
        thesis_version=anchor.thesis_version,
        episode_id=anchor.episode_id,
        decision_id=anchor.decision_id,
        first_fill_at=anchor.first_fill_at,
        receipt_at=receipt,
        filled_quantity=total_quantity,
        average_fill_price=weighted_price,
        fill_count=anchor.fill_count + 1,
        source_references=anchor.source_references + (reference,),
    )



# ---------------------------------------------------------------------------
# Lifecycle policy (`OQ-03` owned numbers)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThesisLifecyclePolicy:
    """The applied, versioned lifecycle policy.

    ``qualification_budget_seconds`` is the owner-gated entry-window duration
    and ``holding_budget_seconds`` are the owner-gated per-class holding bands
    (inclusive integer seconds, the same convention as R5-A's horizon bounds). This
    module ships no number: a policy constructed without them is *not*
    configured and every evaluation abstains rather than defaulting.
    """

    policy_version: str = HORIZON_LIFECYCLE_POLICY_VERSION
    qualification_budget_seconds: int | None = None
    holding_budget_seconds: Mapping[HorizonClass, tuple[int, int]] | None = None

    def __post_init__(self) -> None:
        version = _require_text(self.policy_version, field_name="policy_version")
        if version not in DECLARED_LIFECYCLE_POLICY_VERSIONS:
            raise HorizonLifecycleContractError(
                f"policy_version is not a declared token: {version!r}"
            )
        object.__setattr__(self, "policy_version", version)
        budget = self.qualification_budget_seconds
        if budget is not None:
            object.__setattr__(
                self,
                "qualification_budget_seconds",
                _require_positive_int(
                    budget, field_name="qualification_budget_seconds"
                ),
            )
        bounds = self.holding_budget_seconds
        if bounds is None:
            return
        if not isinstance(bounds, Mapping) or not bounds:
            raise HorizonLifecycleContractError(
                "holding_budget_seconds must be a non-empty mapping or None"
            )
        cleaned: dict[HorizonClass, tuple[int, int]] = {}
        for key, value in bounds.items():
            horizon = require_horizon_class(key, field_name="holding_budget_seconds")
            if horizon not in BOUNDED_CLASSES:
                raise HorizonLifecycleContractError(
                    "holding_budget_seconds may only bound TACTICAL, SWING or "
                    "POSITION"
                )
            if not isinstance(value, tuple) or len(value) != 2:
                raise HorizonLifecycleContractError(
                    "each holding band must be an inclusive (min, max) tuple"
                )
            low = _require_positive_int(
                value[0], field_name="holding_budget_seconds.min"
            )
            high = _require_positive_int(
                value[1], field_name="holding_budget_seconds.max"
            )
            if low > high:
                raise HorizonLifecycleContractError(
                    "a holding band's minimum must not exceed its maximum"
                )
            cleaned[horizon] = (low, high)
        ordered = sorted(cleaned.values())
        for (_, previous_high), (low, _) in zip(ordered, ordered[1:]):
            if low <= previous_high:
                raise HorizonLifecycleContractError(
                    "holding bands must not overlap"
                )
        object.__setattr__(
            self, "holding_budget_seconds", MappingProxyType(cleaned)
        )

    @property
    def configured(self) -> bool:
        """Whether the owner-gated numbers were supplied.

        An unconfigured policy is inert: it abstains, it never guesses.
        """

        return (
            self.qualification_budget_seconds is not None
            and self.holding_budget_seconds is not None
        )

    def band_for(self, horizon_class: HorizonClass | str) -> tuple[int, int] | None:
        """The owner-gated band of one bounded class, or ``None``."""

        horizon = require_horizon_class(horizon_class, field_name="horizon_class")
        if self.holding_budget_seconds is None:
            return None
        return self.holding_budget_seconds.get(horizon)




# ---------------------------------------------------------------------------
# Lifecycle clocks (`LC-01`, `LC-02`, `LC-03`)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThesisLifecycleClock:
    """One immutable observation of one lifecycle clock.

    A clock is arithmetic on explicit instants: ``expires_at`` is derived from
    ``started_at + expected_seconds`` (enforced on construction), so no second
    authority can disagree about the deadline. A clock is never paused: an
    expired clock is terminal, and ``advance_clock`` refuses to move it.
    """

    clock_id: str
    clock_schema_version: str
    thesis_id: str
    clock_name: ThesisClockName
    policy_version: str
    state: ThesisClockState
    started_at: datetime
    expires_at: datetime
    expected_seconds: int
    started_from_anchor_id: str | None
    receipt_at: datetime
    expired_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("clock_id", "thesis_id"):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), field_name=name)
            )
        schema = _require_text(
            self.clock_schema_version, field_name="clock_schema_version"
        )
        if schema != THESIS_CLOCK_SCHEMA_VERSION:
            raise HorizonLifecycleContractError(
                "clock_schema_version is not the ratified "
                f"{THESIS_CLOCK_SCHEMA_VERSION}"
            )
        object.__setattr__(self, "clock_schema_version", schema)
        name = require_clock_name(self.clock_name, field_name="clock_name")
        object.__setattr__(self, "clock_name", name)
        version = _require_text(self.policy_version, field_name="policy_version")
        if version not in DECLARED_LIFECYCLE_POLICY_VERSIONS:
            raise HorizonLifecycleContractError(
                f"policy_version is not a declared token: {version!r}"
            )
        object.__setattr__(self, "policy_version", version)
        if not isinstance(self.state, ThesisClockState):
            try:
                state = ThesisClockState(self.state)
            except ValueError as exc:
                raise HorizonLifecycleContractError(
                    f"state has an unsupported token: {self.state!r}"
                ) from exc
            object.__setattr__(self, "state", state)
        started = _require_utc(self.started_at, field_name="started_at")
        expires = _require_utc(self.expires_at, field_name="expires_at")
        if expires <= started:
            raise HorizonLifecycleContractError("a clock must expire after it starts")
        expected = _require_positive_int(
            self.expected_seconds, field_name="expected_seconds"
        )
        if timedelta(seconds=expected) != expires - started:
            raise HorizonLifecycleContractError(
                "expires_at must be exactly started_at + expected_seconds"
            )
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "expires_at", expires)
        object.__setattr__(self, "expected_seconds", expected)
        receipt = _require_utc(self.receipt_at, field_name="receipt_at")
        if receipt < started:
            raise HorizonLifecycleContractError(
                "clock evidence cannot be received before the clock starts"
            )
        anchor_id = self.started_from_anchor_id
        if anchor_id is not None:
            object.__setattr__(
                self,
                "started_from_anchor_id",
                _require_text(anchor_id, field_name="started_from_anchor_id"),
            )
        if self.state is ThesisClockState.EXPIRED:
            if self.expired_at is None:
                raise HorizonLifecycleContractError(
                    "an expired clock must record the instant expiry was observed"
                )
        elif self.expired_at is not None:
            raise HorizonLifecycleContractError(
                "a running clock cannot record an expiry instant"
            )
        if self.expired_at is not None:
            observed = _require_utc(self.expired_at, field_name="expired_at")
            if observed < expires:
                raise HorizonLifecycleContractError(
                    "expiry cannot be observed before the deadline"
                )
            object.__setattr__(self, "expired_at", observed)
        expected_id = thesis_clock_identity(
            thesis_id=self.thesis_id,
            clock_name=self.clock_name,
            policy_version=self.policy_version,
            started_at=self.started_at,
            expected_seconds=self.expected_seconds,
            started_from_anchor_id=self.started_from_anchor_id,
            clock_schema_version=self.clock_schema_version,
        )
        if self.clock_id != expected_id:
            raise HorizonLifecycleContractError(
                "clock_id is not the identity of the clock it describes"
            )

    @property
    def authoritative(self) -> bool:
        """A clock is preserved evidence. It never carries authority."""

        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "ThesisLifecycleClock",
            "clock_id": self.clock_id,
            "clock_schema_version": self.clock_schema_version,
            "thesis_id": self.thesis_id,
            "clock_name": self.clock_name.value,
            "policy_version": self.policy_version,
            "state": self.state.value,
            "started_at": iso_z(self.started_at, field_name="started_at"),
            "expires_at": iso_z(self.expires_at, field_name="expires_at"),
            "expected_seconds": self.expected_seconds,
            "started_from_anchor_id": self.started_from_anchor_id,
            "receipt_at": iso_z(self.receipt_at, field_name="receipt_at"),
            "expired_at": (
                iso_z(self.expired_at, field_name="expired_at")
                if self.expired_at is not None
                else None
            ),
        }


def thesis_clock_identity(
    *,
    thesis_id: str,
    clock_name: ThesisClockName | str,
    policy_version: str,
    started_at: datetime,
    expected_seconds: int,
    started_from_anchor_id: str | None,
    clock_schema_version: str = THESIS_CLOCK_SCHEMA_VERSION,
) -> str:
    """The deterministic ``TCLK:`` identity of one lifecycle clock.

    Identity binds the thesis, the clock name, the applied policy version, the
    start instant and the derived duration. It excludes the observation instant
    and the state, so re-observing the same clock at a later instant reproduces
    the same identity: advancement is an observation of one clock, never a new
    clock. No wall clock, UUID, process identity or invocation order takes part.
    """

    schema = _require_text(clock_schema_version, field_name="clock_schema_version")
    name = require_clock_name(clock_name, field_name="clock_name")
    version = _require_text(policy_version, field_name="policy_version")
    if version not in DECLARED_LIFECYCLE_POLICY_VERSIONS:
        raise HorizonLifecycleContractError(
            f"policy_version is not a declared token: {version!r}"
        )
    anchor_id = (
        None
        if started_from_anchor_id is None
        else _require_text(started_from_anchor_id, field_name="started_from_anchor_id")
    )
    return stable_hash(
        THESIS_CLOCK_ID_PREFIX,
        {
            "clock_schema_version": schema,
            "clock_name": name.value,
            "policy_version": version,
            "thesis_id": _require_text(thesis_id, field_name="thesis_id"),
            "started_at": iso_z(started_at, field_name="started_at"),
            "expected_seconds": _require_positive_int(
                expected_seconds, field_name="expected_seconds"
            ),
            "started_from_anchor_id": anchor_id,
        },
    )


def _resolve_clock_requirements(
    thesis: HorizonThesis,
    clock_name: ThesisClockName,
    policy: ThesisLifecyclePolicy,
    anchor: FirstFillAnchor | None,
) -> tuple[LifecycleAbstentionReason | None, tuple[datetime, int, str | None] | None]:
    """Resolve one clock's ``(started_at, expected_seconds, anchor_id)``.

    One authority answers "can this clock be observed", so the abstaining seam
    (`evaluate_thesis_lifecycle`) and the strict seam (`start_clock`) cannot
    disagree about it. The deadline is always derived from an explicit instant
    plus an owner-gated duration; no duration is invented here.
    """

    if clock_name is ThesisClockName.QUALIFICATION:
        window_start = thesis.entry_window_start
        window_end = thesis.entry_window_end
        budget = policy.qualification_budget_seconds
        if window_start is None or window_end is None or budget is None:
            return LifecycleAbstentionReason.MISSING_ENTRY_WINDOW, None
        if int((window_end - window_start).total_seconds()) != budget:
            return LifecycleAbstentionReason.POLICY_BAND_MISMATCH, None
        if window_end <= thesis.classified_at:
            raise HorizonLifecycleContractError(
                "the declared entry window ends at or before the thesis "
                "classification instant"
            )
        return (
            None,
            (
                thesis.classified_at,
                int((window_end - thesis.classified_at).total_seconds()),
                None,
            ),
        )
    if anchor is None:
        return LifecycleAbstentionReason.MISSING_FIRST_FILL_ANCHOR, None
    if clock_name is ThesisClockName.HOLDING:
        band = policy.band_for(thesis.horizon_class)
        declared = (
            thesis.expected_holding_seconds_min,
            thesis.expected_holding_seconds_max,
        )
        if band is None or declared[0] is None or declared[1] is None:
            return LifecycleAbstentionReason.UNSUPPORTED_LIFECYCLE_BAND, None
        if band != declared:
            return LifecycleAbstentionReason.POLICY_BAND_MISMATCH, None
        return None, (anchor.first_fill_at, int(band[1]), anchor.anchor_id)
    deadline = thesis.time_invalidation
    if deadline is None:
        return LifecycleAbstentionReason.MISSING_TIME_INVALIDATION, None
    if deadline <= anchor.first_fill_at:
        raise HorizonLifecycleContractError(
            "the anchor cannot be recorded at or after the revision's declared "
            "time invalidation"
        )
    return (
        None,
        (
            anchor.first_fill_at,
            int((deadline - anchor.first_fill_at).total_seconds()),
            anchor.anchor_id,
        ),
    )


def start_clock(
    thesis: HorizonThesis,
    clock_name: ThesisClockName | str,
    *,
    policy: ThesisLifecyclePolicy,
    receipt_at: datetime,
    anchor: FirstFillAnchor | None = None,
) -> ThesisLifecycleClock:
    """Start one lifecycle clock at a preserved instant, or refuse.

    The start instant is evidence (`classified_at` or the anchor's
    `first_fill_at`), never a clock read. A clock the policy and the revision
    cannot support is refused here rather than defaulted or guessed.
    """

    if not isinstance(thesis, HorizonThesis):
        raise HorizonLifecycleContractError("thesis must be a HorizonThesis")
    if not isinstance(policy, ThesisLifecyclePolicy):
        raise HorizonLifecycleContractError("policy must be a ThesisLifecyclePolicy")
    if anchor is not None and not isinstance(anchor, FirstFillAnchor):
        raise HorizonLifecycleContractError("anchor must be a FirstFillAnchor")
    name = require_clock_name(clock_name, field_name="clock_name")
    reason, resolved = _resolve_clock_requirements(thesis, name, policy, anchor)
    if reason is not None or resolved is None:
        raise HorizonLifecycleContractError(
            f"the {name.name} clock cannot be observed: {reason.value}"
        )
    started_at, expected_seconds, anchor_id = resolved
    receipt = _require_utc(receipt_at, field_name="receipt_at")
    if receipt < started_at:
        raise HorizonLifecycleContractError(
            "clock evidence cannot be received before the clock starts"
        )
    expires_at = started_at + timedelta(seconds=expected_seconds)
    return ThesisLifecycleClock(
        clock_id=thesis_clock_identity(
            thesis_id=thesis.thesis_id,
            clock_name=name,
            policy_version=policy.policy_version,
            started_at=started_at,
            expected_seconds=expected_seconds,
            started_from_anchor_id=anchor_id,
        ),
        clock_schema_version=THESIS_CLOCK_SCHEMA_VERSION,
        thesis_id=thesis.thesis_id,
        clock_name=name,
        policy_version=policy.policy_version,
        state=ThesisClockState.RUNNING,
        started_at=started_at,
        expires_at=expires_at,
        expected_seconds=expected_seconds,
        started_from_anchor_id=anchor_id,
        receipt_at=receipt,
        expired_at=None,
    )


def advance_clock(
    clock: ThesisLifecycleClock, *, now: datetime, receipt_at: datetime
) -> ThesisLifecycleClock:
    """Observe one clock at an explicit instant. Never resumes an expiry.

    Expiry is terminal: an already expired clock is refused rather than
    restarted, and a clock cannot be observed before it started. ``now`` is
    always supplied by the caller, so no wall-clock reading can enter lifecycle
    evidence.
    """

    if not isinstance(clock, ThesisLifecycleClock):
        raise HorizonLifecycleContractError("clock must be a ThesisLifecycleClock")
    if clock.state is ThesisClockState.EXPIRED:
        raise HorizonLifecycleContractError(
            "an expired clock is terminal and never resumes"
        )
    instant = _require_utc(now, field_name="now")
    if instant < clock.started_at:
        raise HorizonLifecycleContractError(
            "a clock cannot be observed before it started"
        )
    receipt = _require_utc(receipt_at, field_name="receipt_at")
    if receipt < instant:
        raise HorizonLifecycleContractError(
            "clock evidence cannot be received before the observation instant"
        )
    expired = instant >= clock.expires_at
    return ThesisLifecycleClock(
        clock_id=clock.clock_id,
        clock_schema_version=clock.clock_schema_version,
        thesis_id=clock.thesis_id,
        clock_name=clock.clock_name,
        policy_version=clock.policy_version,
        state=ThesisClockState.EXPIRED if expired else ThesisClockState.RUNNING,
        started_at=clock.started_at,
        expires_at=clock.expires_at,
        expected_seconds=clock.expected_seconds,
        started_from_anchor_id=clock.started_from_anchor_id,
        receipt_at=receipt,
        expired_at=instant if expired else None,
    )


# ---------------------------------------------------------------------------
# Lifecycle observation (`HZ-01` + `LC-01`..`LC-03`)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HorizonThesisLifecycle:
    """The immutable lifecycle observation of one thesis revision.

    One lifecycle id is bound to the thesis revision and (when attached) to the
    first-fill anchor, so re-observing the same lifecycle at a later instant
    reproduces the same ``HLFC:`` identity: only the clocks advance. An
    abstention is recorded with no clocks, because a partially observed clock
    set is not an admissible lifecycle observation.
    """

    lifecycle_id: str
    lifecycle_schema_version: str
    policy_version: str
    thesis_id: str
    thesis_version: int
    horizon_class: HorizonClass
    thesis_lifecycle_state: ThesisLifecycleState
    abstention_reason: LifecycleAbstentionReason
    clocks: tuple[ThesisLifecycleClock, ...]
    evaluated_at: datetime
    receipt_at: datetime
    anchor: FirstFillAnchor | None = None

    def __post_init__(self) -> None:
        for name in ("lifecycle_id", "thesis_id"):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), field_name=name)
            )
        schema = _require_text(
            self.lifecycle_schema_version, field_name="lifecycle_schema_version"
        )
        if schema != HORIZON_LIFECYCLE_SCHEMA_VERSION:
            raise HorizonLifecycleContractError(
                "lifecycle_schema_version is not the ratified "
                f"{HORIZON_LIFECYCLE_SCHEMA_VERSION}"
            )
        object.__setattr__(self, "lifecycle_schema_version", schema)
        version = _require_text(self.policy_version, field_name="policy_version")
        if version not in DECLARED_LIFECYCLE_POLICY_VERSIONS:
            raise HorizonLifecycleContractError(
                f"policy_version is not a declared token: {version!r}"
            )
        object.__setattr__(self, "policy_version", version)
        if isinstance(self.thesis_version, bool) or not isinstance(
            self.thesis_version, int
        ):
            raise HorizonLifecycleContractError("thesis_version must be an integer")
        if self.thesis_version < 1:
            raise HorizonLifecycleContractError("thesis_version must be >= 1")
        object.__setattr__(self, "thesis_version", int(self.thesis_version))
        object.__setattr__(
            self,
            "horizon_class",
            require_horizon_class(self.horizon_class, field_name="horizon_class"),
        )
        try:
            state = ThesisLifecycleState(self.thesis_lifecycle_state)
        except ValueError as exc:
            raise HorizonLifecycleContractError(
                "thesis_lifecycle_state has an unsupported token: "
                f"{self.thesis_lifecycle_state!r}"
            ) from exc
        object.__setattr__(self, "thesis_lifecycle_state", state)
        try:
            reason = LifecycleAbstentionReason(self.abstention_reason)
        except ValueError as exc:
            raise HorizonLifecycleContractError(
                "abstention_reason has an unsupported token: "
                f"{self.abstention_reason!r}"
            ) from exc
        object.__setattr__(self, "abstention_reason", reason)
        evaluated = _require_utc(self.evaluated_at, field_name="evaluated_at")
        receipt = _require_utc(self.receipt_at, field_name="receipt_at")
        if receipt < evaluated:
            raise HorizonLifecycleContractError(
                "lifecycle evidence cannot be received before it was evaluated"
            )
        object.__setattr__(self, "evaluated_at", evaluated)
        object.__setattr__(self, "receipt_at", receipt)
        anchor = self.anchor
        if anchor is not None and not isinstance(anchor, FirstFillAnchor):
            raise HorizonLifecycleContractError("anchor must be a FirstFillAnchor")
        if anchor is not None and anchor.thesis_id != self.thesis_id:
            raise HorizonLifecycleContractError(
                "an anchor must belong to the thesis revision it is bound to"
            )
        clocks = tuple(self.clocks)
        names: list[ThesisClockName] = []
        for clock in clocks:
            if not isinstance(clock, ThesisLifecycleClock):
                raise HorizonLifecycleContractError(
                    "clocks may only contain ThesisLifecycleClock records"
                )
            if clock.thesis_id != self.thesis_id:
                raise HorizonLifecycleContractError(
                    "a clock must belong to the lifecycle's thesis revision"
                )
            names.append(clock.clock_name)
        if tuple(names) != tuple(name for name in CLOCK_ORDER if name in set(names)):
            raise HorizonLifecycleContractError(
                "clocks must be unique and in the declared clock order"
            )
        object.__setattr__(self, "clocks", clocks)
        if reason is LifecycleAbstentionReason.NONE:
            if tuple(names) != CLOCK_ORDER:
                raise HorizonLifecycleContractError(
                    "a supported lifecycle carries exactly the declared clock set"
                )
            if anchor is None:
                raise HorizonLifecycleContractError(
                    "a supported lifecycle must be bound to a first-fill anchor"
                )
        elif clocks:
            raise HorizonLifecycleContractError(
                "an abstained lifecycle observation carries no clocks"
            )
        expected_id = horizon_lifecycle_identity(
            lifecycle_schema_version=self.lifecycle_schema_version,
            policy_version=self.policy_version,
            thesis_id=self.thesis_id,
            thesis_version=self.thesis_version,
            horizon_class=self.horizon_class,
            anchor_id=None if anchor is None else anchor.anchor_id,
        )
        if self.lifecycle_id != expected_id:
            raise HorizonLifecycleContractError(
                "lifecycle_id is not the identity of the lifecycle it describes"
            )

    @property
    def authoritative(self) -> bool:
        """A lifecycle observation never carries authority."""

        return False

    def clock(self, clock_name: ThesisClockName | str) -> ThesisLifecycleClock | None:
        """The recorded clock with that name, or ``None``."""

        name = require_clock_name(clock_name, field_name="clock_name")
        for clock in self.clocks:
            if clock.clock_name is name:
                return clock
        return None

    @property
    def running_clock_names(self) -> tuple[ThesisClockName, ...]:
        """The clocks still running, in the declared clock order."""

        return tuple(
            clock.clock_name
            for clock in self.clocks
            if clock.state is ThesisClockState.RUNNING
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "HorizonThesisLifecycle",
            "lifecycle_id": self.lifecycle_id,
            "lifecycle_schema_version": self.lifecycle_schema_version,
            "policy_version": self.policy_version,
            "thesis_id": self.thesis_id,
            "thesis_version": self.thesis_version,
            "horizon_class": self.horizon_class.value,
            "thesis_lifecycle_state": self.thesis_lifecycle_state.value,
            "abstention_reason": self.abstention_reason.value,
            "clocks": [clock.to_dict() for clock in self.clocks],
            "evaluated_at": iso_z(self.evaluated_at, field_name="evaluated_at"),
            "receipt_at": iso_z(self.receipt_at, field_name="receipt_at"),
            "anchor": None if self.anchor is None else self.anchor.to_dict(),
        }


def horizon_lifecycle_identity(
    *,
    lifecycle_schema_version: str,
    policy_version: str,
    thesis_id: str,
    thesis_version: int,
    horizon_class: HorizonClass | str,
    anchor_id: str | None,
) -> str:
    """The deterministic ``HLFC:`` identity of one lifecycle observation.

    Identity binds the thesis revision, the applied policy version and the
    attached anchor. It excludes the evaluation instant and the clock states, so
    re-evaluating the same lifecycle later reproduces the same identity instead
    of minting a competing observation. No wall clock, UUID, process identity or
    invocation order takes part.
    """

    schema = _require_text(
        lifecycle_schema_version, field_name="lifecycle_schema_version"
    )
    if schema != HORIZON_LIFECYCLE_SCHEMA_VERSION:
        raise HorizonLifecycleContractError(
            "lifecycle_schema_version is not the ratified "
            f"{HORIZON_LIFECYCLE_SCHEMA_VERSION}"
        )
    version = _require_text(policy_version, field_name="policy_version")
    if version not in DECLARED_LIFECYCLE_POLICY_VERSIONS:
        raise HorizonLifecycleContractError(
            f"policy_version is not a declared token: {version!r}"
        )
    horizon = require_horizon_class(horizon_class, field_name="horizon_class")
    if isinstance(thesis_version, bool) or not isinstance(thesis_version, int):
        raise HorizonLifecycleContractError("thesis_version must be an integer")
    if thesis_version < 1:
        raise HorizonLifecycleContractError("thesis_version must be >= 1")
    anchor = (
        None if anchor_id is None else _require_text(anchor_id, field_name="anchor_id")
    )
    return stable_hash(
        HORIZON_LIFECYCLE_ID_PREFIX,
        {
            "lifecycle_schema_version": schema,
            "policy_version": version,
            "thesis_id": _require_text(thesis_id, field_name="thesis_id"),
            "thesis_version": int(thesis_version),
            "horizon_class": horizon.value,
            "anchor_id": anchor,
        },
    )


def _abstained_lifecycle(
    thesis: HorizonThesis,
    policy: ThesisLifecyclePolicy,
    *,
    reason: LifecycleAbstentionReason,
    evaluated_at: datetime,
    receipt_at: datetime,
    anchor: FirstFillAnchor | None,
) -> HorizonThesisLifecycle:
    """Record a governed abstention. No clock is started and none is guessed."""

    return HorizonThesisLifecycle(
        lifecycle_id=horizon_lifecycle_identity(
            lifecycle_schema_version=HORIZON_LIFECYCLE_SCHEMA_VERSION,
            policy_version=policy.policy_version,
            thesis_id=thesis.thesis_id,
            thesis_version=thesis.thesis_version,
            horizon_class=thesis.horizon_class,
            anchor_id=None if anchor is None else anchor.anchor_id,
        ),
        lifecycle_schema_version=HORIZON_LIFECYCLE_SCHEMA_VERSION,
        policy_version=policy.policy_version,
        thesis_id=thesis.thesis_id,
        thesis_version=thesis.thesis_version,
        horizon_class=thesis.horizon_class,
        thesis_lifecycle_state=thesis.lifecycle_state,
        abstention_reason=reason,
        clocks=(),
        evaluated_at=evaluated_at,
        receipt_at=receipt_at,
        anchor=anchor,
    )


def evaluate_thesis_lifecycle(
    thesis: HorizonThesis,
    *,
    policy: ThesisLifecyclePolicy,
    now: datetime,
    receipt_at: datetime,
    anchor: FirstFillAnchor | None = None,
) -> HorizonThesisLifecycle:
    """Observe one thesis revision's lifecycle at an explicit instant.

    This is the abstaining seam: evidence the policy and the revision cannot
    support produces a recorded `LifecycleAbstentionReason` rather than an
    exception, so a missing owner number or a missing invalidation is visible
    evidence instead of a guess. Two conditions still fail closed as errors,
    because they are point-in-time violations rather than missing evidence:

    * evaluating before a clock could start (before `classified_at`, or before
      the first fill the holding clock is anchored to), and
    * evidence received before the instant it describes.

    The returned observation carries no authority: it is research-only evidence
    and every clock it holds is arithmetic on preserved instants.
    """

    if not isinstance(thesis, HorizonThesis):
        raise HorizonLifecycleContractError("thesis must be a HorizonThesis")
    if not isinstance(policy, ThesisLifecyclePolicy):
        raise HorizonLifecycleContractError("policy must be a ThesisLifecyclePolicy")
    if anchor is not None and not isinstance(anchor, FirstFillAnchor):
        raise HorizonLifecycleContractError("anchor must be a FirstFillAnchor")
    if anchor is not None and anchor.thesis_id != thesis.thesis_id:
        raise HorizonLifecycleContractError(
            "an anchor belongs to the thesis revision that recorded it"
        )
    instant = _require_utc(now, field_name="now")
    receipt = _require_utc(receipt_at, field_name="receipt_at")
    if receipt < instant:
        raise HorizonLifecycleContractError(
            "lifecycle evidence cannot be received before the observation instant"
        )

    def _abstain(reason: LifecycleAbstentionReason) -> HorizonThesisLifecycle:
        return _abstained_lifecycle(
            thesis,
            policy,
            reason=reason,
            evaluated_at=instant,
            receipt_at=receipt,
            anchor=anchor,
        )

    if not policy.configured:
        return _abstain(LifecycleAbstentionReason.POLICY_NOT_CONFIGURED)
    if thesis.horizon_class is HorizonClass.UNKNOWN:
        return _abstain(LifecycleAbstentionReason.UNKNOWN_HORIZON_CLASS)
    if policy.band_for(thesis.horizon_class) is None:
        return _abstain(LifecycleAbstentionReason.UNSUPPORTED_LIFECYCLE_BAND)
    clocks: list[ThesisLifecycleClock] = []
    for clock_name in CLOCK_ORDER:
        reason, resolved = _resolve_clock_requirements(
            thesis, clock_name, policy, anchor
        )
        if reason is not None or resolved is None:
            return _abstain(reason)
        clocks.append(
            advance_clock(
                start_clock(
                    thesis,
                    clock_name,
                    policy=policy,
                    receipt_at=receipt,
                    anchor=anchor,
                ),
                now=instant,
                receipt_at=receipt,
            )
        )
    state = (
        ThesisLifecycleState.EXPIRED
        if all(clock.state is ThesisClockState.EXPIRED for clock in clocks)
        else ThesisLifecycleState.ACTIVE
    )
    return HorizonThesisLifecycle(
        lifecycle_id=horizon_lifecycle_identity(
            lifecycle_schema_version=HORIZON_LIFECYCLE_SCHEMA_VERSION,
            policy_version=policy.policy_version,
            thesis_id=thesis.thesis_id,
            thesis_version=thesis.thesis_version,
            horizon_class=thesis.horizon_class,
            anchor_id=None if anchor is None else anchor.anchor_id,
        ),
        lifecycle_schema_version=HORIZON_LIFECYCLE_SCHEMA_VERSION,
        policy_version=policy.policy_version,
        thesis_id=thesis.thesis_id,
        thesis_version=thesis.thesis_version,
        horizon_class=thesis.horizon_class,
        thesis_lifecycle_state=state,
        abstention_reason=LifecycleAbstentionReason.NONE,
        clocks=tuple(clocks),
        evaluated_at=instant,
        receipt_at=receipt,
        anchor=anchor,
    )


def attach_first_fill(
    thesis: HorizonThesis,
    *,
    policy: ThesisLifecyclePolicy,
    first_fill_at: datetime,
    receipt_at: datetime,
    filled_quantity: Any,
    average_fill_price: Any,
    source_reference: str,
    now: datetime,
    previous: HorizonThesisLifecycle | None = None,
) -> HorizonThesisLifecycle:
    """Attach the first-fill anchor and observe the lifecycle in one step.

    ``receipt_at`` is the receipt instant of this observation and must be at or
    after both ``first_fill_at`` and ``now``; it is recorded on the anchor and on
    the lifecycle observation, so no caller can hand the two seams different
    clocks.

    The anchor of a thesis revision is recorded once. Re-attaching the same first
    fill is idempotent: the already recorded anchor is reused, so accumulated
    fills and observation references are never discarded. Attaching a different
    first fill to the same revision is refused rather than rewriting the anchor
    of a revision whose holding clock is already anchored.
    """

    if previous is not None and not isinstance(previous, HorizonThesisLifecycle):
        raise HorizonLifecycleContractError(
            "previous must be a HorizonThesisLifecycle"
        )
    incoming = build_first_fill_anchor(
        thesis,
        first_fill_at=first_fill_at,
        receipt_at=receipt_at,
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        source_reference=source_reference,
    )
    anchor = incoming
    if previous is not None:
        if previous.thesis_id != thesis.thesis_id:
            raise HorizonLifecycleContractError(
                "previous must describe the lifecycle of the same thesis revision"
            )
        if previous.anchor is None:
            raise HorizonLifecycleContractError(
                "previous must carry the anchor being re-attached"
            )
        if previous.anchor.anchor_id != incoming.anchor_id:
            raise HorizonLifecycleContractError(
                "the first-fill anchor of a thesis revision is recorded once and "
                "never rewritten"
            )
        anchor = previous.anchor
    return evaluate_thesis_lifecycle(
        thesis,
        policy=policy,
        now=now,
        receipt_at=receipt_at,
        anchor=anchor,
    )


def remaining_lifecycle_seconds(
    lifecycle: HorizonThesisLifecycle,
    *,
    now: datetime,
    clock_name: ThesisClockName | str = ThesisClockName.HOLDING,
) -> int | None:
    """Remaining whole seconds on one clock at an explicit instant.

    ``None`` means the lifecycle abstained, so an unsupported lifecycle can
    never be read as "zero seconds left". This is the point-in-time read an
    advisory projection consumes: it derives from preserved evidence and never
    from a clock read, and it never returns a negative value.
    """

    if not isinstance(lifecycle, HorizonThesisLifecycle):
        raise HorizonLifecycleContractError(
            "lifecycle must be a HorizonThesisLifecycle"
        )
    clock = lifecycle.clock(clock_name)
    if clock is None:
        return None
    instant = _require_utc(now, field_name="now")
    if instant < clock.started_at:
        raise HorizonLifecycleContractError(
            "a clock cannot be observed before it started"
        )
    return max(0, int((clock.expires_at - instant).total_seconds()))


__all__ = [
    "CLOCK_ORDER",
    "DECLARED_LIFECYCLE_POLICY_VERSIONS",
    "FIRST_FILL_ANCHOR_ID_PREFIX",
    "FIRST_FILL_ANCHOR_SCHEMA_VERSION",
    "HORIZON_LIFECYCLE_ENGINEERING_SCENARIO_POLICY_VERSION",
    "HORIZON_LIFECYCLE_ID_PREFIX",
    "HORIZON_LIFECYCLE_POLICY_VERSION",
    "HORIZON_LIFECYCLE_SCHEMA_VERSION",
    "OWNER_INPUT_STATUS",
    "THESIS_CLOCK_ID_PREFIX",
    "THESIS_CLOCK_SCHEMA_VERSION",
    "FirstFillAnchor",
    "HorizonLifecycleContractError",
    "HorizonThesisLifecycle",
    "LifecycleAbstentionReason",
    "ThesisClockName",
    "ThesisClockState",
    "ThesisLifecycleClock",
    "ThesisLifecyclePolicy",
    "advance_clock",
    "attach_first_fill",
    "build_first_fill_anchor",
    "evaluate_thesis_lifecycle",
    "first_fill_anchor_identity",
    "horizon_lifecycle_identity",
    "record_additional_fill",
    "remaining_lifecycle_seconds",
    "require_clock_name",
    "start_clock",
    "thesis_clock_identity",
]
