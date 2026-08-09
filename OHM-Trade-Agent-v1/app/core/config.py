from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    webhook_secret: str = Field(min_length=12)

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6"
    ai_enabled: bool = False
    coingecko_api_key: str | None = None
    cryptopanic_auth_token: str | None = None
    cryptopanic_api_plan: str = "developer"
    coinmarketcal_api_key: str | None = None

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_enabled: bool = False

    min_alert_score: int = Field(default=80, ge=0, le=100)
    account_equity: float = Field(default=10_000, gt=0)
    risk_per_trade_pct: float = Field(default=0.35, gt=0, le=1)
    max_daily_loss_pct: float = Field(default=1.0, gt=0, le=5)
    max_open_trades: int = Field(default=2, ge=1, le=10)


@lru_cache
def get_settings() -> Settings:
    return Settings()
