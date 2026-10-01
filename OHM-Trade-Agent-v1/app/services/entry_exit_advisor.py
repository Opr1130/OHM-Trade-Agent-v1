"""Legacy entry/exit advisor, now a thin wrapper over the single R4-B0 kernel.

OWNER Decision 3 (R4-B0) established exactly ONE deterministic entry/exit geometry
owner: ``app.opip.execution_geometry``. This module no longer computes geometry. It
resolves the legacy call shape (a ``MarketSnapshot`` plus an optional direction),
delegates to the single pure kernel, and reconstructs its historical
``EntryExitPlan`` return value.

Why a wrapper rather than a deletion
------------------------------------
``EntryExitPlan`` is imported widely by the legacy scan, paper engines, gates and
tests. R4-B0 does not retire legacy authority, so the public shape is preserved
byte-for-byte. Because both paths execute the same kernel, legacy and target
geometry cannot diverge, and no formula is duplicated.

Defect preservation
-------------------
If the legacy behavior has a defect it is preserved here rather than silently
redesigned. Any suspected defect is recorded as a deferred discovery in the
R4-B0 ATDD contract instead of being changed in this increment.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.opip.execution_geometry import (
    TARGET_1_MIN_RR_MULTIPLE,
    GeometryKernelResult,
    compute_geometry_kernel,
)
from app.scanner.models import MarketSnapshot

__all__ = [
    "EntryExitPlan",
    "TARGET_1_MIN_RR_MULTIPLE",
    "build_entry_exit_plan",
]


@dataclass
class EntryExitPlan:
    symbol: str
    valid_now: bool
    entry_style: str

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
    direction: str = "LONG"


def _plan_from_kernel(symbol: str, result: GeometryKernelResult) -> EntryExitPlan:
    return EntryExitPlan(
        symbol=symbol,
        valid_now=result.valid_now,
        entry_style=result.entry_style,
        entry_low=result.entry_low,
        entry_high=result.entry_high,
        chase_limit=result.chase_limit,
        stop_price=result.stop_price,
        target_1=result.target_1,
        target_2=result.target_2,
        reward_to_risk_1=result.reward_to_risk_1,
        reward_to_risk_2=result.reward_to_risk_2,
        risk_level=result.risk_level,
        reason=result.reason,
        direction=result.direction,
    )


def build_entry_exit_plan(
    snapshot: MarketSnapshot,
    risk_level: str,
    direction: str | None = None,
) -> EntryExitPlan:
    """Resolve the legacy inputs and delegate to the single geometry kernel.

    ``direction`` historically defaulted to ``snapshot.trade_direction`` and then
    to ``LONG``; that resolution is preserved here because it is call-shape, not
    geometry. The kernel remains the sole owner of the geometry itself.
    """
    resolved_direction = (direction or snapshot.trade_direction or "LONG").upper()
    result = compute_geometry_kernel(
        symbol=snapshot.symbol,
        risk_level=risk_level,
        direction=resolved_direction,
        last_price=float(snapshot.last_price),
        atr=float(snapshot.atr),
        ema20=float(snapshot.ema20),
        upside_p75_pct=float(getattr(snapshot, "rolling_24h_upside_p75_pct", 0.0) or 0.0),
        downside_p75_pct=float(
            getattr(snapshot, "rolling_24h_downside_p75_pct", 0.0) or 0.0
        ),
    )
    return _plan_from_kernel(snapshot.symbol, result)


def build_geometry_input(snapshot: MarketSnapshot, *, risk_level: str, direction: str | None = None):
    """Build the minimal typed geometry input from a legacy snapshot.

    Provided so a transitional caller can obtain the canonical
    ``ExecutionGeometryInput`` without reaching into the kernel directly. It
    performs no market read.
    """
    from app.opip.contracts.execution_geometry import ExecutionGeometryInput

    resolved_direction = (direction or snapshot.trade_direction or "LONG").upper()
    return ExecutionGeometryInput(
        symbol=snapshot.symbol,
        direction=resolved_direction,
        risk_level=risk_level,
        last_price=float(snapshot.last_price),
        atr=float(snapshot.atr),
        ema20=float(snapshot.ema20),
        rolling_24h_upside_p75_pct=float(
            getattr(snapshot, "rolling_24h_upside_p75_pct", 0.0) or 0.0
        ),
        rolling_24h_downside_p75_pct=float(
            getattr(snapshot, "rolling_24h_downside_p75_pct", 0.0) or 0.0
        ),
    )
