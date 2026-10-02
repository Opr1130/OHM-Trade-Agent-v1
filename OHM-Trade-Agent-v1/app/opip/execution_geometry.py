"""R4-B0 single pure execution-geometry kernel.

OWNER Decision 3 requires exactly ONE deterministic entry/exit geometry owner. The
mathematics in this module is the legacy ``app.services.entry_exit_advisor``
computation, moved verbatim behind a primitive-typed boundary. No formula,
constant, threshold or heuristic is changed: the legacy advisor now delegates
here, so legacy and target paths run the *same* code and cannot diverge.

Purity
------
The kernel is a pure function of primitives. It performs no I/O, reads no clock,
holds no global mutable state and imports no market client, so it can be replayed
from retained evidence.

Scope
-----
Geometry only. It does not rank, size, allocate, forecast or gate. Its output is
consumed by F7 (candidate risk) and handed unchanged to Paper-v2 execution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from app.opip.contracts.execution_geometry import (
    EXECUTION_GEOMETRY_POLICY_VERSION,
    EXECUTION_GEOMETRY_SCHEMA_VERSION,
    EXECUTION_GEOMETRY_ID_PREFIX,
    ExecutionGeometry,
    ExecutionGeometryContractError,
    ExecutionGeometryInput,
    require_geometry_text,
    require_geometry_utc,
)
from app.opip.contracts.serialization import iso_z, stable_hash

#: Unchanged from the legacy advisor: keeps a floor so a quiet symbol never gets
#: a target so close it is immediately eaten by noise/fees.
TARGET_1_MIN_RR_MULTIPLE = 1.2

#: Unchanged legacy ATR/EMA multipliers, preserved exactly.
_LOW_STOP_ATR = 1.5
_LOW_CHASE_ATR = 0.5
_LOW_PULLBACK_ATR = 0.0
_MEDIUM_STOP_ATR = 1.75
_MEDIUM_CHASE_ATR = 0.75
_MEDIUM_PULLBACK_ATR = 0.25
_TARGET_1_MULTIPLE = 2.0
_TARGET_2_MULTIPLE = 3.0
_EXTENSION_ATR = 1.25

_INVALID_REASON = "Invalid or non-positive market geometry; no entry is authorized."
_INVALID_GEOMETRY_REASON = "ATR-derived plan geometry is invalid; no entry is authorized."


@dataclass(frozen=True)
class GeometryKernelResult:
    """The primitive result of one geometry evaluation.

    Mirrors the legacy ``EntryExitPlan`` field-for-field (including ``reason``), so
    the legacy wrapper can reconstruct its object exactly and parity is proven by
    construction rather than by a duplicated second implementation.
    """

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
    reason: str
    direction: str
    entry_reference: float

    @property
    def stop_loss_fraction(self) -> float:
        """Fraction of the entry reference at risk at the stop.

        ``0.0`` exactly when the geometry is the invalid sentinel (``stop_price``
        zero), so an invalid geometry can never be read as allocatable.
        """
        if self.entry_reference <= 0 or self.stop_price <= 0:
            return 0.0
        fraction = abs(self.entry_reference - self.stop_price) / self.entry_reference
        # A pathological ATR can imply risk beyond the whole position; clamp to
        # the contract's [0, 1] domain without ever inventing a favorable value.
        return min(1.0, fraction)


def _realistic_target_1_multiple(
    mechanical_multiple: float,
    percentile_move_pct: float,
    entry_reference: float,
    risk_per_unit: float,
) -> float:
    """Cap (never raise) Target 1 to a historically plausible excursion."""
    values = (mechanical_multiple, percentile_move_pct, entry_reference, risk_per_unit)
    if not all(math.isfinite(float(value)) for value in values):
        return mechanical_multiple
    if risk_per_unit <= 0 or entry_reference <= 0 or percentile_move_pct <= 0:
        return mechanical_multiple
    percentile_price_move = entry_reference * (percentile_move_pct / 100)
    percentile_multiple = percentile_price_move / risk_per_unit
    return max(
        TARGET_1_MIN_RR_MULTIPLE,
        min(mechanical_multiple, percentile_multiple),
    )


def _geometry_is_valid(
    *,
    direction: str,
    entry_low: float,
    entry_high: float,
    chase_limit: float,
    entry_reference: float,
    stop_price: float,
    target_1: float,
    target_2: float,
    risk_per_unit: float,
) -> bool:
    values = (
        entry_low,
        entry_high,
        chase_limit,
        entry_reference,
        stop_price,
        target_1,
        target_2,
        risk_per_unit,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    if any(float(value) <= 0 for value in values):
        return False
    if entry_low > entry_high or risk_per_unit <= 0:
        return False
    if direction == "LONG":
        return (
            stop_price < entry_reference
            and target_1 > entry_reference
            and target_2 > target_1
            and chase_limit >= entry_high
        )
    return (
        stop_price > entry_reference
        and 0 < target_2 < target_1 < entry_reference
        and 0 < chase_limit <= entry_low
    )


def _invalid_kernel_result(
    *,
    symbol: str,
    risk_level: str,
    direction: str,
    reason: str,
    last_price: float,
) -> GeometryKernelResult:
    price = float(last_price) if math.isfinite(float(last_price)) else 0.0
    price = max(0.0, price)
    return GeometryKernelResult(
        entry_style="wait",
        valid_now=False,
        entry_low=price,
        entry_high=price,
        chase_limit=price,
        # Zero-valued risk geometry is an explicit invalid sentinel.
        stop_price=0.0,
        target_1=0.0,
        target_2=0.0,
        reward_to_risk_1=0.0,
        reward_to_risk_2=0.0,
        risk_level=risk_level,
        reason=reason,
        direction=direction,
        entry_reference=0.0,
    )


def compute_geometry_kernel(
    *,
    symbol: str,
    risk_level: str,
    direction: str,
    last_price: float,
    atr: float,
    ema20: float,
    upside_p75_pct: float = 0.0,
    downside_p75_pct: float = 0.0,
) -> GeometryKernelResult:
    """The one pure geometry computation. Moved verbatim from the legacy advisor.

    ``direction`` must already be resolved (the legacy wrapper applies its own
    ``snapshot.trade_direction`` default before calling).
    """
    if risk_level not in {"low", "medium"}:
        raise ValueError("Entry/exit plans are only supported for low or medium risk")
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

    price = float(last_price)
    atr_value = float(atr)
    ema20_value = float(ema20)

    if (
        not math.isfinite(price)
        or not math.isfinite(atr_value)
        or not math.isfinite(ema20_value)
        or price <= 0
        or ema20_value <= 0
        or atr_value <= 0
    ):
        return _invalid_kernel_result(
            symbol=symbol,
            risk_level=risk_level,
            direction=direction,
            reason=_INVALID_REASON,
            last_price=last_price,
        )

    if risk_level == "low":
        stop_distance = atr_value * _LOW_STOP_ATR
        target_1_multiple = _TARGET_1_MULTIPLE
        target_2_multiple = _TARGET_2_MULTIPLE
        chase_atr = _LOW_CHASE_ATR
        pullback_atr = _LOW_PULLBACK_ATR
    else:
        stop_distance = atr_value * _MEDIUM_STOP_ATR
        target_1_multiple = _TARGET_1_MULTIPLE
        target_2_multiple = _TARGET_2_MULTIPLE
        chase_atr = _MEDIUM_CHASE_ATR
        pullback_atr = _MEDIUM_PULLBACK_ATR

    if direction == "LONG":
        entry_low = min(ema20_value, price - (atr_value * pullback_atr))
        entry_high = price
        chase_limit = price + (atr_value * chase_atr)
        entry_reference = (entry_low + entry_high) / 2
        stop_price = entry_reference - stop_distance
        risk_per_unit = entry_reference - stop_price
        target_1_multiple_effective = _realistic_target_1_multiple(
            target_1_multiple,
            upside_p75_pct,
            entry_reference,
            risk_per_unit,
        )
        target_1 = entry_reference + risk_per_unit * target_1_multiple_effective
        target_2 = entry_reference + risk_per_unit * target_2_multiple
        too_extended = price > ema20_value + (atr_value * _EXTENSION_ATR)
        if too_extended:
            entry_style = "wait_for_pullback"
            valid_now = False
            reason = (
                "Price is extended above EMA20 relative to ATR. "
                "Wait for a pullback instead of chasing."
            )
        elif price >= ema20_value:
            entry_style = "pullback_or_retest"
            valid_now = True
            reason = (
                "Trend structure supports a controlled pullback or retest entry "
                "with ATR-based risk."
            )
        else:
            entry_style = "wait"
            valid_now = False
            reason = "Price is below EMA20; wait for trend recovery."
    else:
        # Shorts seek a controlled rebound/retest rather than chasing a dump.
        entry_low = price
        entry_high = max(ema20_value, price + (atr_value * pullback_atr))
        chase_limit = price - (atr_value * chase_atr)
        entry_reference = (entry_low + entry_high) / 2
        stop_price = entry_reference + stop_distance
        risk_per_unit = stop_price - entry_reference
        target_1_multiple_effective = _realistic_target_1_multiple(
            target_1_multiple,
            downside_p75_pct,
            entry_reference,
            risk_per_unit,
        )
        target_1 = entry_reference - risk_per_unit * target_1_multiple_effective
        target_2 = entry_reference - risk_per_unit * target_2_multiple
        too_extended = price < ema20_value - (atr_value * _EXTENSION_ATR)
        if too_extended:
            entry_style = "wait_for_rebound"
            valid_now = False
            reason = (
                "Price is extended below EMA20 relative to ATR. "
                "Wait for a rebound/retest instead of chasing the breakdown."
            )
        elif price <= ema20_value:
            entry_style = "rebound_or_retest"
            valid_now = True
            reason = (
                "Bearish structure supports a controlled rebound/retest short "
                "with ATR-based risk."
            )
        else:
            entry_style = "wait"
            valid_now = False
            reason = "Price is above EMA20; wait for bearish structure to recover."

    geometry_valid = _geometry_is_valid(
        direction=direction,
        entry_low=entry_low,
        entry_high=entry_high,
        chase_limit=chase_limit,
        entry_reference=entry_reference,
        stop_price=stop_price,
        target_1=target_1,
        target_2=target_2,
        risk_per_unit=risk_per_unit,
    )
    if not geometry_valid:
        valid_now = False
        entry_style = "wait"
        reason = _INVALID_GEOMETRY_REASON

    rr1 = abs(target_1 - entry_reference) / risk_per_unit if risk_per_unit > 0 else 0
    rr2 = abs(target_2 - entry_reference) / risk_per_unit if risk_per_unit > 0 else 0

    return GeometryKernelResult(
        entry_style=entry_style,
        valid_now=valid_now,
        entry_low=round(min(entry_low, entry_high), 8),
        entry_high=round(max(entry_low, entry_high), 8),
        chase_limit=round(chase_limit, 8),
        stop_price=round(stop_price, 8),
        target_1=round(target_1, 8),
        target_2=round(target_2, 8),
        reward_to_risk_1=round(rr1, 2),
        reward_to_risk_2=round(rr2, 2),
        risk_level=risk_level,
        reason=reason,
        direction=direction,
        entry_reference=entry_reference,
    )


def build_execution_geometry(
    geometry_input: ExecutionGeometryInput,
    *,
    instrument_version_id: str,
    venue_instrument_id: str,
    source_cutoff: datetime,
    source_evidence_fingerprint: str,
) -> ExecutionGeometry:
    """Build the canonical, identity-stamped ``ExecutionGeometry``.

    The identity is derived from the geometry and its provenance, so the same
    inputs always produce the same identity. The evidence epoch is explicit
    (OWNER Decision 8): a geometry is always bound to the instrument, cutoff and
    evidence fingerprint it was computed from.
    """
    if not isinstance(geometry_input, ExecutionGeometryInput):
        raise ExecutionGeometryContractError(
            "geometry_input must be an ExecutionGeometryInput"
        )
    kernel = compute_geometry_kernel(
        symbol=geometry_input.symbol,
        risk_level=geometry_input.risk_level,
        direction=geometry_input.direction,
        last_price=geometry_input.last_price,
        atr=geometry_input.atr,
        ema20=geometry_input.ema20,
        upside_p75_pct=geometry_input.rolling_24h_upside_p75_pct,
        downside_p75_pct=geometry_input.rolling_24h_downside_p75_pct,
    )
    cutoff = require_geometry_utc(source_cutoff, field_name="source_cutoff")
    instrument = require_geometry_text(
        instrument_version_id, field_name="instrument_version_id"
    )
    venue = require_geometry_text(
        venue_instrument_id, field_name="venue_instrument_id"
    )
    fingerprint = require_geometry_text(
        source_evidence_fingerprint, field_name="source_evidence_fingerprint"
    )
    # The identity is a function of exactly the facts below, so it is computed
    # once, here, and stamped onto the single immutable record.
    identity = stable_hash(
        EXECUTION_GEOMETRY_ID_PREFIX,
        {
            "schema_version": EXECUTION_GEOMETRY_SCHEMA_VERSION,
            "policy_version": EXECUTION_GEOMETRY_POLICY_VERSION,
            "instrument_version_id": instrument,
            "venue_instrument_id": venue,
            "symbol": geometry_input.symbol,
            "direction": kernel.direction,
            "source_cutoff": iso_z(cutoff, field_name="source_cutoff"),
            "source_evidence_fingerprint": fingerprint,
            "entry_style": kernel.entry_style,
            "valid_now": kernel.valid_now,
            "entry_low": kernel.entry_low,
            "entry_high": kernel.entry_high,
            "chase_limit": kernel.chase_limit,
            "stop_price": kernel.stop_price,
            "target_1": kernel.target_1,
            "target_2": kernel.target_2,
            "reward_to_risk_1": kernel.reward_to_risk_1,
            "reward_to_risk_2": kernel.reward_to_risk_2,
            "risk_level": kernel.risk_level,
            "stop_loss_fraction": kernel.stop_loss_fraction,
            "reason": kernel.reason,
        },
    )
    return ExecutionGeometry(
        geometry_id=identity,
        schema_version=EXECUTION_GEOMETRY_SCHEMA_VERSION,
        policy_version=EXECUTION_GEOMETRY_POLICY_VERSION,
        instrument_version_id=instrument,
        venue_instrument_id=venue,
        symbol=geometry_input.symbol,
        direction=kernel.direction,
        source_cutoff=cutoff,
        source_evidence_fingerprint=fingerprint,
        entry_style=kernel.entry_style,
        valid_now=kernel.valid_now,
        entry_low=kernel.entry_low,
        entry_high=kernel.entry_high,
        chase_limit=kernel.chase_limit,
        stop_price=kernel.stop_price,
        target_1=kernel.target_1,
        target_2=kernel.target_2,
        reward_to_risk_1=kernel.reward_to_risk_1,
        reward_to_risk_2=kernel.reward_to_risk_2,
        risk_level=kernel.risk_level,
        stop_loss_fraction=kernel.stop_loss_fraction,
        reason=kernel.reason,
    )


__all__ = [
    "GeometryKernelResult",
    "TARGET_1_MIN_RR_MULTIPLE",
    "build_execution_geometry",
    "compute_geometry_kernel",
]
