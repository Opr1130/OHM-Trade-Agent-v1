from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Callable

from app.exchanges.kraken import KrakenClient
from app.exchanges.kraken_identity import canonicalize_asset, canonicalize_pair
from app.exchanges.kraken_private import KrakenPrivateClient
from app.services.active_trade_registry import ActiveTrade, get_active_trades
from app.services.kraken_position_verification import verify_trade_against_snapshot


CASH_LIKE_ASSETS = {
    "USD", "USDT", "USDC", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF"
}
PREFERRED_QUOTES = ("USD", "USDT")


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
            return ExposureResolution(
                exposures=tuple(),
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
            return ExposureResolution(
                exposures=tuple(),
                coverage_complete=False,
                reason=registry_reason or f"Kraken account state unavailable: {exc}",
            )

        exposures: list[ResolvedExposure] = []
        managed_spot_assets: set[str] = set()
        managed_position_keys: set[tuple[str, str]] = set()

        for trade in local_trades:
            verification = verify_trade_against_snapshot(
                trade,
                balances=balances,
                positions=positions,
            )
            direction = (trade.direction or "LONG").upper()
            pair = canonicalize_pair(trade.symbol)
            if direction == "LONG":
                for quote in (
                    "USDT", "USD", "EUR", "GBP", "CAD", "AUD",
                    "JPY", "CHF", "BTC", "ETH",
                ):
                    if pair.endswith(quote) and len(pair) > len(quote):
                        managed_spot_assets.add(
                            canonicalize_asset(pair[: -len(quote)])
                        )
                        break
            else:
                managed_position_keys.add((pair, direction))

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
        for raw_asset, raw_quantity in balances.items():
            asset = canonicalize_asset(raw_asset)
            try:
                quantity = float(raw_quantity)
            except (TypeError, ValueError):
                continue
            if (
                not asset
                or asset in CASH_LIKE_ASSETS
                or not math.isfinite(quantity)
                or quantity <= 0
                or asset in managed_spot_assets
            ):
                continue
            canonical_balances[asset] = (
                canonical_balances.get(asset, 0.0) + quantity
            )

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
        for asset, quantity in sorted(canonical_balances.items()):
            pair = pairs_by_asset.get(asset)
            if not pair:
                continue
            notional = notionals.get(asset)
            if notional is not None and notional < minimum_notional:
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

        for row in positions.values():
            if not isinstance(row, dict):
                continue
            identity = _position_identity(row)
            if identity is None:
                continue
            pair, direction = identity
            remaining = _remaining_open_position_quantity(row)
            if remaining is None or remaining <= 0:
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
        return ExposureResolution(
            exposures=tuple(exposures),
            coverage_complete=registry_available and pair_catalog_available,
            reason="; ".join(reasons),
        )
