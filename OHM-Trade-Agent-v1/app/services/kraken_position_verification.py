from __future__ import annotations

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
            self._unavailable_reason = str(exc)

    def verify(self, trade: ActiveTrade) -> PositionVerification:
        if self._balances is None or self._positions is None:
            return PositionVerification(
                status="UNAVAILABLE",
                reason=self._unavailable_reason or "Kraken account state was not loaded",
            )

        direction = (trade.direction or "LONG").upper()
        if direction == "SHORT":
            return self._verify_short(trade)
        return self._verify_spot_long(trade)

    def _verify_spot_long(self, trade: ActiveTrade) -> PositionVerification:
        asset = _base_asset(trade.symbol)
        quantity = _balance_for_asset(self._balances or {}, asset)
        if quantity is None:
            return PositionVerification(
                status="UNAVAILABLE",
                reason=f"Could not resolve the base asset for {trade.symbol}",
            )

        # When OHM knows planned capital, ignore residual dust below 1% of the
        # expected entry quantity. Legacy records without capital still require
        # a strictly positive balance; zero is never a valid active position.
        expected = None
        if trade.capital is not None and trade.capital > 0 and trade.entry_price > 0:
            expected = (
                trade.capital
                * max(1.0, float(trade.margin_leverage or 1.0))
                / trade.entry_price
            )
        minimum = expected * 0.01 if expected is not None else 0.0
        if quantity <= minimum:
            return PositionVerification(
                status="ABSENT",
                reason=f"No meaningful {asset} balance exists on Kraken",
                observed_quantity=quantity,
            )
        return PositionVerification(
            status="VERIFIED",
            reason=f"Kraken reports a positive {asset} balance",
            observed_quantity=quantity,
        )

    def _verify_short(self, trade: ActiveTrade) -> PositionVerification:
        wanted_pair = _canonical_pair(trade.symbol)
        remaining = 0.0
        for row in (self._positions or {}).values():
            if not isinstance(row, dict):
                continue
            descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
            pair = row.get("pair") or descr.get("pair") or ""
            side = str(row.get("type") or descr.get("type") or "").lower()
            if _canonical_pair(str(pair or "")) != wanted_pair or side != "sell":
                continue
            try:
                volume = float(row.get("vol") or 0)
                closed = float(row.get("vol_closed") or 0)
            except (TypeError, ValueError):
                continue
            remaining += max(0.0, volume - closed)

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
