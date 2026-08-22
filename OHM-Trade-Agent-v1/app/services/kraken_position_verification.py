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

        direction = (trade.direction or "LONG").upper()
        if direction not in {"LONG", "SHORT"}:
            return PositionVerification(
                status="UNAVAILABLE",
                reason=f"Unsupported active-trade direction {direction!r}",
            )
        if direction == "SHORT":
            return self._verify_short(trade)
        return self._verify_spot_long(trade)

    def _verify_spot_long(self, trade: ActiveTrade) -> PositionVerification:
        asset = _base_asset(trade.symbol)
        quantity = _balance_for_asset(self._balances or {}, asset)
        if quantity is None or not math.isfinite(float(quantity)) or quantity < 0:
            return PositionVerification(
                status="UNAVAILABLE",
                reason=f"Kraken balance for {trade.symbol} is unresolved or non-finite",
            )

        # When OHM knows planned capital, ignore residual dust below 1% of the
        # expected entry quantity. Legacy records without capital still require
        # a strictly positive balance; zero is never a valid active position.
        expected = None
        if trade.capital is not None:
            try:
                capital = float(trade.capital)
                leverage = float(trade.margin_leverage or 1.0)
                entry = float(trade.entry_price)
            except (TypeError, ValueError):
                return PositionVerification(
                    status="UNAVAILABLE",
                    reason=f"Active-trade sizing state for {trade.symbol} is malformed",
                )
            if not all(math.isfinite(value) for value in (capital, leverage, entry)):
                return PositionVerification(
                    status="UNAVAILABLE",
                    reason=f"Active-trade sizing state for {trade.symbol} is non-finite",
                )
            if capital < 0 or leverage <= 0 or entry <= 0:
                return PositionVerification(
                    status="UNAVAILABLE",
                    reason=f"Active-trade sizing state for {trade.symbol} is invalid",
                )
            if capital > 0:
                expected = capital * leverage / entry
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
        if not wanted_pair:
            return PositionVerification(
                status="UNAVAILABLE",
                reason=f"Could not canonicalize {trade.symbol}",
            )

        remaining = 0.0
        matched = False
        for row in (self._positions or {}).values():
            if not isinstance(row, dict):
                continue
            descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
            pair = row.get("pair") or descr.get("pair") or ""
            side = str(row.get("type") or descr.get("type") or "").lower()
            if _canonical_pair(str(pair or "")) != wanted_pair or side != "sell":
                continue
            matched = True
            try:
                volume = float(row.get("vol") or 0)
                closed = float(row.get("vol_closed") or 0)
            except (TypeError, ValueError):
                return PositionVerification(
                    status="UNAVAILABLE",
                    reason=f"Kraken short quantity for {trade.symbol} is malformed",
                )
            if not all(math.isfinite(value) for value in (volume, closed)):
                return PositionVerification(
                    status="UNAVAILABLE",
                    reason=f"Kraken short quantity for {trade.symbol} is non-finite",
                )
            if volume < 0 or closed < 0 or closed > volume:
                return PositionVerification(
                    status="UNAVAILABLE",
                    reason=f"Kraken short quantity for {trade.symbol} is inconsistent",
                )
            remaining += volume - closed

        if matched and not math.isfinite(remaining):
            return PositionVerification(
                status="UNAVAILABLE",
                reason=f"Kraken short quantity for {trade.symbol} is non-finite",
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
