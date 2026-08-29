from __future__ import annotations

from typing import Any

import httpx


CRYPTOPANIC_API_ROOT = "https://cryptopanic.com/api"
ALLOWED_API_PLANS = {"developer", "growth", "enterprise"}


class CryptoPanicAPIError(RuntimeError):
    """Expected CryptoPanic failure with secret-safe health metadata."""

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


class CryptoPanicClient:
    def __init__(
        self,
        auth_token: str,
        api_plan: str = "developer",
        timeout_seconds: float = 15.0,
    ) -> None:
        if api_plan not in ALLOWED_API_PLANS:
            raise ValueError("Unsupported CryptoPanic API plan")
        self.auth_token = auth_token
        self.api_plan = api_plan
        self.timeout_seconds = timeout_seconds

    def get_posts(self, currencies: list[str]) -> list[dict[str, Any]]:
        normalized = sorted({item.upper() for item in currencies if item})
        if not normalized:
            return []
        try:
            response = httpx.get(
                f"{CRYPTOPANIC_API_ROOT}/{self.api_plan}/v2/posts/",
                params={
                    "auth_token": self.auth_token,
                    "public": "true",
                    "currencies": ",".join(normalized),
                    "regions": "en",
                    "kind": "news",
                    "page": 1,
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
            raise CryptoPanicAPIError(
                "CryptoPanic posts request failed",
                status_code=status_code,
                retry_after_seconds=retry_after,
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise CryptoPanicAPIError("CryptoPanic posts response was invalid")
        return [item for item in payload["results"] if isinstance(item, dict)]
