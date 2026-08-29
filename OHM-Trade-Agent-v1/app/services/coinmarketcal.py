from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx


COINMARKETCAL_API_BASE = "https://api.coinmarketcal.com/v2"


class CoinMarketCalAPIError(RuntimeError):
    """Expected CoinMarketCal failure with secret-safe health metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds

    @property
    def rate_limited(self) -> bool:
        return self.status_code == 429


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
            status_code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            retry_after = None
            if isinstance(exc, httpx.HTTPStatusError):
                raw_retry = exc.response.headers.get("Retry-After")
                try:
                    retry_after = int(raw_retry) if raw_retry is not None else None
                except ValueError:
                    retry_after = None
            raise CoinMarketCalAPIError(
                f"CoinMarketCal {path} request failed",
                status_code=status_code,
                retry_after_seconds=retry_after,
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
