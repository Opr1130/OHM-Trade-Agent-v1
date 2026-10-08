from __future__ import annotations

"""Canonical Kraken asset/pair identity helpers.

Kraken exposes the same asset through several public/private surfaces using
modern display symbols (BTC, DOGE, XRP) and legacy ledger/pair codes
(XXBT, XXDG, XXRP, ZUSD, ...). Lifecycle code must never compare those raw
strings directly because a missed match can hide a real fill or position.
"""

import math
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
    "XMLN": "MLN",
    "MLN": "MLN",
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
    "ZUSDC": "USDC",
    "USDC": "USDC",
}

# Pair parsing tries the longest suffix first, but legacy Z-prefixed quote
# codes are only accepted when the remaining base is itself a known Kraken
# legacy asset code. Without that guard, modern assets ending in "Z" (XTZ,
# REZ, ...) are mis-split as XT+ZUSD / RE+ZUSD.
_QUOTE_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        {
            "ZUSDT": "USDT",
            "USDT": "USDT",
            "ZUSDC": "USDC",
            "USDC": "USDC",
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


# Kraken balance/ledger extensions. They are read-only balance identities, not
# trading symbols. Official Balance/Ledgers docs say to transact using the base
# asset (the code with the extension removed). Support documents .S as on-chain
# staked, .M as opt-in rewards, and .P as parachain-bonded. The Balance API
# documents .B as yield-bearing/Earn balances (same family as .S/.M), .F as
# automatic Kraken Rewards, and .T as tokenized balances.
_BALANCE_EXTENSIONS: tuple[str, ...] = (".B", ".F", ".M", ".P", ".S", ".T")

# ETH2 and ETH2.S are Kraken's staking-receipt identities for ETH. Kraken's
# product definition states they are the same asset as ETH, and the public
# AssetPairs catalog exposes no ETH2 quote while ETHUSD is online. This alias
# is explicit and balance-only; it does not change pair parsing.
_BALANCE_RECEIPT_UNDERLYING: dict[str, str] = {"ETH2": "ETH"}


def balance_underlying_asset(asset: str | None) -> str:
    """Spot asset used to price a Kraken balance identity.

    A documented balance extension maps to its base asset. ``ETH2`` receipts
    map to ``ETH``. An unrecognized dotted form is returned unchanged so the
    caller keeps it unpriced and fails closed. Pair parsing is not affected.
    """
    value = _clean(asset)
    if not value:
        return ""
    for extension in _BALANCE_EXTENSIONS:
        if value.endswith(extension) and len(value) > len(extension):
            base = value[: -len(extension)]
            if "." in base or not base:
                return value
            base = canonicalize_asset(base)
            return _BALANCE_RECEIPT_UNDERLYING.get(base, base)
    if value in _BALANCE_RECEIPT_UNDERLYING:
        return _BALANCE_RECEIPT_UNDERLYING[value]
    return canonicalize_asset(value)


def _legacy_quote_can_split(raw_quote: str, raw_base: str) -> bool:
    """Require evidence before interpreting a leading-Z quote as legacy.

    Modern symbols such as XTZUSD legitimately end with the characters ZUSD.
    Kraken's legacy form XXRPZUSD is distinguishable because XXRP is an
    explicit legacy asset alias. Plain USD/USDT/etc. suffixes remain valid for
    arbitrary modern bases.
    """
    if not raw_quote.startswith("Z"):
        return True
    return raw_base in _ASSET_ALIASES and canonicalize_asset(raw_base) != raw_base


def canonicalize_pair(pair: str | None) -> str:
    """Normalize modern and legacy Kraken pair identities to BASEQUOTE.

    Examples:
      XXBTZUSD -> BTCUSD
      XXRPZUSD -> XRPUSD
      XXDGZUSD -> DOGEUSD
      XETHZUSD -> ETHUSD
      XMLNZUSD -> MLNUSD
      XTZUSD   -> XTZUSD
      SOL/USD  -> SOLUSD
    """
    value = _clean(pair)
    if not value:
        return ""

    for raw_quote, canonical_quote in _QUOTE_ALIASES:
        if not (value.endswith(raw_quote) and len(value) > len(raw_quote)):
            continue
        raw_base = value[: -len(raw_quote)]
        if not _legacy_quote_can_split(raw_quote, raw_base):
            continue
        base = canonicalize_asset(raw_base)
        if base:
            return f"{base}{canonical_quote}"
    return value


CANONICAL_QUOTE_ASSETS: tuple[str, ...] = (
    "USDT",
    "USDC",
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "AUD",
    "JPY",
    "CHF",
    "BTC",
    "ETH",
)


def split_canonical_pair(pair: str | None) -> tuple[str, str] | None:
    """Return canonical (base, quote) without duplicating quote lists."""
    value = canonicalize_pair(pair)
    for quote in CANONICAL_QUOTE_ASSETS:
        if value.endswith(quote) and len(value) > len(quote):
            base = canonicalize_asset(value[: -len(quote)])
            if base:
                return base, quote
    return None


def balance_quantity_for_asset(balances: dict[str, float], asset: str | None) -> float | None:
    """Sum every Kraken balance alias that resolves to the requested asset.

    Any matching malformed/non-finite quantity makes the account state
    uncertain, so return ``None`` and let the verifier report UNAVAILABLE
    rather than converting bad private-account data into VERIFIED/ABSENT.
    """
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
            return None
        if not math.isfinite(numeric) or numeric < 0:
            return None
        total += numeric
        found = True
    return total if found else 0.0