from dataclasses import dataclass

from app.exchanges.kraken import KrakenClient
from app.services.pending_setup_registry import PendingSetup


@dataclass
class PendingSetupMonitorResult:
    symbol: str
    status: str
    current_price: float
    reason: str


def monitor_pending_setup(
    setup: PendingSetup,
) -> PendingSetupMonitorResult:
    client = KrakenClient()
    ticker = client.get_ticker(setup.symbol)
    current = ticker["last"]

    # Ideal approved entry zone
    if setup.entry_low <= current <= setup.entry_high:
        return PendingSetupMonitorResult(
            symbol=setup.symbol,
            status="ENTRY_ZONE_REACHED",
            current_price=round(current, 8),
            reason="Price has entered the approved entry zone.",
        )

    # Slightly above ideal entry, but still below chase limit
    if setup.entry_high < current <= setup.chase_limit:
        return PendingSetupMonitorResult(
            symbol=setup.symbol,
            status="NEAR_ENTRY",
            current_price=round(current, 8),
            reason=(
                "Price is above the ideal entry zone but remains "
                "below the chase limit. Wait for the approved entry."
            ),
        )

    # Too expensive to chase
    if current > setup.chase_limit:
        return PendingSetupMonitorResult(
            symbol=setup.symbol,
            status="TOO_EXTENDED",
            current_price=round(current, 8),
            reason="Price moved above the approved chase limit. Do not chase.",
        )

    # Setup has broken down
    if current < setup.stop_price:
        return PendingSetupMonitorResult(
            symbol=setup.symbol,
            status="INVALIDATED",
            current_price=round(current, 8),
            reason="Price moved below the planned invalidation level.",
        )

    # Below entry zone but setup remains valid
    return PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="WAITING",
        current_price=round(current, 8),
        reason="Waiting for price to reach the approved entry zone.",
    )
