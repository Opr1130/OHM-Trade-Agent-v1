import math
from dataclasses import dataclass

from app.exchanges.kraken import KrakenClient
from app.services.entry_timing import evaluate_entry_timing
from app.services.pending_setup_registry import PendingSetup


@dataclass
class PendingSetupMonitorResult:
    symbol: str
    status: str
    current_price: float
    reason: str
    timing_score: int | None = None


def monitor_pending_setup(
    setup: PendingSetup,
) -> PendingSetupMonitorResult:
    client = KrakenClient()
    ticker = client.get_ticker(setup.symbol)
    current = float(ticker["last"])
    direction = (setup.direction or "LONG").upper()

    if not math.isfinite(current) or current <= 0:
        raise ValueError(f"{setup.symbol}: current ticker price must be finite and positive")

    # Invalidation is direction-aware and always wins over timing advice. The
    # stop boundary itself is invalid: equality is not a safe waiting state.
    if direction == "SHORT" and current >= setup.stop_price:
        return PendingSetupMonitorResult(
            symbol=setup.symbol,
            status="INVALIDATED",
            current_price=round(current, 8),
            reason="Price reached or moved above the planned short invalidation level.",
            timing_score=0,
        )
    if direction != "SHORT" and current <= setup.stop_price:
        return PendingSetupMonitorResult(
            symbol=setup.symbol,
            status="INVALIDATED",
            current_price=round(current, 8),
            reason="Price reached or moved below the planned invalidation level.",
            timing_score=0,
        )

    timing = evaluate_entry_timing(
        direction=direction,
        current_price=current,
        entry_low=setup.entry_low,
        entry_high=setup.entry_high,
        chase_limit=setup.chase_limit,
    )

    if timing.action == "DO_NOT_CHASE":
        return PendingSetupMonitorResult(
            symbol=setup.symbol,
            status="TOO_EXTENDED",
            current_price=round(current, 8),
            reason=timing.reason,
            timing_score=timing.score,
        )

    if setup.entry_low <= current <= setup.entry_high:
        return PendingSetupMonitorResult(
            symbol=setup.symbol,
            status="ENTRY_ZONE_REACHED",
            current_price=round(current, 8),
            reason=f"Price entered the approved entry zone. {timing.reason}",
            timing_score=timing.score,
        )

    # A move on the chase side of the zone is near-entry; the opposite side is
    # still waiting for a retest/rebound. This is intentionally directional.
    near_entry = (
        direction == "SHORT" and setup.chase_limit <= current < setup.entry_low
    ) or (
        direction != "SHORT" and setup.entry_high < current <= setup.chase_limit
    )
    if near_entry:
        return PendingSetupMonitorResult(
            symbol=setup.symbol,
            status="NEAR_ENTRY",
            current_price=round(current, 8),
            reason=timing.reason,
            timing_score=timing.score,
        )

    return PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="WAITING",
        current_price=round(current, 8),
        reason=timing.reason,
        timing_score=timing.score,
    )
