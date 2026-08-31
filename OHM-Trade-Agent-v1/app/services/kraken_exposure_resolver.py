from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Callable

from app.exchanges.kraken import KrakenClient
from app.exchanges.kraken_identity import (
    canonicalize_asset,
    canonicalize_pair,
    split_canonical_pair,
)
from app.exchanges.kraken_private import KrakenPrivateClient
from app.services.active_trade_registry import ActiveTrade, get_active_trades
from app.services.kraken_position_verification import verify_trade_against_snapshot


CASH_LIKE_ASSETS = {
    "USD", "USDT", "USDC", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF"
}
PREFERRED_QUOTES = ("USD", "USDT", "USDC")


@dataclass(frozen=True)
class ResolvedExposure:
    status: str
    symbol: str
    direction: str
    observed_quantity: float | None
    reason: str
    trade: ActiveTrade | None = None
    notional_usd: float | None = None


@dataclass(frozen=True)
class ExposureResolution:
    exposures: tuple[ResolvedExposure, ...]
    coverage_complete: bool
    reason: str = ""


TradeLoader = Callable[[], list[ActiveTrade]]
VerifierFactory = Callable[[], Any]


def _minimum_unmanaged_notional_usd() -> float:
    try:
        return max(
            0.0,
            float(os.getenv("OPIP_PROTECTION_MIN_UNMANAGED_NOTIONAL_USD", "25")),
        )
    except ValueError:
        return 25.0


def _remaining_open_position_quantity(row: dict[str, Any]) -> float | None:
    try:
        volume = float(row.get("vol") or 0.0)
        closed = float(row.get("vol_closed") or 0.0)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (volume, closed)):
        return None
    if volume < 0 or closed < 0 or closed > volume:
        return None
    return volume - closed


def _managed_spot_quantity(
    trade: ActiveTrade,
    observed_quantity: float | None,
) -> float | None:
    """Return the verified lifecycle quantity that may suppress spot exposure.

    Legacy lifecycles without explicit capital retain their historical behavior
    and use the verified observed quantity. When sizing exists, only that
    lifecycle's expected quantity is treated as managed so residual account
    balance remains visible as unmanaged exposure.
    """
    try:
        observed = float(observed_quantity) if observed_quantity is not None else math.nan
        leverage = float(trade.margin_leverage or 1.0)
        entry = float(trade.entry_price)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (observed, leverage, entry)):
        return None
    if observed < 0 or leverage <= 0 or entry <= 0:
        return None
    if trade.capital is None:
        return observed
    try:
        capital = float(trade.capital)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(capital) or capital < 0:
        return None
    expected = capital * leverage / entry
    if not math.isfinite(expected) or expected < 0:
        return None
    return min(observed, expected)


def _position_identity(row: dict[str, Any]) -> tuple[str, str] | None:
    descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
    pair = canonicalize_pair(str(row.get("pair") or descr.get("pair") or ""))
    side = str(row.get("type") or descr.get("type") or "").lower()
    if not pair or side not in {"buy", "sell"}:
        return None
    return pair, ("SHORT" if side == "sell" else "LONG")


def _pair_catalog(public_client: KrakenClient) -> dict[str, str]:
    result: dict[str, tuple[int, str]] = {}
    for pair_id, details in public_client.get_asset_pairs().items():
        raw_pair = details.get("altname") or details.get("wsname") or pair_id
        pair = canonicalize_pair(str(raw_pair or ""))
        base = canonicalize_asset(str(details.get("base") or ""))
        quote = canonicalize_asset(str(details.get("quote") or ""))
        if not pair or not base or quote not in PREFERRED_QUOTES:
            continue
        priority = PREFERRED_QUOTES.index(quote)
        previous = result.get(base)
        if previous is None or priority < previous[0]:
            result[base] = (priority, pair)
    return {base: pair for base, (_, pair) in result.items()}


def _ticker_notionals(
    public_client: KrakenClient,
    *,
    quantities: dict[str, float],
    pairs_by_asset: dict[str, str],
) -> dict[str, float | None]:
    wanted_pairs = sorted(
        {
            pair
            for asset, pair in pairs_by_asset.items()
            if asset in quantities and quantities[asset] > 0
        }
    )
    if not wanted_pairs:
        return {}
    try:
        tickers = public_client.get_tickers(wanted_pairs)
    except Exception:
        return {asset: None for asset in quantities}

    by_pair: dict[str, float] = {}
    for raw_pair, row in tickers.items():
        try:
            price = float(row["last"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(price) and price > 0:
            by_pair[canonicalize_pair(raw_pair)] = price

    notionals: dict[str, float | None] = {}
    for asset, quantity in quantities.items():
        pair = canonicalize_pair(pairs_by_asset.get(asset, ""))
        price = by_pair.get(pair)
        notionals[asset] = None if price is None else quantity * price
    return notionals


def _legacy_managed_resolution(
    local_trades: list[ActiveTrade],
    verifier_factory: VerifierFactory,
) -> tuple[ResolvedExposure, ...]:
    """Compatibility/fail-closed fallback when a direct snapshot is unavailable.

    Production normally uses the Kraken-first snapshot path. The fallback keeps
    existing safety-test and degraded-runtime semantics intact without creating
    unmanaged exposure from uncertain account state.
    """
    verifier = verifier_factory()
    verifier.refresh()
    exposures: list[ResolvedExposure] = []
    for trade in local_trades:
        verification = verifier.verify(trade)
        status = {
            "VERIFIED": "VERIFIED_MANAGED",
            "ABSENT": "ABSENT",
            "UNAVAILABLE": "DEGRADED",
        }.get(str(verification.status), "DEGRADED")
        exposures.append(
            ResolvedExposure(
                status=status,
                symbol=trade.symbol,
                direction=(trade.direction or "LONG").upper(),
                observed_quantity=getattr(verification, "observed_quantity", None),
                reason=str(getattr(verification, "reason", "") or ""),
                trade=trade,
            )
        )
    return tuple(exposures)


class KrakenExposureResolver:
    """Kraken-first, read-only exposure resolver for real-account protection."""

    def __init__(
        self,
        *,
        private_client: KrakenPrivateClient | None = None,
        public_client: KrakenClient | None = None,
        trade_loader: TradeLoader = get_active_trades,
        managed_verifier_factory: VerifierFactory | None = None,
    ) -> None:
        self.private_client = private_client or KrakenPrivateClient()
        self.public_client = public_client or KrakenClient()
        self.trade_loader = trade_loader
        self.managed_verifier_factory = managed_verifier_factory

    def resolve(self) -> ExposureResolution:
        try:
            local_trades = list(self.trade_loader())
            registry_available = True
            registry_reason = ""
        except Exception as exc:
            local_trades = []
            registry_available = False
            registry_reason = f"active trade registry unavailable: {exc}"

        if not self.private_client.enabled:
            if self.managed_verifier_factory is not None and local_trades:
                exposures = _legacy_managed_resolution(
                    local_trades,
                    self.managed_verifier_factory,
                )
                return ExposureResolution(
                    exposures=exposures,
                    coverage_complete=False,
                    reason="Kraken direct snapshot unavailable; managed verification fallback used",
                )
            exposures = tuple(
                ResolvedExposure(
                    status="UNKNOWN",
                    symbol=trade.symbol,
                    direction=(trade.direction or "LONG").upper(),
                    observed_quantity=None,
                    reason="Kraken private credentials are not configured",
                    trade=trade,
                )
                for trade in local_trades
            )
            return ExposureResolution(
                exposures=exposures,
                coverage_complete=False,
                reason=registry_reason or "Kraken private credentials are not configured",
            )

        try:
            self.private_client.assert_read_only()
            balances = self.private_client.get_balance()
            positions = self.private_client.get_open_positions()
        except Exception as exc:
            if self.managed_verifier_factory is not None and local_trades:
                exposures = _legacy_managed_resolution(
                    local_trades,
                    self.managed_verifier_factory,
                )
                return ExposureResolution(
                    exposures=exposures,
                    coverage_complete=False,
                    reason=f"Kraken direct snapshot unavailable; managed verification fallback used: {exc}",
                )
            exposures = tuple(
                ResolvedExposure(
                    status="DEGRADED",
                    symbol=trade.symbol,
                    direction=(trade.direction or "LONG").upper(),
                    observed_quantity=None,
                    reason=f"Kraken account state unavailable: {exc}",
                    trade=trade,
                )
                for trade in local_trades
            )
            return ExposureResolution(
                exposures=exposures,
                coverage_complete=False,
                reason=registry_reason or f"Kraken account state unavailable: {exc}",
            )

        exposures: list[ResolvedExposure] = []
        managed_spot_quantities: dict[str, float] = {}
        managed_position_keys: set[tuple[str, str]] = set()
        managed_resolution_gaps: list[str] = []

        for trade in local_trades:
            verification = verify_trade_against_snapshot(
                trade,
                balances=balances,
                positions=positions,
            )
            direction = (trade.direction or "LONG").upper()
            pair = canonicalize_pair(trade.symbol)
            if verification.status == "VERIFIED":
                if direction == "LONG":
                    try:
                        leverage = float(trade.margin_leverage or 1.0)
                    except (TypeError, ValueError):
                        leverage = math.nan
                    if not math.isfinite(leverage) or leverage <= 0:
                        managed_resolution_gaps.append(
                            f"{trade.symbol}: invalid lifecycle leverage"
                        )
                    elif leverage > 1.0:
                        managed_position_keys.add((pair, "LONG"))
                    else:
                        identity = split_canonical_pair(pair)
                        if identity is None:
                            managed_resolution_gaps.append(
                                f"{trade.symbol}: unresolved managed spot pair identity"
                            )
                        else:
                            managed_quantity = _managed_spot_quantity(
                                trade,
                                verification.observed_quantity,
                            )
                            if managed_quantity is None:
                                managed_resolution_gaps.append(
                                    f"{trade.symbol}: unresolved managed spot quantity"
                                )
                            else:
                                asset = identity[0]
                                managed_spot_quantities[asset] = (
                                    managed_spot_quantities.get(asset, 0.0)
                                    + managed_quantity
                                )
                elif direction == "SHORT":
                    managed_position_keys.add((pair, direction))
                else:
                    managed_resolution_gaps.append(
                        f"{trade.symbol}: unsupported lifecycle direction {direction}"
                    )
            elif verification.status == "UNAVAILABLE":
                managed_resolution_gaps.append(
                    f"{trade.symbol}: {verification.reason}"
                )

            status = {
                "VERIFIED": "VERIFIED_MANAGED",
                "ABSENT": "ABSENT",
                "UNAVAILABLE": "DEGRADED",
            }.get(verification.status, "DEGRADED")
            exposures.append(
                ResolvedExposure(
                    status=status,
                    symbol=trade.symbol,
                    direction=direction,
                    observed_quantity=verification.observed_quantity,
                    reason=verification.reason,
                    trade=trade,
                )
            )

        canonical_balances: dict[str, float] = {}
        account_state_gaps: list[str] = []
        for raw_asset, raw_quantity in balances.items():
            asset = canonicalize_asset(raw_asset)
            if not asset:
                account_state_gaps.append(
                    f"unrecognized balance asset {raw_asset!r}"
                )
                continue
            try:
                quantity = float(raw_quantity)
            except (TypeError, ValueError):
                account_state_gaps.append(
                    f"malformed balance quantity for {asset}"
                )
                continue
            if not math.isfinite(quantity) or quantity < 0:
                account_state_gaps.append(
                    f"invalid balance quantity for {asset}"
                )
                continue
            if asset in CASH_LIKE_ASSETS or quantity == 0:
                continue
            canonical_balances[asset] = (
                canonical_balances.get(asset, 0.0) + quantity
            )

        for asset, managed_quantity in sorted(managed_spot_quantities.items()):
            balance_quantity = canonical_balances.get(asset)
            if balance_quantity is None:
                continue
            if managed_quantity > balance_quantity + 1e-12:
                managed_resolution_gaps.append(
                    f"{asset}: managed lifecycle quantity exceeds Kraken balance"
                )
            residual = max(0.0, balance_quantity - managed_quantity)
            if residual <= 1e-12:
                canonical_balances.pop(asset, None)
            else:
                canonical_balances[asset] = residual

        pair_catalog_available = True
        pair_catalog_reason = ""
        try:
            pairs_by_asset = _pair_catalog(self.public_client)
            notionals = _ticker_notionals(
                self.public_client,
                quantities=canonical_balances,
                pairs_by_asset=pairs_by_asset,
            )
        except Exception as exc:
            pair_catalog_available = False
            pair_catalog_reason = f"Kraken public pair discovery unavailable: {exc}"
            pairs_by_asset = {}
            notionals = {}

        minimum_notional = _minimum_unmanaged_notional_usd()
        unpriced_assets: list[str] = []
        for asset, quantity in sorted(canonical_balances.items()):
            pair = pairs_by_asset.get(asset)
            if not pair:
                unpriced_assets.append(asset)
                exposures.append(
                    ResolvedExposure(
                        status="VERIFIED_UNMANAGED",
                        symbol=asset,
                        direction="LONG",
                        observed_quantity=quantity,
                        reason=(
                            "Kraken reports a non-zero balance with no USD/stable-quote pair; "
                            "USD notional is unavailable"
                        ),
                    )
                )
                continue
            notional = notionals.get(asset)
            if notional is None:
                unpriced_assets.append(asset)
            elif notional < minimum_notional:
                continue
            exposures.append(
                ResolvedExposure(
                    status="VERIFIED_UNMANAGED",
                    symbol=pair,
                    direction="LONG",
                    observed_quantity=quantity,
                    notional_usd=notional,
                    reason=(
                        "Kraken reports a non-zero spot balance without matching O'Pip lifecycle context"
                        if notional is not None
                        else "Kraken reports a non-zero spot balance; USD notional is unavailable"
                    ),
                )
            )

        for position_id, row in positions.items():
            if not isinstance(row, dict):
                account_state_gaps.append(
                    f"malformed open position row {position_id}"
                )
                continue
            identity = _position_identity(row)
            if identity is None:
                account_state_gaps.append(
                    f"unresolved open position identity {position_id}"
                )
                continue
            pair, direction = identity
            remaining = _remaining_open_position_quantity(row)
            if remaining is None:
                account_state_gaps.append(
                    f"invalid open position quantity {position_id}"
                )
                continue
            if remaining <= 0:
                continue
            if (pair, direction) in managed_position_keys:
                continue
            exposures.append(
                ResolvedExposure(
                    status="VERIFIED_UNMANAGED",
                    symbol=pair,
                    direction=direction,
                    observed_quantity=remaining,
                    reason="Kraken reports an open position without matching O'Pip lifecycle context",
                )
            )

        reasons = [
            reason for reason in (registry_reason, pair_catalog_reason) if reason
        ]
        if unpriced_assets:
            reasons.append(
                "USD/stable-quote pricing unavailable for held assets: "
                + ",".join(sorted(set(unpriced_assets)))
            )
        if account_state_gaps:
            reasons.append(
                "Kraken account state contains unresolved records: "
                + "; ".join(account_state_gaps[:8])
            )
        if managed_resolution_gaps:
            reasons.append(
                "Managed lifecycle verification incomplete: "
                + "; ".join(managed_resolution_gaps[:8])
            )
        return ExposureResolution(
            exposures=tuple(exposures),
            coverage_complete=(
                registry_available
                and pair_catalog_available
                and not unpriced_assets
                and not account_state_gaps
                and not managed_resolution_gaps
            ),
            reason="; ".join(reasons),
        )