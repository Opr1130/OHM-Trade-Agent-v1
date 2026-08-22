from __future__ import annotations

"""Canonical Kraken asset/pair identity helpers.

Kraken exposes the same asset through several public/private surfaces using
modern display symbols (BTC, DOGE, XRP) and legacy ledger/pair codes
(XXBT, XXDG, XXRP, ZUSD, ...). Lifecycle code must never compare those raw
strings directly because a missed match can hide a real fill or position.
"""

import re


# Explicit aliases are intentionally conservative. They cover Kraken's legacy
# currency codes that appear in Balance, TradesHistory and OpenPositions while
# leaving modern token symbols untouched.
_ASSET_ALIASES: dict[str, str] = {
    "XBT": "BTC",
    "XXBT": "BTC",
    "BTC": "BTC",
    "XETH": "ETH",
    "ETH": "ETH",
    "XDG": "DOGE",
    "XXDG": "DOGE",
    "DOGE": "DOGE",
    "XXRP": "XRP",
    "XRP": "XRP",
    "XXLM": "XLM",
    "XLM": "XLM",
    "XLTC": "LTC",
    "LTC": "LTC",
    "XZEC": "ZEC",
    "ZEC": "ZEC",
    "XXMR": "XMR",
    "XMR": "XMR",
    "XETC": "ETC",
    "ETC": "ETC",
    "ZUSD": "USD",
    "USD": "USD",
    "ZEUR": "EUR",
    "EUR": "EUR",
    "ZGBP": "GBP",
    "GBP": "GBP",
    "ZCAD": "CAD",
    "CAD": "CAD",
    "ZAUD": "AUD",
    "AUD": "AUD",
    "ZJPY": "JPY",
    "JPY": "JPY",
    "ZCHF": "CHF",
    "CHF": "CHF",
    "ZUSDT": "USDT",
    "USDT": "USDT",
}

# Pair parsing must prefer the longest raw suffix first (for example ZUSDT
# before USDT and XXBT before XBT).
_QUOTE_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        {
            "ZUSDT": "USDT",
            "USDT": "USDT",
            "ZUSD": "USD",
            "USD": "USD",
            "ZEUR": "EUR",
            "EUR": "EUR",
            "ZGBP": "GBP",
            "GBP": "GBP",
            "ZCAD": "CAD",
            "CAD": "CAD",
            "ZAUD": "AUD",
            "AUD": "AUD",
            "ZJPY": "JPY",
            "JPY": "JPY",
            "ZCHF": "CHF",
            "CHF": "CHF",
            "XXBT": "BTC",
            "XBT": "BTC",
            "BTC": "BTC",
            "XETH": "ETH",
            "ETH": "ETH",
        }.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)


def _clean(value: str | None) -> str:
    return re.sub(r"[\s/\-_]", "", str(value or "").upper())


def canonicalize_asset(asset: str | None) -> str:
    """Return OHM's canonical display asset for a Kraken asset code."""
    value = _clean(asset)
    return _ASSET_ALIASES.get(value, value)


def canonicalize_pair(pair: str | None) -> str:
    """Normalize modern and legacy Kraken pair identities to BASEQUOTE.

    Examples:
      XXBTZUSD -> BTCUSD
      XXRPZUSD -> XRPUSD
      XXDGZUSD -> DOGEUSD
      XETHZUSD -> ETHUSD
      SOL/USD  -> SOLUSD
    """
    value = _clean(pair)
    if not value:
        return ""

    for raw_quote, canonical_quote in _QUOTE_ALIASES:
        if value.endswith(raw_quote) and len(value) > len(raw_quote):
            raw_base = value[: -len(raw_quote)]
            base = canonicalize_asset(raw_base)
            if base:
                return f"{base}{canonical_quote}"
    return value


def balance_quantity_for_asset(balances: dict[str, float], asset: str | None) -> float | None:
    """Sum every Kraken balance alias that resolves to the requested asset."""
    wanted = canonicalize_asset(asset)
    if not wanted:
        return None

    total = 0.0
    found = False
    for raw_asset, amount in balances.items():
        if canonicalize_asset(str(raw_asset)) != wanted:
            continue
        try:
            numeric = float(amount)
        except (TypeError, ValueError):
            continue
        total += numeric
        found = True
    return total if found else 0.0
