"""R4-B0 deterministic F7 -> Paper-v2 execution handoff and candidate bridge.

This module is the single typed boundary between the F7 portfolio selector and
Paper-v2 execution. It is a **lineage/evidence contract**: it proves that the
geometry, direction, instrument and decision ancestry Paper-v2 is about to
execute are exactly the ones F7 selected. It grants no execution authority of its
own - Paper-v2 remains the execution owner, and only an F7 ``SELECTED`` allocation
may produce a handoff.

What the bridge solves
----------------------
F7 mints ``PCAND:`` economic candidate identities; Paper-v2 expects ``OPIPC:``
execution candidate identities. Rather than minting an unrelated second candidate,
``ExecutionCandidateBridge`` *derives* the ``OPIPC:`` identity deterministically
from the ``PCAND:`` identity plus the shared lineage, so both names denote one
opportunity. Any disagreement fails closed.

Determinism
-----------
Every identity here is a pure function of its inputs. No clock, UUID, process
identity, random value or retry counter participates, so a replay reproduces the
same handoff and the same bridge, and a retry cannot double-reserve or
double-admit.

Authority
---------
``CASH_NO_TRADE`` and ``INSUFFICIENT_EVIDENCE`` produce no handoff. No Top-8,
profit-ranking, alert or Committee result can produce one - only an F7
``PortfolioDecision`` can.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from app.opip.contracts.portfolio import (
    PortfolioDecision,
    PortfolioStatus,
)
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import require_utc

PORTFOLIO_PAPER_HANDOFF_SCHEMA_VERSION = "portfolio-paper-handoff-v1"
PORTFOLIO_EXECUTION_BRIDGE_SCHEMA_VERSION = "portfolio-execution-bridge-v1"
PORTFOLIO_PAPER_HANDOFF_ID_PREFIX = "PHAND"
PORTFOLIO_EXECUTION_BRIDGE_ID_PREFIX = "PBRIDGE"
OPIPC_CANDIDATE_ID_PREFIX = "OPIPC"

#: The declared quantity-derivation rule. Quantity is the allocated paper capital
#: divided by the geometry's entry reference, floored to this many decimals so the
#: derived notional can never exceed the allocation. There is no second sizing
#: policy and no post-F7 adjustment.
QUANTITY_DERIVATION_ALLOCATED_CAPITAL_OVER_ENTRY_REFERENCE = (
    "ALLOCATED_CAPITAL_OVER_ENTRY_REFERENCE_FLOOR_V1"
)
QUANTITY_DECIMALS = 8

#: Directions this handoff supports. Kept explicit so the bridge cannot be handed
#: an arbitrary string.
SUPPORTED_HANDOFF_DIRECTIONS = frozenset({"LONG", "SHORT"})


class PortfolioPaperHandoffError(ValueError):
    """A structural or lineage violation. Always fails closed."""


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise PortfolioPaperHandoffError(f"{field_name} must be non-empty text")
    return value


def _require_utc(value: Any, *, field_name: str) -> datetime:
    try:
        return require_utc(value, field_name=field_name)
    except Exception as exc:  # noqa: BLE001 - normalize to this contract's error
        raise PortfolioPaperHandoffError(f"{field_name}: {exc}") from exc


def _require_non_negative(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PortfolioPaperHandoffError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise PortfolioPaperHandoffError(f"{field_name} must be finite and non-negative")
    return number


def _require_direction(value: Any) -> str:
    if not isinstance(value, str) or value not in SUPPORTED_HANDOFF_DIRECTIONS:
        raise PortfolioPaperHandoffError("direction must be the exact token LONG or SHORT")
    return value


@dataclass(frozen=True)
class PaperExecutionLineage:
    """The declared upstream facts the handoff must bind but F7 does not carry.

    These are supplied explicitly by the composing caller from the same observed
    evidence epoch. They are validated against F7's own records, so a caller
    cannot use them to substitute a different instrument, episode or cutoff.
    """

    snapshot_id: str
    snapshot_cutoff: datetime
    instrument_version_id: str
    venue_instrument_id: str
    cohort_id: str
    native_symbol: str
    quote_currency: str
    entry_reference: float
    detector_claim_id: str | None = None
    decision_context_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "snapshot_id",
            "instrument_version_id",
            "venue_instrument_id",
            "cohort_id",
            "native_symbol",
            "quote_currency",
        ):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), field_name=name)
            )
        object.__setattr__(
            self,
            "snapshot_cutoff",
            _require_utc(self.snapshot_cutoff, field_name="snapshot_cutoff"),
        )
        entry_reference = _require_non_negative(
            self.entry_reference, field_name="entry_reference"
        )
        if entry_reference <= 0:
            raise PortfolioPaperHandoffError("entry_reference must be positive")
        object.__setattr__(self, "entry_reference", entry_reference)
        if self.detector_claim_id is not None:
            object.__setattr__(
                self,
                "detector_claim_id",
                _require_text(self.detector_claim_id, field_name="detector_claim_id"),
            )


@dataclass(frozen=True)
class ExecutionCandidateBridge:
    """The deterministic ``PCAND`` -> ``OPIPC`` candidate identity bridge.

    Both identities denote one opportunity. The ``OPIPC:`` identity is *derived*
    from the ``PCAND:`` identity plus the shared lineage, so it can never name an
    unrelated candidate, and the same inputs always produce the same pair.
    """

    schema_version: str
    economic_candidate_id: str
    execution_candidate_id: str
    episode_id: str
    instrument_version_id: str
    venue_instrument_id: str
    direction: str
    snapshot_id: str
    snapshot_cutoff: datetime
    feasibility_decision_id: str
    forecast_id: str
    geometry_id: str
    portfolio_decision_id: str
    allocation_id: str
    portfolio_version: str
    bridge_id: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != PORTFOLIO_EXECUTION_BRIDGE_SCHEMA_VERSION:
            raise PortfolioPaperHandoffError(
                "bridge schema_version is not the ratified "
                f"{PORTFOLIO_EXECUTION_BRIDGE_SCHEMA_VERSION}"
            )
        for name in (
            "economic_candidate_id",
            "execution_candidate_id",
            "episode_id",
            "instrument_version_id",
            "venue_instrument_id",
            "feasibility_decision_id",
            "forecast_id",
            "geometry_id",
            "portfolio_decision_id",
            "allocation_id",
            "portfolio_version",
        ):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), field_name=name)
            )
        object.__setattr__(self, "direction", _require_direction(self.direction))
        object.__setattr__(self, "snapshot_id", _require_text(self.snapshot_id, field_name="snapshot_id"))
        object.__setattr__(
            self,
            "snapshot_cutoff",
            _require_utc(self.snapshot_cutoff, field_name="snapshot_cutoff"),
        )
        expected = _bridge_identity(self)
        if self.bridge_id == "":
            object.__setattr__(self, "bridge_id", expected)
        elif self.bridge_id != expected:
            raise PortfolioPaperHandoffError(
                "bridge_id does not match its content; build bridges with "
                "build_execution_candidate_bridge"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "economic_candidate_id": self.economic_candidate_id,
            "execution_candidate_id": self.execution_candidate_id,
            "episode_id": self.episode_id,
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "direction": self.direction,
            "snapshot_id": self.snapshot_id,
            "snapshot_cutoff": iso_z(self.snapshot_cutoff, field_name="snapshot_cutoff"),
            "feasibility_decision_id": self.feasibility_decision_id,
            "forecast_id": self.forecast_id,
            "geometry_id": self.geometry_id,
            "portfolio_decision_id": self.portfolio_decision_id,
            "allocation_id": self.allocation_id,
            "portfolio_version": self.portfolio_version,
            "bridge_id": self.bridge_id,
        }


def _bridge_identity(bridge: ExecutionCandidateBridge) -> str:
    return stable_hash(
        PORTFOLIO_EXECUTION_BRIDGE_ID_PREFIX,
        {
            "schema_version": bridge.schema_version,
            "economic_candidate_id": bridge.economic_candidate_id,
            "execution_candidate_id": bridge.execution_candidate_id,
            "episode_id": bridge.episode_id,
            "instrument_version_id": bridge.instrument_version_id,
            "venue_instrument_id": bridge.venue_instrument_id,
            "direction": bridge.direction,
            "snapshot_id": bridge.snapshot_id,
            "snapshot_cutoff": iso_z(bridge.snapshot_cutoff, field_name="snapshot_cutoff"),
            "feasibility_decision_id": bridge.feasibility_decision_id,
            "forecast_id": bridge.forecast_id,
            "geometry_id": bridge.geometry_id,
            "portfolio_decision_id": bridge.portfolio_decision_id,
            "allocation_id": bridge.allocation_id,
            "portfolio_version": bridge.portfolio_version,
        },
    )


def derive_execution_candidate_id(*, economic_candidate_id: str, lineage_key: Mapping[str, Any]) -> str:
    """Derive the ``OPIPC:`` identity for one ``PCAND:`` candidate.

    Deterministic and collision-resistant: the execution identity is a function of
    the economic identity plus the shared lineage, so two different opportunities
    cannot share an execution candidate and one opportunity cannot acquire two.
    """
    return stable_hash(
        OPIPC_CANDIDATE_ID_PREFIX,
        {"economic_candidate_id": economic_candidate_id, **dict(lineage_key)},
    )


@dataclass(frozen=True)
class PortfolioPaperHandoff:
    """One selected F7 allocation bound to a Paper-v2 execution candidate.

    Lineage and evidence only. It is not an execution authority, and it does not
    reserve capital: F7's reservation plan remains a plan while the canonical
    Paper-v2 writer remains the sole reservation authority.
    """

    schema_version: str
    # lineage
    snapshot_id: str
    snapshot_cutoff: datetime
    detector_claim_id: str | None
    episode_id: str
    feasibility_decision_id: str
    forecast_id: str
    portfolio_decision_id: str
    allocation_id: str
    reservation_plan_id: str
    reservation_id: str
    geometry_id: str
    instrument_version_id: str
    venue_instrument_id: str
    cohort_id: str
    native_symbol: str
    quote_currency: str
    direction: str
    # capital / quantity (paper only)
    allocated_capital: float
    requested_reservation_amount: float
    requested_notional: float
    requested_quantity: float
    quantity_derivation: str
    entry_reference: float
    # decision ancestry / versions
    portfolio_version: str
    panel_fingerprint: str
    capital_state_fingerprint: str
    exposure_fingerprint: str
    evaluation_time: datetime
    decision_context_id: str | None
    economic_candidate_id: str
    execution_candidate_id: str
    bridge: ExecutionCandidateBridge
    handoff_id: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != PORTFOLIO_PAPER_HANDOFF_SCHEMA_VERSION:
            raise PortfolioPaperHandoffError(
                "handoff schema_version is not the ratified "
                f"{PORTFOLIO_PAPER_HANDOFF_SCHEMA_VERSION}"
            )
        for name in (
            "snapshot_id",
            "episode_id",
            "feasibility_decision_id",
            "forecast_id",
            "portfolio_decision_id",
            "allocation_id",
            "reservation_plan_id",
            "reservation_id",
            "geometry_id",
            "instrument_version_id",
            "venue_instrument_id",
            "cohort_id",
            "native_symbol",
            "quote_currency",
            "portfolio_version",
            "panel_fingerprint",
            "capital_state_fingerprint",
            "exposure_fingerprint",
            "economic_candidate_id",
            "execution_candidate_id",
        ):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), field_name=name)
            )
        object.__setattr__(self, "direction", _require_direction(self.direction))
        object.__setattr__(
            self, "snapshot_cutoff", _require_utc(self.snapshot_cutoff, field_name="snapshot_cutoff")
        )
        object.__setattr__(
            self, "evaluation_time", _require_utc(self.evaluation_time, field_name="evaluation_time")
        )
        if not isinstance(self.bridge, ExecutionCandidateBridge):
            raise PortfolioPaperHandoffError("bridge must be an ExecutionCandidateBridge")
        for name in (
            "allocated_capital",
            "requested_reservation_amount",
            "requested_notional",
            "requested_quantity",
            "entry_reference",
        ):
            object.__setattr__(
                self, name, _require_non_negative(getattr(self, name), field_name=name)
            )
        if self.quantity_derivation != QUANTITY_DERIVATION_ALLOCATED_CAPITAL_OVER_ENTRY_REFERENCE:
            raise PortfolioPaperHandoffError(
                "quantity_derivation is not the declared rule"
            )
        # Capital and quantity are internally consistent by construction, so a
        # directly constructed handoff cannot carry zero capital, a fabricated
        # quantity or an unbacked notional.
        if self.allocated_capital <= 0 or self.entry_reference <= 0:
            raise PortfolioPaperHandoffError(
                "an actionable handoff requires positive capital and entry reference"
            )
        if abs(self.requested_reservation_amount - self.allocated_capital) > 1e-9:
            raise PortfolioPaperHandoffError(
                "requested_reservation_amount must equal the allocated capital"
            )
        expected_quantity = derive_quantity(
            allocated_capital=self.allocated_capital,
            entry_reference=self.entry_reference,
        )
        if abs(self.requested_quantity - expected_quantity) > 1e-9:
            raise PortfolioPaperHandoffError(
                "requested_quantity is not the declared derivation of the allocation"
            )
        if abs(self.requested_notional - self.requested_quantity * self.entry_reference) > 1e-9:
            raise PortfolioPaperHandoffError(
                "requested_notional must equal quantity times the entry reference"
            )
        if self.requested_notional > self.allocated_capital + 1e-9:
            raise PortfolioPaperHandoffError(
                "requested_notional exceeds the allocated capital"
            )
        # The bridged execution candidate must be the one this handoff names, and
        # the bridge must describe this handoff's own lineage.
        if self.bridge.execution_candidate_id != self.execution_candidate_id:
            raise PortfolioPaperHandoffError(
                "bridge execution candidate does not match the handoff"
            )
        if self.bridge.economic_candidate_id != self.economic_candidate_id:
            raise PortfolioPaperHandoffError(
                "bridge economic candidate does not match the handoff"
            )
        _require_bridge_matches_handoff(self.bridge, self)
        expected = _handoff_identity(self)
        if self.handoff_id == "":
            object.__setattr__(self, "handoff_id", expected)
        elif self.handoff_id != expected:
            raise PortfolioPaperHandoffError(
                "handoff_id does not match its content; build handoffs with "
                "build_portfolio_paper_handoffs"
            )

    @property
    def is_long(self) -> bool:
        return self.direction == "LONG"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "handoff_id": self.handoff_id,
            "snapshot_id": self.snapshot_id,
            "snapshot_cutoff": iso_z(self.snapshot_cutoff, field_name="snapshot_cutoff"),
            "detector_claim_id": self.detector_claim_id,
            "episode_id": self.episode_id,
            "feasibility_decision_id": self.feasibility_decision_id,
            "forecast_id": self.forecast_id,
            "portfolio_decision_id": self.portfolio_decision_id,
            "allocation_id": self.allocation_id,
            "reservation_plan_id": self.reservation_plan_id,
            "reservation_id": self.reservation_id,
            "geometry_id": self.geometry_id,
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "cohort_id": self.cohort_id,
            "native_symbol": self.native_symbol,
            "quote_currency": self.quote_currency,
            "direction": self.direction,
            "allocated_capital": self.allocated_capital,
            "requested_reservation_amount": self.requested_reservation_amount,
            "requested_notional": self.requested_notional,
            "requested_quantity": self.requested_quantity,
            "quantity_derivation": self.quantity_derivation,
            "entry_reference": self.entry_reference,
            "portfolio_version": self.portfolio_version,
            "panel_fingerprint": self.panel_fingerprint,
            "capital_state_fingerprint": self.capital_state_fingerprint,
            "exposure_fingerprint": self.exposure_fingerprint,
            "evaluation_time": iso_z(self.evaluation_time, field_name="evaluation_time"),
            "decision_context_id": self.decision_context_id,
            "economic_candidate_id": self.economic_candidate_id,
            "execution_candidate_id": self.execution_candidate_id,
            "bridge": self.bridge.to_dict(),
        }


def _require_bridge_matches_handoff(
    bridge: ExecutionCandidateBridge, handoff: PortfolioPaperHandoff
) -> None:
    for handoff_field, bridge_field in (
        ("episode_id", "episode_id"),
        ("instrument_version_id", "instrument_version_id"),
        ("venue_instrument_id", "venue_instrument_id"),
        ("direction", "direction"),
        ("snapshot_id", "snapshot_id"),
        ("feasibility_decision_id", "feasibility_decision_id"),
        ("forecast_id", "forecast_id"),
        ("geometry_id", "geometry_id"),
        ("portfolio_decision_id", "portfolio_decision_id"),
        ("allocation_id", "allocation_id"),
        ("portfolio_version", "portfolio_version"),
    ):
        if getattr(handoff, handoff_field) != getattr(bridge, bridge_field):
            raise PortfolioPaperHandoffError(
                f"bridge {bridge_field} does not match the handoff {handoff_field}"
            )
    if bridge.snapshot_cutoff != handoff.snapshot_cutoff:
        raise PortfolioPaperHandoffError(
            "bridge snapshot_cutoff does not match the handoff"
        )


def _handoff_identity(handoff: PortfolioPaperHandoff) -> str:
    return stable_hash(
        PORTFOLIO_PAPER_HANDOFF_ID_PREFIX,
        {
            "schema_version": handoff.schema_version,
            "snapshot_id": handoff.snapshot_id,
            "snapshot_cutoff": iso_z(handoff.snapshot_cutoff, field_name="snapshot_cutoff"),
            "detector_claim_id": handoff.detector_claim_id,
            "episode_id": handoff.episode_id,
            "feasibility_decision_id": handoff.feasibility_decision_id,
            "forecast_id": handoff.forecast_id,
            "portfolio_decision_id": handoff.portfolio_decision_id,
            "allocation_id": handoff.allocation_id,
            "reservation_plan_id": handoff.reservation_plan_id,
            "reservation_id": handoff.reservation_id,
            "geometry_id": handoff.geometry_id,
            "instrument_version_id": handoff.instrument_version_id,
            "venue_instrument_id": handoff.venue_instrument_id,
            "cohort_id": handoff.cohort_id,
            "native_symbol": handoff.native_symbol,
            "quote_currency": handoff.quote_currency,
            "direction": handoff.direction,
            "allocated_capital": handoff.allocated_capital,
            "requested_reservation_amount": handoff.requested_reservation_amount,
            "requested_notional": handoff.requested_notional,
            "requested_quantity": handoff.requested_quantity,
            "quantity_derivation": handoff.quantity_derivation,
            "entry_reference": handoff.entry_reference,
            "portfolio_version": handoff.portfolio_version,
            "panel_fingerprint": handoff.panel_fingerprint,
            "capital_state_fingerprint": handoff.capital_state_fingerprint,
            "exposure_fingerprint": handoff.exposure_fingerprint,
            "evaluation_time": iso_z(handoff.evaluation_time, field_name="evaluation_time"),
            "decision_context_id": handoff.decision_context_id,
            "economic_candidate_id": handoff.economic_candidate_id,
            "execution_candidate_id": handoff.execution_candidate_id,
        },
    )


def derive_quantity(*, allocated_capital: float, entry_reference: float) -> float:
    """The declared deterministic paper quantity for one allocation.

    Floors to ``QUANTITY_DECIMALS`` so the derived notional can never exceed the F7
    allocation. There is no second sizing policy and no post-F7 adjustment.
    """
    capital = _require_non_negative(allocated_capital, field_name="allocated_capital")
    reference = _require_non_negative(entry_reference, field_name="entry_reference")
    if reference <= 0:
        raise PortfolioPaperHandoffError("entry_reference must be positive")
    factor = 10**QUANTITY_DECIMALS
    return math.floor((capital / reference) * factor) / factor


def _normalize_symbol(value: Any) -> str:
    """Comparable instrument token: uppercase alphanumerics only."""
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def geometry_entry_reference(geometry: Any) -> float:
    """The geometry's own entry reference: the midpoint of its entry band.

    This is the one authoritative sizing price. Quantity is derived from it, never
    from a caller-supplied number, so a substituted reference cannot inflate the
    paper exposure a handoff describes.
    """
    low = float(geometry.entry_low)
    high = float(geometry.entry_high)
    return (low + high) / 2


def build_execution_candidate_bridge(
    *,
    candidate: Any,
    allocation: Any,
    geometry: Any,
    lineage: PaperExecutionLineage,
    portfolio_decision_id: str,
    portfolio_version: str,
    evaluation_time: datetime,
) -> ExecutionCandidateBridge:
    """Build the deterministic bridge for one selected allocation.

    Every cross-link is validated before the bridge is minted, so a mismatch in
    episode, instrument, venue, symbol, direction, cutoff, feasibility, forecast,
    geometry or portfolio version fails closed rather than being bridged.

    ``evaluation_time`` is the F7 decision's own instant. The lineage cutoff, the
    geometry cutoff and the geometry's evidence fingerprint must all belong to that
    observed epoch: evidence dated after the decision is look-ahead and is refused
    (OWNER Decision 8).
    """
    instant = _require_utc(evaluation_time, field_name="evaluation_time")
    if candidate.episode_id != allocation.episode_id:
        raise PortfolioPaperHandoffError(
            "allocation episode does not match the candidate episode"
        )
    if str(candidate.direction.value) != str(allocation.direction.value):
        raise PortfolioPaperHandoffError(
            "allocation direction does not match the candidate direction"
        )
    # Causality: evidence must not post-date the F7 decision that selected it.
    if lineage.snapshot_cutoff > instant:
        raise PortfolioPaperHandoffError(
            "lineage snapshot_cutoff is later than the F7 evaluation instant"
        )
    if geometry.source_cutoff > instant:
        raise PortfolioPaperHandoffError(
            "geometry source_cutoff is later than the F7 evaluation instant"
        )
    if geometry.source_cutoff != lineage.snapshot_cutoff:
        raise PortfolioPaperHandoffError(
            "geometry source_cutoff does not match the declared snapshot cutoff"
        )
    if geometry.instrument_version_id != lineage.instrument_version_id:
        raise PortfolioPaperHandoffError(
            "geometry instrument_version_id does not match the declared lineage"
        )
    if geometry.venue_instrument_id != lineage.venue_instrument_id:
        raise PortfolioPaperHandoffError(
            "geometry venue_instrument_id does not match the declared lineage"
        )
    if geometry.direction != str(candidate.direction.value):
        raise PortfolioPaperHandoffError(
            "geometry direction does not match the candidate direction"
        )
    # Instrument binding: the geometry, the candidate and the declared lineage must
    # all name the same instrument, so another market's geometry cannot bridge here.
    if _normalize_symbol(geometry.symbol) != _normalize_symbol(lineage.native_symbol):
        raise PortfolioPaperHandoffError(
            "geometry symbol does not match the declared lineage native symbol"
        )
    if _normalize_symbol(candidate.symbol) != _normalize_symbol(lineage.native_symbol):
        raise PortfolioPaperHandoffError(
            "candidate symbol does not match the declared lineage native symbol"
        )
    # A geometry that is not actionable (an explicit WAIT, or the invalid sentinel)
    # must never become an execution candidate.
    if not bool(getattr(geometry, "is_actionable", False)):
        raise PortfolioPaperHandoffError(
            "the execution geometry is not actionable; a wait is not an entry"
        )
    if candidate.forecast is None:
        raise PortfolioPaperHandoffError(
            "a selected candidate must carry a forecast before it can be bridged"
        )
    # Sizing is bound to the exact geometry, not to a caller-supplied number.
    reference = geometry_entry_reference(geometry)
    if abs(float(lineage.entry_reference) - reference) > 1e-9:
        raise PortfolioPaperHandoffError(
            "lineage entry_reference does not match the geometry entry reference"
        )
    # The F7 candidate's own stop risk must correspond to the exact geometry.
    _require_stop_fraction_matches_geometry(candidate, geometry)

    economic_candidate_id = str(candidate.candidate_id)
    execution_candidate_id = derive_execution_candidate_id(
        economic_candidate_id=economic_candidate_id,
        lineage_key={
            "episode_id": candidate.episode_id,
            "instrument_version_id": lineage.instrument_version_id,
            "venue_instrument_id": lineage.venue_instrument_id,
            "direction": str(candidate.direction.value),
            "snapshot_id": lineage.snapshot_id,
            "snapshot_cutoff": iso_z(lineage.snapshot_cutoff, field_name="snapshot_cutoff"),
            "geometry_id": geometry.geometry_id,
            "forecast_id": candidate.forecast.decision_id,
            "allocation_id": str(allocation.allocation_id),
            "portfolio_version": portfolio_version,
        },
    )
    return ExecutionCandidateBridge(
        schema_version=PORTFOLIO_EXECUTION_BRIDGE_SCHEMA_VERSION,
        economic_candidate_id=economic_candidate_id,
        execution_candidate_id=execution_candidate_id,
        episode_id=str(candidate.episode_id),
        instrument_version_id=lineage.instrument_version_id,
        venue_instrument_id=lineage.venue_instrument_id,
        direction=str(candidate.direction.value),
        snapshot_id=lineage.snapshot_id,
        snapshot_cutoff=lineage.snapshot_cutoff,
        feasibility_decision_id=str(candidate.feasibility_decision_id),
        forecast_id=str(candidate.forecast.decision_id),
        geometry_id=str(geometry.geometry_id),
        portfolio_decision_id=portfolio_decision_id,
        allocation_id=str(allocation.allocation_id),
        portfolio_version=portfolio_version,
    )


def _require_stop_fraction_matches_geometry(candidate: Any, geometry: Any) -> None:
    """The F7 risk input must be the exact geometry's stop risk."""
    fraction = float(candidate.stop_loss_fraction)
    if abs(fraction - float(geometry.stop_loss_fraction)) > 1e-9:
        raise PortfolioPaperHandoffError(
            "candidate stop_loss_fraction does not correspond to the supplied geometry"
        )


def require_reservation_covers_allocation(
    *, reservation: Any, allocated_capital: float
) -> None:
    """The plan's own reserved capital must cover the selected allocation.

    The F7 reservation stays a *plan*; this only proves the plan does not promise
    less than the allocation it authorized. A handoff must never enlarge capital
    beyond what F7 planned.
    """
    allocated = _require_non_negative(allocated_capital, field_name="allocated_capital")
    reserved = _require_non_negative(
        getattr(reservation, "reserved_capital", 0.0), field_name="reserved_capital"
    )
    if reserved + 1e-9 < allocated:
        raise PortfolioPaperHandoffError(
            "reservation does not cover the selected allocation"
        )


def build_portfolio_paper_handoffs(
    decision: PortfolioDecision,
    *,
    candidates: Mapping[str, Any],
    geometries: Mapping[str, Any],
    lineages: Mapping[str, PaperExecutionLineage],
    portfolio_version: str,
) -> tuple[PortfolioPaperHandoff, ...]:
    """Build one handoff per selected F7 allocation.

    Returns an empty tuple for ``CASH_NO_TRADE`` and ``INSUFFICIENT_EVIDENCE``: those
    are legitimate production outcomes that authorize no paper entry. Only
    ``SELECTED`` produces handoffs, and exactly one per selected allocation.
    """
    if not isinstance(decision, PortfolioDecision):
        raise PortfolioPaperHandoffError("decision must be a PortfolioDecision")
    if decision.status is not PortfolioStatus.SELECTED:
        # CASH_NO_TRADE and INSUFFICIENT_EVIDENCE authorize no execution candidate.
        return ()

    selected = decision.allocations
    if not selected:
        raise PortfolioPaperHandoffError("a SELECTED decision must carry an allocation")
    if decision.reservation_plan is None:
        raise PortfolioPaperHandoffError(
            "a SELECTED decision must carry a reservation plan"
        )
    plan = decision.reservation_plan
    if plan.portfolio_version != portfolio_version:
        raise PortfolioPaperHandoffError(
            "reservation plan portfolio version does not match the declared version"
        )
    reservations_by_candidate = {
        str(reservation.candidate_id): reservation for reservation in plan.reservations
    }

    handoffs: list[PortfolioPaperHandoff] = []
    for allocation in selected:
        candidate_id = str(allocation.candidate_id)
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise PortfolioPaperHandoffError(
                f"selected allocation {candidate_id} has no F7 candidate"
            )
        geometry = geometries.get(candidate_id)
        if geometry is None:
            raise PortfolioPaperHandoffError(
                f"selected allocation {candidate_id} has no execution geometry"
            )
        reservation = reservations_by_candidate.get(candidate_id)
        if reservation is None:
            raise PortfolioPaperHandoffError(
                f"selected allocation {candidate_id} is not covered by the reservation plan"
            )
        lineage = lineages.get(str(candidate.episode_id))
        if lineage is None:
            raise PortfolioPaperHandoffError(
                f"no declared execution lineage for episode {candidate.episode_id}"
            )
        # The allocation may not be enlarged: the plan's own reserved capital is
        # the ceiling, and the reservation may not promise less than the allocation.
        allocated = float(allocation.allocated_capital)
        require_reservation_covers_allocation(
            reservation=reservation, allocated_capital=allocated
        )
        if float(candidate.liquidity_capacity_notional) + 1e-9 < allocated:
            raise PortfolioPaperHandoffError(
                "allocation exceeds the candidate's liquidity capacity"
            )
        if str(reservation.direction.value) != str(allocation.direction.value):
            raise PortfolioPaperHandoffError(
                "reservation direction does not match the selected allocation"
            )

        bridge = build_execution_candidate_bridge(
            candidate=candidate,
            allocation=allocation,
            geometry=geometry,
            lineage=lineage,
            portfolio_decision_id=str(decision.decision_id),
            portfolio_version=portfolio_version,
            evaluation_time=decision.evaluation_time,
        )
        # Sizing is bound to the geometry, not to a caller-supplied number.
        entry_reference = geometry_entry_reference(geometry)
        quantity = derive_quantity(
            allocated_capital=allocated, entry_reference=entry_reference
        )
        notional = quantity * entry_reference
        if notional > allocated + 1e-9:
            raise PortfolioPaperHandoffError(
                "derived notional exceeds the selected allocation"
            )
        handoffs.append(
            PortfolioPaperHandoff(
                schema_version=PORTFOLIO_PAPER_HANDOFF_SCHEMA_VERSION,
                snapshot_id=lineage.snapshot_id,
                snapshot_cutoff=lineage.snapshot_cutoff,
                detector_claim_id=lineage.detector_claim_id,
                episode_id=str(candidate.episode_id),
                feasibility_decision_id=str(candidate.feasibility_decision_id),
                forecast_id=str(candidate.forecast.decision_id),
                portfolio_decision_id=str(decision.decision_id),
                allocation_id=str(allocation.allocation_id),
                reservation_plan_id=str(plan.plan_id),
                reservation_id=str(reservation.reservation_id),
                geometry_id=str(geometry.geometry_id),
                instrument_version_id=lineage.instrument_version_id,
                venue_instrument_id=lineage.venue_instrument_id,
                cohort_id=lineage.cohort_id,
                native_symbol=lineage.native_symbol,
                quote_currency=lineage.quote_currency,
                direction=str(candidate.direction.value),
                allocated_capital=allocated,
                requested_reservation_amount=allocated,
                requested_notional=notional,
                requested_quantity=quantity,
                quantity_derivation=QUANTITY_DERIVATION_ALLOCATED_CAPITAL_OVER_ENTRY_REFERENCE,
                entry_reference=entry_reference,
                portfolio_version=portfolio_version,
                panel_fingerprint=str(decision.panel_fingerprint or ""),
                capital_state_fingerprint=str(decision.capital_state_fingerprint or ""),
                exposure_fingerprint=str(decision.exposure_fingerprint or ""),
                evaluation_time=decision.evaluation_time,
                decision_context_id=lineage.decision_context_id,
                economic_candidate_id=bridge.economic_candidate_id,
                execution_candidate_id=bridge.execution_candidate_id,
                bridge=bridge,
            )
        )
    # Deterministic order independent of the caller's mapping order.
    handoffs.sort(key=lambda item: item.execution_candidate_id)
    return tuple(handoffs)


__all__ = [
    "OPIPC_CANDIDATE_ID_PREFIX",
    "PORTFOLIO_EXECUTION_BRIDGE_ID_PREFIX",
    "PORTFOLIO_EXECUTION_BRIDGE_SCHEMA_VERSION",
    "PORTFOLIO_PAPER_HANDOFF_ID_PREFIX",
    "PORTFOLIO_PAPER_HANDOFF_SCHEMA_VERSION",
    "QUANTITY_DECIMALS",
    "QUANTITY_DERIVATION_ALLOCATED_CAPITAL_OVER_ENTRY_REFERENCE",
    "SUPPORTED_HANDOFF_DIRECTIONS",
    "ExecutionCandidateBridge",
    "PaperExecutionLineage",
    "PortfolioPaperHandoff",
    "PortfolioPaperHandoffError",
    "build_execution_candidate_bridge",
    "build_portfolio_paper_handoffs",
    "derive_execution_candidate_id",
    "derive_quantity",
    "require_reservation_covers_allocation",
]
