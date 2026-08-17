from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Shipped verbatim in TRADINGVIEW_WAVE8_2.md as a copy-pasteable example. It is
# a fixed, publicly documented string, not a secret with any entropy — an
# operator who deploys it unchanged gets no real second factor beyond network
# trust. Settings.validate_tradingview_production_secret refuses to boot with
# it while the bridge is enabled in production.
_DEFAULT_TRADINGVIEW_VERIFICATION_VALUE = "verified-by-trusted-proxy"
_MIN_TRADINGVIEW_BEARER_LENGTH = 43


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

    # PRICE_MOVEMENT_MODE: off disables the radar, shadow records evidence and
    # outcomes without messages, alert additionally permits non-actionable
    # WATCH/READY Telegram updates. Confirmed entries still require every
    # existing OHM trade gate.
    price_movement_mode: str = Field(
        default="shadow",
        pattern=r"^(off|shadow|alert)$",
    )
    price_movement_watch_score: int = Field(default=35, ge=0, le=100)
    price_movement_ready_score: int = Field(default=70, ge=0, le=100)
    price_movement_expiry_hours: int = Field(default=12, ge=1, le=72)
    price_movement_alert_cooldown_seconds: int = Field(
        default=21_600,
        ge=300,
        le=86_400,
    )

    min_alert_score: int = Field(default=80, ge=0, le=100)
    account_equity: float = Field(default=10_000, gt=0)
    risk_per_trade_pct: float = Field(default=0.35, gt=0, le=1)
    max_daily_loss_pct: float = Field(default=1.0, gt=0, le=5)
    max_open_trades: int = Field(default=2, ge=1, le=10)

    # Margin Intelligence v1 is advisory/manual only. The configured ceiling
    # mirrors what the account is permitted to use, while validation remains
    # conservatively fixed at 2x until outcome data justifies anything else.
    max_margin_leverage: float = Field(default=3.0, ge=2.0, le=3.0)

    # Wave 8.2 TradingView Intelligence Bridge: disabled until an operator
    # deliberately enables it, configures the private-network verification
    # boundary, and (in production) rotates the verification value away from
    # the documented default. TradingView is candidate evidence only; it can
    # never place, confirm, or override anything downstream of the inbox —
    # see app/services/tradingview_inbox.py.
    tradingview_v2_enabled: bool = False
    tradingview_trusted_proxy_ips: str = "127.0.0.1,::1"
    # Bounds are intentionally tighter than earlier drafts: a freshness window
    # or divergence tolerance loose enough to "always pass" quietly defeats
    # the gate it is supposed to be. 30 minutes / 3% are already generous for
    # a 15-minute-bar strategy.
    tradingview_max_event_age_seconds: int = Field(default=300, ge=30, le=1800)
    tradingview_future_tolerance_seconds: int = Field(default=30, ge=0, le=300)
    tradingview_max_price_divergence_pct: float = Field(default=1.0, gt=0, le=3.0)
    tradingview_dedup_window_seconds: int = Field(default=900, ge=60, le=86400)
    tradingview_max_payload_bytes: int = Field(default=32768, ge=1024, le=262144)
    tradingview_expected_schema_version: str = "2"
    tradingview_allowed_script_versions: str = "ohm-signal-v1"
    tradingview_internal_verification_value: str = _DEFAULT_TRADINGVIEW_VERIFICATION_VALUE
    # Per-deployment bearer credential embedded in the TradingView alert body.
    # The reverse-proxy header proves the request crossed the expected network
    # boundary; this token proves the alert belongs to this OHM deployment.
    # It is removed before schema validation and is never persisted.
    tradingview_webhook_token: str | None = None
    # Retention/backstop knobs so the durable inbox cannot grow unbounded and
    # a permanently-failing event cannot retry forever.
    tradingview_max_attempts: int = Field(default=5, ge=1, le=20)
    tradingview_retention_seconds: int = Field(default=7 * 24 * 3600, ge=3600, le=30 * 24 * 3600)
    tradingview_processing_timeout_seconds: int = Field(default=120, ge=10, le=900)
    tradingview_processing_batch_limit: int = Field(default=2, ge=1, le=3)
    tradingview_kraken_timeout_seconds: float = Field(default=5.0, ge=1.0, le=10.0)
    tradingview_max_inbox_events: int = Field(default=5000, ge=100, le=50000)

    @model_validator(mode="after")
    def validate_tradingview_production_secret(self) -> "Settings":
        if self.price_movement_watch_score > self.price_movement_ready_score:
            raise ValueError(
                "PRICE_MOVEMENT_WATCH_SCORE cannot exceed PRICE_MOVEMENT_READY_SCORE"
            )
        if not self.tradingview_v2_enabled:
            return self

        token = (self.tradingview_webhook_token or "").strip()
        if len(token) < _MIN_TRADINGVIEW_BEARER_LENGTH:
            raise ValueError(
                "TRADINGVIEW_V2_ENABLED requires a unique URL-safe "
                f"TRADINGVIEW_WEBHOOK_TOKEN of at least {_MIN_TRADINGVIEW_BEARER_LENGTH} characters."
            )
        if not all(char.isalnum() or char in "-_" for char in token):
            raise ValueError("TRADINGVIEW_WEBHOOK_TOKEN must be URL-safe (letters, digits, '-' and '_' only).")
        self.tradingview_webhook_token = token

        if self.app_env.strip().casefold() == "production":
            verification = self.tradingview_internal_verification_value.strip()
            if (
                verification.casefold() == _DEFAULT_TRADINGVIEW_VERIFICATION_VALUE.casefold()
                or len(verification) < 32
            ):
                raise ValueError(
                    "TRADINGVIEW_V2_ENABLED is true in production but "
                    "TRADINGVIEW_INTERNAL_VERIFICATION_VALUE is the documented default "
                    "or shorter than 32 characters. Generate a unique value before enabling."
                )
            self.tradingview_internal_verification_value = verification
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
