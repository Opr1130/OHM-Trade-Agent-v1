from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from app.services.coingecko import CoinGeckoAPIError, CoinGeckoClient


AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class CoinGeckoGlobalContext:
    status: str
    api_mode: str
    total_market_cap_usd: float | None = None
    total_volume_usd: float | None = None
    btc_market_cap_percentage: float | None = None
    eth_market_cap_percentage: float | None = None
    market_cap_change_24h_pct: float | None = None
    updated_at: int | None = None
    age_seconds: float | None = None
    warnings: tuple[str, ...] = ()


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def unavailable_global_context(
    api_mode: str,
    warning: str,
) -> CoinGeckoGlobalContext:
    return CoinGeckoGlobalContext(
        status=UNAVAILABLE,
        api_mode=api_mode,
        warnings=(warning,),
    )


def parse_global_market_context(
    data: dict[str, Any],
    api_mode: str,
    now: datetime | None = None,
) -> CoinGeckoGlobalContext:
    now = now or datetime.now(timezone.utc)
    market_cap = data.get("total_market_cap")
    volume = data.get("total_volume")
    dominance = data.get("market_cap_percentage")
    updated_at_value = data.get("updated_at")
    updated_at = updated_at_value if isinstance(updated_at_value, int) else None
    age_seconds = (
        max(0.0, now.timestamp() - updated_at)
        if updated_at is not None
        else None
    )
    warnings: list[str] = []
    if updated_at is None:
        warnings.append("CoinGecko global updated_at is unavailable")
    return CoinGeckoGlobalContext(
        status=AVAILABLE,
        api_mode=api_mode,
        total_market_cap_usd=_number(
            market_cap.get("usd") if isinstance(market_cap, dict) else None
        ),
        total_volume_usd=_number(
            volume.get("usd") if isinstance(volume, dict) else None
        ),
        btc_market_cap_percentage=_number(
            dominance.get("btc") if isinstance(dominance, dict) else None
        ),
        eth_market_cap_percentage=_number(
            dominance.get("eth") if isinstance(dominance, dict) else None
        ),
        market_cap_change_24h_pct=_number(
            data.get("market_cap_change_percentage_24h_usd")
        ),
        updated_at=updated_at,
        age_seconds=age_seconds,
        warnings=tuple(warnings),
    )


def load_coingecko_global_context(
    api_key: str | None = None,
    client: CoinGeckoClient | None = None,
    now: datetime | None = None,
) -> CoinGeckoGlobalContext:
    client = client or CoinGeckoClient(api_key=api_key)
    try:
        data = client.get_global_market()
    except CoinGeckoAPIError:
        return unavailable_global_context(
            client.api_mode, "CoinGecko global request unavailable"
        )
    return parse_global_market_context(data, client.api_mode, now)
