from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.scanner.execution_validation import ExecutionValidation
    from app.scanner.market_data_validation import MarketDataValidation
    from app.scanner.reference_market_validation import ReferenceMarketValidation


@dataclass
class ScoreCard:
    score: int
    strengths: list[str]
    weaknesses: list[str]
    warnings: list[str]


@dataclass
class MarketSnapshot:
    symbol: str
    last_price: float

    ema20: float
    ema50: float
    ema200: float
    rsi: float

    macd_line: float
    macd_signal: float
    macd_histogram: float

    atr: float
    atr_pct: float
    volume_ratio: float

    technical_score: int
    trend: str

    recent_24h_high: float = 0.0
    recent_24h_low: float = 0.0
    recent_72h_high: float = 0.0
    recent_72h_low: float = 0.0

    momentum_6h_pct: float = 0.0
    momentum_24h_pct: float = 0.0
    momentum_72h_pct: float = 0.0

    distance_to_24h_high_pct: float = 0.0
    distance_to_72h_high_pct: float = 0.0

    realized_range_24h_pct: float = 0.0
    realized_range_72h_pct: float = 0.0
    average_hourly_range_24h_pct: float = 0.0
    average_hourly_range_72h_pct: float = 0.0

    rolling_24h_range_median_pct: float = 0.0
    rolling_24h_range_p75_pct: float = 0.0
    rolling_24h_range_p90_pct: float = 0.0
    rolling_72h_range_median_pct: float = 0.0
    rolling_72h_range_p75_pct: float = 0.0
    rolling_72h_range_p90_pct: float = 0.0

    rolling_24h_upside_median_pct: float = 0.0
    rolling_24h_upside_p75_pct: float = 0.0
    rolling_24h_upside_p90_pct: float = 0.0
    rolling_72h_upside_median_pct: float = 0.0
    rolling_72h_upside_p75_pct: float = 0.0
    rolling_72h_upside_p90_pct: float = 0.0

    underlying_asset: str = ""
    primary_pair: str = ""
    secondary_pair: str | None = None
    primary_quote_currency: str = ""
    primary_24h_liquidity_usd: float = 0.0
    secondary_24h_liquidity_usd: float = 0.0
    combined_24h_liquidity_usd: float = 0.0
    liquidity_rank: int = 0

    secondary_volume_ratio: float | None = None
    cross_pair_confirmation_status: str = "SINGLE_MARKET"
    cross_pair_strengths: list[str] | None = None
    cross_pair_warnings: list[str] | None = None
    kraken_public_symbol: str = ""
    ticker_last: float = 0.0
    ticker_bid: float = 0.0
    ticker_ask: float = 0.0
    market_data_validation: MarketDataValidation | None = None
    execution_validation: ExecutionValidation | None = None
    cross_pair_price_divergence_pct: float | None = None
    cross_pair_price_status: str = "UNAVAILABLE"
    independent_market_reference: ReferenceMarketValidation | None = None
