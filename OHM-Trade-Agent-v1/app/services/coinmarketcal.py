from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx


COINMARKETCAL_API_BASE = "https://api.coinmarketcal.com/v2"


class CoinMarketCalAPIError(RuntimeError):
    """Raised for expected CoinMarketCal network/API/response failures."""


class CoinMarketCalClient:
    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _get(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            response = httpx.get(
                f"{COINMARKETCAL_API_BASE}{path}",
                params=params,
                headers={
                    "x-api-key": self.api_key,
                    "Accept": "application/json",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CoinMarketCalAPIError(
                f"CoinMarketCal {path} request failed"
            ) from exc
        if not isinstance(payload, dict):
            raise CoinMarketCalAPIError(
                f"CoinMarketCal {path} response was invalid"
            )
        body = payload.get("data")
        if body is None:
            body = payload.get("body")
        if isinstance(body, dict):
            body = body.get("data") or body.get("events") or body.get("coins")
        if not isinstance(body, list):
            raise CoinMarketCalAPIError(
                f"CoinMarketCal {path} response body was invalid"
            )
        return [item for item in body if isinstance(item, dict)]

    def get_coins(self, query: str) -> list[dict[str, Any]]:
        return self._get("/coins", {"q": query, "limit": 200})

    def get_events(
        self,
        slugs: list[str],
        from_time: datetime,
        to_time: datetime,
    ) -> list[dict[str, Any]]:
        normalized = sorted({slug for slug in slugs if slug})
        if not normalized:
            return []
        return self._get(
            "/events",
            {
                "coins": ",".join(normalized),
                "from": from_time.isoformat(),
                "to": to_time.isoformat(),
                "limit": 100,
            },
        )
