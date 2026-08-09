from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from app.scanner.models import MarketSnapshot
from app.services.coingecko import CoinGeckoClient


CONFIRMED = "CONFIRMED"
WARN = "WARN"
MATERIAL_DIVERGENCE = "MATERIAL_DIVERGENCE"
AMBIGUOUS = "AMBIGUOUS"
STALE = "STALE"
UNAVAILABLE = "UNAVAILABLE"
UNIQUE = "UNIQUE"

# Informational reference boundaries only; neither value is a trading gate.
REFERENCE_WARN_DIVERGENCE_PCT = 2.0
REFERENCE_MATERIAL_DIVERGENCE_PCT = 5.0
REFERENCE_STALE_SECONDS = 300.0
SYMBOL_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}


@dataclass(frozen=True)
class ReferenceMarketValidation:
    status: str
    available: bool
    mapping_status: str
    api_mode: str
    coingecko_id: str | None = None
    coingecko_name: str | None = None
    matched_candidate_count: int = 0
    reference_price_usd: float | None = None
    kraken_normalized_price_usd: float | None = None
    absolute_price_difference_usd: float | None = None
    price_divergence_pct: float | None = None
    market_cap_usd: float | None = None
    market_cap_rank: int | None = None
    volume_24h_usd: float | None = None
    change_24h_pct: float | None = None
    last_updated: str | None = None
    age_seconds: float | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReferenceValidationSummary:
    requested: int
    available: int
    unavailable: int
    ambiguous: int
    api_mode: str


def _symbol(value: str) -> str:
    normalized = value.upper()
    return SYMBOL_ALIASES.get(normalized, normalized)


def _candidate_symbol(candidate: MarketSnapshot) -> str:
    if candidate.underlying_asset:
        return _symbol(candidate.underlying_asset)
    value = candidate.primary_pair or candidate.symbol
    for quote in ("USDT", "USD"):
        if value.upper().endswith(quote):
            value = value[:-len(quote)]
            break
    return _symbol(value)


def _unavailable(api_mode: str, warning: str) -> ReferenceMarketValidation:
    return ReferenceMarketValidation(
        status=UNAVAILABLE,
        available=False,
        mapping_status=UNAVAILABLE,
        api_mode=api_mode,
        warnings=(warning,),
    )


def _select_match(matches: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = [item for item in matches if isinstance(item.get("market_cap_rank"), int)]
    if ranked:
        return min(ranked, key=lambda item: item["market_cap_rank"])
    return max(matches, key=lambda item: float(item.get("market_cap") or 0.0))


def evaluate_reference_market(
    candidate: MarketSnapshot,
    market_rows: list[dict[str, Any]],
    usdt_usd_rate: float | None,
    api_mode: str,
    now: datetime | None = None,
) -> ReferenceMarketValidation:
    now = now or datetime.now(timezone.utc)
    wanted = _candidate_symbol(candidate)
    matches = [
        item for item in market_rows
        if _symbol(str(item.get("symbol", ""))) == wanted
    ]
    if not matches:
        return _unavailable(api_mode, f"No CoinGecko match for {wanted}")
    selected = _select_match(matches)
    mapping_status = AMBIGUOUS if len(matches) > 1 else UNIQUE
    warnings: list[str] = []
    if mapping_status == AMBIGUOUS:
        warnings.append(
            f"Symbol matched {len(matches)} CoinGecko assets; highest-ranked reference selected"
        )

    reference_price = selected.get("current_price")
    if not isinstance(reference_price, (int, float)) or not math.isfinite(reference_price) or reference_price <= 0:
        return _unavailable(api_mode, "Selected CoinGecko reference has no valid USD price")
    kraken_price = candidate.ticker_last or candidate.last_price
    if candidate.primary_quote_currency == "USDT":
        if usdt_usd_rate is None or usdt_usd_rate <= 0:
            return _unavailable(api_mode, "Kraken USDT/USD conversion unavailable")
        kraken_price *= usdt_usd_rate
    if not math.isfinite(kraken_price) or kraken_price <= 0:
        return _unavailable(api_mode, "Kraken normalized USD price unavailable")

    absolute_difference = abs(float(reference_price) - kraken_price)
    divergence = absolute_difference / kraken_price * 100
    last_updated = selected.get("last_updated")
    age: float | None = None
    if isinstance(last_updated, str):
        try:
            updated = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            age = max(0.0, (now - updated).total_seconds())
        except ValueError:
            warnings.append("CoinGecko last_updated timestamp is invalid")

    if divergence > REFERENCE_MATERIAL_DIVERGENCE_PCT:
        warnings.append("CoinGecko and Kraken prices materially diverge")
    elif divergence > REFERENCE_WARN_DIVERGENCE_PCT:
        warnings.append("CoinGecko and Kraken prices differ moderately")
    if age is None or age > REFERENCE_STALE_SECONDS:
        warnings.append("CoinGecko reference is stale or has unknown age")

    if mapping_status == AMBIGUOUS:
        status = AMBIGUOUS
    elif age is None or age > REFERENCE_STALE_SECONDS:
        status = STALE
    elif divergence > REFERENCE_MATERIAL_DIVERGENCE_PCT:
        status = MATERIAL_DIVERGENCE
    elif divergence > REFERENCE_WARN_DIVERGENCE_PCT:
        status = WARN
    else:
        status = CONFIRMED
    return ReferenceMarketValidation(
        status=status,
        available=True,
        mapping_status=mapping_status,
        api_mode=api_mode,
        coingecko_id=str(selected.get("id")) if selected.get("id") else None,
        coingecko_name=str(selected.get("name")) if selected.get("name") else None,
        matched_candidate_count=len(matches),
        reference_price_usd=float(reference_price),
        kraken_normalized_price_usd=kraken_price,
        absolute_price_difference_usd=absolute_difference,
        price_divergence_pct=divergence,
        market_cap_usd=(float(selected["market_cap"]) if selected.get("market_cap") is not None else None),
        market_cap_rank=(int(selected["market_cap_rank"]) if selected.get("market_cap_rank") is not None else None),
        volume_24h_usd=(float(selected["total_volume"]) if selected.get("total_volume") is not None else None),
        change_24h_pct=(float(selected["price_change_percentage_24h"]) if selected.get("price_change_percentage_24h") is not None else None),
        last_updated=last_updated if isinstance(last_updated, str) else None,
        age_seconds=age,
        warnings=tuple(warnings),
    )


def validate_finalist_references(
    candidates: list[MarketSnapshot],
    usdt_usd_rate: float | None,
    api_key: str | None = None,
    client: CoinGeckoClient | None = None,
    now: datetime | None = None,
) -> ReferenceValidationSummary:
    client = client or CoinGeckoClient(api_key=api_key)
    api_mode = client.api_mode
    requested = candidates[:8]
    symbols = [_candidate_symbol(item) for item in requested]
    try:
        rows = client.get_markets_by_symbols(symbols)
    except Exception:
        rows = []
        for candidate in requested:
            candidate.independent_market_reference = _unavailable(
                api_mode, "CoinGecko batch request unavailable"
            )
    else:
        for candidate in requested:
            candidate.independent_market_reference = evaluate_reference_market(
                candidate, rows, usdt_usd_rate, api_mode, now
            )
    references = [item.independent_market_reference for item in requested]
    return ReferenceValidationSummary(
        requested=len(requested),
        available=sum(bool(item and item.available) for item in references),
        unavailable=sum(bool(item and not item.available) for item in references),
        ambiguous=sum(bool(item and item.mapping_status == AMBIGUOUS) for item in references),
        api_mode=api_mode,
    )
