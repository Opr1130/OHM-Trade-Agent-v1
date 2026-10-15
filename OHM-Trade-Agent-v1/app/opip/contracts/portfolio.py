"""Portfolio Selector vocabulary and typed contracts (R3 F7).

This is the shared, pure vocabulary for the fifth R3 slice: the F7 constrained
economic / portfolio selector that sits after the F6 Forecast Engine. It owns the
selection-status vocabulary, the cash/no-trade vocabulary, the abstention
vocabulary, the evidence-status vocabulary, the reservation-plan vocabulary, the
explicit capital and exposure snapshots, the constraint/economics policy, the
immutable candidate, allocation, reservation, decision and comparator records,
and their deterministic identities.

Boundaries that are deliberate and enforced here:

* A selection status is exactly ``SELECTED``, ``CASH_NO_TRADE`` or
  ``INSUFFICIENT_EVIDENCE``. ``CASH_NO_TRADE`` is a real competing decision, not
  an error: the selector holds cash whenever no admissible constrained portfolio
  beats cash. A quota is never filled to force a trade.
* ``INSUFFICIENT_EVIDENCE`` is a governed abstention, not a veto: it is returned
  when the evidence needed to make an economic decision is missing, stale or
  untrusted. Missing evidence is never favorable evidence.
* The primary objective is expected portfolio net dollars over one common,
  declared evaluation window. Hit rate, profit factor and capital-hours are
  diagnostics only, never the objective and never a promotion threshold.
* Every fraction is relative to the explicit ``PortfolioCapitalState`` base. No
  numeric constraint has a default here: the caller supplies the observed
  production values explicitly, and the legacy alias module reuses the existing
  production constants instead of restating them.
* No ordinal score is a probability and no score is scaled into one. F7 never
  converts a ranking score, an alert confidence or a Committee rubric into an
  economic input; economic inputs come only from a trusted F6 ``FORECAST``.
* The decision identity is a deterministic function of the ratified versions,
  the explicit capital/exposure/panel fingerprints, the evaluation window, the
  evaluation instant, the status and the record's own semantic payload. No UUID,
  clock, retry count, process identity or database sequence takes part, and a
  forged identity fails closed.

Nothing here reads a clock, the environment, the filesystem, the network or a
database, and nothing here places, reserves or authorizes any paper or funded
order.

SHADOW / NON-AUTHORITATIVE. This vocabulary is a research artifact. It is not
wired into ``run_cycle`` or ``scan_opportunities``, it activates no Feature Bus,
it invokes no production reservation writer, and it writes no canonical evidence.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any

from app.opip.contracts.forecast import (
    DistributionKind,
    EntryExecutionOutcome,
    ForecastDecision,
    ForecastStatus,
)
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import TemporalIntegrityError, require_utc

#: Schema version of the durable ``PortfolioDecision`` record.
PORTFOLIO_DECISION_SCHEMA_VERSION = "portfolio-decision-v1"

#: Applied portfolio-selector implementation version. A deterministic code artifact.
PORTFOLIO_SELECTOR_VERSION = "portfolio-selector-v1"

#: Applied portfolio policy version. A deterministic, replayable code artifact
#: tagged by this token; it is not a caller-selected input.
PORTFOLIO_POLICY_VERSION = "portfolio-shadow-policy-v1"

#: Schema version of the ``PortfolioReservationPlan`` record.
PORTFOLIO_RESERVATION_PLAN_SCHEMA_VERSION = "portfolio-reservation-plan-v1"

#: Applied frozen legacy comparator version.
PORTFOLIO_COMPARATOR_VERSION = "portfolio-legacy-comparator-v1"

#: Version of the F7 comparison / evaluation framework.
PORTFOLIO_EVALUATION_VERSION = "portfolio-evaluation-v1"

#: Semantic prefixes of the deterministic identities.
PORTFOLIO_DECISION_ID_PREFIX = "PSEL"
PORTFOLIO_CANDIDATE_ID_PREFIX = "PCAND"
PORTFOLIO_ALLOCATION_ID_PREFIX = "PALLOC"
PORTFOLIO_RESERVATION_PLAN_ID_PREFIX = "PRSV"
PORTFOLIO_RESERVATION_ID_PREFIX = "PRES"
PORTFOLIO_WINDOW_ID_PREFIX = "PWIN"
PORTFOLIO_CAPITAL_STATE_FINGERPRINT_PREFIX = "PCAP"
PORTFOLIO_EXPOSURE_FINGERPRINT_PREFIX = "PEXP"
PORTFOLIO_PANEL_FINGERPRINT_PREFIX = "PPANEL"
PORTFOLIO_POLICY_ID_PREFIX = "PPOL"
PORTFOLIO_COMPARATOR_ID_PREFIX = "PCMP"

#: The two supported directions. F7 performs no hedging and opens no new asset.
PORTFOLIO_DIRECTIONS: tuple[str, ...] = ("LONG", "SHORT")


class PortfolioContractError(ValueError):
    """A structural contract violation. Always fails closed."""


class PortfolioStatus(str, Enum):
    """The F7 selection status. There is no fourth token."""

    SELECTED = "SELECTED"
    CASH_NO_TRADE = "CASH_NO_TRADE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class PortfolioDirection(str, Enum):
    """The direction of one candidate or allocation."""

    LONG = "LONG"
    SHORT = "SHORT"


class PortfolioCashReason(str, Enum):
    """Why cash / no-trade was selected over every admissible portfolio."""

    NO_POSITIVE_EXPECTED_NET_DOLLARS = "NO_POSITIVE_EXPECTED_NET_DOLLARS"
    CONSTRAINTS_EXCLUDE_ALL_CANDIDATES = "CONSTRAINTS_EXCLUDE_ALL_CANDIDATES"


class PortfolioAbstentionReason(str, Enum):
    """The bounded, machine-readable abstention vocabulary.

    An abstention is a governed evidence disposition, not a veto. It carries one
    of these reasons so the gap is auditable. Missing evidence is never favorable.
    """

    NO_ELIGIBLE_CANDIDATES = "NO_ELIGIBLE_CANDIDATES"
    NO_QUALIFIED_FORECAST = "NO_QUALIFIED_FORECAST"
    CAPITAL_STATE_UNAVAILABLE = "CAPITAL_STATE_UNAVAILABLE"
    EXPOSURE_STATE_UNAVAILABLE = "EXPOSURE_STATE_UNAVAILABLE"


class PortfolioEvidenceStatus(str, Enum):
    """The data-quality / execution-evidence gate for one candidate.

    ``VALID`` is the only status that permits an economic allocation; any other
    status is missing or failed evidence and never improves a candidate's rank.
    """

    VALID = "VALID"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class ReservationStatus(str, Enum):
    """One reservation's lifecycle state inside a plan."""

    PLANNED = "PLANNED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    FILL_ADJUSTED = "FILL_ADJUSTED"


class ReservationReleaseReason(str, Enum):
    """Why a planned reservation stopped being planned."""

    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FILL_ADJUSTED = "FILL_ADJUSTED"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def require_portfolio_enum(enum_type: type[Enum], value: Any, *, field_name: str) -> Any:
    """Coerce one enum token strictly; reject malformed or unsupported tokens."""
    if isinstance(value, enum_type):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise PortfolioContractError(f"{field_name} must be a {enum_type.__name__} token")
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            raise PortfolioContractError(
                f"{field_name} has an unsupported token: {value!r}"
            ) from exc
    raise PortfolioContractError(f"{field_name} must be a {enum_type.__name__} token")


def require_portfolio_text(value: Any, *, field_name: str) -> str:
    """Validate one required canonical text token (non-empty, no surrounding space)."""
    if not isinstance(value, str):
        raise PortfolioContractError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise PortfolioContractError(
            f"{field_name} must be a non-empty, whitespace-free token"
        )
    return value


def require_portfolio_utc(value: Any, *, field_name: str) -> datetime:
    """Validate one required explicit UTC instant (naive/malformed fails closed)."""
    if not isinstance(value, datetime):
        raise PortfolioContractError(f"{field_name} must be an explicit datetime")
    try:
        return require_utc(value, field_name=field_name)
    except TemporalIntegrityError as exc:
        raise PortfolioContractError(str(exc)) from exc


def require_finite(value: Any, *, field_name: str) -> float:
    """Validate one finite decimal number; bool, non-numeric or non-finite fails closed."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PortfolioContractError(f"{field_name} must be a finite decimal number")
    number = float(value)
    if not math.isfinite(number):
        raise PortfolioContractError(f"{field_name} must be finite")
    return number


def require_positive(value: Any, *, field_name: str) -> float:
    number = require_finite(value, field_name=field_name)
    if number <= 0.0:
        raise PortfolioContractError(f"{field_name} must be positive")
    return number


def require_non_negative(value: Any, *, field_name: str) -> float:
    number = require_finite(value, field_name=field_name)
    if number < 0.0:
        raise PortfolioContractError(f"{field_name} must not be negative")
    return number


def require_fraction(value: Any, *, field_name: str) -> float:
    """Validate one fraction within ``(0, 1]``. It is never clipped into range."""
    number = require_finite(value, field_name=field_name)
    if number <= 0.0 or number > 1.0:
        raise PortfolioContractError(f"{field_name} must be within (0, 1]")
    return number


def require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PortfolioContractError(f"{field_name} must be an integer")
    if value < 0:
        raise PortfolioContractError(f"{field_name} must not be negative")
    return value


def require_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PortfolioContractError(f"{field_name} must be an integer")
    if value <= 0:
        raise PortfolioContractError(f"{field_name} must be positive")
    return value


def _parse_persisted_utc(value: Any, *, field_name: str) -> datetime:
    """Strictly parse one durable UTC instant at a durability trust boundary."""
    if not isinstance(value, str):
        raise PortfolioContractError(f"{field_name} must be an ISO-8601 UTC string")
    if value == "" or value != value.strip():
        raise PortfolioContractError(
            f"{field_name} must be a non-empty, whitespace-free ISO-8601 UTC string"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PortfolioContractError(f"{field_name} must be an ISO-8601 UTC instant") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PortfolioContractError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise PortfolioContractError(f"{field_name} must be a UTC instant")
    return parsed.astimezone(timezone.utc)


def _require_durable_mapping(
    value: Any, *, field_name: str, expected_keys: tuple[str, ...]
) -> Mapping[str, Any]:
    """Refuse a non-mapping, or one whose key set is not exactly ``expected_keys``."""
    if not isinstance(value, Mapping):
        raise PortfolioContractError(f"{field_name} must be a mapping")
    present = set(value.keys())
    expected = set(expected_keys)
    missing = sorted(expected - present)
    if missing:
        raise PortfolioContractError(
            f"{field_name} is missing mandatory keys: " + ", ".join(missing)
        )
    unknown = sorted(str(key) for key in present - expected)
    if unknown:
        raise PortfolioContractError(
            f"{field_name} carries unknown keys: " + ", ".join(unknown)
        )
    return value


def _freeze_mapping(raw: Any, *, field_name: str, value_kind: str) -> Mapping[str, Any]:
    """Deep-freeze a text-keyed mapping of text, counts or finite numbers."""
    if not isinstance(raw, Mapping):
        raise PortfolioContractError(f"{field_name} must be a mapping")
    frozen: dict[str, Any] = {}
    for key, item in raw.items():
        token = require_portfolio_text(key, field_name=f"{field_name} key")
        if value_kind == "text":
            frozen[token] = require_portfolio_text(item, field_name=f"{field_name}[{token}]")
        elif value_kind == "int":
            frozen[token] = require_non_negative_int(item, field_name=f"{field_name}[{token}]")
        elif value_kind == "non_negative":
            frozen[token] = require_non_negative(item, field_name=f"{field_name}[{token}]")
        else:
            frozen[token] = require_finite(item, field_name=f"{field_name}[{token}]")
    return MappingProxyType(dict(sorted(frozen.items())))


def _mapping_to_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(value)}


# ---------------------------------------------------------------------------
# Explicit window, capital and exposure snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioEvaluationWindow:
    """The one common, declared evaluation window shared by the whole panel.

    Every candidate is evaluated over this single window; a per-candidate window
    is refused so a favourable window can never be cherry-picked per candidate.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = require_portfolio_utc(self.start, field_name="window.start")
        end = require_portfolio_utc(self.end, field_name="window.end")
        if end <= start:
            raise PortfolioContractError("window.end must be strictly after window.start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def window_identity(self) -> str:
        return portfolio_window_identity(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": iso_z(self.start, field_name="window.start"),
            "end": iso_z(self.end, field_name="window.end"),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioEvaluationWindow":
        body = _require_durable_mapping(
            raw, field_name="window", expected_keys=("start", "end")
        )
        return cls(
            start=_parse_persisted_utc(body["start"], field_name="window.start"),
            end=_parse_persisted_utc(body["end"], field_name="window.end"),
        )


def portfolio_window_identity(window: PortfolioEvaluationWindow) -> str:
    """The deterministic ``PWIN:`` identity of one declared evaluation window."""
    if not isinstance(window, PortfolioEvaluationWindow):
        raise PortfolioContractError("window must be a PortfolioEvaluationWindow")
    return stable_hash(PORTFOLIO_WINDOW_ID_PREFIX, window.to_dict())


@dataclass(frozen=True)
class PortfolioCapitalState:
    """The explicit capital-state snapshot passed into the pure selector.

    The selector never inspects a live balance: the caller supplies the available
    capital base, the portfolio version it belongs to and the instant it was
    observed. Fractions are relative to ``available_capital``.
    """

    portfolio_version: str
    available_capital: float
    currency: str
    as_of: datetime
    capital_state_fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "portfolio_version",
            require_portfolio_text(self.portfolio_version, field_name="portfolio_version"),
        )
        object.__setattr__(
            self,
            "available_capital",
            require_positive(self.available_capital, field_name="available_capital"),
        )
        object.__setattr__(
            self, "currency", require_portfolio_text(self.currency, field_name="currency")
        )
        object.__setattr__(
            self, "as_of", require_portfolio_utc(self.as_of, field_name="as_of")
        )
        expected = stable_hash(
            PORTFOLIO_CAPITAL_STATE_FINGERPRINT_PREFIX, self._fingerprint_payload()
        )
        if self.capital_state_fingerprint == "":
            object.__setattr__(self, "capital_state_fingerprint", expected)
        elif self.capital_state_fingerprint != expected:
            raise PortfolioContractError(
                "capital_state_fingerprint does not match its inputs; build capital "
                "state with the portfolio selector"
            )

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "portfolio_version": self.portfolio_version,
            "available_capital": self.available_capital,
            "currency": self.currency,
            "as_of": iso_z(self.as_of, field_name="as_of"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_version": self.portfolio_version,
            "available_capital": self.available_capital,
            "currency": self.currency,
            "as_of": iso_z(self.as_of, field_name="as_of"),
            "capital_state_fingerprint": self.capital_state_fingerprint,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioCapitalState":
        body = _require_durable_mapping(
            raw,
            field_name="capital_state",
            expected_keys=(
                "portfolio_version",
                "available_capital",
                "currency",
                "as_of",
                "capital_state_fingerprint",
            ),
        )
        return cls(
            portfolio_version=body["portfolio_version"],
            available_capital=body["available_capital"],
            currency=body["currency"],
            as_of=_parse_persisted_utc(body["as_of"], field_name="as_of"),
            capital_state_fingerprint=body["capital_state_fingerprint"],
        )


_EXPOSURE_DURABLE_KEYS: tuple[str, ...] = (
    "portfolio_version",
    "gross_exposure",
    "open_positions",
    "loss_at_stop",
    "same_direction_counts",
    "symbol_exposure",
    "common_shock_group_counts",
    "as_of",
    "exposure_fingerprint",
)


@dataclass(frozen=True)
class PortfolioExposureSnapshot:
    """The explicit current-exposure snapshot passed into the pure selector.

    Existing gross exposure, position count, loss already at stop, per-direction
    counts, per-symbol exposure and per-common-shock-group counts are all explicit.
    The selector never reads a live portfolio.
    """

    portfolio_version: str
    gross_exposure: float
    open_positions: int
    loss_at_stop: float
    same_direction_counts: Mapping[str, int]
    symbol_exposure: Mapping[str, float]
    common_shock_group_counts: Mapping[str, int]
    as_of: datetime
    exposure_fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "portfolio_version",
            require_portfolio_text(self.portfolio_version, field_name="portfolio_version"),
        )
        object.__setattr__(
            self,
            "gross_exposure",
            require_non_negative(self.gross_exposure, field_name="gross_exposure"),
        )
        object.__setattr__(
            self,
            "open_positions",
            require_non_negative_int(self.open_positions, field_name="open_positions"),
        )
        object.__setattr__(
            self,
            "loss_at_stop",
            require_non_negative(self.loss_at_stop, field_name="loss_at_stop"),
        )
        object.__setattr__(
            self,
            "same_direction_counts",
            _freeze_mapping(
                self.same_direction_counts,
                field_name="same_direction_counts",
                value_kind="int",
            ),
        )
        object.__setattr__(
            self,
            "symbol_exposure",
            _freeze_mapping(
                self.symbol_exposure,
                field_name="symbol_exposure",
                value_kind="non_negative",
            ),
        )
        object.__setattr__(
            self,
            "common_shock_group_counts",
            _freeze_mapping(
                self.common_shock_group_counts,
                field_name="common_shock_group_counts",
                value_kind="int",
            ),
        )
        object.__setattr__(
            self, "as_of", require_portfolio_utc(self.as_of, field_name="as_of")
        )
        expected = stable_hash(
            PORTFOLIO_EXPOSURE_FINGERPRINT_PREFIX, self._fingerprint_payload()
        )
        if self.exposure_fingerprint == "":
            object.__setattr__(self, "exposure_fingerprint", expected)
        elif self.exposure_fingerprint != expected:
            raise PortfolioContractError(
                "exposure_fingerprint does not match its inputs; build exposure with "
                "the portfolio selector"
            )

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "portfolio_version": self.portfolio_version,
            "gross_exposure": self.gross_exposure,
            "open_positions": self.open_positions,
            "loss_at_stop": self.loss_at_stop,
            "same_direction_counts": _mapping_to_dict(self.same_direction_counts),
            "symbol_exposure": _mapping_to_dict(self.symbol_exposure),
            "common_shock_group_counts": _mapping_to_dict(self.common_shock_group_counts),
            "as_of": iso_z(self.as_of, field_name="as_of"),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._fingerprint_payload()
        payload["as_of"] = iso_z(self.as_of, field_name="as_of")
        payload["exposure_fingerprint"] = self.exposure_fingerprint
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioExposureSnapshot":
        body = _require_durable_mapping(
            raw, field_name="exposure", expected_keys=_EXPOSURE_DURABLE_KEYS
        )
        return cls(
            portfolio_version=body["portfolio_version"],
            gross_exposure=body["gross_exposure"],
            open_positions=body["open_positions"],
            loss_at_stop=body["loss_at_stop"],
            same_direction_counts=body["same_direction_counts"],
            symbol_exposure=body["symbol_exposure"],
            common_shock_group_counts=body["common_shock_group_counts"],
            as_of=_parse_persisted_utc(body["as_of"], field_name="as_of"),
            exposure_fingerprint=body["exposure_fingerprint"],
        )


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


_POLICY_DURABLE_KEYS: tuple[str, ...] = (
    "policy_version",
    "max_gross_exposure_fraction",
    "max_positions",
    "max_same_direction",
    "max_capital_fraction_per_candidate",
    "max_symbol_exposure_fraction",
    "max_common_shock_group_positions",
    "max_portfolio_loss_fraction",
    "attributable_operating_cost",
    "policy_identity",
)


@dataclass(frozen=True)
class PortfolioPolicy:
    """The applied, versioned F7 policy (constraints + explicit economics).

    Every numeric constraint is explicit and has no default: the observed
    production values are supplied by the legacy alias helper, which imports the
    existing production constants rather than restating them. ``attributable_operating_cost``
    of ``None`` means the operating-cost evidence is unavailable and is reported as
    unknown; it is never silently treated as zero.
    """

    max_gross_exposure_fraction: float
    max_positions: int
    max_same_direction: int
    max_capital_fraction_per_candidate: float
    max_symbol_exposure_fraction: float
    max_common_shock_group_positions: int
    max_portfolio_loss_fraction: float
    attributable_operating_cost: float | None = None
    policy_version: str = PORTFOLIO_POLICY_VERSION
    policy_identity: str = ""

    def __post_init__(self) -> None:
        for name in (
            "max_gross_exposure_fraction",
            "max_capital_fraction_per_candidate",
            "max_symbol_exposure_fraction",
            "max_portfolio_loss_fraction",
        ):
            object.__setattr__(
                self, name, require_fraction(getattr(self, name), field_name=name)
            )
        for name in (
            "max_positions",
            "max_same_direction",
            "max_common_shock_group_positions",
        ):
            object.__setattr__(
                self, name, require_positive_int(getattr(self, name), field_name=name)
            )
        if self.attributable_operating_cost is not None:
            object.__setattr__(
                self,
                "attributable_operating_cost",
                require_non_negative(
                    self.attributable_operating_cost,
                    field_name="attributable_operating_cost",
                ),
            )
        value = require_portfolio_text(self.policy_version, field_name="policy_version")
        if value != PORTFOLIO_POLICY_VERSION:
            raise PortfolioContractError(
                f"policy_version is not the ratified {PORTFOLIO_POLICY_VERSION}"
            )
        object.__setattr__(self, "policy_version", value)
        expected = stable_hash(
            PORTFOLIO_POLICY_ID_PREFIX, self._identity_payload()
        )
        if self.policy_identity == "":
            object.__setattr__(self, "policy_identity", expected)
        elif self.policy_identity != expected:
            raise PortfolioContractError(
                "policy_identity does not match its constraints; build the policy with "
                "the portfolio selector"
            )

    @property
    def operating_cost_available(self) -> bool:
        return self.attributable_operating_cost is not None

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "max_gross_exposure_fraction": self.max_gross_exposure_fraction,
            "max_positions": self.max_positions,
            "max_same_direction": self.max_same_direction,
            "max_capital_fraction_per_candidate": self.max_capital_fraction_per_candidate,
            "max_symbol_exposure_fraction": self.max_symbol_exposure_fraction,
            "max_common_shock_group_positions": self.max_common_shock_group_positions,
            "max_portfolio_loss_fraction": self.max_portfolio_loss_fraction,
            "attributable_operating_cost": self.attributable_operating_cost,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["policy_identity"] = self.policy_identity
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioPolicy":
        body = _require_durable_mapping(
            raw, field_name="policy", expected_keys=_POLICY_DURABLE_KEYS
        )
        return cls(
            max_gross_exposure_fraction=body["max_gross_exposure_fraction"],
            max_positions=body["max_positions"],
            max_same_direction=body["max_same_direction"],
            max_capital_fraction_per_candidate=body["max_capital_fraction_per_candidate"],
            max_symbol_exposure_fraction=body["max_symbol_exposure_fraction"],
            max_common_shock_group_positions=body["max_common_shock_group_positions"],
            max_portfolio_loss_fraction=body["max_portfolio_loss_fraction"],
            attributable_operating_cost=body["attributable_operating_cost"],
            policy_version=body["policy_version"],
            policy_identity=body["policy_identity"],
        )


# ---------------------------------------------------------------------------
# Legacy comparator observables (the frozen legacy panel inputs)
# ---------------------------------------------------------------------------


_LEGACY_OBSERVABLES_KEYS: tuple[str, ...] = (
    "technical_score",
    "attainability_score",
    "target_2_move_pct",
    "execution_drag_pct",
    "book_coverage_status",
    "recent_trade_status",
    "independent_reference_status",
    "cross_pair_confirmation_status",
)


@dataclass(frozen=True)
class LegacyComparatorObservables:
    """The legacy comparator's decision inputs for one panel member.

    These are the exact observables the frozen legacy ranking needs. Carrying them
    on the F7 candidate guarantees both selectors evaluate the *same* panel: there
    is no separate, cherry-picked legacy population.
    """

    technical_score: int
    attainability_score: float
    target_2_move_pct: float
    book_coverage_status: str
    recent_trade_status: str
    cross_pair_confirmation_status: str
    execution_drag_pct: float | None = None
    independent_reference_status: str | None = None

    def __post_init__(self) -> None:
        score = require_non_negative_int(self.technical_score, field_name="technical_score")
        if score > 100:
            raise PortfolioContractError("technical_score must be within [0, 100]")
        object.__setattr__(self, "technical_score", score)
        attainability = require_finite(
            self.attainability_score, field_name="attainability_score"
        )
        if attainability < 0.0 or attainability > 100.0:
            raise PortfolioContractError("attainability_score must be within [0, 100]")
        object.__setattr__(self, "attainability_score", attainability)
        object.__setattr__(
            self,
            "target_2_move_pct",
            require_non_negative(self.target_2_move_pct, field_name="target_2_move_pct"),
        )
        if self.execution_drag_pct is not None:
            object.__setattr__(
                self,
                "execution_drag_pct",
                require_non_negative(self.execution_drag_pct, field_name="execution_drag_pct"),
            )
        for name in (
            "book_coverage_status",
            "recent_trade_status",
            "cross_pair_confirmation_status",
        ):
            object.__setattr__(
                self, name, require_portfolio_text(getattr(self, name), field_name=name)
            )
        if self.independent_reference_status is not None:
            object.__setattr__(
                self,
                "independent_reference_status",
                require_portfolio_text(
                    self.independent_reference_status,
                    field_name="independent_reference_status",
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "technical_score": self.technical_score,
            "attainability_score": self.attainability_score,
            "target_2_move_pct": self.target_2_move_pct,
            "execution_drag_pct": self.execution_drag_pct,
            "book_coverage_status": self.book_coverage_status,
            "recent_trade_status": self.recent_trade_status,
            "independent_reference_status": self.independent_reference_status,
            "cross_pair_confirmation_status": self.cross_pair_confirmation_status,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "LegacyComparatorObservables":
        body = _require_durable_mapping(
            raw, field_name="legacy_observables", expected_keys=_LEGACY_OBSERVABLES_KEYS
        )
        return cls(**body)


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------


_CANDIDATE_DURABLE_KEYS: tuple[str, ...] = (
    "candidate_id",
    "episode_id",
    "feasibility_decision_id",
    "forecast_decision_id",
    "symbol",
    "direction",
    "common_shock_group",
    "data_quality_status",
    "execution_evidence_status",
    "liquidity_capacity_notional",
    "requested_capital_fraction",
    "stop_loss_fraction",
    "legacy_observables",
    "forecast",
)


@dataclass(frozen=True)
class PortfolioCandidate:
    """One immutable candidate on the frozen panel.

    The candidate preserves the frozen F4/F5/F6 lineage and carries the explicit
    economic inputs F7 needs. Its deterministic ``PCAND:`` identity is a function
    of its lineage and semantics, so two structurally identical candidates collide
    rather than minting duplicate capital.
    """

    episode_id: str
    feasibility_decision_id: str
    symbol: str
    direction: PortfolioDirection
    common_shock_group: str
    data_quality_status: PortfolioEvidenceStatus
    execution_evidence_status: PortfolioEvidenceStatus
    liquidity_capacity_notional: float
    requested_capital_fraction: float
    stop_loss_fraction: float
    legacy_observables: LegacyComparatorObservables
    forecast: ForecastDecision | None = None
    candidate_id: str = ""

    def __post_init__(self) -> None:
        for name in ("episode_id", "feasibility_decision_id", "symbol", "common_shock_group"):
            object.__setattr__(
                self, name, require_portfolio_text(getattr(self, name), field_name=name)
            )
        object.__setattr__(
            self,
            "direction",
            require_portfolio_enum(PortfolioDirection, self.direction, field_name="direction"),
        )
        object.__setattr__(
            self,
            "data_quality_status",
            require_portfolio_enum(
                PortfolioEvidenceStatus, self.data_quality_status, field_name="data_quality_status"
            ),
        )
        object.__setattr__(
            self,
            "execution_evidence_status",
            require_portfolio_enum(
                PortfolioEvidenceStatus,
                self.execution_evidence_status,
                field_name="execution_evidence_status",
            ),
        )
        object.__setattr__(
            self,
            "liquidity_capacity_notional",
            require_positive(
                self.liquidity_capacity_notional, field_name="liquidity_capacity_notional"
            ),
        )
        object.__setattr__(
            self,
            "requested_capital_fraction",
            require_fraction(
                self.requested_capital_fraction, field_name="requested_capital_fraction"
            ),
        )
        object.__setattr__(
            self,
            "stop_loss_fraction",
            require_fraction(self.stop_loss_fraction, field_name="stop_loss_fraction"),
        )
        if not isinstance(self.legacy_observables, LegacyComparatorObservables):
            raise PortfolioContractError(
                "legacy_observables must be LegacyComparatorObservables"
            )
        if self.forecast is not None and not isinstance(self.forecast, ForecastDecision):
            raise PortfolioContractError("forecast must be a ForecastDecision")
        expected = stable_hash(PORTFOLIO_CANDIDATE_ID_PREFIX, self._identity_payload())
        if self.candidate_id == "":
            object.__setattr__(self, "candidate_id", expected)
        elif self.candidate_id != expected:
            raise PortfolioContractError(
                "candidate_id does not match its lineage; build candidates with the "
                "portfolio selector"
            )

    @property
    def forecast_decision_id(self) -> str | None:
        return None if self.forecast is None else self.forecast.decision_id

    @property
    def evidence_complete(self) -> bool:
        """True only when the data-quality and execution-evidence gates are ``VALID``."""
        return (
            self.data_quality_status is PortfolioEvidenceStatus.VALID
            and self.execution_evidence_status is PortfolioEvidenceStatus.VALID
        )

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "feasibility_decision_id": self.feasibility_decision_id,
            "forecast_decision_id": self.forecast_decision_id,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "common_shock_group": self.common_shock_group,
            "data_quality_status": self.data_quality_status.value,
            "execution_evidence_status": self.execution_evidence_status.value,
            "liquidity_capacity_notional": self.liquidity_capacity_notional,
            "requested_capital_fraction": self.requested_capital_fraction,
            "stop_loss_fraction": self.stop_loss_fraction,
            "legacy_observables": self.legacy_observables.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "episode_id": self.episode_id,
            "feasibility_decision_id": self.feasibility_decision_id,
            "forecast_decision_id": self.forecast_decision_id,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "common_shock_group": self.common_shock_group,
            "data_quality_status": self.data_quality_status.value,
            "execution_evidence_status": self.execution_evidence_status.value,
            "liquidity_capacity_notional": self.liquidity_capacity_notional,
            "requested_capital_fraction": self.requested_capital_fraction,
            "stop_loss_fraction": self.stop_loss_fraction,
            "legacy_observables": self.legacy_observables.to_dict(),
            "forecast": None if self.forecast is None else self.forecast.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioCandidate":
        body = _require_durable_mapping(
            raw, field_name="candidate", expected_keys=_CANDIDATE_DURABLE_KEYS
        )
        forecast = (
            None if body["forecast"] is None else ForecastDecision.from_dict(body["forecast"])
        )
        declared_forecast_id = body["forecast_decision_id"]
        actual_forecast_id = None if forecast is None else forecast.decision_id
        if declared_forecast_id != actual_forecast_id:
            raise PortfolioContractError(
                "candidate.forecast_decision_id does not match its nested forecast"
            )
        return cls(
            candidate_id=body["candidate_id"],
            episode_id=body["episode_id"],
            feasibility_decision_id=body["feasibility_decision_id"],
            symbol=body["symbol"],
            direction=body["direction"],
            common_shock_group=body["common_shock_group"],
            data_quality_status=body["data_quality_status"],
            execution_evidence_status=body["execution_evidence_status"],
            liquidity_capacity_notional=body["liquidity_capacity_notional"],
            requested_capital_fraction=body["requested_capital_fraction"],
            stop_loss_fraction=body["stop_loss_fraction"],
            legacy_observables=LegacyComparatorObservables.from_dict(
                body["legacy_observables"]
            ),
            forecast=forecast,
        )


def portfolio_panel_fingerprint(
    window: PortfolioEvaluationWindow, candidates: Any
) -> str:
    """The deterministic ``PPANEL:`` fingerprint of the frozen candidate panel.

    It binds the declared window and the canonical, sorted candidate ids, so both
    F7 and the frozen legacy comparator provably evaluate the same population and
    an input reordering cannot change the identity. It also fails closed on a
    duplicate candidate identity or a duplicate symbol/direction pair, so every
    consumer of the panel inherits the same population validation.
    """
    if not isinstance(window, PortfolioEvaluationWindow):
        raise PortfolioContractError("window must be a PortfolioEvaluationWindow")
    if not isinstance(candidates, (list, tuple)):
        raise PortfolioContractError("candidates must be a list or tuple")
    identifiers: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, PortfolioCandidate):
            raise PortfolioContractError("candidates must be PortfolioCandidate values")
        identifiers.append(candidate.candidate_id)
    if len(identifiers) != len(set(identifiers)):
        raise PortfolioContractError(
            "the candidate panel carries a duplicate candidate identity; duplicate "
            "candidates cannot create duplicate capital"
        )
    pairs = [(candidate.symbol, candidate.direction.value) for candidate in candidates]
    if len(pairs) != len(set(pairs)):
        raise PortfolioContractError(
            "the candidate panel carries a duplicate symbol/direction pair"
        )
    return stable_hash(
        PORTFOLIO_PANEL_FINGERPRINT_PREFIX,
        {
            "window": window.to_dict(),
            "candidate_ids": sorted(identifiers),
        },
    )


# ---------------------------------------------------------------------------
# Allocation and reservation plan
# ---------------------------------------------------------------------------


_ALLOCATION_DURABLE_KEYS: tuple[str, ...] = (
    "allocation_id",
    "candidate_id",
    "episode_id",
    "symbol",
    "direction",
    "allocated_capital",
    "expected_net_dollars",
    "expected_net_dollars_lower_bound",
    "expected_net_dollars_upper_bound",
    "expected_fill_probability",
    "expected_no_fill_probability",
    "loss_at_stop",
)


@dataclass(frozen=True)
class PortfolioAllocation:
    """One immutable selection allocation for one candidate.

    ``expected_net_dollars`` is the candidate's F6 unconditional expected net
    return applied to the allocated capital. NO_FILL is *included* in that
    expectation and is never treated as a losing trade: its contribution is zero.
    The entry-execution and conditional post-fill families stay separate and are
    never collapsed into one win probability.
    """

    candidate_id: str
    episode_id: str
    symbol: str
    direction: PortfolioDirection
    allocated_capital: float
    expected_net_dollars: float
    expected_net_dollars_lower_bound: float
    expected_net_dollars_upper_bound: float
    expected_fill_probability: float
    expected_no_fill_probability: float
    loss_at_stop: float
    allocation_id: str = ""

    def __post_init__(self) -> None:
        for name in ("candidate_id", "episode_id", "symbol"):
            object.__setattr__(
                self, name, require_portfolio_text(getattr(self, name), field_name=name)
            )
        object.__setattr__(
            self,
            "direction",
            require_portfolio_enum(PortfolioDirection, self.direction, field_name="direction"),
        )
        object.__setattr__(
            self,
            "allocated_capital",
            require_positive(self.allocated_capital, field_name="allocated_capital"),
        )
        for name in (
            "expected_net_dollars",
            "expected_net_dollars_lower_bound",
            "expected_net_dollars_upper_bound",
            "loss_at_stop",
        ):
            object.__setattr__(
                self, name, require_finite(getattr(self, name), field_name=name)
            )
        if self.expected_net_dollars_lower_bound > self.expected_net_dollars_upper_bound:
            raise PortfolioContractError(
                "expected_net_dollars_lower_bound must not exceed the upper bound"
            )
        for name in ("expected_fill_probability", "expected_no_fill_probability"):
            number = require_finite(getattr(self, name), field_name=name)
            if number < 0.0 or number > 1.0:
                raise PortfolioContractError(f"{name} must be within [0, 1]")
            object.__setattr__(self, name, number)
        expected = self.compute_identity()
        if self.allocation_id == "":
            object.__setattr__(self, "allocation_id", expected)
        elif self.allocation_id != expected:
            raise PortfolioContractError(
                "allocation_id does not match its content; build allocations with the "
                "portfolio selector"
            )

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "episode_id": self.episode_id,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "allocated_capital": self.allocated_capital,
            "expected_net_dollars": self.expected_net_dollars,
            "expected_net_dollars_lower_bound": self.expected_net_dollars_lower_bound,
            "expected_net_dollars_upper_bound": self.expected_net_dollars_upper_bound,
            "expected_fill_probability": self.expected_fill_probability,
            "expected_no_fill_probability": self.expected_no_fill_probability,
            "loss_at_stop": self.loss_at_stop,
        }

    def compute_identity(self) -> str:
        return stable_hash(PORTFOLIO_ALLOCATION_ID_PREFIX, self._identity_payload())

    def with_identity(self) -> "PortfolioAllocation":
        return PortfolioAllocation(**{**self.to_dict(), "allocation_id": self.compute_identity()})

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["allocation_id"] = self.allocation_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioAllocation":
        body = _require_durable_mapping(
            raw, field_name="allocation", expected_keys=_ALLOCATION_DURABLE_KEYS
        )
        allocation = cls(
            candidate_id=body["candidate_id"],
            episode_id=body["episode_id"],
            symbol=body["symbol"],
            direction=body["direction"],
            allocated_capital=body["allocated_capital"],
            expected_net_dollars=body["expected_net_dollars"],
            expected_net_dollars_lower_bound=body["expected_net_dollars_lower_bound"],
            expected_net_dollars_upper_bound=body["expected_net_dollars_upper_bound"],
            expected_fill_probability=body["expected_fill_probability"],
            expected_no_fill_probability=body["expected_no_fill_probability"],
            loss_at_stop=body["loss_at_stop"],
            allocation_id=body["allocation_id"],
        )
        if allocation.allocation_id != allocation.compute_identity():
            raise PortfolioContractError(
                "allocation_id does not match its content; build allocations with the "
                "portfolio selector"
            )
        return allocation


_RESERVATION_DURABLE_KEYS: tuple[str, ...] = (
    "reservation_id",
    "candidate_id",
    "symbol",
    "direction",
    "reserved_capital",
    "status",
    "portfolio_version",
    "created_at",
    "expires_at",
    "release_reason",
)


@dataclass(frozen=True)
class PortfolioReservation:
    """One immutable planned reservation inside a plan.

    Reservations are a deterministic *plan*. No production reservation writer is
    invoked, no Paper-v2 reservation is mutated, and no live capital is touched.
    Release, expiry and fill-adjustment are pure transforms that return a new plan.
    """

    candidate_id: str
    symbol: str
    direction: PortfolioDirection
    reserved_capital: float
    portfolio_version: str
    created_at: datetime
    expires_at: datetime
    status: ReservationStatus = ReservationStatus.PLANNED
    release_reason: ReservationReleaseReason | None = None
    reservation_id: str = ""

    def __post_init__(self) -> None:
        for name in ("candidate_id", "symbol", "portfolio_version"):
            object.__setattr__(
                self, name, require_portfolio_text(getattr(self, name), field_name=name)
            )
        object.__setattr__(
            self,
            "direction",
            require_portfolio_enum(PortfolioDirection, self.direction, field_name="direction"),
        )
        object.__setattr__(
            self,
            "reserved_capital",
            require_non_negative(self.reserved_capital, field_name="reserved_capital"),
        )
        created = require_portfolio_utc(self.created_at, field_name="created_at")
        expires = require_portfolio_utc(self.expires_at, field_name="expires_at")
        if expires <= created:
            raise PortfolioContractError("expires_at must be strictly after created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)
        status = require_portfolio_enum(ReservationStatus, self.status, field_name="status")
        object.__setattr__(self, "status", status)
        reason = self.release_reason
        if reason is not None:
            reason = require_portfolio_enum(
                ReservationReleaseReason, reason, field_name="release_reason"
            )
        object.__setattr__(self, "release_reason", reason)
        if status is ReservationStatus.PLANNED and reason is not None:
            raise PortfolioContractError("a PLANNED reservation carries no release reason")
        if status is not ReservationStatus.PLANNED and reason is None:
            raise PortfolioContractError("a released/expired/adjusted reservation needs a reason")
        expected = stable_hash(PORTFOLIO_RESERVATION_ID_PREFIX, self._identity_payload())
        if self.reservation_id == "":
            object.__setattr__(self, "reservation_id", expected)
        elif self.reservation_id != expected:
            raise PortfolioContractError(
                "reservation_id does not match its content; build reservations with the "
                "portfolio selector"
            )

    @property
    def is_active(self) -> bool:
        """True while the reservation still holds capital.

        ``FILL_ADJUSTED`` is an *active* state: after a partial fill the residual
        capital stays reserved and can still be adjusted, released or expired.
        """
        return self.status in (ReservationStatus.PLANNED, ReservationStatus.FILL_ADJUSTED)

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "reserved_capital": self.reserved_capital,
            "portfolio_version": self.portfolio_version,
            "created_at": iso_z(self.created_at, field_name="created_at"),
            "expires_at": iso_z(self.expires_at, field_name="expires_at"),
            "status": self.status.value,
            "release_reason": None if self.release_reason is None else self.release_reason.value,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["reservation_id"] = self.reservation_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioReservation":
        body = _require_durable_mapping(
            raw, field_name="reservation", expected_keys=_RESERVATION_DURABLE_KEYS
        )
        return cls(
            candidate_id=body["candidate_id"],
            symbol=body["symbol"],
            direction=body["direction"],
            reserved_capital=body["reserved_capital"],
            portfolio_version=body["portfolio_version"],
            created_at=_parse_persisted_utc(body["created_at"], field_name="created_at"),
            expires_at=_parse_persisted_utc(body["expires_at"], field_name="expires_at"),
            status=body["status"],
            release_reason=body["release_reason"],
            reservation_id=body["reservation_id"],
        )


_PLAN_DURABLE_KEYS: tuple[str, ...] = (
    "plan_id",
    "plan_schema_version",
    "portfolio_version",
    "window_identity",
    "created_at",
    "reservations",
)


@dataclass(frozen=True)
class PortfolioReservationPlan:
    """The deterministic reservation *plan* bound to one explicit portfolio version.

    This is a shadow plan only. Applying it against a different (stale) portfolio
    version fails closed. No production reservation writer is ever invoked.
    """

    portfolio_version: str
    window_identity: str
    created_at: datetime
    reservations: tuple[PortfolioReservation, ...]
    plan_schema_version: str = PORTFOLIO_RESERVATION_PLAN_SCHEMA_VERSION
    plan_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "portfolio_version",
            require_portfolio_text(self.portfolio_version, field_name="portfolio_version"),
        )
        object.__setattr__(
            self,
            "window_identity",
            require_portfolio_text(self.window_identity, field_name="window_identity"),
        )
        object.__setattr__(
            self, "created_at", require_portfolio_utc(self.created_at, field_name="created_at")
        )
        schema = require_portfolio_text(
            self.plan_schema_version, field_name="plan_schema_version"
        )
        if schema != PORTFOLIO_RESERVATION_PLAN_SCHEMA_VERSION:
            raise PortfolioContractError(
                "plan_schema_version is not the ratified "
                f"{PORTFOLIO_RESERVATION_PLAN_SCHEMA_VERSION}"
            )
        object.__setattr__(self, "plan_schema_version", schema)
        reservations = tuple(self.reservations)
        for reservation in reservations:
            if not isinstance(reservation, PortfolioReservation):
                raise PortfolioContractError(
                    "reservations must be PortfolioReservation values"
                )
            if reservation.portfolio_version != self.portfolio_version:
                raise PortfolioContractError(
                    "every reservation must carry the plan's portfolio_version"
                )
        object.__setattr__(self, "reservations", reservations)
        expected = stable_hash(PORTFOLIO_RESERVATION_PLAN_ID_PREFIX, self._identity_payload())
        if self.plan_id == "":
            object.__setattr__(self, "plan_id", expected)
        elif self.plan_id != expected:
            raise PortfolioContractError(
                "plan_id does not match its content; build plans with the portfolio selector"
            )

    @property
    def planned_capital(self) -> float:
        return sum(
            reservation.reserved_capital
            for reservation in self.reservations
            if reservation.is_active
        )

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "plan_schema_version": self.plan_schema_version,
            "portfolio_version": self.portfolio_version,
            "window_identity": self.window_identity,
            "created_at": iso_z(self.created_at, field_name="created_at"),
            "reservations": [reservation.to_dict() for reservation in self.reservations],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["plan_id"] = self.plan_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioReservationPlan":
        body = _require_durable_mapping(
            raw, field_name="reservation_plan", expected_keys=_PLAN_DURABLE_KEYS
        )
        raw_reservations = body["reservations"]
        if not isinstance(raw_reservations, (list, tuple)):
            raise PortfolioContractError("reservation_plan.reservations must be a list")
        plan = cls(
            portfolio_version=body["portfolio_version"],
            window_identity=body["window_identity"],
            created_at=_parse_persisted_utc(body["created_at"], field_name="created_at"),
            reservations=tuple(
                PortfolioReservation.from_dict(item) for item in raw_reservations
            ),
            plan_schema_version=body["plan_schema_version"],
            plan_id=body["plan_id"],
        )
        if plan.plan_id != stable_hash(
            PORTFOLIO_RESERVATION_PLAN_ID_PREFIX, plan._identity_payload()
        ):
            raise PortfolioContractError(
                "plan_id does not match its content; a forged plan fails closed"
            )
        return plan


def _rebuilt_plan(
    plan: PortfolioReservationPlan,
    reservations: tuple[PortfolioReservation, ...],
) -> PortfolioReservationPlan:
    return PortfolioReservationPlan(
        portfolio_version=plan.portfolio_version,
        window_identity=plan.window_identity,
        created_at=plan.created_at,
        reservations=reservations,
    )


def assert_reservation_plan_current(
    plan: PortfolioReservationPlan, *, current_portfolio_version: str
) -> None:
    """Fail closed when a plan is applied against a stale portfolio version."""
    if not isinstance(plan, PortfolioReservationPlan):
        raise PortfolioContractError("plan must be a PortfolioReservationPlan")
    version = require_portfolio_text(
        current_portfolio_version, field_name="current_portfolio_version"
    )
    if plan.portfolio_version != version:
        raise PortfolioContractError(
            "reservation plan portfolio_version is stale; refusing to apply it"
        )


def release_reservation(
    plan: PortfolioReservationPlan,
    *,
    candidate_id: str,
    reason: ReservationReleaseReason,
    at_time: datetime,
) -> PortfolioReservationPlan:
    """Release one planned reservation, returning a new deterministic plan."""
    target = require_portfolio_text(candidate_id, field_name="candidate_id")
    resolved_reason = require_portfolio_enum(
        ReservationReleaseReason, reason, field_name="reason"
    )
    instant = require_portfolio_utc(at_time, field_name="at_time")
    updated: list[PortfolioReservation] = []
    found = False
    for reservation in plan.reservations:
        if reservation.candidate_id != target:
            updated.append(reservation)
            continue
        found = True
        if not reservation.is_active:
            raise PortfolioContractError("only an active reservation can be released")
        updated.append(
            PortfolioReservation(
                candidate_id=reservation.candidate_id,
                symbol=reservation.symbol,
                direction=reservation.direction,
                reserved_capital=0.0,
                portfolio_version=reservation.portfolio_version,
                created_at=reservation.created_at,
                expires_at=reservation.expires_at,
                status=(
                    ReservationStatus.EXPIRED
                    if resolved_reason is ReservationReleaseReason.EXPIRED
                    else ReservationStatus.RELEASED
                ),
                release_reason=resolved_reason,
            )
        )
    if not found:
        raise PortfolioContractError("no reservation exists for that candidate id")
    _ = instant
    return _rebuilt_plan(plan, tuple(updated))


def expire_reservations(
    plan: PortfolioReservationPlan, *, at_time: datetime
) -> PortfolioReservationPlan:
    """Expire every active reservation whose declared expiry has passed."""
    instant = require_portfolio_utc(at_time, field_name="at_time")
    updated: list[PortfolioReservation] = []
    for reservation in plan.reservations:
        if reservation.is_active and reservation.expires_at <= instant:
            updated.append(
                PortfolioReservation(
                    candidate_id=reservation.candidate_id,
                    symbol=reservation.symbol,
                    direction=reservation.direction,
                    reserved_capital=0.0,
                    portfolio_version=reservation.portfolio_version,
                    created_at=reservation.created_at,
                    expires_at=reservation.expires_at,
                    status=ReservationStatus.EXPIRED,
                    release_reason=ReservationReleaseReason.EXPIRED,
                )
            )
        else:
            updated.append(reservation)
    return _rebuilt_plan(plan, tuple(updated))


def adjust_reservation_for_fill(
    plan: PortfolioReservationPlan,
    *,
    candidate_id: str,
    filled_capital: float,
    at_time: datetime,
) -> PortfolioReservationPlan:
    """Adjust one planned reservation to its post-fill remaining exposure."""
    target = require_portfolio_text(candidate_id, field_name="candidate_id")
    filled = require_non_negative(filled_capital, field_name="filled_capital")
    _ = require_portfolio_utc(at_time, field_name="at_time")
    updated: list[PortfolioReservation] = []
    found = False
    for reservation in plan.reservations:
        if reservation.candidate_id != target:
            updated.append(reservation)
            continue
        found = True
        if not reservation.is_active:
            raise PortfolioContractError("only an active reservation can be adjusted")
        remaining = reservation.reserved_capital - filled
        if remaining < 0.0:
            raise PortfolioContractError("filled_capital exceeds the reserved capital")
        if remaining == 0.0:
            # Fully filled: no residual capital remains reserved.
            updated.append(
                PortfolioReservation(
                    candidate_id=reservation.candidate_id,
                    symbol=reservation.symbol,
                    direction=reservation.direction,
                    reserved_capital=0.0,
                    portfolio_version=reservation.portfolio_version,
                    created_at=reservation.created_at,
                    expires_at=reservation.expires_at,
                    status=ReservationStatus.RELEASED,
                    release_reason=ReservationReleaseReason.FILL_ADJUSTED,
                )
            )
        else:
            # Partially filled: the residual stays reserved and remains adjustable.
            updated.append(
                PortfolioReservation(
                    candidate_id=reservation.candidate_id,
                    symbol=reservation.symbol,
                    direction=reservation.direction,
                    reserved_capital=remaining,
                    portfolio_version=reservation.portfolio_version,
                    created_at=reservation.created_at,
                    expires_at=reservation.expires_at,
                    status=ReservationStatus.FILL_ADJUSTED,
                    release_reason=ReservationReleaseReason.FILL_ADJUSTED,
                )
            )
    if not found:
        raise PortfolioContractError("no reservation exists for that candidate id")
    return _rebuilt_plan(plan, tuple(updated))


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


_DECISION_DURABLE_KEYS: tuple[str, ...] = (
    "decision_id",
    "decision_schema_version",
    "selector_version",
    "policy_version",
    "policy_identity",
    "status",
    "cash_reason",
    "abstention_reason",
    "panel_fingerprint",
    "capital_state_fingerprint",
    "exposure_fingerprint",
    "window",
    "evaluation_time",
    "allocations",
    "reservation_plan",
    "comparator_result_id",
    "expected_net_dollars",
    "expected_net_dollars_lower_bound",
    "expected_net_dollars_upper_bound",
    "loss_at_stop",
    "unallocated_capital",
    "operating_cost_available",
    "attributable_operating_cost",
    "diagnostics",
    "evidence_references",
)


def portfolio_decision_identity(
    *,
    decision_schema_version: str,
    selector_version: str,
    policy_version: str,
    policy_identity: str,
    status: PortfolioStatus | str,
    cash_reason: PortfolioCashReason | str | None,
    abstention_reason: PortfolioAbstentionReason | str | None,
    panel_fingerprint: str | None,
    capital_state_fingerprint: str | None,
    exposure_fingerprint: str | None,
    window_identity: str | None,
    evaluation_time: datetime,
    allocations: tuple[PortfolioAllocation, ...],
    reservation_plan_id: str | None,
    comparator_result_id: str | None,
    expected_net_dollars: float | None,
    expected_net_dollars_lower_bound: float | None,
    expected_net_dollars_upper_bound: float | None,
    loss_at_stop: float | None,
    unallocated_capital: float | None,
) -> str:
    """The deterministic ``PSEL:<digest>`` identity of one portfolio decision.

    Identity binds the ratified versions, the policy identity, the explicit
    capital/exposure/panel fingerprints, the declared window, the evaluation
    instant, the status and the decision's own semantic payload: the ordered
    allocations, the reservation-plan id, the comparator id, the objective and
    every derived aggregate (the uncertainty bounds, the loss at stop and the
    unallocated capital). No UUID, clock, retry count, process identity or
    database sequence takes part, and a forged decision fails closed.
    """
    status_token = require_portfolio_enum(PortfolioStatus, status, field_name="status")
    cash_token: str | None = None
    if cash_reason is not None:
        cash_token = require_portfolio_enum(
            PortfolioCashReason, cash_reason, field_name="cash_reason"
        ).value
    abstention_token: str | None = None
    if abstention_reason is not None:
        abstention_token = require_portfolio_enum(
            PortfolioAbstentionReason, abstention_reason, field_name="abstention_reason"
        ).value
    instant = require_portfolio_utc(evaluation_time, field_name="evaluation_time")
    ordered = tuple(allocations)
    payload: dict[str, Any] = {
        "decision_schema_version": require_portfolio_text(
            decision_schema_version, field_name="decision_schema_version"
        ),
        "selector_version": require_portfolio_text(
            selector_version, field_name="selector_version"
        ),
        "policy_version": require_portfolio_text(policy_version, field_name="policy_version"),
        "policy_identity": require_portfolio_text(
            policy_identity, field_name="policy_identity"
        ),
        "status": status_token.value,
        "cash_reason": cash_token,
        "abstention_reason": abstention_token,
        "panel_fingerprint": panel_fingerprint,
        "capital_state_fingerprint": capital_state_fingerprint,
        "exposure_fingerprint": exposure_fingerprint,
        "window_identity": window_identity,
        "evaluation_time": iso_z(instant, field_name="evaluation_time"),
        "allocations": [allocation.to_dict() for allocation in ordered],
        "reservation_plan_id": reservation_plan_id,
        "comparator_result_id": comparator_result_id,
        "expected_net_dollars": expected_net_dollars,
        "expected_net_dollars_lower_bound": expected_net_dollars_lower_bound,
        "expected_net_dollars_upper_bound": expected_net_dollars_upper_bound,
        "loss_at_stop": loss_at_stop,
        "unallocated_capital": unallocated_capital,
    }
    return stable_hash(PORTFOLIO_DECISION_ID_PREFIX, payload)


@dataclass(frozen=True)
class PortfolioDecision:
    """One immutable F7 portfolio decision.

    A ``SELECTED`` decision carries the ordered allocations, the deterministic
    reservation plan and the objective. A ``CASH_NO_TRADE`` decision deliberately
    holds cash and carries its reason. An ``INSUFFICIENT_EVIDENCE`` decision is a
    governed abstention and carries no fabricated allocation.
    """

    decision_id: str
    decision_schema_version: str
    selector_version: str
    policy_version: str
    policy_identity: str
    status: PortfolioStatus
    evaluation_time: datetime
    panel_fingerprint: str | None = None
    capital_state_fingerprint: str | None = None
    exposure_fingerprint: str | None = None
    window: PortfolioEvaluationWindow | None = None
    cash_reason: PortfolioCashReason | None = None
    abstention_reason: PortfolioAbstentionReason | None = None
    allocations: tuple[PortfolioAllocation, ...] = ()
    reservation_plan: PortfolioReservationPlan | None = None
    comparator_result_id: str | None = None
    expected_net_dollars: float | None = None
    expected_net_dollars_lower_bound: float | None = None
    expected_net_dollars_upper_bound: float | None = None
    loss_at_stop: float | None = None
    unallocated_capital: float | None = None
    attributable_operating_cost: float | None = None
    diagnostics: Mapping[str, float] | None = None
    evidence_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, expected in (
            ("decision_schema_version", PORTFOLIO_DECISION_SCHEMA_VERSION),
            ("selector_version", PORTFOLIO_SELECTOR_VERSION),
            ("policy_version", PORTFOLIO_POLICY_VERSION),
        ):
            if getattr(self, name) != expected:
                raise PortfolioContractError(f"{name} is not the ratified {expected}")
        object.__setattr__(
            self,
            "decision_id",
            require_portfolio_text(self.decision_id, field_name="decision_id"),
        )
        object.__setattr__(
            self,
            "policy_identity",
            require_portfolio_text(self.policy_identity, field_name="policy_identity"),
        )
        object.__setattr__(
            self,
            "status",
            require_portfolio_enum(PortfolioStatus, self.status, field_name="status"),
        )
        object.__setattr__(
            self,
            "evaluation_time",
            require_portfolio_utc(self.evaluation_time, field_name="evaluation_time"),
        )
        if self.window is not None and not isinstance(
            self.window, PortfolioEvaluationWindow
        ):
            raise PortfolioContractError("window must be a PortfolioEvaluationWindow")
        if self.cash_reason is not None:
            object.__setattr__(
                self,
                "cash_reason",
                require_portfolio_enum(
                    PortfolioCashReason, self.cash_reason, field_name="cash_reason"
                ),
            )
        if self.abstention_reason is not None:
            object.__setattr__(
                self,
                "abstention_reason",
                require_portfolio_enum(
                    PortfolioAbstentionReason,
                    self.abstention_reason,
                    field_name="abstention_reason",
                ),
            )
        allocations = tuple(self.allocations)
        for allocation in allocations:
            if not isinstance(allocation, PortfolioAllocation):
                raise PortfolioContractError(
                    "allocations must be PortfolioAllocation values"
                )
        object.__setattr__(self, "allocations", allocations)
        if self.reservation_plan is not None and not isinstance(
            self.reservation_plan, PortfolioReservationPlan
        ):
            raise PortfolioContractError(
                "reservation_plan must be a PortfolioReservationPlan"
            )
        if self.diagnostics is None:
            object.__setattr__(self, "diagnostics", MappingProxyType({}))
        else:
            object.__setattr__(
                self,
                "diagnostics",
                _freeze_mapping(self.diagnostics, field_name="diagnostics", value_kind="number"),
            )
        for name in (
            "expected_net_dollars",
            "expected_net_dollars_lower_bound",
            "expected_net_dollars_upper_bound",
            "loss_at_stop",
            "unallocated_capital",
            "attributable_operating_cost",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, require_finite(value, field_name=name))
        references = tuple(
            require_portfolio_text(item, field_name="evidence_reference")
            for item in self.evidence_references
        )
        object.__setattr__(self, "evidence_references", references)
        self._validate_status_payload()
        self._validate_identity()

    @property
    def reservation_plan_id(self) -> str | None:
        return None if self.reservation_plan is None else self.reservation_plan.plan_id

    @property
    def selected_candidate_ids(self) -> tuple[str, ...]:
        return tuple(allocation.candidate_id for allocation in self.allocations)

    @property
    def operating_cost_available(self) -> bool:
        return self.attributable_operating_cost is not None

    def _validate_status_payload(self) -> None:
        if self.status is PortfolioStatus.SELECTED:
            if self.cash_reason is not None or self.abstention_reason is not None:
                raise PortfolioContractError(
                    "a SELECTED decision carries neither a cash nor an abstention reason"
                )
            if not self.allocations:
                raise PortfolioContractError("a SELECTED decision requires an allocation")
            if self.reservation_plan is None:
                raise PortfolioContractError(
                    "a SELECTED decision requires a reservation plan"
                )
            if self.window is None or self.expected_net_dollars is None:
                raise PortfolioContractError(
                    "a SELECTED decision requires a window and an objective"
                )
            if self.expected_net_dollars <= 0.0:
                raise PortfolioContractError(
                    "a SELECTED decision must beat cash (a strictly positive objective)"
                )
            return
        if self.status is PortfolioStatus.CASH_NO_TRADE:
            if self.cash_reason is None:
                raise PortfolioContractError("a CASH_NO_TRADE decision requires a reason")
            if self.abstention_reason is not None:
                raise PortfolioContractError(
                    "a CASH_NO_TRADE decision carries no abstention reason"
                )
            if self.allocations or self.reservation_plan is not None:
                raise PortfolioContractError(
                    "a CASH_NO_TRADE decision commits no capital"
                )
            return
        # INSUFFICIENT_EVIDENCE
        if self.abstention_reason is None:
            raise PortfolioContractError(
                "an INSUFFICIENT_EVIDENCE decision requires an abstention reason"
            )
        if self.cash_reason is not None:
            raise PortfolioContractError(
                "an INSUFFICIENT_EVIDENCE decision carries no cash reason"
            )
        if self.allocations or self.reservation_plan is not None:
            raise PortfolioContractError(
                "an INSUFFICIENT_EVIDENCE decision carries no fabricated allocation"
            )
        if self.expected_net_dollars is not None:
            raise PortfolioContractError(
                "an INSUFFICIENT_EVIDENCE decision invents no objective"
            )

    def _validate_identity(self) -> None:
        expected = portfolio_decision_identity(
            decision_schema_version=self.decision_schema_version,
            selector_version=self.selector_version,
            policy_version=self.policy_version,
            policy_identity=self.policy_identity,
            status=self.status,
            cash_reason=self.cash_reason,
            abstention_reason=self.abstention_reason,
            panel_fingerprint=self.panel_fingerprint,
            capital_state_fingerprint=self.capital_state_fingerprint,
            exposure_fingerprint=self.exposure_fingerprint,
            window_identity=(
                None if self.window is None else self.window.window_identity
            ),
            evaluation_time=self.evaluation_time,
            allocations=self.allocations,
            reservation_plan_id=self.reservation_plan_id,
            comparator_result_id=self.comparator_result_id,
            expected_net_dollars=self.expected_net_dollars,
            expected_net_dollars_lower_bound=self.expected_net_dollars_lower_bound,
            expected_net_dollars_upper_bound=self.expected_net_dollars_upper_bound,
            loss_at_stop=self.loss_at_stop,
            unallocated_capital=self.unallocated_capital,
        )
        if self.decision_id != expected:
            raise PortfolioContractError(
                "decision_id does not match its binding; build decisions with the "
                "portfolio selector"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "decision_schema_version": self.decision_schema_version,
            "selector_version": self.selector_version,
            "policy_version": self.policy_version,
            "policy_identity": self.policy_identity,
            "status": self.status.value,
            "cash_reason": None if self.cash_reason is None else self.cash_reason.value,
            "abstention_reason": (
                None if self.abstention_reason is None else self.abstention_reason.value
            ),
            "panel_fingerprint": self.panel_fingerprint,
            "capital_state_fingerprint": self.capital_state_fingerprint,
            "exposure_fingerprint": self.exposure_fingerprint,
            "window": None if self.window is None else self.window.to_dict(),
            "evaluation_time": iso_z(self.evaluation_time, field_name="evaluation_time"),
            "allocations": [allocation.to_dict() for allocation in self.allocations],
            "reservation_plan": (
                None if self.reservation_plan is None else self.reservation_plan.to_dict()
            ),
            "comparator_result_id": self.comparator_result_id,
            "expected_net_dollars": self.expected_net_dollars,
            "expected_net_dollars_lower_bound": self.expected_net_dollars_lower_bound,
            "expected_net_dollars_upper_bound": self.expected_net_dollars_upper_bound,
            "loss_at_stop": self.loss_at_stop,
            "unallocated_capital": self.unallocated_capital,
            "operating_cost_available": self.operating_cost_available,
            "attributable_operating_cost": self.attributable_operating_cost,
            "diagnostics": _mapping_to_dict(self.diagnostics),
            "evidence_references": list(self.evidence_references),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioDecision":
        """Reconstitute one durable decision, re-running every constructor invariant."""
        body = _require_durable_mapping(
            raw, field_name="decision", expected_keys=_DECISION_DURABLE_KEYS
        )
        raw_allocations = body["allocations"]
        if not isinstance(raw_allocations, (list, tuple)):
            raise PortfolioContractError("decision.allocations must be a list")
        references = body["evidence_references"]
        if not isinstance(references, (list, tuple)):
            raise PortfolioContractError("decision.evidence_references must be a list")
        return cls(
            decision_id=body["decision_id"],
            decision_schema_version=body["decision_schema_version"],
            selector_version=body["selector_version"],
            policy_version=body["policy_version"],
            policy_identity=body["policy_identity"],
            status=body["status"],
            cash_reason=body["cash_reason"],
            abstention_reason=body["abstention_reason"],
            panel_fingerprint=body["panel_fingerprint"],
            capital_state_fingerprint=body["capital_state_fingerprint"],
            exposure_fingerprint=body["exposure_fingerprint"],
            window=(
                None
                if body["window"] is None
                else PortfolioEvaluationWindow.from_dict(body["window"])
            ),
            evaluation_time=_parse_persisted_utc(
                body["evaluation_time"], field_name="evaluation_time"
            ),
            allocations=tuple(
                PortfolioAllocation.from_dict(item) for item in raw_allocations
            ),
            reservation_plan=(
                None
                if body["reservation_plan"] is None
                else PortfolioReservationPlan.from_dict(body["reservation_plan"])
            ),
            comparator_result_id=body["comparator_result_id"],
            expected_net_dollars=body["expected_net_dollars"],
            expected_net_dollars_lower_bound=body["expected_net_dollars_lower_bound"],
            expected_net_dollars_upper_bound=body["expected_net_dollars_upper_bound"],
            loss_at_stop=body["loss_at_stop"],
            unallocated_capital=body["unallocated_capital"],
            attributable_operating_cost=body["attributable_operating_cost"],
            diagnostics=body["diagnostics"],
            evidence_references=tuple(references),
        )


def expected_fill_probability(forecast: ForecastDecision) -> tuple[float, float]:
    """Return ``(P(any fill), P(NO_FILL))`` from a FORECAST entry distribution.

    The two entry-execution families stay separate: this only reads the entry
    distribution and never collapses ``P(fill)`` with ``P(target | fill)``.
    """
    if not isinstance(forecast, ForecastDecision):
        raise PortfolioContractError("forecast must be a ForecastDecision")
    if forecast.status is not ForecastStatus.FORECAST or forecast.entry_distribution is None:
        raise PortfolioContractError("expected fill probability requires a FORECAST")
    distribution = forecast.entry_distribution
    if distribution.kind is not DistributionKind.ENTRY_EXECUTION:
        raise PortfolioContractError("a fill probability requires the entry-execution family")
    no_fill = distribution.probability(EntryExecutionOutcome.NO_FILL.value)
    partial = distribution.probability(EntryExecutionOutcome.PARTIAL_FILL.value)
    full = distribution.probability(EntryExecutionOutcome.FULL_FILL.value)
    return (partial + full, no_fill)


# ---------------------------------------------------------------------------
# R5-D portfolio plan and common-window rotation comparison
# (PP-01, PP-02, PP-04, RO-01, RO-02)
# ---------------------------------------------------------------------------


#: Schema version of the durable ``PortfolioPlan`` record (frozen ``SC-08``).
PORTFOLIO_PLAN_SCHEMA_VERSION = "portfolio-plan-v1"

#: Version of the plan-level alternative comparison framework (``PP-04``).
PORTFOLIO_PLAN_COMPARISON_VERSION = "portfolio-plan-comparison-v1"

#: Applied plan policy version. A deterministic, replayable code artifact; it is
#: not a caller-selected input and it ratifies no owner number.
PORTFOLIO_PLAN_ENGINEERING_SCENARIO_POLICY_VERSION = (
    "portfolio-plan-engineering-scenario-policy-v1"
)

#: Semantic prefixes of the deterministic R5-D identities.
PORTFOLIO_PLAN_ID_PREFIX = "PPLAN"
PORTFOLIO_PLAN_POLICY_ID_PREFIX = "PPLP"
PORTFOLIO_ALTERNATIVE_ID_PREFIX = "PALT"
PORTFOLIO_PANEL_MANIFEST_ID_PREFIX = "PMANI"
PORTFOLIO_PLAN_REJECTION_ID_PREFIX = "PREJ"
PORTFOLIO_COMPARISON_ID_PREFIX = "PRCMP"
PORTFOLIO_SWITCH_ASSESSMENT_ID_PREFIX = "PRSW"

#: The frozen ``SC-08`` record keys of ``PortfolioPlan`` in their frozen order.
PORTFOLIO_PLAN_RECORD_KEYS: tuple[str, ...] = (
    "candidate_panel_manifest",
    "portfolio_version",
    "plan_expiry",
    "policy_forecast_ids",
    "alternatives_including_cash",
    "action_sequence",
    "expected_economics",
    "constraints",
)

#: The frozen ``SC-08`` policy/forecast-id key set. Every key is explicit.
PORTFOLIO_PLAN_POLICY_ID_KEYS: tuple[str, ...] = (
    "plan_policy",
    "portfolio_policy",
    "rotation_policy",
    "selector",
)

#: The three common-window alternatives of ``RO-01``. There is no fourth leg.
PORTFOLIO_ALTERNATIVE_KINDS: tuple[str, ...] = ("HOLD", "EXIT_TO_CASH", "EXIT_THEN_ENTER")

#: Deterministic preference used only to break exact ties (``PP-04``): a tie
#: prefers holding, then cash, then switching, so a tie never churns the book.
PORTFOLIO_ALTERNATIVE_PREFERENCE: tuple[str, ...] = (
    "HOLD",
    "EXIT_TO_CASH",
    "EXIT_THEN_ENTER",
)

#: The declared, reviewer-visible tie-breaking rule (``PP-04``).
PORTFOLIO_ALTERNATIVE_TIE_BREAK_RULE = (
    "net-descending-then-declared-leg-preference-then-alternative-id"
)

#: The reason-code vocabulary of one recorded rejection (``PP-04``).
PORTFOLIO_PLAN_REJECTION_REASONS: tuple[str, ...] = (
    "ADVANTAGE_BELOW_MINIMUM",
    "EXCLUDED_BY_CONSTRAINT",
    "EVIDENCE_INCOMPLETE",
    "LOWER_EXPECTED_NET",
    "PREFERENCE_TIE_BREAK",
)

#: ``ADD`` is deliberately absent from the eligible disposition set (``PP-02``).
PORTFOLIO_PLAN_DISABLED_DISPOSITIONS: tuple[str, ...] = ("ADD",)

#: The owner inputs this increment cannot decide (``OQ-05``, ``OQ-06``).
OPEN_OWNER_INPUTS: tuple[str, ...] = ("OQ-05", "OQ-06")
OWNER_INPUT_STATUS = "OWNER_DECISION_REQUIRED"


class PortfolioPlanDisposition(str, Enum):
    """The eligible plan dispositions of ``PP-02``. There is no ninth token.

    ``ADD`` is excluded on purpose: it stays disabled unless an existing
    approved policy supports it, so it is not an eligible disposition here.
    """

    HOLD_CASH = "HOLD_CASH"
    WAIT = "WAIT"
    HOLD_POSITION = "HOLD_POSITION"
    NEW_ENTRY = "NEW_ENTRY"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    ROTATE = "ROTATE"


#: The declared eligible disposition tokens, derived from the single vocabulary.
PORTFOLIO_PLAN_DISPOSITIONS: tuple[str, ...] = tuple(
    disposition.value for disposition in PortfolioPlanDisposition
)


class PortfolioAlternativeKind(str, Enum):
    """The three compared common-window legs of ``RO-01``."""

    HOLD = "HOLD"
    EXIT_TO_CASH = "EXIT_TO_CASH"
    EXIT_THEN_ENTER = "EXIT_THEN_ENTER"


class PortfolioPlanAbstentionReason(str, Enum):
    """The bounded, machine-readable abstention vocabulary of the switch.

    Each member is one specific, auditable gap. ``ADVANTAGE_BELOW_MINIMUM`` is
    reached only when every numeric input *is* declared, so a governed
    abstention is never confused with an unratified policy.
    """

    POLICY_NOT_BOUND = "POLICY_NOT_BOUND"
    PLAN_NOT_CURRENT = "PLAN_NOT_CURRENT"
    PLAN_EXPIRED = "PLAN_EXPIRED"
    CASH_COMPARISON_UNAVAILABLE = "CASH_COMPARISON_UNAVAILABLE"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    TURNOVER_BUDGET_EXHAUSTED = "TURNOVER_BUDGET_EXHAUSTED"
    UNCERTAINTY_BAND_UNCLEAR = "UNCERTAINTY_BAND_UNCLEAR"
    ADVANTAGE_BELOW_MINIMUM = "ADVANTAGE_BELOW_MINIMUM"


#: The closed abstention precedence of the switching decision (``RO-02``). The
#: first satisfied reason is the one reported, so the gap is never ambiguous.
PORTFOLIO_PLAN_ABSTENTION_PRECEDENCE: tuple[str, ...] = tuple(
    reason.value for reason in PortfolioPlanAbstentionReason
)


class RotationSwitchStatus(str, Enum):
    """Whether a discretionary rotation advantage was proven. ``ABSTAINED``
    is a governed disposition, not a failure and not a veto."""

    ROTATION_ADVANTAGE_PROVEN = "ROTATION_ADVANTAGE_PROVEN"
    ABSTAINED = "ABSTAINED"


class UncertaintyTreatment(str, Enum):
    """The declared treatment of the compared uncertainty band (``OQ-05``)."""

    LOWER_BOUND = "LOWER_BOUND"
    EXPECTED = "EXPECTED"


@dataclass(frozen=True)
class PortfolioPanelManifest:
    """The deterministic candidate-panel manifest one plan revision rests on.

    The manifest is the frozen, replayable record of *which* candidates and
    which horizon sleeves a plan revision was built from. Candidate ids are
    stored in the canonical sorted sequence, a duplicate id is refused, and the
    manifest identity is a pure function of the manifest's own contents. It is
    evidence only: it is not an order, a reservation or a position.
    """

    window_identity: str
    panel_fingerprint: str
    candidate_ids: tuple[str, ...]
    horizon_sleeves: Mapping[str, float]
    manifest_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "window_identity",
            require_portfolio_text(self.window_identity, field_name="window_identity"),
        )
        object.__setattr__(
            self,
            "panel_fingerprint",
            require_portfolio_text(self.panel_fingerprint, field_name="panel_fingerprint"),
        )
        raw_ids = self.candidate_ids
        if not isinstance(raw_ids, (list, tuple)):
            raise PortfolioContractError("candidate_ids must be a list of candidate ids")
        candidate_ids = tuple(
            require_portfolio_text(item, field_name="candidate_ids[]") for item in raw_ids
        )
        if len(set(candidate_ids)) != len(candidate_ids):
            raise PortfolioContractError("candidate_ids must not repeat a candidate")
        if candidate_ids != tuple(sorted(candidate_ids)):
            raise PortfolioContractError(
                "candidate_ids must be the canonical sorted candidate set"
            )
        object.__setattr__(self, "candidate_ids", candidate_ids)
        sleeves = _freeze_mapping(
            self.horizon_sleeves, field_name="horizon_sleeves", value_kind="non_negative"
        )
        if not sleeves:
            raise PortfolioContractError(
                "horizon_sleeves must declare at least one horizon sleeve"
            )
        if sum(sleeves.values()) > 1.0:
            raise PortfolioContractError(
                "horizon_sleeves must not commit more than the whole panel"
            )
        object.__setattr__(self, "horizon_sleeves", sleeves)
        expected = stable_hash(
            PORTFOLIO_PANEL_MANIFEST_ID_PREFIX, self._identity_payload()
        )
        if self.manifest_id == "":
            object.__setattr__(self, "manifest_id", expected)
        elif self.manifest_id != expected:
            raise PortfolioContractError(
                "manifest_id does not match its content; a forged manifest fails closed"
            )

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "window_identity": self.window_identity,
            "panel_fingerprint": self.panel_fingerprint,
            "candidate_ids": list(self.candidate_ids),
            "horizon_sleeves": _mapping_to_dict(self.horizon_sleeves),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["manifest_id"] = self.manifest_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioPanelManifest":
        body = _require_durable_mapping(
            raw,
            field_name="candidate_panel_manifest",
            expected_keys=(
                "window_identity",
                "panel_fingerprint",
                "candidate_ids",
                "horizon_sleeves",
                "manifest_id",
            ),
        )
        raw_ids = body["candidate_ids"]
        if not isinstance(raw_ids, (list, tuple)):
            raise PortfolioContractError(
                "candidate_panel_manifest.candidate_ids must be a list"
            )
        raw_sleeves = body["horizon_sleeves"]
        if not isinstance(raw_sleeves, Mapping):
            raise PortfolioContractError(
                "candidate_panel_manifest.horizon_sleeves must be a mapping"
            )
        manifest = cls(
            window_identity=body["window_identity"],
            panel_fingerprint=body["panel_fingerprint"],
            candidate_ids=tuple(raw_ids),
            horizon_sleeves=dict(raw_sleeves),
            manifest_id=body["manifest_id"],
        )
        if manifest.manifest_id != stable_hash(
            PORTFOLIO_PANEL_MANIFEST_ID_PREFIX, manifest._identity_payload()
        ):
            raise PortfolioContractError(
                "manifest_id does not match its content; a forged manifest fails closed"
            )
        return manifest


#: The sign a plan action's capital delta must carry, per disposition (``PP-02``).
#: ``WAIT``/``HOLD_*`` move no capital, an entry commits capital, and a reduction
#: or exit releases it. A rotate action carries either sign but must name the
#: compared ``EXIT_THEN_ENTER`` alternative it came from.
_ACTION_DELTA_SIGNS: dict[str, str] = {
    PortfolioPlanDisposition.HOLD_CASH.value: "zero",
    PortfolioPlanDisposition.WAIT.value: "zero",
    PortfolioPlanDisposition.HOLD_POSITION.value: "zero",
    PortfolioPlanDisposition.NEW_ENTRY.value: "positive",
    PortfolioPlanDisposition.REDUCE.value: "negative",
    PortfolioPlanDisposition.EXIT.value: "negative",
    PortfolioPlanDisposition.ROTATE.value: "either",
}


@dataclass(frozen=True)
class PortfolioAlternative:
    """One compared alternative over the single common evaluation window.

    The record carries the whole cost stack that separates the legs: exit fee,
    entry fee, spread, latency and the attributable operating cost. Holding
    charges no round trip, so a ``HOLD`` leg carrying any round-trip cost fails
    closed instead of quietly becoming a cheaper "hold". A missing operating
    cost or a missing uncertainty band is *missing evidence*: the expected net
    and the conservative advantage stay unavailable rather than assumed
    favourable, and a band that excludes its own expectation is refused.
    """

    kind: PortfolioAlternativeKind
    subject_ref: str
    expected_gross_net: float
    exit_fee: float = 0.0
    entry_fee: float = 0.0
    spread_cost: float = 0.0
    latency_cost: float = 0.0
    attributable_operating_cost: float | None = None
    uncertainty_lower_bound: float | None = None
    uncertainty_upper_bound: float | None = None
    alternative_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            require_portfolio_enum(
                PortfolioAlternativeKind, self.kind, field_name="kind"
            ),
        )
        object.__setattr__(
            self,
            "subject_ref",
            require_portfolio_text(self.subject_ref, field_name="subject_ref"),
        )
        object.__setattr__(
            self,
            "expected_gross_net",
            require_finite(self.expected_gross_net, field_name="expected_gross_net"),
        )
        for name in ("exit_fee", "entry_fee", "spread_cost", "latency_cost"):
            object.__setattr__(
                self, name, require_non_negative(getattr(self, name), field_name=name)
            )
        if self.attributable_operating_cost is not None:
            object.__setattr__(
                self,
                "attributable_operating_cost",
                require_non_negative(
                    self.attributable_operating_cost,
                    field_name="attributable_operating_cost",
                ),
            )
        for name in ("uncertainty_lower_bound", "uncertainty_upper_bound"):
            if getattr(self, name) is not None:
                object.__setattr__(
                    self, name, require_finite(getattr(self, name), field_name=name)
                )
        if (self.uncertainty_lower_bound is None) != (self.uncertainty_upper_bound is None):
            raise PortfolioContractError(
                "the uncertainty band must be declared on both bounds or on neither"
            )
        if self.uncertainty_lower_bound is not None and (
            float(self.uncertainty_lower_bound) > float(self.uncertainty_upper_bound)
        ):
            raise PortfolioContractError(
                "uncertainty_lower_bound must not exceed uncertainty_upper_bound"
            )
        if self.kind is PortfolioAlternativeKind.HOLD and (
            self.exit_fee != 0.0
            or self.entry_fee != 0.0
            or self.spread_cost != 0.0
            or self.latency_cost != 0.0
        ):
            raise PortfolioContractError(
                "a HOLD alternative charges no round-trip cost; refusing a costed hold"
            )
        if self.kind is PortfolioAlternativeKind.EXIT_TO_CASH and self.entry_fee != 0.0:
            raise PortfolioContractError(
                "an EXIT_TO_CASH alternative charges no entry fee"
            )
        if self.has_operating_cost:
            net = self.expected_net_dollars
            if self.uncertainty_lower_bound is not None and not (
                float(self.uncertainty_lower_bound)
                <= net
                <= float(self.uncertainty_upper_bound)
            ):
                raise PortfolioContractError(
                    "the uncertainty band must contain the alternative's own expected net"
                )
        expected = stable_hash(
            PORTFOLIO_ALTERNATIVE_ID_PREFIX, self._identity_payload()
        )
        if self.alternative_id == "":
            object.__setattr__(self, "alternative_id", expected)
        elif self.alternative_id != expected:
            raise PortfolioContractError(
                "alternative_id does not match its content; a forged alternative fails closed"
            )

    @property
    def is_hold(self) -> bool:
        return self.kind is PortfolioAlternativeKind.HOLD

    @property
    def is_cash(self) -> bool:
        return self.kind is PortfolioAlternativeKind.EXIT_TO_CASH

    @property
    def is_replacement(self) -> bool:
        return self.kind is PortfolioAlternativeKind.EXIT_THEN_ENTER

    @property
    def has_operating_cost(self) -> bool:
        return self.attributable_operating_cost is not None

    @property
    def has_uncertainty_band(self) -> bool:
        return self.uncertainty_lower_bound is not None

    @property
    def round_trip_cost(self) -> float:
        """The whole round trip: exit fee, entry fee, spread and latency."""
        return self.exit_fee + self.entry_fee + self.spread_cost + self.latency_cost

    @property
    def expected_net_dollars(self) -> float:
        """Net dollars of this leg, or a governed refusal while evidence is missing."""
        if not self.has_operating_cost:
            raise PortfolioContractError(
                "expected net is unavailable: the attributable operating cost is not declared"
            )
        return (
            self.expected_gross_net
            - self.round_trip_cost
            - float(self.attributable_operating_cost)
        )

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "subject_ref": self.subject_ref,
            "expected_gross_net": self.expected_gross_net,
            "exit_fee": self.exit_fee,
            "entry_fee": self.entry_fee,
            "spread_cost": self.spread_cost,
            "latency_cost": self.latency_cost,
            "attributable_operating_cost": self.attributable_operating_cost,
            "uncertainty_lower_bound": self.uncertainty_lower_bound,
            "uncertainty_upper_bound": self.uncertainty_upper_bound,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["alternative_id"] = self.alternative_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioAlternative":
        body = _require_durable_mapping(
            raw,
            field_name="alternative",
            expected_keys=(
                "kind",
                "subject_ref",
                "expected_gross_net",
                "exit_fee",
                "entry_fee",
                "spread_cost",
                "latency_cost",
                "attributable_operating_cost",
                "uncertainty_lower_bound",
                "uncertainty_upper_bound",
                "alternative_id",
            ),
        )
        alternative = cls(
            kind=body["kind"],
            subject_ref=body["subject_ref"],
            expected_gross_net=body["expected_gross_net"],
            exit_fee=body["exit_fee"],
            entry_fee=body["entry_fee"],
            spread_cost=body["spread_cost"],
            latency_cost=body["latency_cost"],
            attributable_operating_cost=body["attributable_operating_cost"],
            uncertainty_lower_bound=body["uncertainty_lower_bound"],
            uncertainty_upper_bound=body["uncertainty_upper_bound"],
            alternative_id=body["alternative_id"],
        )
        if alternative.alternative_id != stable_hash(
            PORTFOLIO_ALTERNATIVE_ID_PREFIX, alternative._identity_payload()
        ):
            raise PortfolioContractError(
                "alternative_id does not match its content; a forged alternative fails closed"
            )
        return alternative


@dataclass(frozen=True)
class PortfolioPlanRejection:
    """One alternative the plan examined and did not select, with its reason code.

    Eligibility, exclusions and the deterministic tie-break outcome are recorded
    here rather than implied: every non-selected alternative appears exactly once
    with a reason code from the declared vocabulary, so a reviewer can see why
    the plan's action sequence looks the way it does.
    """

    alternative_id: str
    reason_code: str
    rejection_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "alternative_id",
            require_portfolio_text(self.alternative_id, field_name="alternative_id"),
        )
        reason = require_portfolio_text(self.reason_code, field_name="reason_code")
        if reason not in PORTFOLIO_PLAN_REJECTION_REASONS:
            raise PortfolioContractError(f"reason_code has an unnamed token: {reason!r}")
        object.__setattr__(self, "reason_code", reason)
        expected = stable_hash(
            PORTFOLIO_PLAN_REJECTION_ID_PREFIX, self._identity_payload()
        )
        if self.rejection_id == "":
            object.__setattr__(self, "rejection_id", expected)
        elif self.rejection_id != expected:
            raise PortfolioContractError(
                "rejection_id does not match its content; a forged rejection fails closed"
            )

    def _identity_payload(self) -> dict[str, Any]:
        return {"alternative_id": self.alternative_id, "reason_code": self.reason_code}

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["rejection_id"] = self.rejection_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioPlanRejection":
        body = _require_durable_mapping(
            raw,
            field_name="rejection",
            expected_keys=("alternative_id", "reason_code", "rejection_id"),
        )
        return cls(
            alternative_id=body["alternative_id"],
            reason_code=body["reason_code"],
            rejection_id=body["rejection_id"],
        )


@dataclass(frozen=True)
class PortfolioPlanAction:
    """One step of the plan's declared action sequence (``SC-08``).

    The sequence is declared, not executed: this record authorizes nothing. Its
    closed vocabulary has no ``ADD`` token, and the capital sign is checked per
    disposition so a "wait" that quietly moves capital, or an "exit" that is
    really a hold, fails closed.
    """

    sequence: int
    disposition: PortfolioPlanDisposition
    subject_ref: str
    capital_delta: float
    alternative_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "sequence",
            require_positive_int(self.sequence, field_name="sequence"),
        )
        disposition = require_portfolio_enum(
            PortfolioPlanDisposition, self.disposition, field_name="disposition"
        )
        if disposition.value in PORTFOLIO_PLAN_DISABLED_DISPOSITIONS:
            raise PortfolioContractError(
                f"{disposition.value} is a disabled disposition and is not eligible here"
            )
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(
            self,
            "subject_ref",
            require_portfolio_text(self.subject_ref, field_name="subject_ref"),
        )
        delta = require_finite(self.capital_delta, field_name="capital_delta")
        object.__setattr__(self, "capital_delta", delta)
        required = _ACTION_DELTA_SIGNS[disposition.value]
        if required == "zero" and delta != 0.0:
            raise PortfolioContractError(
                f"a {disposition.value} action moves no capital; refusing a non-zero delta"
            )
        if required == "positive" and delta <= 0.0:
            raise PortfolioContractError(
                f"a {disposition.value} action commits capital and needs a positive delta"
            )
        if required == "negative" and delta >= 0.0:
            raise PortfolioContractError(
                f"a {disposition.value} action releases capital and needs a negative delta"
            )
        if disposition is PortfolioPlanDisposition.ROTATE:
            if not isinstance(self.alternative_id, str) or self.alternative_id.strip() == "":
                raise PortfolioContractError(
                    "a ROTATE action must name the compared EXIT_THEN_ENTER alternative"
                )
        elif self.alternative_id is not None:
            object.__setattr__(
                self,
                "alternative_id",
                require_portfolio_text(self.alternative_id, field_name="alternative_id"),
            )

    @property
    def is_proven_rotation(self) -> bool:
        """A rotation action only exists because a switch advantage was proven."""
        return self.disposition is PortfolioPlanDisposition.ROTATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "disposition": self.disposition.value,
            "subject_ref": self.subject_ref,
            "capital_delta": self.capital_delta,
            "alternative_id": self.alternative_id,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioPlanAction":
        body = _require_durable_mapping(
            raw,
            field_name="action",
            expected_keys=(
                "sequence",
                "disposition",
                "subject_ref",
                "capital_delta",
                "alternative_id",
            ),
        )
        return cls(
            sequence=body["sequence"],
            disposition=body["disposition"],
            subject_ref=body["subject_ref"],
            capital_delta=body["capital_delta"],
            alternative_id=body["alternative_id"],
        )


@dataclass(frozen=True)
class PortfolioPlanEconomics:
    """The plan-level expected economics over the common window (``SC-08``).

    Trading net and the attributable operating cost stay separate so an operating
    cost can never be presented as a trading edge, and the declared band must
    contain the plan's own expected net.
    """

    expected_trading_net: float
    expected_operating_cost: float
    uncertainty_lower_bound: float
    uncertainty_upper_bound: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expected_trading_net",
            require_finite(self.expected_trading_net, field_name="expected_trading_net"),
        )
        object.__setattr__(
            self,
            "expected_operating_cost",
            require_non_negative(
                self.expected_operating_cost, field_name="expected_operating_cost"
            ),
        )
        object.__setattr__(
            self,
            "uncertainty_lower_bound",
            require_finite(
                self.uncertainty_lower_bound, field_name="uncertainty_lower_bound"
            ),
        )
        object.__setattr__(
            self,
            "uncertainty_upper_bound",
            require_finite(
                self.uncertainty_upper_bound, field_name="uncertainty_upper_bound"
            ),
        )
        if self.uncertainty_lower_bound > self.uncertainty_upper_bound:
            raise PortfolioContractError(
                "uncertainty_lower_bound must not exceed uncertainty_upper_bound"
            )
        if not (
            self.uncertainty_lower_bound
            <= self.expected_net_dollars
            <= self.uncertainty_upper_bound
        ):
            raise PortfolioContractError(
                "the declared economics band must contain the plan's own expected net"
            )

    @property
    def expected_net_dollars(self) -> float:
        return self.expected_trading_net - self.expected_operating_cost

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_trading_net": self.expected_trading_net,
            "expected_operating_cost": self.expected_operating_cost,
            "uncertainty_lower_bound": self.uncertainty_lower_bound,
            "uncertainty_upper_bound": self.uncertainty_upper_bound,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioPlanEconomics":
        body = _require_durable_mapping(
            raw,
            field_name="expected_economics",
            expected_keys=(
                "expected_trading_net",
                "expected_operating_cost",
                "uncertainty_lower_bound",
                "uncertainty_upper_bound",
            ),
        )
        return cls(
            expected_trading_net=body["expected_trading_net"],
            expected_operating_cost=body["expected_operating_cost"],
            uncertainty_lower_bound=body["uncertainty_lower_bound"],
            uncertainty_upper_bound=body["uncertainty_upper_bound"],
        )


@dataclass(frozen=True)
class RotationComparison:
    """The matched common-window comparison of the three ``RO-01`` legs.

    The three legs share exactly one window identity and one subject, and they
    are compared under the same cost stack. Exit costs are counted once: the
    composed ``EXIT_THEN_ENTER`` leg carries the exit leg's own exit fee exactly
    and never understates the exit leg's spread, latency or attributable
    operating cost, so a switch cannot be shown cheaper than the round trip it
    actually pays and charging the round trip twice fails closed. The old
    position's acquisition cost is retained for accounting only; a past
    acquisition cost is sunk and is never a benefit or a cost of either leg.
    """

    window_identity: str
    subject_ref: str
    hold: PortfolioAlternative
    cash: PortfolioAlternative
    replacement: PortfolioAlternative
    acquisition_cost_for_accounting: float | None = None
    comparison_version: str = PORTFOLIO_PLAN_COMPARISON_VERSION
    comparison_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "window_identity",
            require_portfolio_text(self.window_identity, field_name="window_identity"),
        )
        object.__setattr__(
            self,
            "subject_ref",
            require_portfolio_text(self.subject_ref, field_name="subject_ref"),
        )
        for name in ("hold", "cash", "replacement"):
            leg = getattr(self, name)
            if not isinstance(leg, PortfolioAlternative):
                raise PortfolioContractError(
                    f"{name} must be a PortfolioAlternative of the common window"
                )
            if leg.subject_ref != self.subject_ref:
                raise PortfolioContractError(
                    "every compared alternative must carry the comparison's subject"
                )
            if not leg.has_operating_cost:
                raise PortfolioContractError(
                    "the comparison is unavailable: the attributable operating cost of "
                    f"the {name} leg is not declared"
                )
        if not self.hold.is_hold:
            raise PortfolioContractError("the hold leg must be the HOLD alternative")
        if not self.cash.is_cash:
            raise PortfolioContractError("the cash leg must be the EXIT_TO_CASH alternative")
        if not self.replacement.is_replacement:
            raise PortfolioContractError(
                "the replacement leg must be the EXIT_THEN_ENTER alternative"
            )
        if not self.exit_costs_are_counted_once:
            raise PortfolioContractError(
                "exit costs are counted once: the composed leg must carry the exit "
                "leg's fee exactly and must never understate the exit leg's spread, "
                "latency or attributable operating cost"
            )
        if self.acquisition_cost_for_accounting is not None:
            object.__setattr__(
                self,
                "acquisition_cost_for_accounting",
                require_non_negative(
                    self.acquisition_cost_for_accounting,
                    field_name="acquisition_cost_for_accounting",
                ),
            )
        version = require_portfolio_text(
            self.comparison_version, field_name="comparison_version"
        )
        if version != PORTFOLIO_PLAN_COMPARISON_VERSION:
            raise PortfolioContractError(
                f"comparison_version is not the ratified {PORTFOLIO_PLAN_COMPARISON_VERSION}"
            )
        object.__setattr__(self, "comparison_version", version)
        expected = stable_hash(PORTFOLIO_COMPARISON_ID_PREFIX, self._identity_payload())
        if self.comparison_id == "":
            object.__setattr__(self, "comparison_id", expected)
        elif self.comparison_id != expected:
            raise PortfolioContractError(
                "comparison_id does not match its content; a forged comparison fails closed"
            )

    @property
    def window_count(self) -> int:
        """Always one: a per-leg window would let a favourable window be picked."""
        return 1

    @property
    def expected_advantage_over_hold(self) -> float:
        return self.replacement.expected_net_dollars - self.hold.expected_net_dollars

    @property
    def expected_advantage_over_cash(self) -> float:
        return self.replacement.expected_net_dollars - self.cash.expected_net_dollars

    @property
    def expected_advantage_over_best_alternative(self) -> float:
        """The switching advantage must beat *both* holding and cash."""
        return self.replacement.expected_net_dollars - max(
            self.hold.expected_net_dollars, self.cash.expected_net_dollars
        )

    @property
    def conservative_advantage_over_best_alternative(self) -> float:
        """The advantage read conservatively: the replacement's lower bound against
        the best competing upper bound. Missing evidence refuses instead of
        assuming the favourable side of the band."""
        if not (
            self.hold.has_uncertainty_band
            and self.cash.has_uncertainty_band
            and self.replacement.has_uncertainty_band
        ):
            raise PortfolioContractError(
                "the conservative advantage requires the uncertainty band of every leg"
            )
        competing = max(
            float(self.hold.uncertainty_upper_bound),
            float(self.cash.uncertainty_upper_bound),
        )
        return float(self.replacement.uncertainty_lower_bound) - competing

    @property
    def exit_costs_are_counted_once(self) -> bool:
        """Whether the composed leg really carries the exit leg's exit costs once.

        The exit fee is an exact carry: the composed leg charges the exit leg's
        fee once and only once. The exit leg's spread, latency and attributable
        operating cost are the *floor* of the composed leg's own, because a
        replacement pays the exit it actually made and then pays its entry on top
        of it. Understating any of them would make the switch look cheaper than
        the round trip it pays, which is the falsification direction; a
        deliberately higher figure only makes the switch less attractive and can
        never license a switch the after-cost comparison does not support. A leg
        without a declared operating cost is not a usable comparison at all.
        """

        cash = self.cash
        replacement = self.replacement
        if not (cash.has_operating_cost and replacement.has_operating_cost):
            return False
        return (
            replacement.exit_fee == cash.exit_fee
            and replacement.spread_cost >= cash.spread_cost
            and replacement.latency_cost >= cash.latency_cost
            and float(replacement.attributable_operating_cost)
            >= float(cash.attributable_operating_cost)
        )

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "window_identity": self.window_identity,
            "subject_ref": self.subject_ref,
            "comparison_version": self.comparison_version,
            "hold": self.hold.to_dict(),
            "cash": self.cash.to_dict(),
            "replacement": self.replacement.to_dict(),
            "acquisition_cost_for_accounting": self.acquisition_cost_for_accounting,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["comparison_id"] = self.comparison_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "RotationComparison":
        body = _require_durable_mapping(
            raw,
            field_name="comparison",
            expected_keys=(
                "window_identity",
                "subject_ref",
                "comparison_version",
                "hold",
                "cash",
                "replacement",
                "acquisition_cost_for_accounting",
                "comparison_id",
            ),
        )
        return cls(
            window_identity=body["window_identity"],
            subject_ref=body["subject_ref"],
            hold=PortfolioAlternative.from_dict(body["hold"]),
            cash=PortfolioAlternative.from_dict(body["cash"]),
            replacement=PortfolioAlternative.from_dict(body["replacement"]),
            acquisition_cost_for_accounting=body["acquisition_cost_for_accounting"],
            comparison_version=body["comparison_version"],
            comparison_id=body["comparison_id"],
        )


def compose_rotation_replacement(
    *,
    subject_ref: str,
    cash: PortfolioAlternative,
    entry_gross_net: float,
    entry_fee: float,
    entry_latency_cost: float = 0.0,
    entry_operating_cost: float | None = None,
    entry_spread_cost: float = 0.0,
    entry_uncertainty_lower_bound: float | None = None,
    entry_uncertainty_upper_bound: float | None = None,
) -> PortfolioAlternative:
    """Compose the two-leg ``EXIT_THEN_ENTER`` alternative from both legs.

    The exit leg's exit fee, spread and latency are carried into the composed leg
    exactly once and are never re-charged, so a rotation is not shown cheaper (or
    dearer) than the round trip it actually pays. The old position's past
    acquisition cost has no parameter here at all: it is sunk and cannot enter
    the comparison. The composed band exists only when both legs declare a band;
    a missing band stays missing rather than being narrowed, and a missing
    attributable operating cost on either leg refuses the composition instead of
    looking favourable.
    """

    if not isinstance(cash, PortfolioAlternative) or not cash.is_cash:
        raise PortfolioContractError(
            "cash must be the EXIT_TO_CASH alternative of the exit leg"
        )
    subject = require_portfolio_text(subject_ref, field_name="subject_ref")
    if cash.subject_ref != subject:
        raise PortfolioContractError("the exit leg must carry the composed leg's subject")
    if not cash.has_operating_cost:
        raise PortfolioContractError(
            "the exit leg's attributable operating cost is not declared"
        )
    if entry_operating_cost is None:
        raise PortfolioContractError(
            "the entry leg's attributable operating cost is not declared"
        )
    gross = require_finite(entry_gross_net, field_name="entry_gross_net")
    fee = require_non_negative(entry_fee, field_name="entry_fee")
    latency = require_non_negative(entry_latency_cost, field_name="entry_latency_cost")
    spread = require_non_negative(entry_spread_cost, field_name="entry_spread_cost")
    operating = require_non_negative(
        entry_operating_cost, field_name="entry_operating_cost"
    )
    if (entry_uncertainty_lower_bound is None) != (entry_uncertainty_upper_bound is None):
        raise PortfolioContractError(
            "the entry uncertainty band must be declared on both bounds or on neither"
        )
    band: tuple[float, float] | None = None
    if entry_uncertainty_lower_bound is not None:
        entry_lower = require_finite(
            entry_uncertainty_lower_bound, field_name="entry_uncertainty_lower_bound"
        )
        entry_upper = require_finite(
            entry_uncertainty_upper_bound, field_name="entry_uncertainty_upper_bound"
        )
        if entry_lower > entry_upper:
            raise PortfolioContractError(
                "entry_uncertainty_lower_bound must not exceed entry_uncertainty_upper_bound"
            )
        if cash.has_uncertainty_band:
            band = (
                float(cash.uncertainty_lower_bound) + entry_lower,
                float(cash.uncertainty_upper_bound) + entry_upper,
            )
    return PortfolioAlternative(
        kind=PortfolioAlternativeKind.EXIT_THEN_ENTER,
        subject_ref=subject,
        expected_gross_net=cash.expected_gross_net + gross,
        exit_fee=cash.exit_fee,
        entry_fee=fee,
        spread_cost=cash.spread_cost + spread,
        latency_cost=cash.latency_cost + latency,
        attributable_operating_cost=float(cash.attributable_operating_cost) + operating,
        uncertainty_lower_bound=None if band is None else band[0],
        uncertainty_upper_bound=None if band is None else band[1],
    )


def rotation_comparison(
    *,
    window: PortfolioEvaluationWindow,
    subject_ref: str,
    hold: PortfolioAlternative,
    cash: PortfolioAlternative,
    replacement: PortfolioAlternative,
    acquisition_cost_for_accounting: float | None = None,
) -> RotationComparison:
    """Build the matched common-window comparison of the three ``RO-01`` legs.

    There is deliberately no per-leg window parameter: all three legs are
    compared over the single declared window, so a favourable window cannot be
    attached to one leg and a favourable window cannot be cherry-picked.
    """

    if not isinstance(window, PortfolioEvaluationWindow):
        raise PortfolioContractError(
            "window must be the declared PortfolioEvaluationWindow shared by the legs"
        )
    return RotationComparison(
        window_identity=window.window_identity,
        subject_ref=subject_ref,
        hold=hold,
        cash=cash,
        replacement=replacement,
        acquisition_cost_for_accounting=acquisition_cost_for_accounting,
    )


def rank_plan_alternatives(alternatives: Any) -> tuple[PortfolioAlternative, ...]:
    """Rank the observed eligible alternatives deterministically (``PP-04``).

    The ranking is a pure function of the recorded alternatives: best expected
    net first, then the declared preference (hold, then cash, then switch), then
    the alternative id. No hidden score and no market-wide optimum takes part, so
    "best" can only ever mean best among the observed eligible alternatives. An
    alternative whose cost stack or expected net is incomplete cannot be ranked:
    missing evidence refuses instead of being treated as favourable, and an exact
    tie prefers holding over churning.
    """

    if not isinstance(alternatives, (list, tuple)) or not alternatives:
        raise PortfolioContractError(
            "alternatives must be a non-empty list of alternatives"
        )
    seen_kinds: set[str] = set()
    seen_ids: set[str] = set()
    for alternative in alternatives:
        if not isinstance(alternative, PortfolioAlternative):
            raise PortfolioContractError(
                "alternatives must be PortfolioAlternative values"
            )
        if not alternative.has_operating_cost:
            raise PortfolioContractError(
                "an alternative without an attributable operating cost cannot be ranked"
            )
        if alternative.kind.value in seen_kinds:
            raise PortfolioContractError(
                "a plan compares each alternative kind at most once"
            )
        seen_kinds.add(alternative.kind.value)
        if alternative.alternative_id in seen_ids:
            raise PortfolioContractError(
                "a plan compares each alternative id at most once"
            )
        seen_ids.add(alternative.alternative_id)
    preference = {
        kind: index for index, kind in enumerate(PORTFOLIO_ALTERNATIVE_PREFERENCE)
    }
    return tuple(
        sorted(
            alternatives,
            key=lambda item: (
                -item.expected_net_dollars,
                preference[item.kind.value],
                item.alternative_id,
            ),
        )
    )


def select_plan_alternative(alternatives: Any) -> PortfolioAlternative:
    """Best among the observed eligible alternatives: the head of the ranking."""
    return rank_plan_alternatives(alternatives)[0]


def assert_plan_selects_best_observed(selected: Any, alternatives: Any) -> None:
    """Fail closed unless the selected alternative is the ranking's head.

    A plan cannot claim a better alternative than the best one it observed and
    recorded, and it cannot select an alternative it never compared.
    """

    ranked = rank_plan_alternatives(alternatives)
    if not isinstance(selected, PortfolioAlternative):
        raise PortfolioContractError("selected must be a PortfolioAlternative")
    if selected.alternative_id != ranked[0].alternative_id:
        raise PortfolioContractError(
            "the plan must select the best observed eligible alternative"
        )


@dataclass(frozen=True)
class PortfolioPlanPolicyBinding:
    """The frozen ``SC-08`` ``policy_forecast_ids`` binding of one plan revision.

    All four ids are explicit and frozen to a ratified, replayable code artifact.
    There is no implicit "current policy" lookup and no caller-supplied mode
    string, so a recorded plan can only ever be replayed against the policies it
    actually recorded, and it ratifies no owner number.
    """

    plan_policy: str = PORTFOLIO_PLAN_ENGINEERING_SCENARIO_POLICY_VERSION
    portfolio_policy: str = PORTFOLIO_POLICY_VERSION
    rotation_policy: str = PORTFOLIO_PLAN_COMPARISON_VERSION
    selector: str = PORTFOLIO_SELECTOR_VERSION
    policy_id: str = ""

    def __post_init__(self) -> None:
        applied = {
            "plan_policy": PORTFOLIO_PLAN_ENGINEERING_SCENARIO_POLICY_VERSION,
            "portfolio_policy": PORTFOLIO_POLICY_VERSION,
            "rotation_policy": PORTFOLIO_PLAN_COMPARISON_VERSION,
            "selector": PORTFOLIO_SELECTOR_VERSION,
        }
        for name, version in applied.items():
            value = require_portfolio_text(getattr(self, name), field_name=name)
            if value != version:
                raise PortfolioContractError(
                    f"{name} is not the applied {version}; an unpinned policy fails closed"
                )
            object.__setattr__(self, name, value)
        expected = stable_hash(PORTFOLIO_PLAN_POLICY_ID_PREFIX, self._identity_payload())
        if self.policy_id == "":
            object.__setattr__(self, "policy_id", expected)
        elif self.policy_id != expected:
            raise PortfolioContractError(
                "policy_id does not match its content; a forged binding fails closed"
            )

    def _identity_payload(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in PORTFOLIO_PLAN_POLICY_ID_KEYS}

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["policy_id"] = self.policy_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioPlanPolicyBinding":
        body = _require_durable_mapping(
            raw,
            field_name="policy_forecast_ids",
            expected_keys=tuple(PORTFOLIO_PLAN_POLICY_ID_KEYS) + ("policy_id",),
        )
        binding = cls(
            plan_policy=body["plan_policy"],
            portfolio_policy=body["portfolio_policy"],
            rotation_policy=body["rotation_policy"],
            selector=body["selector"],
            policy_id=body["policy_id"],
        )
        if binding.policy_id != stable_hash(
            PORTFOLIO_PLAN_POLICY_ID_PREFIX, binding._identity_payload()
        ):
            raise PortfolioContractError(
                "policy_id does not match its content; a forged binding fails closed"
            )
        return binding


@dataclass(frozen=True)
class PortfolioPlanConstraints:
    """The declared numeric constraints a plan was built under (``SC-08``).

    Every value is an owner-ratified input. An unratified input is *absent*
    (``None``) rather than defaulted to a convenient number, so a missing
    constraint can never be silently satisfied by the plan itself: the switch
    abstains with ``POLICY_NOT_BOUND`` instead. ``OQ-05`` (uncertainty treatment)
    and ``OQ-06`` (cooldown and turnover budget) are still open owner inputs, and
    this record declares that rather than ratifying anything.
    """

    min_switch_advantage_dollars: float | None = None
    uncertainty_treatment: UncertaintyTreatment | None = None
    turnover_budget_dollars: float | None = None
    cooldown_hours: float | None = None
    open_owner_inputs: tuple[str, ...] = OPEN_OWNER_INPUTS
    owner_input_status: str = OWNER_INPUT_STATUS

    def __post_init__(self) -> None:
        if self.min_switch_advantage_dollars is not None:
            object.__setattr__(
                self,
                "min_switch_advantage_dollars",
                require_positive(
                    self.min_switch_advantage_dollars,
                    field_name="min_switch_advantage_dollars",
                ),
            )
        if self.uncertainty_treatment is not None:
            object.__setattr__(
                self,
                "uncertainty_treatment",
                require_portfolio_enum(
                    UncertaintyTreatment,
                    self.uncertainty_treatment,
                    field_name="uncertainty_treatment",
                ),
            )
        if self.turnover_budget_dollars is not None:
            object.__setattr__(
                self,
                "turnover_budget_dollars",
                require_non_negative(
                    self.turnover_budget_dollars, field_name="turnover_budget_dollars"
                ),
            )
        if self.cooldown_hours is not None:
            object.__setattr__(
                self,
                "cooldown_hours",
                require_non_negative(self.cooldown_hours, field_name="cooldown_hours"),
            )
        raw_inputs = self.open_owner_inputs
        if not isinstance(raw_inputs, (list, tuple)):
            raise PortfolioContractError("open_owner_inputs must be a list of input ids")
        declared = tuple(
            require_portfolio_text(item, field_name="open_owner_inputs[]")
            for item in raw_inputs
        )
        if declared != OPEN_OWNER_INPUTS:
            raise PortfolioContractError(
                "open_owner_inputs must be the frozen open owner input set"
            )
        object.__setattr__(self, "open_owner_inputs", declared)
        status = require_portfolio_text(
            self.owner_input_status, field_name="owner_input_status"
        )
        if status != OWNER_INPUT_STATUS:
            raise PortfolioContractError(
                f"owner_input_status must be {OWNER_INPUT_STATUS} while OQ-05/OQ-06 are open"
            )
        object.__setattr__(self, "owner_input_status", status)

    @property
    def is_ratified(self) -> bool:
        """True only when every constraint the switch needs is declared.

        Missing evidence is never a favourable default: an unratified constraint
        is a ``POLICY_NOT_BOUND`` abstention, not a licensed switch.
        """
        return (
            self.min_switch_advantage_dollars is not None
            and self.uncertainty_treatment is not None
            and self.turnover_budget_dollars is not None
            and self.cooldown_hours is not None
        )

    @property
    def unratified_fields(self) -> tuple[str, ...]:
        """The constraint fields that are still absent, in declared order."""
        return tuple(
            name
            for name in (
                "min_switch_advantage_dollars",
                "uncertainty_treatment",
                "turnover_budget_dollars",
                "cooldown_hours",
            )
            if getattr(self, name) is None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_switch_advantage_dollars": self.min_switch_advantage_dollars,
            "uncertainty_treatment": None
            if self.uncertainty_treatment is None
            else self.uncertainty_treatment.value,
            "turnover_budget_dollars": self.turnover_budget_dollars,
            "cooldown_hours": self.cooldown_hours,
            "open_owner_inputs": list(self.open_owner_inputs),
            "owner_input_status": self.owner_input_status,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioPlanConstraints":
        body = _require_durable_mapping(
            raw,
            field_name="constraints",
            expected_keys=(
                "min_switch_advantage_dollars",
                "uncertainty_treatment",
                "turnover_budget_dollars",
                "cooldown_hours",
                "open_owner_inputs",
                "owner_input_status",
            ),
        )
        raw_inputs = body["open_owner_inputs"]
        if not isinstance(raw_inputs, (list, tuple)):
            raise PortfolioContractError("constraints.open_owner_inputs must be a list")
        return cls(
            min_switch_advantage_dollars=body["min_switch_advantage_dollars"],
            uncertainty_treatment=body["uncertainty_treatment"],
            turnover_budget_dollars=body["turnover_budget_dollars"],
            cooldown_hours=body["cooldown_hours"],
            open_owner_inputs=tuple(raw_inputs),
            owner_input_status=body["owner_input_status"],
        )


def unratified_plan_constraints() -> PortfolioPlanConstraints:
    """The constraints of a plan built while ``OQ-05``/``OQ-06`` are still open.

    It declares no number: the switch therefore abstains with
    ``POLICY_NOT_BOUND`` rather than inventing a threshold.
    """
    return PortfolioPlanConstraints()


def _switch_turnover_charge(
    *,
    comparison: RotationComparison,
    switched_capital_dollars: float | None,
) -> float | None:
    """The dollars one switch charges against the turnover budget, or ``None``.

    ``RO-02``'s safeguard is a *turnover* budget: what it bounds is the capital a
    switch moves, not merely the fee, spread and latency stack that switch pays.
    The charge is therefore the capital the switch turns over, floored by that
    switch's own round-trip cost so a declared capital can never record a switch
    as though it were cheaper than the costs it already commits.

    ``None`` means the capital is not established. An unestablished charge cannot
    be bounded, so the branch that reads this fails closed instead of treating an
    unknown capital as no turnover at all.
    """

    if switched_capital_dollars is None:
        return None
    capital = require_non_negative(
        switched_capital_dollars, field_name="switched_capital_dollars"
    )
    return max(capital, comparison.replacement.round_trip_cost)


def _switch_gate_evidence_reasons(
    *,
    window_identity: str,
    constraints: PortfolioPlanConstraints,
    comparison: Any = None,
    cooldown_reason: PortfolioPlanAbstentionReason | None = None,
    turn_over_committed_dollars: float = 0.0,
    switched_capital_dollars: float | None = None,
) -> tuple[tuple[PortfolioPlanAbstentionReason, ...], float | None]:
    """The clock-free, history-free evidence branches of the switch gate (``RO-02``).

    Only the branches the *record itself* can settle are evaluated here: the
    policy binding, the availability of the common-window comparison, the cooldown
    verdict the caller established from its own clock and switch history, the
    turnover budget and the treatment-adjusted advantage. ``PLAN_NOT_CURRENT`` and
    ``PLAN_EXPIRED`` are not reachable without a decision clock and stay with the
    decision-time gate, which remains the authority over the switch.

    The turnover branch is charged in the capital the switch *turns over* rather
    than in the round-trip cost alone: a switch that moves a large position for a
    small fee is exactly the churn a turnover budget exists to bound. The charge is
    the capital the switch moves - recorded by the plan's own declared ``ROTATE``
    step, or declared by the caller for a switch the plan does not declare - and is
    never below that switch's own round-trip cost. An unestablished capital cannot
    be bounded and abstains with ``TURNOVER_BUDGET_EXHAUSTED``.

    *Every* satisfied branch is returned in the frozen
    ``PORTFOLIO_PLAN_ABSTENTION_PRECEDENCE`` order, so the caller reports the same
    single reason the ratified precedence names and no caller can read a favourable
    branch out of a losing one. The advantage is reported only when it is well
    defined and no branch abstains: an advantage that cannot be computed is never
    assumed, and an abstention never carries one.
    """

    if not isinstance(constraints, PortfolioPlanConstraints):
        raise PortfolioContractError("constraints must be a PortfolioPlanConstraints")
    if not constraints.is_ratified:
        return (PortfolioPlanAbstentionReason.POLICY_NOT_BOUND,), None
    window = require_portfolio_text(window_identity, field_name="window_identity")
    comparison_available = (
        isinstance(comparison, RotationComparison)
        and comparison.window_identity == window
        and comparison.exit_costs_are_counted_once
        and comparison.hold.has_operating_cost
        and comparison.cash.has_operating_cost
        and comparison.replacement.has_operating_cost
    )
    if not comparison_available:
        return (PortfolioPlanAbstentionReason.CASH_COMPARISON_UNAVAILABLE,), None
    reasons: list[PortfolioPlanAbstentionReason] = []
    if cooldown_reason is not None:
        reasons.append(cooldown_reason)
    committed = require_non_negative(
        turn_over_committed_dollars, field_name="turn_over_committed_dollars"
    )
    charge = _switch_turnover_charge(
        comparison=comparison, switched_capital_dollars=switched_capital_dollars
    )
    if charge is None or committed + charge > float(
        constraints.turnover_budget_dollars
    ):
        reasons.append(PortfolioPlanAbstentionReason.TURNOVER_BUDGET_EXHAUSTED)
    treatment = constraints.uncertainty_treatment
    has_bands = (
        comparison.hold.has_uncertainty_band
        and comparison.cash.has_uncertainty_band
        and comparison.replacement.has_uncertainty_band
    )
    if treatment is UncertaintyTreatment.LOWER_BOUND and not has_bands:
        reasons.append(PortfolioPlanAbstentionReason.UNCERTAINTY_BAND_UNCLEAR)
    if treatment is UncertaintyTreatment.LOWER_BOUND and has_bands:
        advantage = comparison.conservative_advantage_over_best_alternative
    else:
        advantage = comparison.expected_advantage_over_best_alternative
    if advantage < float(constraints.min_switch_advantage_dollars):
        reasons.append(PortfolioPlanAbstentionReason.ADVANTAGE_BELOW_MINIMUM)
    ordered = tuple(
        sorted(
            reasons,
            key=lambda reason: PORTFOLIO_PLAN_ABSTENTION_PRECEDENCE.index(reason.value),
        )
    )
    if ordered:
        return ordered, None
    return (), advantage


def _evaluate_switch_gate(
    *,
    window_identity: str,
    constraints: PortfolioPlanConstraints,
    plan_id: str,
    plan_expiry: datetime,
    now: datetime,
    comparison: Any = None,
    current_plan_id: str | None = None,
    seconds_since_last_switch: float | None = None,
    turn_over_committed_dollars: float = 0.0,
    switched_capital_dollars: float | None = None,
) -> tuple[PortfolioPlanAbstentionReason | None, float | None]:
    """Read the closed abstention precedence of the switching decision (``RO-02``).

    The first satisfied reason is the one reported, so the gap is never
    ambiguous: an unratified policy is reported before anything numeric, a stale
    plan before a comparison gap, and a governed ``ADVANTAGE_BELOW_MINIMUM``
    abstention is reached only once every numeric input *is* declared. A
    ``None`` reason means a rotation advantage was proven, and that is the only
    outcome that may carry a ``ROTATE`` action. The returned advantage is the
    treatment-adjusted one, read conservatively (the replacement's lower bound
    against the best competing upper bound) when ``OQ-05`` selects
    ``LOWER_BOUND``.

    Freshness is required evidence rather than an optional refinement: an
    evaluation that does not establish which revision is current cannot show that
    the record it read is the current one, so an unestablished current revision
    abstains with ``PLAN_NOT_CURRENT`` instead of being assumed to be live. The
    turnover branch is charged in the capital the switch turns over, which is the
    plan's own declared ``ROTATE`` step or the caller's declaration for a switch
    the plan does not declare.
    """

    if not isinstance(constraints, PortfolioPlanConstraints):
        raise PortfolioContractError("constraints must be a PortfolioPlanConstraints")
    if not constraints.is_ratified:
        return PortfolioPlanAbstentionReason.POLICY_NOT_BOUND, None
    window = require_portfolio_text(window_identity, field_name="window_identity")
    identity = require_portfolio_text(plan_id, field_name="plan_id")
    if current_plan_id is None:
        return PortfolioPlanAbstentionReason.PLAN_NOT_CURRENT, None
    current = require_portfolio_text(current_plan_id, field_name="current_plan_id")
    if current != identity:
        return PortfolioPlanAbstentionReason.PLAN_NOT_CURRENT, None
    evaluated_at = require_portfolio_utc(now, field_name="now")
    expiry = require_portfolio_utc(plan_expiry, field_name="plan_expiry")
    if evaluated_at >= expiry:
        return PortfolioPlanAbstentionReason.PLAN_EXPIRED, None
    cooldown_reason: PortfolioPlanAbstentionReason | None = None
    if constraints.cooldown_hours:
        if seconds_since_last_switch is None:
            cooldown_reason = PortfolioPlanAbstentionReason.COOLDOWN_ACTIVE
        else:
            elapsed = require_non_negative(
                seconds_since_last_switch, field_name="seconds_since_last_switch"
            )
            if elapsed < float(constraints.cooldown_hours) * 3600.0:
                cooldown_reason = PortfolioPlanAbstentionReason.COOLDOWN_ACTIVE
    reasons, advantage = _switch_gate_evidence_reasons(
        window_identity=window,
        constraints=constraints,
        comparison=comparison,
        cooldown_reason=cooldown_reason,
        turn_over_committed_dollars=turn_over_committed_dollars,
        switched_capital_dollars=switched_capital_dollars,
    )
    if reasons:
        return reasons[0], None
    return None, advantage


def _plan_record_key_order() -> tuple[str, ...]:
    """The frozen ``SC-08`` key order of ``PortfolioPlan.to_dict``."""
    return tuple(PORTFOLIO_PLAN_RECORD_KEYS) + (
        "rejected_alternatives",
        "comparisons",
        "plan_id",
    )


@dataclass(frozen=True)
class PortfolioPlan:
    """One durable, non-authoritative portfolio plan revision (frozen ``SC-08``).

    The record carries exactly the frozen ``SC-08`` keys: the panel manifest it
    was built from, its version, its expiry, its declared policy/forecast ids,
    the compared alternatives *including cash*, the declared action sequence, the
    expected economics and the constraints it was built under. Holding the record
    grants no order, sizing, reservation or execution authority.

    A rotation is present as evidence only: a ``ROTATE`` action exists only when
    the compared advantage was proven under a ratified policy, and a plan that
    treats the old position's past acquisition cost as anything but sunk fails
    closed.
    """

    window_identity: str
    candidate_panel_manifest: PortfolioPanelManifest
    portfolio_version: str
    plan_expiry: datetime
    policy_forecast_ids: PortfolioPlanPolicyBinding
    alternatives_including_cash: tuple[PortfolioAlternative, ...]
    action_sequence: tuple[PortfolioPlanAction, ...]
    expected_economics: PortfolioPlanEconomics
    constraints: PortfolioPlanConstraints
    rejected_alternatives: tuple[PortfolioPlanRejection, ...] = ()
    comparisons: tuple[RotationComparison, ...] = ()
    plan_id: str = ""

    @property
    def authoritative(self) -> bool:
        """Always ``False``: a plan is evidence, never an authority."""
        return False

    @property
    def schema_version(self) -> str:
        return PORTFOLIO_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "window_identity",
            require_portfolio_text(self.window_identity, field_name="window_identity"),
        )
        manifest = self.candidate_panel_manifest
        if not isinstance(manifest, PortfolioPanelManifest):
            raise PortfolioContractError(
                "candidate_panel_manifest must be the plan's PortfolioPanelManifest"
            )
        if manifest.window_identity != self.window_identity:
            raise PortfolioContractError(
                "the panel manifest must belong to the plan's own evaluation window"
            )
        version = require_portfolio_text(
            self.portfolio_version, field_name="portfolio_version"
        )
        if version != PORTFOLIO_PLAN_SCHEMA_VERSION:
            raise PortfolioContractError(
                f"portfolio_version is not the ratified {PORTFOLIO_PLAN_SCHEMA_VERSION}"
            )
        object.__setattr__(self, "portfolio_version", version)
        object.__setattr__(
            self,
            "plan_expiry",
            require_portfolio_utc(self.plan_expiry, field_name="plan_expiry"),
        )
        if not isinstance(self.policy_forecast_ids, PortfolioPlanPolicyBinding):
            raise PortfolioContractError(
                "policy_forecast_ids must be the plan's declared policy binding"
            )
        if not isinstance(self.expected_economics, PortfolioPlanEconomics):
            raise PortfolioContractError(
                "expected_economics must be a PortfolioPlanEconomics"
            )
        if not isinstance(self.constraints, PortfolioPlanConstraints):
            raise PortfolioContractError("constraints must be a PortfolioPlanConstraints")
        object.__setattr__(
            self, "alternatives_including_cash", self._freeze_alternatives()
        )
        object.__setattr__(self, "comparisons", self._freeze_comparisons())
        object.__setattr__(
            self, "action_sequence", self._freeze_action_sequence()
        )
        object.__setattr__(
            self, "rejected_alternatives", self._freeze_rejections()
        )
        expected = stable_hash(PORTFOLIO_PLAN_ID_PREFIX, self._identity_payload())
        if self.plan_id == "":
            object.__setattr__(self, "plan_id", expected)
        elif self.plan_id != expected:
            raise PortfolioContractError(
                "plan_id does not match its content; a forged plan fails closed"
            )

    def _freeze_alternatives(self) -> tuple[PortfolioAlternative, ...]:
        raw = self.alternatives_including_cash
        if not isinstance(raw, (list, tuple)) or not raw:
            raise PortfolioContractError(
                "alternatives_including_cash must be a non-empty set of alternatives"
            )
        alternatives: list[PortfolioAlternative] = []
        seen_kinds: set[str] = set()
        seen_ids: set[str] = set()
        subjects: set[str] = set()
        for alternative in raw:
            if not isinstance(alternative, PortfolioAlternative):
                raise PortfolioContractError(
                    "alternatives_including_cash must hold PortfolioAlternative values"
                )
            if alternative.kind.value not in PORTFOLIO_ALTERNATIVE_KINDS:
                raise PortfolioContractError(
                    "only the three declared common-window legs may be compared"
                )
            if alternative.kind.value in seen_kinds:
                raise PortfolioContractError(
                    "the plan compares each common-window leg at most once"
                )
            seen_kinds.add(alternative.kind.value)
            if alternative.alternative_id in seen_ids:
                raise PortfolioContractError(
                    "the plan compares each alternative id at most once"
                )
            seen_ids.add(alternative.alternative_id)
            subjects.add(alternative.subject_ref)
            alternatives.append(alternative)
        for required in (
            PortfolioAlternativeKind.HOLD.value,
            PortfolioAlternativeKind.EXIT_TO_CASH.value,
        ):
            if required not in seen_kinds:
                raise PortfolioContractError(
                    f"the plan must always compare the {required} leg; there is no "
                    "alternative in which cash is not compared"
                )
        if len(subjects) != 1:
            raise PortfolioContractError(
                "a plan compares one subject over one common evaluation window"
            )
        return tuple(alternatives)

    def _freeze_comparisons(self) -> tuple[RotationComparison, ...]:
        raw = self.comparisons
        if not isinstance(raw, (list, tuple)):
            raise PortfolioContractError("comparisons must be a list of comparisons")
        by_id = {item.alternative_id: item for item in self.alternatives_including_cash}
        comparisons: list[RotationComparison] = []
        seen: set[str] = set()
        for comparison in raw:
            if not isinstance(comparison, RotationComparison):
                raise PortfolioContractError(
                    "comparisons must hold RotationComparison values"
                )
            if comparison.window_identity != self.window_identity:
                raise PortfolioContractError(
                    "a comparison must share the plan's own evaluation window"
                )
            for name in ("hold", "cash", "replacement"):
                leg = getattr(comparison, name)
                recorded = by_id.get(leg.alternative_id)
                if recorded is None:
                    raise PortfolioContractError(
                        f"a comparison's {name} leg must be one of the alternatives "
                        "this plan actually compared; a substituted or forged "
                        "comparison fails closed. An alternative id is a content hash "
                        "of the leg, so a matching id is a matching leg."
                    )
            if comparison.comparison_id in seen:
                raise PortfolioContractError("a comparison may only be recorded once")
            seen.add(comparison.comparison_id)
            comparisons.append(comparison)
        return tuple(comparisons)

    def _freeze_action_sequence(self) -> tuple[PortfolioPlanAction, ...]:
        raw = self.action_sequence
        if not isinstance(raw, (list, tuple)) or not raw:
            raise PortfolioContractError(
                "action_sequence must declare at least one action"
            )
        by_id = {item.alternative_id: item for item in self.alternatives_including_cash}
        proven_rotations = {
            comparison.replacement.alternative_id for comparison in self.comparisons
        }
        actions: list[PortfolioPlanAction] = []
        seen_sequences: set[int] = set()
        for action in raw:
            if not isinstance(action, PortfolioPlanAction):
                raise PortfolioContractError(
                    "action_sequence must hold PortfolioPlanAction values"
                )
            if action.sequence in seen_sequences:
                raise PortfolioContractError(
                    "action_sequence must not repeat a step number"
                )
            seen_sequences.add(action.sequence)
            if action.alternative_id is not None:
                alternative = by_id.get(action.alternative_id)
                if alternative is None:
                    raise PortfolioContractError(
                        "an action may only name an alternative the plan compared"
                    )
                if action.subject_ref != alternative.subject_ref:
                    raise PortfolioContractError(
                        "an action must name an alternative of its own subject"
                    )
                if action.disposition is PortfolioPlanDisposition.ROTATE:
                    if not alternative.is_replacement:
                        raise PortfolioContractError(
                            "a ROTATE action must name the compared EXIT_THEN_ENTER alternative"
                        )
                    if not self.constraints.is_ratified:
                        raise PortfolioContractError(
                            "a ROTATE action requires a ratified switch policy; the governed "
                            "outcome is a POLICY_NOT_BOUND abstention, not a rotation"
                        )
                    if action.alternative_id not in proven_rotations:
                        raise PortfolioContractError(
                            "a ROTATE action must carry the common-window comparison that "
                            "proves the switch advantage"
                        )
                    self._assert_static_switch_admission(action)
            actions.append(action)
        declared = tuple(action.sequence for action in actions)
        if declared != tuple(range(1, len(actions) + 1)):
            raise PortfolioContractError(
                "action_sequence must be the consecutive declared step order"
            )
        return tuple(actions)

    def _assert_static_switch_admission(self, action: PortfolioPlanAction) -> None:
        """The clock-free admission a declared ``ROTATE`` step must already satisfy.

        A plan declares what its own recorded evidence supports, so the requested
        switch must be the head of the plan's own observed eligible ranking, and
        every branch of the switch gate that the plan's own evidence *can* settle
        must already admit it: a ratified versioned policy, an available
        common-window comparison bound to the plan's own legs, a turnover budget
        the step's own declared capital still fits, an uncertainty treatment the
        recorded bands can be read under, and a treatment-adjusted advantage at or
        above the ratified minimum.

        Two branches deliberately stay with the decision-time gate because this
        record cannot settle them: ``PLAN_EXPIRED`` needs a decision clock,
        ``COOLDOWN_ACTIVE`` needs the switch history and ``PLAN_NOT_CURRENT`` needs
        the current revision identity. This is a build-time *necessary* condition,
        never an authorization, and the decision-time gate remains the authority
        over the switch. The turnover branch is read here in the capital the step
        itself declares and with no prior turnover recorded, which is the most
        favourable case a switch can face, so a plan whose own declared capital
        already exceeds the whole budget cannot be licensed by any history.
        """

        alternative_id = action.alternative_id
        if alternative_id != self.best_observed_alternative_id:
            raise PortfolioContractError(
                "a ROTATE action must name the best observed eligible alternative: a "
                "plan cannot claim a better alternative than the best one it compared"
            )
        reasons, _ = _switch_gate_evidence_reasons(
            window_identity=self.window_identity,
            constraints=self.constraints,
            comparison=self.comparison_for_alternative(alternative_id),
            turn_over_committed_dollars=0.0,
            switched_capital_dollars=action.capital_delta,
        )
        if reasons:
            raise PortfolioContractError(
                "a ROTATE action requires the switch gate to admit it, but the plan's "
                f"own evidence abstains with {reasons[0].value}; a declared rotation "
                "must be a proven advantage and not an abstention"
            )

    def _freeze_rejections(self) -> tuple[PortfolioPlanRejection, ...]:
        raw = self.rejected_alternatives
        if not isinstance(raw, (list, tuple)):
            raise PortfolioContractError(
                "rejected_alternatives must be a list of rejections"
            )
        compared = {item.alternative_id for item in self.alternatives_including_cash}
        rejections: list[PortfolioPlanRejection] = []
        seen: set[str] = set()
        for rejection in raw:
            if not isinstance(rejection, PortfolioPlanRejection):
                raise PortfolioContractError(
                    "rejected_alternatives must hold PortfolioPlanRejection values"
                )
            if rejection.alternative_id not in compared:
                raise PortfolioContractError(
                    "a rejection must name an alternative the plan actually compared"
                )
            if rejection.alternative_id in seen:
                raise PortfolioContractError(
                    "each examined alternative must be recorded exactly once"
                )
            seen.add(rejection.alternative_id)
            rejections.append(rejection)
        return tuple(rejections)

    def assert_rejections_are_complete(self) -> None:
        """Every compared alternative but the selected one is recorded once.

        The selection is the head of the plan's own ranking, so a plan cannot
        omit the reason a compared alternative was passed over, and it cannot
        record a rejection for the alternative it selected.
        """

        selected = select_plan_alternative(self.alternatives_including_cash)
        recorded = {item.alternative_id for item in self.rejected_alternatives}
        expected = {
            item.alternative_id for item in self.alternatives_including_cash
        } - {selected.alternative_id}
        if recorded != expected:
            raise PortfolioContractError(
                "every compared alternative the plan did not select must be recorded "
                "exactly once, and the selected one must not be"
            )

    @property
    def compared_alternative_ids(self) -> tuple[str, ...]:
        return tuple(item.alternative_id for item in self.alternatives_including_cash)

    @property
    def compared_kinds(self) -> tuple[str, ...]:
        """The common-window legs the plan compared, in the frozen leg order."""
        present = {item.kind.value for item in self.alternatives_including_cash}
        return tuple(kind for kind in PORTFOLIO_ALTERNATIVE_KINDS if kind in present)

    @property
    def compared_subject_ref(self) -> str:
        return self.alternatives_including_cash[0].subject_ref

    @property
    def has_cash_comparison(self) -> bool:
        """Cash is always a compared alternative; the plan carries its own leg."""
        return any(item.is_cash for item in self.alternatives_including_cash)

    @property
    def has_replacement_comparison(self) -> bool:
        """True only when a composed ``EXIT_THEN_ENTER`` leg was compared."""
        return any(item.is_replacement for item in self.alternatives_including_cash)

    @property
    def rotation_actions(self) -> tuple[PortfolioPlanAction, ...]:
        return tuple(
            action
            for action in self.action_sequence
            if action.disposition is PortfolioPlanDisposition.ROTATE
        )

    @property
    def is_rotation_plan(self) -> bool:
        """A plan carrying a declared ``ROTATE`` step. It still executes nothing."""
        return bool(self.rotation_actions)

    @property
    def best_observed_alternative_id(self) -> str:
        """The best of the alternatives this plan observed and recorded."""
        return select_plan_alternative(self.alternatives_including_cash).alternative_id

    def comparison_for_alternative(self, alternative_id: Any) -> RotationComparison | None:
        """The comparison proving a switch into ``alternative_id``, if recorded."""
        wanted = require_portfolio_text(alternative_id, field_name="alternative_id")
        for comparison in self.comparisons:
            if comparison.replacement.alternative_id == wanted:
                return comparison
        return None

    def _default_comparison(self, comparison: Any) -> Any:
        """The comparison under evaluation, always one this plan recorded.

        A caller may only point at a comparison the plan already recorded: an
        external comparison is matched by its content identity and the *recorded*
        comparison is used, so a caller cannot substitute more favourable economics
        for the plan's own evidence and the plan cannot be evaluated against a
        comparison it never preregistered. Anything the plan did not record is not
        used. Without an explicit pointer the comparison recorded for the plan's own
        ``ROTATE`` step is used, then a single recorded comparison. If none can be
        identified the switch has no cash comparison and abstains.
        """

        if comparison is not None:
            if not isinstance(comparison, RotationComparison):
                return None
            for recorded in self.comparisons:
                if recorded.comparison_id == comparison.comparison_id:
                    return recorded
            return None
        rotations = self.rotation_actions
        if rotations and rotations[0].alternative_id is not None:
            found = self.comparison_for_alternative(rotations[0].alternative_id)
            if found is not None:
                return found
        if len(self.comparisons) == 1:
            return self.comparisons[0]
        return None

    def _resolve_switch_capital(self, declared: float | None) -> float | None:
        """The capital the switch under evaluation turns over, or ``None``.

        The record's own statement of the capital a switch moves is the plan's
        declared ``ROTATE`` step. A caller may declare the capital of a switch the
        plan does not declare - the hold-oriented plan asked what a rotation *would*
        turn over - and may never lower the plan's own recorded figure, because
        understating the capital is how a switch could slip past a turnover budget.
        ``None`` means the capital is unestablished, and the turnover branch fails
        closed on it.
        """

        rotations = self.rotation_actions
        if not rotations:
            return declared
        recorded = rotations[0].capital_delta
        if declared is None:
            return recorded
        stated = require_non_negative(declared, field_name="switched_capital_dollars")
        return max(recorded, stated)

    def switch_abstention_reason(
        self,
        *,
        now: datetime,
        comparison: Any = None,
        current_plan_id: str | None = None,
        seconds_since_last_switch: float | None = None,
        turn_over_committed_dollars: float = 0.0,
        switched_capital_dollars: float | None = None,
    ) -> PortfolioPlanAbstentionReason | None:
        """The one governed abstention reason, or ``None`` when proven.

        ``None`` means the rotation advantage was proven under the ratified policy
        and its declared uncertainty treatment. Anything else is a specific,
        recorded abstention reported in the frozen precedence order. Freshness is
        required evidence: an evaluation that does not name the current revision
        abstains with ``PLAN_NOT_CURRENT`` rather than assuming this record is it.
        """

        reason, _ = _evaluate_switch_gate(
            window_identity=self.window_identity,
            constraints=self.constraints,
            plan_id=self.plan_id,
            plan_expiry=self.plan_expiry,
            now=now,
            comparison=self._default_comparison(comparison),
            current_plan_id=current_plan_id,
            seconds_since_last_switch=seconds_since_last_switch,
            turn_over_committed_dollars=turn_over_committed_dollars,
            switched_capital_dollars=self._resolve_switch_capital(
                switched_capital_dollars
            ),
        )
        return reason

    def rotation_switch_status(
        self,
        *,
        now: datetime,
        comparison: Any = None,
        current_plan_id: str | None = None,
        seconds_since_last_switch: float | None = None,
        turn_over_committed_dollars: float = 0.0,
        switched_capital_dollars: float | None = None,
    ) -> RotationSwitchStatus:
        """Whether a discretionary rotation advantage was proven (``RO-02``)."""

        reason = self.switch_abstention_reason(
            now=now,
            comparison=comparison,
            current_plan_id=current_plan_id,
            seconds_since_last_switch=seconds_since_last_switch,
            turn_over_committed_dollars=turn_over_committed_dollars,
            switched_capital_dollars=switched_capital_dollars,
        )
        if reason is None:
            return RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN
        return RotationSwitchStatus.ABSTAINED

    def assess_rotation_switch(
        self,
        *,
        now: datetime,
        comparison: Any = None,
        current_plan_id: str | None = None,
        seconds_since_last_switch: float | None = None,
        turn_over_committed_dollars: float = 0.0,
        switched_capital_dollars: float | None = None,
    ) -> "RotationSwitchAssessment":
        """The durable, replayable outcome record of one switch evaluation."""

        return assess_rotation_switch(
            self,
            now=now,
            comparison=comparison,
            current_plan_id=current_plan_id,
            seconds_since_last_switch=seconds_since_last_switch,
            turn_over_committed_dollars=turn_over_committed_dollars,
            switched_capital_dollars=switched_capital_dollars,
        )

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "candidate_panel_manifest": self.candidate_panel_manifest.to_dict(),
            "portfolio_version": self.portfolio_version,
            "plan_expiry": iso_z(self.plan_expiry, field_name="plan_expiry"),
            "policy_forecast_ids": self.policy_forecast_ids.to_dict(),
            "alternatives_including_cash": [
                item.to_dict() for item in self.alternatives_including_cash
            ],
            "action_sequence": [item.to_dict() for item in self.action_sequence],
            "expected_economics": self.expected_economics.to_dict(),
            "constraints": self.constraints.to_dict(),
            "rejected_alternatives": [
                item.to_dict() for item in self.rejected_alternatives
            ],
            "comparisons": [item.to_dict() for item in self.comparisons],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["plan_id"] = self.plan_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "PortfolioPlan":
        body = _require_durable_mapping(
            raw, field_name="plan", expected_keys=_plan_record_key_order()
        )
        for name in ("alternatives_including_cash", "action_sequence", "comparisons"):
            if not isinstance(body[name], (list, tuple)):
                raise PortfolioContractError(f"plan.{name} must be a list")
        rejected = body["rejected_alternatives"]
        if not isinstance(rejected, (list, tuple)):
            raise PortfolioContractError("plan.rejected_alternatives must be a list")
        plan = cls(
            window_identity=body["candidate_panel_manifest"]["window_identity"],
            candidate_panel_manifest=PortfolioPanelManifest.from_dict(
                body["candidate_panel_manifest"]
            ),
            portfolio_version=body["portfolio_version"],
            plan_expiry=_parse_persisted_utc(body["plan_expiry"], field_name="plan_expiry"),
            policy_forecast_ids=PortfolioPlanPolicyBinding.from_dict(
                body["policy_forecast_ids"]
            ),
            alternatives_including_cash=tuple(
                PortfolioAlternative.from_dict(item)
                for item in body["alternatives_including_cash"]
            ),
            action_sequence=tuple(
                PortfolioPlanAction.from_dict(item) for item in body["action_sequence"]
            ),
            expected_economics=PortfolioPlanEconomics.from_dict(
                body["expected_economics"]
            ),
            constraints=PortfolioPlanConstraints.from_dict(body["constraints"]),
            rejected_alternatives=tuple(
                PortfolioPlanRejection.from_dict(item) for item in rejected
            ),
            comparisons=tuple(
                RotationComparison.from_dict(item) for item in body["comparisons"]
            ),
            plan_id=body["plan_id"],
        )
        if plan.plan_id != stable_hash(PORTFOLIO_PLAN_ID_PREFIX, plan._identity_payload()):
            raise PortfolioContractError(
                "plan_id does not match its content; a forged plan fails closed"
            )
        return plan


@dataclass(frozen=True)
class RotationSwitchAssessment:
    """The durable outcome of one rotation-switch evaluation (``RO-02``).

    This is the machine-readable answer to "was a discretionary rotation
    advantage proven, and if not, which governed gap stopped it". ``ABSTAINED`` is
    a governed disposition: not a failure, not a veto, and it authorizes no order.
    A proven assessment must carry the advantage, the ratified minimum it met and
    the ratified uncertainty treatment, so an "advantage" can never be recorded
    without the policy that licensed it.
    """

    plan_id: str
    window_identity: str
    status: RotationSwitchStatus
    decided_at: datetime
    abstention_reason: PortfolioPlanAbstentionReason | None = None
    advantage_dollars: float | None = None
    minimum_advantage_dollars: float | None = None
    uncertainty_treatment: UncertaintyTreatment | None = None
    assessment_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "plan_id", require_portfolio_text(self.plan_id, field_name="plan_id")
        )
        object.__setattr__(
            self,
            "window_identity",
            require_portfolio_text(self.window_identity, field_name="window_identity"),
        )
        status = require_portfolio_enum(
            RotationSwitchStatus, self.status, field_name="status"
        )
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "decided_at",
            require_portfolio_utc(self.decided_at, field_name="decided_at"),
        )
        if self.abstention_reason is not None:
            object.__setattr__(
                self,
                "abstention_reason",
                require_portfolio_enum(
                    PortfolioPlanAbstentionReason,
                    self.abstention_reason,
                    field_name="abstention_reason",
                ),
            )
        if self.uncertainty_treatment is not None:
            object.__setattr__(
                self,
                "uncertainty_treatment",
                require_portfolio_enum(
                    UncertaintyTreatment,
                    self.uncertainty_treatment,
                    field_name="uncertainty_treatment",
                ),
            )
        if self.advantage_dollars is not None:
            object.__setattr__(
                self,
                "advantage_dollars",
                require_finite(self.advantage_dollars, field_name="advantage_dollars"),
            )
        if self.minimum_advantage_dollars is not None:
            object.__setattr__(
                self,
                "minimum_advantage_dollars",
                require_positive(
                    self.minimum_advantage_dollars,
                    field_name="minimum_advantage_dollars",
                ),
            )
        abstained = status is RotationSwitchStatus.ABSTAINED
        if abstained != (self.abstention_reason is not None):
            raise PortfolioContractError(
                "an ABSTAINED assessment must name exactly one abstention reason, "
                "and a proven advantage must name none"
            )
        if not abstained:
            if self.advantage_dollars is None or self.minimum_advantage_dollars is None:
                raise PortfolioContractError(
                    "a proven advantage must record the advantage and the minimum it met"
                )
            if self.advantage_dollars < self.minimum_advantage_dollars:
                raise PortfolioContractError(
                    "a proven advantage must meet the ratified minimum advantage"
                )
            if self.uncertainty_treatment is None:
                raise PortfolioContractError(
                    "a proven advantage must record the ratified uncertainty treatment"
                )
        expected = stable_hash(
            PORTFOLIO_SWITCH_ASSESSMENT_ID_PREFIX, self._identity_payload()
        )
        if self.assessment_id == "":
            object.__setattr__(self, "assessment_id", expected)
        elif self.assessment_id != expected:
            raise PortfolioContractError(
                "assessment_id does not match its content; a forged assessment fails closed"
            )

    @property
    def is_abstained(self) -> bool:
        """``ABSTAINED`` is a governed disposition, not a failure."""
        return self.status is RotationSwitchStatus.ABSTAINED

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "window_identity": self.window_identity,
            "status": self.status.value,
            "decided_at": iso_z(self.decided_at, field_name="decided_at"),
            "abstention_reason": None
            if self.abstention_reason is None
            else self.abstention_reason.value,
            "advantage_dollars": self.advantage_dollars,
            "minimum_advantage_dollars": self.minimum_advantage_dollars,
            "uncertainty_treatment": None
            if self.uncertainty_treatment is None
            else self.uncertainty_treatment.value,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["assessment_id"] = self.assessment_id
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "RotationSwitchAssessment":
        body = _require_durable_mapping(
            raw,
            field_name="assessment",
            expected_keys=(
                "plan_id",
                "window_identity",
                "status",
                "decided_at",
                "abstention_reason",
                "advantage_dollars",
                "minimum_advantage_dollars",
                "uncertainty_treatment",
                "assessment_id",
            ),
        )
        assessment = cls(
            plan_id=body["plan_id"],
            window_identity=body["window_identity"],
            status=body["status"],
            decided_at=_parse_persisted_utc(
                body["decided_at"], field_name="decided_at"
            ),
            abstention_reason=body["abstention_reason"],
            advantage_dollars=body["advantage_dollars"],
            minimum_advantage_dollars=body["minimum_advantage_dollars"],
            uncertainty_treatment=body["uncertainty_treatment"],
            assessment_id=body["assessment_id"],
        )
        if assessment.assessment_id != stable_hash(
            PORTFOLIO_SWITCH_ASSESSMENT_ID_PREFIX, assessment._identity_payload()
        ):
            raise PortfolioContractError(
                "assessment_id does not match its content; a forged assessment fails closed"
            )
        return assessment


def assess_rotation_switch(
    plan: Any,
    *,
    now: datetime,
    comparison: Any = None,
    current_plan_id: str | None = None,
    seconds_since_last_switch: float | None = None,
    turn_over_committed_dollars: float = 0.0,
    switched_capital_dollars: float | None = None,
) -> RotationSwitchAssessment:
    """Evaluate the switch of one plan and record the governed outcome (``RO-02``).

    The assessment is evidence: it authorizes no order, no sizing and no
    execution, and it cannot overrule a deterministic rejection. When the policy
    is not ratified the assessment abstains with ``POLICY_NOT_BOUND`` and records
    no advantage at all, so an unratified run cannot look like a licensed switch.
    The capital the switch turns over is read from the plan's own declared
    ``ROTATE`` step, so an evaluation can neither omit it nor report less turnover
    than the record declares, and an evaluation that does not name the current
    revision abstains with ``PLAN_NOT_CURRENT``.
    """

    if not isinstance(plan, PortfolioPlan):
        raise PortfolioContractError("plan must be a PortfolioPlan")
    reason, advantage = _evaluate_switch_gate(
        window_identity=plan.window_identity,
        constraints=plan.constraints,
        plan_id=plan.plan_id,
        plan_expiry=plan.plan_expiry,
        now=now,
        comparison=plan._default_comparison(comparison),
        current_plan_id=current_plan_id,
        seconds_since_last_switch=seconds_since_last_switch,
        turn_over_committed_dollars=turn_over_committed_dollars,
        switched_capital_dollars=plan._resolve_switch_capital(
            switched_capital_dollars
        ),
    )
    abstained = reason is not None
    return RotationSwitchAssessment(
        plan_id=plan.plan_id,
        window_identity=plan.window_identity,
        status=RotationSwitchStatus.ABSTAINED
        if abstained
        else RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN,
        decided_at=require_portfolio_utc(now, field_name="now"),
        abstention_reason=reason,
        advantage_dollars=advantage,
        minimum_advantage_dollars=None
        if abstained
        else plan.constraints.min_switch_advantage_dollars,
        uncertainty_treatment=None
        if abstained
        else plan.constraints.uncertainty_treatment,
    )


def assert_plan_record_keys(plan: Any) -> tuple[str, ...]:
    """Fail closed unless the plan serializes the frozen ``SC-08`` record keys.

    The eight ``SC-08`` keys must all be present, in their frozen order, before
    the record's own additions. A plan that renamed, dropped, reordered or
    silently extended a frozen key is not the ratified ``SC-08`` record.
    """

    if not isinstance(plan, PortfolioPlan):
        raise PortfolioContractError("plan must be a PortfolioPlan")
    keys = tuple(plan.to_dict())
    expected = _plan_record_key_order()
    if keys != expected:
        raise PortfolioContractError(
            "the plan must serialize the frozen SC-08 record keys in the frozen order"
        )
    for required in PORTFOLIO_PLAN_RECORD_KEYS:
        if required not in keys:
            raise PortfolioContractError(
                f"the frozen SC-08 record key {required!r} is missing"
            )
    return keys


__all__ = [
    "LegacyComparatorObservables",
    "OPEN_OWNER_INPUTS",
    "OWNER_INPUT_STATUS",
    "PORTFOLIO_ALLOCATION_ID_PREFIX",
    "PORTFOLIO_ALTERNATIVE_ID_PREFIX",
    "PORTFOLIO_ALTERNATIVE_KINDS",
    "PORTFOLIO_ALTERNATIVE_PREFERENCE",
    "PORTFOLIO_ALTERNATIVE_TIE_BREAK_RULE",
    "PORTFOLIO_CANDIDATE_ID_PREFIX",
    "PORTFOLIO_CAPITAL_STATE_FINGERPRINT_PREFIX",
    "PORTFOLIO_COMPARATOR_ID_PREFIX",
    "PORTFOLIO_COMPARATOR_VERSION",
    "PORTFOLIO_COMPARISON_ID_PREFIX",
    "PORTFOLIO_DECISION_ID_PREFIX",
    "PORTFOLIO_DECISION_SCHEMA_VERSION",
    "PORTFOLIO_DIRECTIONS",
    "PORTFOLIO_EVALUATION_VERSION",
    "PORTFOLIO_EXPOSURE_FINGERPRINT_PREFIX",
    "PORTFOLIO_PANEL_FINGERPRINT_PREFIX",
    "PORTFOLIO_PANEL_MANIFEST_ID_PREFIX",
    "PORTFOLIO_PLAN_ABSTENTION_PRECEDENCE",
    "PORTFOLIO_PLAN_COMPARISON_VERSION",
    "PORTFOLIO_PLAN_DISABLED_DISPOSITIONS",
    "PORTFOLIO_PLAN_DISPOSITIONS",
    "PORTFOLIO_PLAN_ENGINEERING_SCENARIO_POLICY_VERSION",
    "PORTFOLIO_PLAN_ID_PREFIX",
    "PORTFOLIO_PLAN_POLICY_ID_KEYS",
    "PORTFOLIO_PLAN_POLICY_ID_PREFIX",
    "PORTFOLIO_PLAN_RECORD_KEYS",
    "PORTFOLIO_PLAN_REJECTION_ID_PREFIX",
    "PORTFOLIO_PLAN_REJECTION_REASONS",
    "PORTFOLIO_PLAN_SCHEMA_VERSION",
    "PORTFOLIO_POLICY_ID_PREFIX",
    "PORTFOLIO_POLICY_VERSION",
    "PORTFOLIO_RESERVATION_ID_PREFIX",
    "PORTFOLIO_RESERVATION_PLAN_ID_PREFIX",
    "PORTFOLIO_RESERVATION_PLAN_SCHEMA_VERSION",
    "PORTFOLIO_SELECTOR_VERSION",
    "PORTFOLIO_SWITCH_ASSESSMENT_ID_PREFIX",
    "PORTFOLIO_WINDOW_ID_PREFIX",
    "PortfolioAbstentionReason",
    "PortfolioAllocation",
    "PortfolioAlternative",
    "PortfolioAlternativeKind",
    "PortfolioCandidate",
    "PortfolioCapitalState",
    "PortfolioCashReason",
    "PortfolioContractError",
    "PortfolioDecision",
    "PortfolioDirection",
    "PortfolioEvaluationWindow",
    "PortfolioEvidenceStatus",
    "PortfolioExposureSnapshot",
    "PortfolioPanelManifest",
    "PortfolioPlan",
    "PortfolioPlanAbstentionReason",
    "PortfolioPlanAction",
    "PortfolioPlanConstraints",
    "PortfolioPlanDisposition",
    "PortfolioPlanEconomics",
    "PortfolioPlanPolicyBinding",
    "PortfolioPlanRejection",
    "PortfolioPolicy",
    "PortfolioReservation",
    "PortfolioReservationPlan",
    "PortfolioStatus",
    "ReservationReleaseReason",
    "ReservationStatus",
    "RotationComparison",
    "RotationSwitchAssessment",
    "RotationSwitchStatus",
    "UncertaintyTreatment",
    "adjust_reservation_for_fill",
    "assert_plan_record_keys",
    "assert_plan_selects_best_observed",
    "assert_reservation_plan_current",
    "assess_rotation_switch",
    "compose_rotation_replacement",
    "expected_fill_probability",
    "expire_reservations",
    "portfolio_decision_identity",
    "portfolio_panel_fingerprint",
    "portfolio_window_identity",
    "rank_plan_alternatives",
    "release_reservation",
    "require_finite",
    "require_fraction",
    "require_non_negative",
    "require_non_negative_int",
    "require_portfolio_enum",
    "require_portfolio_text",
    "require_portfolio_utc",
    "require_positive",
    "require_positive_int",
    "rotation_comparison",
    "select_plan_alternative",
    "unratified_plan_constraints",
]
