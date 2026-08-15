from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from app.services.coinalyze_market_intelligence import (
    CoinalyzeMarketIntelligenceProvider,
)
from app.services.market_intelligence import (
    MarketIntelligenceAssessment,
    assess_market_intelligence,
)
from app.services.market_intelligence_models import MarketIntelligenceBundle
from app.services.market_intelligence_provider import (
    MarketIntelligenceProvider,
    ProviderResult,
    collect_market_intelligence,
)


DEFAULT_EXTERNAL_ENRICHMENT_CANDIDATES = 8


def build_configured_market_intelligence_providers(
    env: Mapping[str, str] | None = None,
) -> tuple[MarketIntelligenceProvider, ...]:
    """Build only providers whose runtime credentials/config are available.

    Credentials are read from process environment and are never copied into
    provider specs, snapshots, warnings, or persisted evidence.
    """
    source = os.environ if env is None else env
    providers: list[MarketIntelligenceProvider] = []

    coinalyze_key = source.get("COINALYZE_API_KEY", "").strip()
    if coinalyze_key:
        providers.append(CoinalyzeMarketIntelligenceProvider(api_key=coinalyze_key))

    return tuple(providers)


def merge_market_intelligence_results(
    symbol: str,
    results: Sequence[ProviderResult],
) -> MarketIntelligenceBundle:
    """Merge successful provider bundles without manufacturing missing data."""
    bundles = [result.bundle for result in results if result.ok and result.bundle is not None]

    derivatives = tuple(
        item
        for bundle in bundles
        for item in bundle.derivatives
    )
    volatility = tuple(
        item
        for bundle in bundles
        for item in bundle.volatility
    )
    flows = tuple(
        item
        for bundle in bundles
        for item in bundle.flows
    )
    sentiment = tuple(
        item
        for bundle in bundles
        for item in bundle.sentiment
    )

    macro_candidates = [bundle.macro for bundle in bundles if bundle.macro is not None]
    macro = (
        max(macro_candidates, key=lambda item: item.observed_at)
        if macro_candidates
        else None
    )

    return MarketIntelligenceBundle(
        symbol=symbol,
        derivatives=derivatives,
        volatility=volatility,
        macro=macro,
        flows=flows,
        sentiment=sentiment,
    )


def build_market_intelligence_assessments(
    candidates: Sequence[tuple[str, str]],
    *,
    providers: Sequence[MarketIntelligenceProvider] | None = None,
    max_candidates: int = DEFAULT_EXTERNAL_ENRICHMENT_CANDIDATES,
) -> dict[str, MarketIntelligenceAssessment]:
    """Enrich a bounded finalist list and return symbol-keyed assessments.

    `candidates` contains `(symbol, direction)` pairs ordered by the upstream
    scanner. The cap prevents broad-universe scans from exhausting external
    provider budgets. External intelligence remains optional context only.
    """
    if max_candidates < 0:
        raise ValueError("max_candidates cannot be negative")

    configured = tuple(providers) if providers is not None else build_configured_market_intelligence_providers()
    if not configured or max_candidates == 0:
        return {}

    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_symbol, raw_direction in candidates:
        symbol = raw_symbol.strip()
        if not symbol or symbol in seen:
            continue
        selected.append((symbol, raw_direction.strip().upper() or "LONG"))
        seen.add(symbol)
        if len(selected) >= max_candidates:
            break

    assessments: dict[str, MarketIntelligenceAssessment] = {}
    for symbol, direction in selected:
        results = collect_market_intelligence(symbol, list(configured))
        bundle = merge_market_intelligence_results(symbol, results)
        assessments[symbol] = assess_market_intelligence(bundle, direction=direction)

    return assessments
