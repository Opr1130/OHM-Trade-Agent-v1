from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
