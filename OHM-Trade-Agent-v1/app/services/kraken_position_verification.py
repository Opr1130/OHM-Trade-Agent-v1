from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.exchanges.kraken_private import KrakenPrivateClient
from app.services.active_trade_registry import ActiveTrade
from app.services.kraken_reconciliation import (
    _balance_for_asset,
    _base_asset,
    _canonical_pair,
)


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
    remaining = 0.0
    matched = False
    for row in positions.values():
        if not isinstance(row, dict):
            continue
        descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
        pair = row.get("pair") or descr.get("pair") or ""
        row_side = str(row.get("type") or descr.get("type") or "").lower()
        if _canonical_pair(str(pair or "")) != wanted_pair or row_side != side:
            continue
        matched = True
        try:
            volume = float(row.get("vol") or 0)
            closed = float(row.get("vol_closed") or 0)
        except (TypeError, ValueError):
            return None, "malformed"
        if not all(math.isfinite(value) for value in (volume, closed)):
            return None, "non-finite"
        if volume < 0 or closed < 0 or closed > volume:
            return None, "inconsistent"
        remaining += volume - closed
    if not matched:
        return 0.0, None
    if not math.isfinite(remaining):
        return None, "non-finite"
    return remaining, None


def verify_trade_against_snapshot(
    trade: ActiveTrade,
    *,
    balances: dict[str, float],
    positions: dict[str, dict[str, Any]],
) -> PositionVerification:
    """Verify one local lifecycle against an already-loaded Kraken snapshot."""
    direction = (trade.direction or "LONG").upper()
    if direction not in {"LONG", "SHORT"}:
        return PositionVerification(
            status="UNAVAILABLE",
            reason=f"Unsupported active-trade direction {direction!r}",
        )

    wanted_pair = _canonical_pair(trade.symbol)
    if not wanted_pair:
        return PositionVerification(
            status="UNAVAILABLE",
            reason=f"Could not canonicalize {trade.symbol}",
        )

    if direction == "LONG":
        try:
            leverage = float(trade.margin_leverage or 1.0)
        except (TypeError, ValueError):
            return PositionVerification(
                status="UNAVAILABLE",
                reason=f"Active-trade leverage for {trade.symbol} is malformed",
            )
        if not math.isfinite(leverage) or leverage <= 0:
            return PositionVerification(
                status="UNAVAILABLE",
                reason=f"Active-trade leverage for {trade.symbol} is invalid",
            )

        if leverage > 1.0:
            quantity, position_error = _open_position_quantity(
                positions,
                wanted_pair=wanted_pair,
                side="buy",
            )
            if position_error:
                return PositionVerification(
                    status="UNAVAILABLE",
                    reason=(
                        f"Kraken long quantity for {trade.symbol} "
                        f"is {position_error}"
                    ),
                )
            if quantity is not None and quantity > 0:
                return PositionVerification(
                    status="VERIFIED",
                    reason=f"Kraken reports an open long position for {trade.symbol}",
                    observed_quantity=quantity,
                )

        asset = _base_asset(trade.symbol)
        quantity = _balance_for_asset(balances, asset)
        if quantity is None or not math.isfinite(float(quantity)) or quantity < 0:
            return PositionVerification(
                status="UNAVAILABLE",
                reason=(
                    f"Kraken balance for {trade.symbol} "
                    "is unresolved or non-finite"
                ),
            )

        expected = None
        if trade.capital is not None:
            try:
                capital = float(trade.capital)
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
            if capital < 0 or entry <= 0:
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

    remaining, position_error = _open_position_quantity(
        positions,
        wanted_pair=wanted_pair,
        side="sell",
    )
    if position_error:
        return PositionVerification(
            status="UNAVAILABLE",
            reason=f"Kraken short quantity for {trade.symbol} is {position_error}",
        )
    if remaining is None or remaining <= 0:
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
    """Read-only account gate for active-trade protection notifications."""

    def __init__(self, client: KrakenPrivateClient | None = None) -> None:
        self.client = client or KrakenPrivateClient()
        self._balances: dict[str, float] | None = None
        self._positions: dict[str, dict[str, Any]] | None = None
        self._unavailable_reason = ""

    def refresh(self) -> None:
        if not self.client.enabled:
            self._unavailable_reason = (
                "Kraken private credentials are not configured"
            )
            return
        try:
            self.client.assert_read_only()
            self._balances = self.client.get_balance()
            self._positions = self.client.get_open_positions()
        except Exception as exc:
            self._balances = None
            self._positions = None
            self._unavailable_reason = str(exc)

    def verify(self, trade: ActiveTrade) -> PositionVerification:
        if self._balances is None or self._positions is None:
            return PositionVerification(
                status="UNAVAILABLE",
                reason=(
                    self._unavailable_reason
                    or "Kraken account state was not loaded"
                ),
            )
        return verify_trade_against_snapshot(
            trade,
            balances=self._balances,
            positions=self._positions,
        )
