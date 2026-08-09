from dataclasses import dataclass


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
