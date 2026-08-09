from __future__ import annotations

from typing import Any

import httpx


COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoAPIError(RuntimeError):
    """Raised when the public/demo CoinGecko market request fails."""


class CoinGeckoClient:
    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    @property
    def api_mode(self) -> str:
        return "DEMO_KEY" if self.api_key else "KEYLESS"

    def get_markets_by_symbols(
        self,
        symbols: list[str],
    ) -> list[dict[str, Any]]:
        normalized = sorted({symbol.lower() for symbol in symbols if symbol})
        if not normalized:
            return []
        headers = (
            {"x-cg-demo-api-key": self.api_key}
            if self.api_key
            else {}
        )
        try:
            response = httpx.get(
                f"{COINGECKO_API_BASE}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "symbols": ",".join(normalized),
                    "include_tokens": "all",
                    "sparkline": "false",
                    "per_page": 250,
                    "page": 1,
                },
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CoinGeckoAPIError("CoinGecko markets request failed") from exc
        if not isinstance(payload, list):
            raise CoinGeckoAPIError("CoinGecko markets response was not a list")
        return [item for item in payload if isinstance(item, dict)]
