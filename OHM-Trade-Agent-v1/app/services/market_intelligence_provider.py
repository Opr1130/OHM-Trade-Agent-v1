from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.services.market_intelligence_models import MarketIntelligenceBundle


class MarketIntelligenceProvider(Protocol):
    name: str

    def fetch(self, symbol: str) -> MarketIntelligenceBundle:
        """Return normalized evidence for one symbol.

        Implementations must fail closed into UNAVAILABLE/partial evidence rather
        than inventing zero values. Provider exceptions should be caught by the
        orchestration layer so no external feed can stop the scanner.
        """
        ...


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    ok: bool
    bundle: MarketIntelligenceBundle | None = None
    error: str | None = None
    error_kind: str | None = None
    rate_limited: bool = False
    retry_after_seconds: int | None = None


def collect_market_intelligence(
    symbol: str,
    providers: list[MarketIntelligenceProvider],
) -> tuple[ProviderResult, ...]:
    results: list[ProviderResult] = []
    for provider in providers:
        try:
            bundle = provider.fetch(symbol)
        except Exception as exc:
            results.append(
                ProviderResult(
                    provider=provider.name,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                    error_kind=type(exc).__name__,
                    rate_limited=bool(
                        getattr(exc, "retry_after_seconds", None) is not None
                        or "RateLimit" in type(exc).__name__
                    ),
                    retry_after_seconds=getattr(
                        exc,
                        "retry_after_seconds",
                        None,
                    ),
                )
            )
            continue
        results.append(ProviderResult(provider=provider.name, ok=True, bundle=bundle))
    return tuple(results)
