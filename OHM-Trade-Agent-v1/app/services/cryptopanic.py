from __future__ import annotations

from typing import Any

import httpx


CRYPTOPANIC_API_ROOT = "https://cryptopanic.com/api"
ALLOWED_API_PLANS = {"developer", "growth", "enterprise"}


class CryptoPanicAPIError(RuntimeError):
    """Raised for expected CryptoPanic network/API/response failures."""


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
            raise CryptoPanicAPIError("CryptoPanic posts request failed") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise CryptoPanicAPIError("CryptoPanic posts response was invalid")
        return [item for item in payload["results"] if isinstance(item, dict)]
