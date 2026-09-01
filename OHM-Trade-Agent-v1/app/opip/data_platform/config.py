from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value or default))
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class DataPlatformConfig:
    """Runtime settings with safe, disabled defaults.

    The shipper settings are used only on the analytics host.  The dashboard
    DSN is a separate read-only credential and is consulted only by dashboard
    historical reads.
    """

    shipper_enabled: bool = False
    historical_reads_enabled: bool = False
    database_url: str | None = None
    dashboard_database_url: str | None = None
    data_root: Path = Path("/var/lib/opip-learning/data")
    batch_size: int = 1000
    poll_seconds: int = 30
    connect_timeout_seconds: int = 2
    statement_timeout_ms: int = 1500

    @classmethod
    def from_env(cls) -> "DataPlatformConfig":
        return cls(
            shipper_enabled=_enabled(os.getenv("OPIP_DATA_PLATFORM_SHIPPER_ENABLED")),
            historical_reads_enabled=_enabled(
                os.getenv("OPIP_DATA_PLATFORM_READS_ENABLED")
            ),
            database_url=(os.getenv("OPIP_ANALYTICS_DATABASE_URL") or None),
            dashboard_database_url=(
                os.getenv("OPIP_DASHBOARD_DATABASE_URL") or None
            ),
            data_root=Path(
                os.getenv("OPIP_DATA_PLATFORM_DATA_ROOT", "/var/lib/opip-learning/data")
            ),
            batch_size=_positive_int(os.getenv("OPIP_DATA_PLATFORM_BATCH_SIZE"), 1000),
            poll_seconds=_positive_int(os.getenv("OPIP_DATA_PLATFORM_POLL_SECONDS"), 30),
            connect_timeout_seconds=_positive_int(
                os.getenv("OPIP_DATA_PLATFORM_CONNECT_TIMEOUT_SECONDS"), 2
            ),
            statement_timeout_ms=_positive_int(
                os.getenv("OPIP_DATA_PLATFORM_STATEMENT_TIMEOUT_MS"), 1500
            ),
        )

    def require_shipper_dsn(self) -> str:
        if not self.shipper_enabled:
            raise RuntimeError("O'Pip data-platform shipper is disabled")
        if not self.database_url:
            raise RuntimeError("OPIP_ANALYTICS_DATABASE_URL is required")
        return self.database_url

    def dashboard_dsn(self) -> str | None:
        if not self.historical_reads_enabled:
            return None
        return self.dashboard_database_url
