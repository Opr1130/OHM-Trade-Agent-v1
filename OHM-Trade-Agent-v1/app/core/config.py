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

    # O'Pip Sequence 2 Event Intelligence Foundation. Dark by default.
    # Enabling this records external evidence only; it does not participate in
    # qualification, ranking, notification, paper admission, or exchange action.
    opip_event_store_enabled: bool = False
    opip_event_ingest_interval_seconds: int = Field(default=300, ge=60, le=3600)
    # Keep external evidence I/O bounded so a shadow provider cannot occupy
    # enough of the unified-cycle minute to starve the next risk-protection pass.
    opip_event_provider_timeout_seconds: float = Field(default=5.0, ge=1.0, le=10.0)
    # Verified WARM event segments are promoted to local COLD storage after
    # this age. COLD is never automatically purged by Event Intelligence.
    opip_event_cold_after_days: int = Field(default=30, ge=1, le=3650)
    # At most one new CoinMarketCal identity lookup per capture by default.
    # This gradually expands non-finalist coverage without creating a burst of
    # provider traffic or delaying higher-priority lifecycle work.
    opip_event_mapping_lookups_per_capture: int = Field(default=1, ge=0, le=4)

    # O'Pip Sequence 4 production shadow streaming worker. This is public
    # market-data evidence only and carries no exchange/trading authority.
    opip_streaming_enabled: bool = False
    opip_streaming_symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT"
    opip_streaming_binance_url: str = "wss://fstream.binance.com/public/stream"
    opip_streaming_bybit_url: str = "wss://stream.bybit.com/v5/public/linear"
    opip_streaming_queue_maxsize: int = Field(default=5000, ge=100, le=10000)
    opip_streaming_retention_hours: int = Field(default=72, ge=24, le=168)
    opip_streaming_health_interval_seconds: float = Field(default=5.0, ge=1.0, le=30.0)

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_enabled: bool = False
    # Optional second boundary for group chats. When set, Telegram commands
    # and lifecycle callback buttons are accepted only from this Telegram user
    # inside the already-configured chat.
    telegram_command_user_id: str | None = None
    telegram_command_rate_limit_per_minute: int = Field(default=12, ge=1, le=60)

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

    # Paper Trade v1 is a physically separate simulation subsystem. Runtime
    # activation is NOT controlled here; the operator toggles it through the
    # dedicated atomic paper-control registry so on/off requires no container
    # rebuild. These fields are simulation assumptions only.
    paper_trade_starting_equity: float = Field(default=10_000.0, gt=0)
    paper_trade_capital_per_trade: float = Field(default=1_000.0, gt=0)
    paper_trade_max_positions: int = Field(default=3, ge=1, le=20)
    paper_trade_fee_rate: float = Field(default=0.004, ge=0.0, le=0.02)
    paper_trade_slippage_bps: float = Field(default=10.0, ge=0.0, le=100.0)
    paper_trade_tp1_fraction: float = Field(default=0.5, gt=0.0, lt=1.0)
    paper_trade_pending_ttl_hours: int = Field(default=24, ge=1, le=168)
    paper_trade_max_hold_hours: int = Field(default=24, ge=1, le=168)
    paper_trade_candle_interval_minutes: int = Field(default=15)

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

    # ---------------------------------------------------------------------
    # Signal Quality / Explosion Detection v1 (Phase 1)
    #
    # Every numeric value below is an INTERPRETABLE PRIOR, not a calibrated
    # figure. Phase 1 has no access to a scored production observation history,
    # so nothing here has been fitted to outcomes. Phase 2 replays
    # full_market_observations.jsonl (correcting for its event-sampling bias)
    # to decide whether these priors actually separate explosive movers from
    # failed pumps. Until then the whole feature ships dark: enabling it only
    # changes which advisory Broad Watch cards are rendered, never trade
    # authority, order placement, or any execution gate.
    # ---------------------------------------------------------------------
    signal_quality_v1_enabled: bool = False
    # Optional widening of the main Broad Watch feed to the earliest stage.
    # Off by default: EARLY_BUILDING is explicitly a pre-confirmation tier.
    signal_quality_early_alerts_enabled: bool = False

    # Phase 3A: forward decision telemetry. Appends one row per scored
    # candidate to a local append-only log, purely so future analysis has the
    # actual live decision state instead of depending only on event-sampled
    # JSONL reconstruction. Writing is fail-soft (a telemetry failure can never
    # affect scoring or alerting) and one-directional (telemetry is never read
    # back into a decision). Composes with signal_quality_v1_enabled: while
    # that flag is off, process_full_market_observations() already returns no
    # candidates, so this flag being on writes nothing regardless.
    decision_telemetry_v1_enabled: bool = False

    # Hard tradeability bands (24h USD notional). Below the minimum a market is
    # suppressed outright no matter how strong its pattern is.
    signal_quality_min_liquidity_usd: float = Field(default=100_000.0, gt=0)
    signal_quality_observation_liquidity_usd: float = Field(default=250_000.0, gt=0)
    signal_quality_preferred_liquidity_usd: float = Field(default=1_000_000.0, gt=0)

    # Runtime scan history. Retained per symbol for temporal features; this is
    # feature state, deliberately separate from the event-sampled JSONL
    # learning stream.
    signal_quality_history_scans: int = Field(default=8, ge=2, le=64)
    # Nominal cadence used to time-normalise per-scan rates. The repository
    # ships no cron entry for app.jobs.scan_movers; 600s mirrors the only
    # shipped scan-class schedule (deploy/cron.d/ohm-wave5-explosion-learning).
    signal_quality_scan_interval_seconds: int = Field(default=600, ge=30, le=7200)
    # A persistence chain survives a gap of up to interval * multiplier, so one
    # late scan does not reset it but a real outage does.
    signal_quality_continuity_multiplier: float = Field(default=2.5, ge=1.0, le=10.0)
    # How long a symbol's history survives without being observed.
    # collect_full_market_observations fail-softs on a ticker batch error, so a
    # symbol can vanish from a scan for reasons that have nothing to do with
    # delisting. Retaining its history across that gap is what stops a
    # transient Kraken error from silently erasing feature state.
    #
    # This is deliberately NOT the continuity window: retention decides whether
    # the evidence is kept, continuity decides whether it still earns
    # persistence credit. A returning symbol keeps its snapshots and loses its
    # chain. The prior is one hour (6 scans at the default cadence); the
    # validator below requires it to outlast the continuity window.
    signal_quality_stale_history_retention_seconds: int = Field(default=3600, ge=300, le=86_400)

    signal_quality_max_cards_per_scan: int = Field(default=4, ge=1, le=20)

    # Stage-machine entry priors. All advisory; ACTIONABLE_REVIEW authorises
    # human review only and never an entry.
    signal_quality_early_building_opportunity: int = Field(default=55, ge=0, le=100)
    signal_quality_early_building_explosion: int = Field(default=50, ge=0, le=100)
    signal_quality_early_building_tradeability: int = Field(default=20, ge=0, le=100)
    signal_quality_breakout_opportunity: int = Field(default=70, ge=0, le=100)
    signal_quality_breakout_explosion: int = Field(default=65, ge=0, le=100)
    signal_quality_breakout_tradeability: int = Field(default=40, ge=0, le=100)
    signal_quality_breakout_min_persistence_scans: int = Field(default=2, ge=1, le=32)
    signal_quality_breakout_max_exhaustion: int = Field(default=25, ge=0, le=100)
    signal_quality_actionable_opportunity: int = Field(default=80, ge=0, le=100)
    signal_quality_actionable_explosion: int = Field(default=75, ge=0, le=100)
    signal_quality_actionable_tradeability: int = Field(default=70, ge=0, le=100)
    signal_quality_actionable_min_persistence_scans: int = Field(default=3, ge=1, le=32)
    signal_quality_actionable_max_exhaustion: int = Field(default=20, ge=0, le=100)

    @model_validator(mode="after")
    def validate_paper_trade_config(self) -> "Settings":
        required_cash = self.paper_trade_capital_per_trade * (
            1.0 + self.paper_trade_fee_rate
        )
        if required_cash > self.paper_trade_starting_equity:
            raise ValueError(
                "PAPER_TRADE capital plus entry fee cannot exceed "
                "PAPER_TRADE_STARTING_EQUITY"
            )
        if self.paper_trade_candle_interval_minutes not in {
            1, 5, 15, 30, 60, 240, 1440, 10080, 21600
        }:
            raise ValueError("PAPER_TRADE_CANDLE_INTERVAL_MINUTES is not a Kraken OHLC interval")
        return self

    @model_validator(mode="after")
    def validate_signal_quality_bands(self) -> "Settings":
        """Keep the Phase 1 priors internally ordered.

        A misordered band would silently invert the gate it exists to enforce
        (for example an observation floor below the hard-suppression floor),
        so this refuses to boot rather than degrading quietly.
        """
        if not (
            self.signal_quality_min_liquidity_usd
            <= self.signal_quality_observation_liquidity_usd
            <= self.signal_quality_preferred_liquidity_usd
        ):
            raise ValueError(
                "SIGNAL_QUALITY liquidity bands must be ordered: "
                "MIN <= OBSERVATION <= PREFERRED"
            )
        ladder = (
            (
                "opportunity",
                self.signal_quality_early_building_opportunity,
                self.signal_quality_breakout_opportunity,
                self.signal_quality_actionable_opportunity,
            ),
            (
                "explosion",
                self.signal_quality_early_building_explosion,
                self.signal_quality_breakout_explosion,
                self.signal_quality_actionable_explosion,
            ),
            (
                "tradeability",
                self.signal_quality_early_building_tradeability,
                self.signal_quality_breakout_tradeability,
                self.signal_quality_actionable_tradeability,
            ),
        )
        for name, early, breakout, actionable in ladder:
            if not (early <= breakout <= actionable):
                raise ValueError(
                    f"SIGNAL_QUALITY {name} stage thresholds must increase: "
                    "EARLY_BUILDING <= BREAKOUT <= ACTIONABLE"
                )
        if (
            self.signal_quality_breakout_min_persistence_scans
            > self.signal_quality_actionable_min_persistence_scans
        ):
            raise ValueError(
                "SIGNAL_QUALITY_ACTIONABLE_MIN_PERSISTENCE_SCANS cannot be below "
                "SIGNAL_QUALITY_BREAKOUT_MIN_PERSISTENCE_SCANS"
            )
        if self.signal_quality_actionable_max_exhaustion > self.signal_quality_breakout_max_exhaustion:
            raise ValueError(
                "SIGNAL_QUALITY_ACTIONABLE_MAX_EXHAUSTION cannot exceed "
                "SIGNAL_QUALITY_BREAKOUT_MAX_EXHAUSTION"
            )
        if self.signal_quality_history_scans < self.signal_quality_actionable_min_persistence_scans + 1:
            raise ValueError(
                "SIGNAL_QUALITY_HISTORY_SCANS must retain at least one scan more "
                "than ACTIONABLE_MIN_PERSISTENCE_SCANS or the top stage is unreachable"
            )
        continuity_window = (
            self.signal_quality_scan_interval_seconds * self.signal_quality_continuity_multiplier
        )
        if self.signal_quality_stale_history_retention_seconds < continuity_window:
            raise ValueError(
                "SIGNAL_QUALITY_STALE_HISTORY_RETENTION_SECONDS must outlast the "
                "continuity window (SCAN_INTERVAL_SECONDS * CONTINUITY_MULTIPLIER = "
                f"{continuity_window:.0f}s). Pruning history before continuity can even "
                "break would delete the evidence a returning symbol needs."
            )
        return self

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
