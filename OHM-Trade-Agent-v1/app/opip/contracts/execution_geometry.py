"""R4-B0 canonical execution-geometry contract.

OWNER Decision 3 (R4-B0) establishes exactly ONE deterministic entry/exit
geometry owner. This module is that owner's typed contract. The geometry math is
NOT reimplemented here: ``app.opip.execution_geometry`` carries the single pure
kernel, and both the legacy advisor (``app.services.entry_exit_advisor``) and the
target F3-F7 spine consume it, so the two paths cannot diverge.

Scope boundary
--------------
``ExecutionGeometry`` owns *entry/exit geometry only*: where a position is
entered, chased, stopped and targeted, and the risk it implies. It does NOT own,
and must not grow into:

* candidate ranking or allocation (F7 owns that);
* forecast probability or expected return (F6 owns that);
* feasibility / margin / liquidity eligibility (F5 owns that);
* the feature vector (FeatureSnapshot owns that).

Per OWNER Decision 1, ``FeatureSnapshot`` deliberately does NOT become a
universal execution/risk record; geometry is a separate typed projection over the
same validated point-in-time evidence, and this contract therefore carries its
own minimal input (``ExecutionGeometryInput``) plus explicit evidence anchoring
(instrument identity, cutoff, evidence fingerprint) rather than the whole legacy
``MarketSnapshot``.

Determinism
-----------
The identity is a deterministic ``EGEOM:`` function of the immutable geometry
facts and its provenance, so the same inputs always yield the same identity and a
forged identity fails closed. No clock, UUID or process state participates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import require_utc

EXECUTION_GEOMETRY_SCHEMA_VERSION = "execution-geometry-v1"
EXECUTION_GEOMETRY_POLICY_VERSION = "execution-geometry-policy-v1"
EXECUTION_GEOMETRY_ID_PREFIX = "EGEOM"

#: The directions this contract represents. Kept explicit so a caller cannot
#: smuggle an arbitrary string into a geometry identity.
SUPPORTED_GEOMETRY_DIRECTIONS = frozenset({"LONG", "SHORT"})

#: The risk levels the single kernel supports. Unchanged from the legacy advisor.
SUPPORTED_RISK_LEVELS = frozenset({"low", "medium"})

#: Entry styles the kernel can emit, including the invalid sentinel ``wait``.
GEOMETRY_ENTRY_STYLES = frozenset(
    {
        "wait",
        "pullback_or_retest",
        "wait_for_pullback",
        "rebound_or_retest",
        "wait_for_rebound",
    }
)


class ExecutionGeometryContractError(ValueError):
    """A structural geometry violation. Always fails closed."""


def require_geometry_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ExecutionGeometryContractError(f"{field_name} must be non-empty text")
    return value


def require_geometry_utc(value: Any, *, field_name: str) -> datetime:
    try:
        return require_utc(value, field_name=field_name)
    except Exception as exc:  # noqa: BLE001 - normalize to this contract's error
        raise ExecutionGeometryContractError(f"{field_name}: {exc}") from exc


def require_finite(value: Any, *, field_name: str) -> float:
    """A finite number. Booleans are refused (``True`` is not a price)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionGeometryContractError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ExecutionGeometryContractError(f"{field_name} must be a finite number")
    return number


def require_non_negative(value: Any, *, field_name: str) -> float:
    number = require_finite(value, field_name=field_name)
    if number < 0:
        raise ExecutionGeometryContractError(f"{field_name} must not be negative")
    return number


def require_fraction(value: Any, *, field_name: str) -> float:
    """A fraction in ``[0, 1]``. Zero is the invalid-geometry sentinel."""
    number = require_finite(value, field_name=field_name)
    if number < 0 or number > 1:
        raise ExecutionGeometryContractError(f"{field_name} must be within [0, 1]")
    return number


def require_direction(value: Any) -> str:
    if not isinstance(value, str) or value.upper() not in SUPPORTED_GEOMETRY_DIRECTIONS:
        raise ExecutionGeometryContractError("direction must be LONG or SHORT")
    return value.upper()


def require_risk_level(value: Any) -> str:
    if not isinstance(value, str) or value not in SUPPORTED_RISK_LEVELS:
        raise ExecutionGeometryContractError(
            "geometry risk_level must be one of: " + ", ".join(sorted(SUPPORTED_RISK_LEVELS))
        )
    return value


@dataclass(frozen=True)
class ExecutionGeometryInput:
    """The minimal typed point-in-time market-geometry input.

    Deliberately NOT the legacy ``MarketSnapshot``: only the primitive facts the
    single kernel reads. The producer supplies them from already-observed
    point-in-time evidence; this record performs no market read.
    """

    symbol: str
    direction: str
    risk_level: str
    last_price: float
    atr: float
    ema20: float
    #: The symbol's own rolling upside/downside excursion percentiles. Absent is
    #: expressed as a non-positive value, exactly as the legacy advisor treats a
    #: missing percentile (it then keeps the mechanical target multiple).
    rolling_24h_upside_p75_pct: float = 0.0
    rolling_24h_downside_p75_pct: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "symbol", require_geometry_text(self.symbol, field_name="symbol")
        )
        object.__setattr__(self, "direction", require_direction(self.direction))
        object.__setattr__(self, "risk_level", require_risk_level(self.risk_level))
        # These may be non-finite on a rejected legacy record; the kernel maps a
        # non-finite value to the explicit invalid plan, so finiteness is not
        # enforced here. Type is still enforced so a repr cannot leak in.
        for name in (
            "last_price",
            "atr",
            "ema20",
            "rolling_24h_upside_p75_pct",
            "rolling_24h_downside_p75_pct",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ExecutionGeometryContractError(f"{name} must be a number")
            object.__setattr__(self, name, float(value))


@dataclass(frozen=True)
class ExecutionGeometry:
    """The one immutable entry/exit geometry for one instrument and direction.

    ``stop_loss_fraction`` is the fraction of the entry reference at risk at the
    stop (``|entry_reference - stop_price| / entry_reference``). It is ``0.0``
    exactly when the geometry is the explicit invalid sentinel, so a downstream
    consumer (F7) can never mistake an invalid geometry for an allocatable one.
    """

    geometry_id: str
    schema_version: str
    policy_version: str

    instrument_version_id: str
    venue_instrument_id: str
    symbol: str
    direction: str

    source_cutoff: datetime
    source_evidence_fingerprint: str

    entry_style: str
    valid_now: bool
    entry_low: float
    entry_high: float
    chase_limit: float
    stop_price: float
    target_1: float
    target_2: float
    reward_to_risk_1: float
    reward_to_risk_2: float
    risk_level: str
    stop_loss_fraction: float
    reason: str

    def __post_init__(self) -> None:
        if self.schema_version != EXECUTION_GEOMETRY_SCHEMA_VERSION:
            raise ExecutionGeometryContractError(
                f"schema_version is not the ratified {EXECUTION_GEOMETRY_SCHEMA_VERSION}"
            )
        if self.policy_version != EXECUTION_GEOMETRY_POLICY_VERSION:
            raise ExecutionGeometryContractError(
                f"policy_version is not the ratified {EXECUTION_GEOMETRY_POLICY_VERSION}"
            )
        object.__setattr__(
            self,
            "geometry_id",
            require_geometry_text(self.geometry_id, field_name="geometry_id"),
        )
        for name in (
            "instrument_version_id",
            "venue_instrument_id",
            "symbol",
            "source_evidence_fingerprint",
            "reason",
        ):
            object.__setattr__(
                self, name, require_geometry_text(getattr(self, name), field_name=name)
            )
        object.__setattr__(self, "direction", require_direction(self.direction))
        object.__setattr__(self, "risk_level", require_risk_level(self.risk_level))
        object.__setattr__(
            self,
            "source_cutoff",
            require_geometry_utc(self.source_cutoff, field_name="source_cutoff"),
        )
        if not isinstance(self.valid_now, bool):
            raise ExecutionGeometryContractError("valid_now must be a bool")
        if not isinstance(self.entry_style, str) or self.entry_style not in GEOMETRY_ENTRY_STYLES:
            raise ExecutionGeometryContractError(
                "entry_style is not a recognized geometry entry style"
            )
        for name in (
            "entry_low",
            "entry_high",
            "chase_limit",
            "stop_price",
            "target_1",
            "target_2",
            "reward_to_risk_1",
            "reward_to_risk_2",
        ):
            object.__setattr__(
                self, name, require_non_negative(getattr(self, name), field_name=name)
            )
        object.__setattr__(
            self,
            "stop_loss_fraction",
            require_fraction(self.stop_loss_fraction, field_name="stop_loss_fraction"),
        )
        expected = execution_geometry_identity(self)
        if self.geometry_id == "":
            raise ExecutionGeometryContractError("geometry_id must be supplied")
        if self.geometry_id != expected:
            raise ExecutionGeometryContractError(
                "geometry_id does not match its content; build geometry with "
                "build_execution_geometry"
            )

    @property
    def is_actionable(self) -> bool:
        """True only for a geometry that may be handed to paper execution."""
        return self.valid_now and self.stop_loss_fraction > 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "geometry_id": self.geometry_id,
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "source_cutoff": iso_z(self.source_cutoff, field_name="source_cutoff"),
            "source_evidence_fingerprint": self.source_evidence_fingerprint,
            "entry_style": self.entry_style,
            "valid_now": self.valid_now,
            "entry_low": self.entry_low,
            "entry_high": self.entry_high,
            "chase_limit": self.chase_limit,
            "stop_price": self.stop_price,
            "target_1": self.target_1,
            "target_2": self.target_2,
            "reward_to_risk_1": self.reward_to_risk_1,
            "reward_to_risk_2": self.reward_to_risk_2,
            "risk_level": self.risk_level,
            "stop_loss_fraction": self.stop_loss_fraction,
            "reason": self.reason,
        }


def execution_geometry_identity(geometry: ExecutionGeometry) -> str:
    """The deterministic ``EGEOM:`` identity of one geometry.

    A function of the immutable geometry facts and their provenance only, so
    identical inputs yield an identical identity and a tampered record fails
    closed. Created/released metadata and any clock do not participate.
    """
    payload = {
        "schema_version": geometry.schema_version,
        "policy_version": geometry.policy_version,
        "instrument_version_id": geometry.instrument_version_id,
        "venue_instrument_id": geometry.venue_instrument_id,
        "symbol": geometry.symbol,
        "direction": geometry.direction,
        "source_cutoff": iso_z(geometry.source_cutoff, field_name="source_cutoff"),
        "source_evidence_fingerprint": geometry.source_evidence_fingerprint,
        "entry_style": geometry.entry_style,
        "valid_now": geometry.valid_now,
        "entry_low": geometry.entry_low,
        "entry_high": geometry.entry_high,
        "chase_limit": geometry.chase_limit,
        "stop_price": geometry.stop_price,
        "target_1": geometry.target_1,
        "target_2": geometry.target_2,
        "reward_to_risk_1": geometry.reward_to_risk_1,
        "reward_to_risk_2": geometry.reward_to_risk_2,
        "risk_level": geometry.risk_level,
        "stop_loss_fraction": geometry.stop_loss_fraction,
    }
    return stable_hash(EXECUTION_GEOMETRY_ID_PREFIX, payload)


def assert_geometry_matches_evidence_epoch(
    geometry: ExecutionGeometry,
    *,
    instrument_version_id: str,
    venue_instrument_id: str,
    source_cutoff: datetime,
    source_evidence_fingerprint: str,
) -> None:
    """OWNER Decision 8: geometry must be anchored to the observed evidence epoch.

    A geometry derived from a different instrument, cutoff or evidence epoch is
    refused rather than silently accepted, so three projections of the same
    observation cannot drift apart.
    """
    if geometry.instrument_version_id != instrument_version_id:
        raise ExecutionGeometryContractError(
            "geometry instrument_version_id does not match the evidence epoch"
        )
    if geometry.venue_instrument_id != venue_instrument_id:
        raise ExecutionGeometryContractError(
            "geometry venue_instrument_id does not match the evidence epoch"
        )
    if geometry.source_evidence_fingerprint != source_evidence_fingerprint:
        raise ExecutionGeometryContractError(
            "geometry source_evidence_fingerprint does not match the evidence epoch"
        )
    if geometry.source_cutoff != require_geometry_utc(source_cutoff, field_name="source_cutoff"):
        raise ExecutionGeometryContractError(
            "geometry source_cutoff does not match the evidence epoch"
        )


__all__ = [
    "EXECUTION_GEOMETRY_ID_PREFIX",
    "EXECUTION_GEOMETRY_POLICY_VERSION",
    "EXECUTION_GEOMETRY_SCHEMA_VERSION",
    "GEOMETRY_ENTRY_STYLES",
    "SUPPORTED_GEOMETRY_DIRECTIONS",
    "SUPPORTED_RISK_LEVELS",
    "ExecutionGeometry",
    "ExecutionGeometryContractError",
    "ExecutionGeometryInput",
    "assert_geometry_matches_evidence_epoch",
    "execution_geometry_identity",
    "require_direction",
    "require_finite",
    "require_fraction",
    "require_geometry_text",
    "require_geometry_utc",
    "require_non_negative",
    "require_risk_level",
]
