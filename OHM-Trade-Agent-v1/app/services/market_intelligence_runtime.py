from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from typing import Any

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


@dataclass(frozen=True)
class MarketIntelligenceEvidence:
    """Normalized finalist evidence retained for review and shadow learning."""

    direction: str
    bundle: MarketIntelligenceBundle
    assessment: MarketIntelligenceAssessment
    provider_errors: tuple[str, ...] = ()


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


def _selected_candidates(
    candidates: Sequence[tuple[str, str]],
    max_candidates: int,
) -> list[tuple[str, str]]:
    if max_candidates < 0:
        raise ValueError("max_candidates cannot be negative")

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
    return selected


def build_market_intelligence_evidence(
    candidates: Sequence[tuple[str, str]],
    *,
    providers: Sequence[MarketIntelligenceProvider] | None = None,
    max_candidates: int = DEFAULT_EXTERNAL_ENRICHMENT_CANDIDATES,
) -> dict[str, MarketIntelligenceEvidence]:
    """Collect bounded finalist evidence without exposing provider credentials."""
    configured = (
        tuple(providers)
        if providers is not None
        else build_configured_market_intelligence_providers()
    )
    if not configured or max_candidates == 0:
        if max_candidates < 0:
            raise ValueError("max_candidates cannot be negative")
        return {}

    selected = _selected_candidates(candidates, max_candidates)
    evidence: dict[str, MarketIntelligenceEvidence] = {}
    for symbol, direction in selected:
        results = collect_market_intelligence(symbol, list(configured))
        bundle = merge_market_intelligence_results(symbol, results)
        assessment = assess_market_intelligence(bundle, direction=direction)
        provider_errors = tuple(
            f"{result.provider}: {result.error or 'provider failed'}"
            for result in results
            if not result.ok
        )
        evidence[symbol] = MarketIntelligenceEvidence(
            direction=direction,
            bundle=bundle,
            assessment=assessment,
            provider_errors=provider_errors,
        )
    return evidence


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
    evidence = build_market_intelligence_evidence(
        candidates,
        providers=providers,
        max_candidates=max_candidates,
    )
    return {symbol: item.assessment for symbol, item in evidence.items()}


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def serialize_market_intelligence_evidence(
    evidence: MarketIntelligenceEvidence,
) -> dict[str, Any]:
    """Return JSON-safe evidence suitable for Chief context and shadow storage.

    The evidence object contains normalized market fields only. API keys live on
    provider client objects and are never reachable from this serialized shape.
    """
    return {
        "direction": evidence.direction,
        "assessment": _json_safe(evidence.assessment),
        "bundle": _json_safe(evidence.bundle),
        "provider_errors": list(evidence.provider_errors),
        "authority": "context_only_no_gate_override",
        "context_score_is_probability": False,
    }
