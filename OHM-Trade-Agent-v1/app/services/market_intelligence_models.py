from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
STALE = "STALE"


@dataclass(frozen=True)
class DerivativesSnapshot:
    symbol: str
    provider: str
    status: str = AVAILABLE
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    funding_rate_pct: float | None = None
    open_interest_usd: float | None = None
    open_interest_change_15m_pct: float | None = None
    open_interest_change_1h_pct: float | None = None
    open_interest_change_24h_pct: float | None = None
    open_interest_acceleration_zscore: float | None = None
    liquidations_1h_usd: float | None = None
    liquidations_24h_usd: float | None = None
    long_short_ratio: float | None = None
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VolatilitySnapshot:
    asset: str
    provider: str
    status: str = AVAILABLE
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    implied_volatility_index: float | None = None
    change_24h_pct: float | None = None
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MacroSnapshot:
    provider: str
    status: str = AVAILABLE
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fed_funds_rate_pct: float | None = None
    ten_year_yield_pct: float | None = None
    two_year_yield_pct: float | None = None
    dollar_index: float | None = None
    liquidity_signal: str | None = None
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FlowSnapshot:
    asset: str
    provider: str
    status: str = AVAILABLE
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    whale_transfer_count_1h: int | None = None
    whale_transfer_usd_1h: float | None = None
    exchange_inflow_usd_1h: float | None = None
    exchange_outflow_usd_1h: float | None = None
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SentimentSnapshot:
    asset: str
    provider: str
    status: str = AVAILABLE
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sentiment_score: float | None = None
    social_volume_change_24h_pct: float | None = None
    social_dominance_pct: float | None = None
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketIntelligenceBundle:
    symbol: str
    derivatives: tuple[DerivativesSnapshot, ...] = ()
    volatility: tuple[VolatilitySnapshot, ...] = ()
    macro: MacroSnapshot | None = None
    flows: tuple[FlowSnapshot, ...] = ()
    sentiment: tuple[SentimentSnapshot, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
