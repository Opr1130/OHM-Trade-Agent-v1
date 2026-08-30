"""Explicit, code-reviewed streaming venue instrument identities.

These mappings are configuration, not ticker inference. Only an exact
(provider, provider_symbol) entry can produce canonical identity. Unknown
symbols fail closed and may still contribute transport telemetry, but they
cannot participate in asset-specific or cross-venue features.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.opip.events.contract import MappingStatus
from app.opip.streaming.contract import StreamProvider


@dataclass(frozen=True)
class StreamingInstrumentIdentity:
    provider: StreamProvider
    provider_symbol: str
    canonical_asset_id: str
    canonical_asset_name: str

    def __post_init__(self) -> None:
        if not self.provider_symbol.strip():
            raise ValueError("provider_symbol is required")
        if not self.canonical_asset_id.strip():
            raise ValueError("canonical_asset_id is required")
        if not self.canonical_asset_name.strip():
            raise ValueError("canonical_asset_name is required")


_INITIAL_IDENTITIES = (
    StreamingInstrumentIdentity(
        StreamProvider.BINANCE, "BTCUSDT", "bitcoin", "Bitcoin"
    ),
    StreamingInstrumentIdentity(
        StreamProvider.BINANCE, "ETHUSDT", "ethereum", "Ethereum"
    ),
    StreamingInstrumentIdentity(
        StreamProvider.BINANCE, "SOLUSDT", "solana", "Solana"
    ),
    StreamingInstrumentIdentity(
        StreamProvider.BYBIT, "BTCUSDT", "bitcoin", "Bitcoin"
    ),
    StreamingInstrumentIdentity(
        StreamProvider.BYBIT, "ETHUSDT", "ethereum", "Ethereum"
    ),
    StreamingInstrumentIdentity(
        StreamProvider.BYBIT, "SOLUSDT", "solana", "Solana"
    ),
)
_INDEX = {
    (row.provider, row.provider_symbol.upper()): row
    for row in _INITIAL_IDENTITIES
}


def resolve_streaming_instrument(
    provider: StreamProvider,
    provider_symbol: str,
) -> tuple[MappingStatus, str | None, str | None]:
    """Resolve only an explicitly reviewed venue instrument binding."""
    row = _INDEX.get((provider, str(provider_symbol or "").strip().upper()))
    if row is None:
        return MappingStatus.UNKNOWN, None, None
    return MappingStatus.UNIQUE, row.canonical_asset_id, row.canonical_asset_name


def initial_symbols(provider: StreamProvider) -> tuple[str, ...]:
    """Return the exact <=3-symbol initial production shadow universe."""
    return tuple(
        row.provider_symbol
        for row in _INITIAL_IDENTITIES
        if row.provider is provider
    )
