"""Dedicated least-privilege configuration for the public stream worker."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.opip.streaming.binance import DEFAULT_BINANCE_PUBLIC_STREAM_URL
from app.opip.streaming.bybit import DEFAULT_BYBIT_PUBLIC_LINEAR_URL


class StreamingWorkerSettings(BaseSettings):
    """Only OPIP_STREAMING_* variables are visible to the worker."""

    model_config = SettingsConfigDict(
        env_prefix="OPIP_STREAMING_",
        extra="ignore",
    )

    enabled: bool = False
    symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT"
    binance_url: str = DEFAULT_BINANCE_PUBLIC_STREAM_URL
    bybit_url: str = DEFAULT_BYBIT_PUBLIC_LINEAR_URL
    queue_maxsize: int = Field(default=5000, ge=100, le=10000)
    retention_hours: int = Field(default=72, ge=24, le=168)
    health_interval_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
