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
