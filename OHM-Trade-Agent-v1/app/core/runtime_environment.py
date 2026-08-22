from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeEnvironmentSettings(BaseSettings):
    """Read only runtime-mode fields using the same .env semantics as Settings."""

    app_env: str = "unknown"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_runtime_app_env() -> str:
    return (RuntimeEnvironmentSettings().app_env or "unknown").strip().lower()


def conservative_runtime() -> bool:
    return get_runtime_app_env() not in {"development", "dev", "test", "testing"}
