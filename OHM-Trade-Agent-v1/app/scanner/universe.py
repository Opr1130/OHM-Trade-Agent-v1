from __future__ import annotations

from dataclasses import dataclass, replace

from app.exchanges.kraken import KrakenClient


DEFAULT_UNIQUE_ASSET_LIMIT = 200
TICKER_BATCH_SIZE = 100

EXCLUDED_BASE_ASSETS = {
    # Stablecoins / tokenized fiat
    "USDT", "USDC", "DAI", "PYUSD", "TUSD", "EURT", "EURC",
    "USDQ", "USDG", "USDR", "USDS", "RLUSD", "FDUSD", "GUSD", "USDP",
    # Fiat currencies
    "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF",
}

EXCLUDED_SUFFIXES = {".D", ".S", "2L", "2S", "3L", "3S", "5L", "5S"}
BASE_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}
ELIGIBLE_QUOTES = {"USD", "USDT"}


@dataclass(frozen=True)
class UniverseAsset:
    base_asset: str
    usd_pair: str | None = None
    usdt_pair: str | None = None
    primary_pair: str = ""
    secondary_pair: str | None = None
    primary_quote_currency: str = ""
    usd_24h_notional_usd: float = 0.0
    usdt_24h_notional_quote: float = 0.0
    usdt_24h_notional_usd_equivalent: float = 0.0
    combined_24h_notional_usd: float = 0.0
    liquidity_rank: int = 0


@dataclass(frozen=True)
class UniverseBuildResult:
    assets: list[UniverseAsset]
    eligible_usd_markets: int
    eligible_usdt_markets: int
    unique_underlying_assets: int
    selected_liquid_assets: int
    usdt_usd_rate: float | None
    warnings: list[str]
    ticker_batch_requests: int


@dataclass(frozen=True)
class _EligibleMarket:
    pair_id: str
    altname: str
    display_pair: str
    base_asset: str
    quote_currency: str


def _normalize_base(base: str) -> str:
    return BASE_ALIASES.get(base.upper(), base.upper())


def _market_symbols(details: dict) -> tuple[str, str] | None:
    wsname = details.get("wsname")
    if isinstance(wsname, str) and "/" in wsname:
        base, quote = wsname.upper().split("/", maxsplit=1)
        return _normalize_base(base), quote

    altname = details.get("altname")
    if not isinstance(altname, str):
        return None
    upper = altname.upper()
    for quote in ("USDT", "USD"):
        if upper.endswith(quote):
            return _normalize_base(upper[:-len(quote)]), quote
    return None


def _display_pair(base_asset: str, quote_currency: str) -> str:
    return f"{base_asset}{quote_currency}"


def _is_excluded_market(details: dict) -> bool:
    symbols = _market_symbols(details)
    altname = details.get("altname")
    if symbols is None or not isinstance(altname, str):
        return True
    base_asset, quote_currency = symbols
    if quote_currency not in ELIGIBLE_QUOTES or base_asset in EXCLUDED_BASE_ASSETS:
        return True
    upper_altname = altname.upper()
    return any(
        upper_altname.endswith(f"{suffix}{quote_currency}")
        or upper_altname.endswith(suffix)
        for suffix in EXCLUDED_SUFFIXES
    )


def _conversion_direction(details: dict) -> str | None:
    symbols = _market_symbols(details)
    if symbols == ("USDT", "USD"):
        return "direct"
    if symbols == ("USD", "USDT"):
        return "inverse"
    return None


def _ticker_for_market(
    tickers: dict[str, dict[str, float]],
    market: _EligibleMarket,
) -> dict[str, float] | None:
    return (
        tickers.get(market.pair_id)
        or tickers.get(market.altname)
        or tickers.get(market.display_pair)
    )


def build_kraken_asset_universe(
    client: KrakenClient | None = None,
    limit: int = DEFAULT_UNIQUE_ASSET_LIMIT,
) -> UniverseBuildResult:
    client = client or KrakenClient()
    pair_details = client.get_asset_pairs()
    markets: list[_EligibleMarket] = []
    conversion_pairs: dict[str, tuple[str, str]] = {}
    warnings: list[str] = []

    for pair_id, details in pair_details.items():
        direction = _conversion_direction(details)
        if direction:
            conversion_pairs[pair_id] = (
                direction,
                str(details.get("altname", pair_id)),
            )
        if _is_excluded_market(details):
            continue
        base_asset, quote_currency = _market_symbols(details)  # type: ignore[misc]
        markets.append(_EligibleMarket(
            pair_id=pair_id,
            altname=str(details.get("altname", pair_id)).upper(),
            display_pair=_display_pair(base_asset, quote_currency),
            base_asset=base_asset,
            quote_currency=quote_currency,
        ))

    request_ids = sorted({market.pair_id for market in markets} | set(conversion_pairs))
    all_tickers: dict[str, dict[str, float]] = {}
    ticker_batch_requests = 0
    for start in range(0, len(request_ids), TICKER_BATCH_SIZE):
        ticker_batch_requests += 1
        batch = request_ids[start:start + TICKER_BATCH_SIZE]
        try:
            all_tickers.update(client.get_tickers(batch))
        except Exception as exc:
            warnings.append(f"Ticker batch skipped after Kraken error: {exc}")

    usdt_usd_rate: float | None = None
    for pair_id, (direction, altname) in sorted(
        conversion_pairs.items(), key=lambda item: item[1][0] != "direct"
    ):
        ticker = all_tickers.get(pair_id) or all_tickers.get(altname)
        if ticker is None or ticker.get("last", 0) <= 0:
            continue
        last = ticker["last"]
        usdt_usd_rate = last if direction == "direct" else 1 / last
        break

    if any(market.quote_currency == "USDT" for market in markets) and usdt_usd_rate is None:
        warnings.append(
            "USDT/USD conversion unavailable; USDT liquidity excluded from USD ranking"
        )

    by_asset: dict[str, dict[str, object]] = {}
    for market in markets:
        ticker = _ticker_for_market(all_tickers, market)
        if ticker is None:
            continue
        last_price = ticker.get("last", 0.0)
        volume_24h = ticker.get("volume_24h", 0.0)
        quote_notional = last_price * volume_24h
        if last_price <= 0 or quote_notional <= 0:
            continue
        item = by_asset.setdefault(market.base_asset, {})
        if market.quote_currency == "USD":
            item["usd_pair"] = market.display_pair
            item["usd_notional"] = quote_notional
        else:
            item["usdt_pair"] = market.display_pair
            item["usdt_quote_notional"] = quote_notional
            item["usdt_usd_notional"] = (
                quote_notional * usdt_usd_rate
                if usdt_usd_rate is not None
                else 0.0
            )

    assets: list[UniverseAsset] = []
    for base_asset, item in by_asset.items():
        usd_pair = item.get("usd_pair")
        usdt_pair = item.get("usdt_pair")
        usd_notional = float(item.get("usd_notional", 0.0))
        usdt_quote_notional = float(item.get("usdt_quote_notional", 0.0))
        usdt_usd_notional = float(item.get("usdt_usd_notional", 0.0))
        combined = usd_notional + usdt_usd_notional
        if combined <= 0:
            continue

        # The primary pair is a persistent lifecycle identity, not a liquidity
        # prize. Pending setups, active trades, callbacks, and monitors all use
        # the pair symbol as their key, so a ticker-driven USD/USDT flip would
        # split one underlying asset across two lifecycle records. ACCOUNT_EQUITY
        # is also USD-denominated. Keep USD canonical whenever it exists; USDT
        # contributes discovery, liquidity, and later cross-pair confirmation.
        # A future lifecycle may separate underlying_asset, analysis_pair, and
        # execution_pair, but that routing redesign is outside Universe v2.
        if usd_pair:
            primary_pair = str(usd_pair)
            secondary_pair = str(usdt_pair) if usdt_pair else None
            primary_quote = "USD"
        else:
            primary_pair = str(usdt_pair)
            secondary_pair = None
            primary_quote = "USDT"

        assets.append(UniverseAsset(
            base_asset=base_asset,
            usd_pair=str(usd_pair) if usd_pair else None,
            usdt_pair=str(usdt_pair) if usdt_pair else None,
            primary_pair=primary_pair,
            secondary_pair=secondary_pair,
            primary_quote_currency=primary_quote,
            usd_24h_notional_usd=usd_notional,
            usdt_24h_notional_quote=usdt_quote_notional,
            usdt_24h_notional_usd_equivalent=usdt_usd_notional,
            combined_24h_notional_usd=combined,
        ))

    assets.sort(key=lambda asset: (-asset.combined_24h_notional_usd, asset.base_asset))
    unique_count = len(assets)
    selected = [
        replace(asset, liquidity_rank=rank)
        for rank, asset in enumerate(assets[:limit], start=1)
    ]
    return UniverseBuildResult(
        assets=selected,
        eligible_usd_markets=sum(m.quote_currency == "USD" for m in markets),
        eligible_usdt_markets=sum(m.quote_currency == "USDT" for m in markets),
        unique_underlying_assets=unique_count,
        selected_liquid_assets=len(selected),
        usdt_usd_rate=usdt_usd_rate,
        warnings=warnings,
        ticker_batch_requests=ticker_batch_requests,
    )


def get_kraken_usd_universe(
    client: KrakenClient | None = None,
    limit: int = 100,
) -> list[str]:
    """Backward-compatible primary-pair list for older callers."""
    return [
        asset.primary_pair
        for asset in build_kraken_asset_universe(client=client, limit=limit).assets
    ]
