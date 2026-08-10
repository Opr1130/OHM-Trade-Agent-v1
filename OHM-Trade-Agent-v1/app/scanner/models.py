from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.scanner.execution_validation import ExecutionValidation
    from app.scanner.market_data_validation import MarketDataValidation
    from app.scanner.news_context import NewsContext
    from app.scanner.reference_market_validation import ReferenceMarketValidation
    from app.scanner.scheduled_catalysts import ScheduledCatalystContext


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
    distance_to_24h_low_pct: float = 0.0
    distance_to_72h_low_pct: float = 0.0

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

    rolling_24h_downside_median_pct: float = 0.0
    rolling_24h_downside_p75_pct: float = 0.0
    rolling_24h_downside_p90_pct: float = 0.0
    rolling_72h_downside_median_pct: float = 0.0
    rolling_72h_downside_p75_pct: float = 0.0
    rolling_72h_downside_p90_pct: float = 0.0

    # LONG is the backwards-compatible default. Short-list candidates are
    # cloned with SHORT and a direction-specific technical score.
    trade_direction: str = "LONG"

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
    news_context: NewsContext | None = None
    scheduled_catalyst_context: ScheduledCatalystContext | None = None

    # US retail margin evidence. These fields are informational/advisory only;
    # no automatic Kraken order is placed by v1.
    margin_eligible: bool = False
    margin_venue_symbol: str | None = None
    margin_max_leverage: float | None = None
    margin_validation_status: str = "NOT_REQUIRED"
    margin_warnings: list[str] | None = None
