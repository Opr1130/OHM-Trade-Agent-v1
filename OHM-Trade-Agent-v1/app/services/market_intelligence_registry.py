from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    category: str
    credential_env: str | None
    priority: int
    optional: bool = True


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec("coinalyze", "derivatives", "COINALYZE_API_KEY", 10),
    ProviderSpec("binance_futures", "derivatives_reference", None, 20),
    ProviderSpec("fred", "macro", "FRED_API_KEY", 30),
    ProviderSpec("deribit_dvol", "volatility", None, 40),
    ProviderSpec("whale_alert", "flows", "WHALE_ALERT_API_KEY", 50),
    ProviderSpec("lunarcrush", "sentiment", "LUNARCRUSH_API_KEY", 60),
    ProviderSpec("santiment", "sentiment_onchain", "SANTIMENT_API_KEY", 70),
    ProviderSpec("glassnode", "onchain", "GLASSNODE_API_KEY", 80),
    ProviderSpec("cryptoquant", "onchain", "CRYPTOQUANT_API_KEY", 90),
    ProviderSpec("coinglass", "derivatives_premium", "COINGLASS_API_KEY", 100),
    ProviderSpec("kaiko", "institutional_market_data", "KAIKO_API_KEY", 110),
    ProviderSpec("amberdata", "institutional_derivatives", "AMBERDATA_API_KEY", 120),
)


def provider_spec(name: str) -> ProviderSpec:
    normalized = name.strip().lower()
    for spec in PROVIDER_SPECS:
        if spec.name == normalized:
            return spec
    raise KeyError(f"unknown market-intelligence provider: {name}")


def build_priority_order(enabled: set[str] | None = None) -> tuple[ProviderSpec, ...]:
    specs = PROVIDER_SPECS
    if enabled is not None:
        normalized = {item.strip().lower() for item in enabled}
        specs = tuple(spec for spec in specs if spec.name in normalized)
    return tuple(sorted(specs, key=lambda item: item.priority))
