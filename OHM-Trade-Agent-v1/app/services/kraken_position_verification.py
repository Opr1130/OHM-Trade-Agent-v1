from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.exchanges.kraken_private import KrakenPrivateClient
from app.services.active_trade_registry import ActiveTrade
from app.services.kraken_reconciliation import _balance_for_asset, _base_asset, _canonical_pair


@dataclass(frozen=True)
class PositionVerification:
    status: str
    reason: str
    observed_quantity: float | None = None

    @property
    def verified(self) -> bool:
        return self.status == "VERIFIED"


def _open_position_quantity(
    positions: dict[str, dict[str, Any]],
    *,
    wanted_pair: str,
    side: str,
) -> tuple[float | None, str | None]:
    remaining, position_error = _open_position_quantity(
        positions,
        wanted_pair=wanted_pair,
        side="sell",
    )
    if position_error:
        return PositionVerification(
            status="UNAVAILABLE",
            reason=f"Kraken short quantity for {trade.symbol} is {position_error.lower()}",
        )
    if remaining <= 0:
        return PositionVerification(
            status="ABSENT",
            reason=f"No open Kraken short position exists for {trade.symbol}",
            observed_quantity=remaining,
        )
    return PositionVerification(
        status="VERIFIED",
        reason=f"Kraken reports an open short position for {trade.symbol}",
        observed_quantity=remaining,
    )


class KrakenPositionVerifier:
    """Read-only account gate for actionable active-trade notifications.

    The local active-trade registry is operational state, not proof that Kraken
    still has exposure. Fetch account state once per monitor run and require a
    verified balance/position before sending HOLD, EXIT, or emergency alerts.
    """

    def __init__(self, client: KrakenPrivateClient | None = None) -> None:
        self.client = client or KrakenPrivateClient()
        self._balances: dict[str, float] | None = None
        self._positions: dict[str, dict[str, Any]] | None = None
        self._unavailable_reason = ""

    def refresh(self) -> None:
        if not self.client.enabled:
            self._unavailable_reason = "Kraken private credentials are not configured"
            return
        try:
            self.client.assert_read_only()
            self._balances = self.client.get_balance()
            self._positions = self.client.get_open_positions()
        except Exception as exc:
            # Any malformed/private-account response must fail closed. The
            # monitor runner will surface this as verification unavailable and
            # must not turn uncertain account state into an actionable alert.
            self._balances = None
            self._positions = None
            self._unavailable_reason = str(exc)

    def verify(self, trade: ActiveTrade) -> PositionVerification:
        if self._balances is None or self._positions is None:
            return PositionVerification(
                status="UNAVAILABLE",
                reason=self._unavailable_reason or "Kraken account state was not loaded",
            )
        return verify_trade_against_snapshot(
            trade,
            balances=self._balances,
            positions=self._positions,
        )
