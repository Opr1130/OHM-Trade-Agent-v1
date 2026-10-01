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


__all__ = [
    "PORTFOLIO_ALLOCATION_ID_PREFIX",
    "PORTFOLIO_CANDIDATE_ID_PREFIX",
    "PORTFOLIO_CAPITAL_STATE_FINGERPRINT_PREFIX",
    "PORTFOLIO_COMPARATOR_ID_PREFIX",
    "PORTFOLIO_COMPARATOR_VERSION",
    "PORTFOLIO_DECISION_ID_PREFIX",
    "PORTFOLIO_DECISION_SCHEMA_VERSION",
    "PORTFOLIO_DIRECTIONS",
    "PORTFOLIO_EVALUATION_VERSION",
    "PORTFOLIO_EXPOSURE_FINGERPRINT_PREFIX",
    "PORTFOLIO_PANEL_FINGERPRINT_PREFIX",
    "PORTFOLIO_POLICY_ID_PREFIX",
    "PORTFOLIO_POLICY_VERSION",
    "PORTFOLIO_RESERVATION_ID_PREFIX",
    "PORTFOLIO_RESERVATION_PLAN_ID_PREFIX",
    "PORTFOLIO_RESERVATION_PLAN_SCHEMA_VERSION",
    "PORTFOLIO_SELECTOR_VERSION",
    "PORTFOLIO_WINDOW_ID_PREFIX",
    "LegacyComparatorObservables",
    "PortfolioAbstentionReason",
    "PortfolioAllocation",
    "PortfolioCandidate",
    "PortfolioCapitalState",
    "PortfolioCashReason",
    "PortfolioContractError",
    "PortfolioDecision",
    "PortfolioDirection",
    "PortfolioEvaluationWindow",
    "PortfolioEvidenceStatus",
    "PortfolioExposureSnapshot",
    "PortfolioPolicy",
    "PortfolioReservation",
    "PortfolioReservationPlan",
    "PortfolioStatus",
    "ReservationReleaseReason",
    "ReservationStatus",
    "adjust_reservation_for_fill",
    "assert_reservation_plan_current",
    "expire_reservations",
    "expected_fill_probability",
    "portfolio_decision_identity",
    "portfolio_panel_fingerprint",
    "portfolio_window_identity",
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
]
