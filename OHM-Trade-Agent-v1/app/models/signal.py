from datetime import datetime, timezone
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TradingSignal(BaseModel):
    symbol: str = Field(min_length=2, max_length=30)
    asset_class: Literal["crypto", "stock"]
    timeframe: str = Field(default="4h", max_length=10)
    side: Literal["long", "short"] = "long"
    price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    target_price: float = Field(gt=0)

    rsi: float = Field(ge=0, le=100)
    volume_ratio: float = Field(description="Current volume divided by average volume", ge=0)
    ema_fast: float = Field(gt=0)
    ema_slow: float = Field(gt=0)
    breakout: bool = False
    market_regime: Literal["bullish", "neutral", "bearish"] = "neutral"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_prices(self) -> "TradingSignal":
        if self.side == "long" and not (self.stop_price < self.price < self.target_price):
            raise ValueError("Long signal requires stop_price < price < target_price")
        if self.side == "short" and not (self.target_price < self.price < self.stop_price):
            raise ValueError("Short signal requires target_price < price < stop_price")
        return self


class RiskPlan(BaseModel):
    risk_dollars: float
    position_size: float
    reward_to_risk: float
    allowed: bool
    rejection_reason: str | None = None


class SignalDecision(BaseModel):
    symbol: str
    deterministic_score: int = Field(ge=0, le=100)
    ai_score: int | None = Field(default=None, ge=0, le=100)
    final_score: int = Field(ge=0, le=100)
    action: Literal["alert", "watch", "reject"]
    summary: str
    risk: RiskPlan


class TradingViewSignalV2(BaseModel):
    """Strict, credential-free candidate evidence emitted by the Pine
    indicator. TradingView is candidate evidence only — this model has no
    field, and no downstream consumer, that can place an order, confirm a
    fill, or change portfolio/risk state. See app/services/tradingview_inbox.py.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = Field(min_length=1, max_length=16)
    # Regex-constrained even though validate_event_policy() also checks this
    # against an operator allowlist — defense in depth for any future caller
    # that constructs this model without going through that check first.
    script_version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    signal_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    symbol: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9:/._-]+$")
    exchange: Literal["KRAKEN"]
    direction: Literal["LONG", "SHORT"]
    bar_closed_at: datetime
    timeframe: Literal["15"]
    bar_confirmed: Literal[True]
    regime_4h: Literal["BULLISH", "NEUTRAL", "BEARISH"]
    setup_1h: Literal["BREAKOUT", "PULLBACK", "REVERSAL"]
    entry_trigger_15m: Literal["BREAKOUT", "RECLAIM", "REVERSAL"]
    technical_score: float = Field(ge=0, le=100)
    trend_score: float = Field(ge=0, le=100)
    momentum_score: float = Field(ge=0, le=100)
    volume_score: float = Field(ge=0, le=100)
    structure_score: float = Field(ge=0, le=100)
    volatility_score: float = Field(ge=0, le=100)
    tradingview_close: float = Field(gt=0)
    ema_20: float = Field(gt=0)
    ema_50: float = Field(gt=0)
    ema_200: float = Field(gt=0)
    adx: float = Field(ge=0, le=100)
    rsi: float = Field(ge=0, le=100)
    macd_histogram: float
    relative_volume: float = Field(ge=0)
    atr: float = Field(gt=0)
    atr_percentage: float = Field(gt=0, le=100)
    vwap: float = Field(gt=0)
    warnings: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("bar_closed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("bar_closed_at must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator(
        "technical_score", "trend_score", "momentum_score", "volume_score",
        "structure_score", "volatility_score", "tradingview_close", "ema_20",
        "ema_50", "ema_200", "adx", "rsi", "macd_histogram",
        "relative_volume", "atr", "atr_percentage", "vwap",
    )
    @classmethod
    def finite_numbers_only(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("numeric values must be finite")
        return value

    @field_validator("warnings")
    @classmethod
    def bounded_warnings(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 160 for item in value):
            raise ValueError("warnings must be non-empty and at most 160 characters")
        # Free text is never forwarded to the Chief AI payload (see
        # tradingview_inbox.py evidence-building functions) and is stripped of
        # control characters before it is ever persisted or logged.
        return [
            "".join(char for char in item if char.isprintable())
            for item in value
        ]
