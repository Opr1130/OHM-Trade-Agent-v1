from __future__ import annotations

from typing import Any

import httpx


COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoAPIError(RuntimeError):
    """Expected CoinGecko failure with secret-safe health metadata."""

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
            raise CoinGeckoAPIError(
                "CoinGecko markets request failed",
                status_code=status_code,
                retry_after_seconds=retry_after,
            ) from exc
        if not isinstance(payload, list):
            raise CoinGeckoAPIError("CoinGecko markets response was not a list")
        return [item for item in payload if isinstance(item, dict)]

    def get_global_market(self) -> dict[str, Any]:
        """Return CoinGecko's independent aggregate market snapshot."""
        headers = (
            {"x-cg-demo-api-key": self.api_key}
            if self.api_key
            else {}
        )
        try:
            response = httpx.get(
                f"{COINGECKO_API_BASE}/global",
                headers=headers,
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
            raise CoinGeckoAPIError(
                "CoinGecko global request failed",
                status_code=status_code,
                retry_after_seconds=retry_after,
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise CoinGeckoAPIError("CoinGecko global response was invalid")
        return payload["data"]
