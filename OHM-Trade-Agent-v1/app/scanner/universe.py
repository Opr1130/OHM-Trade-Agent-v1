from __future__ import annotations

from app.exchanges.kraken import KrakenClient


EXCLUDED_BASE_ASSETS = {
    # Stablecoins / tokenized fiat
    "USDT",
    "USDC",
    "DAI",
    "PYUSD",
    "TUSD",
    "EURT",
    "EURC",
    "USDQ",
    "USDG",
    "USDR",
    "USDS",
    "RLUSD",
    "FDUSD",
    "GUSD",
    "USDP",

    # Fiat currencies
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "AUD",
    "JPY",
    "CHF",
}


DISPLAY_SYMBOL_OVERRIDES = {
    "XBTUSD": "BTCUSD",
    "XDGUSD": "DOGEUSD",
}


EXCLUDED_SUFFIXES = {
    ".D",
    ".S",
    "2L",
    "2S",
    "3L",
    "3S",
    "5L",
    "5S",
}


def _display_base(details: dict) -> str | None:
    wsname = details.get("wsname")

    if isinstance(wsname, str) and "/" in wsname:
        return wsname.split("/", maxsplit=1)[0].upper()

    altname = details.get("altname")

    if isinstance(altname, str) and altname.upper().endswith("USD"):
        return altname[:-3].upper()

    return None


def _is_excluded_pair(details: dict) -> bool:
    altname = details.get("altname")
    base_symbol = _display_base(details)

    if not isinstance(altname, str) or not base_symbol:
        return True

    if base_symbol in EXCLUDED_BASE_ASSETS:
        return True

    upper_altname = altname.upper()

    return any(
        upper_altname.endswith(f"{suffix}USD")
        or upper_altname.endswith(suffix)
        for suffix in EXCLUDED_SUFFIXES
    )


def _normalize_display_symbol(symbol: str) -> str:
    return DISPLAY_SYMBOL_OVERRIDES.get(symbol.upper(), symbol.upper())


def get_kraken_usd_universe(
    client: KrakenClient | None = None,
    limit: int = 100,
) -> list[str]:
    client = client or KrakenClient()
    pairs = client.get_asset_pairs()

    altname_by_pair_id: dict[str, str] = {}

    for pair_id, details in pairs.items():
        if details.get("quote") != "ZUSD":
            continue

        altname = details.get("altname")

        if not isinstance(altname, str):
            continue

        if _is_excluded_pair(details):
            continue

        altname_by_pair_id[pair_id] = altname

    pair_ids = sorted(altname_by_pair_id)
    ranked: list[tuple[str, float]] = []
    batch_size = 100

    for start in range(0, len(pair_ids), batch_size):
        batch = pair_ids[start:start + batch_size]
        tickers = client.get_tickers(batch)

        for returned_pair_id, ticker in tickers.items():
            altname = altname_by_pair_id.get(returned_pair_id)

            if altname is None:
                continue

            last_price = ticker["last"]
            volume_24h = ticker["volume_24h"]
            usd_notional_volume = last_price * volume_24h

            if last_price <= 0 or usd_notional_volume <= 0:
                continue

            ranked.append((altname, usd_notional_volume))

    ranked.sort(key=lambda item: item[1], reverse=True)

    symbols: list[str] = []
    seen: set[str] = set()

    for symbol, _ in ranked:
        display_symbol = _normalize_display_symbol(symbol)

        if display_symbol in seen:
            continue

        seen.add(display_symbol)
        symbols.append(display_symbol)

        if len(symbols) >= limit:
            break

    return symbols
