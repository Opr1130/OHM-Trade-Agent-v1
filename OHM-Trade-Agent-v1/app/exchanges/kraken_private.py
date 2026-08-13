from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx


KRAKEN_API_BASE = "https://api.kraken.com"
READ_ONLY_PERMISSIONS = {
    "query-funds",
    "query-open-trades",
    "query-closed-trades",
    "query-ledger",
    "export-data",
    "create-ws-token",
}
DANGEROUS_PERMISSIONS = {
    "modify-trades",
    "close-trades",
    "withdraw-funds",
    "add-funds",
    "earn-funds",
    "add-withdraw-address",
    "update-withdraw-address",
}


class KrakenPrivateAPIError(RuntimeError):
    """Raised when an authenticated Kraken request fails."""


class KrakenPermissionError(KrakenPrivateAPIError):
    """Raised when the configured key has permissions OHM must not use."""


@dataclass(frozen=True)
class KrakenKeyInfo:
    name: str
    permissions: frozenset[str]
    last_used: str | None = None

    @property
    def is_read_only(self) -> bool:
        return not bool(self.permissions & DANGEROUS_PERMISSIONS)


class KrakenPrivateClient:
    """Minimal authenticated Spot REST client for read-only account reconciliation.

    OHM intentionally exposes no order-placement, cancellation, deposit, or
    withdrawal methods here. The client is an observation surface only.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("KRAKEN_API_KEY", "")
        self.api_secret = api_secret if api_secret is not None else os.getenv("KRAKEN_API_SECRET", "")
        self.timeout_seconds = timeout_seconds
        self._last_nonce = 0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _nonce(self) -> int:
        candidate = time.time_ns() // 1_000_000
        self._last_nonce = max(candidate, self._last_nonce + 1)
        return self._last_nonce

    def _signature(self, url_path: str, postdata: str, nonce: int) -> str:
        encoded = (str(nonce) + postdata).encode()
        message = url_path.encode() + hashlib.sha256(encoded).digest()
        try:
            secret = base64.b64decode(self.api_secret)
        except Exception as exc:  # pragma: no cover - defensive configuration error
            raise KrakenPrivateAPIError("KRAKEN_API_SECRET is not valid base64") from exc
        digest = hmac.new(secret, message, hashlib.sha512).digest()
        return base64.b64encode(digest).decode()

    def _post(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        if not self.enabled:
            raise KrakenPrivateAPIError("Kraken private API credentials are not configured")
        url_path = f"/0/private/{endpoint}"
        payload = dict(params or {})
        nonce = self._nonce()
        payload["nonce"] = nonce

        # Kraken signs the exact form-encoded POST body. Build it once and use
        # the same bytes for both the signature and the HTTP request so bools,
        # ordering, and future parameter types can never encode differently.
        postdata = urlencode(payload)
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self._signature(url_path, postdata, nonce),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            response = httpx.post(
                f"{KRAKEN_API_BASE}{url_path}",
                content=postdata.encode(),
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KrakenPrivateAPIError(f"Kraken private request failed: {exc}") from exc
        body = response.json()
        errors = body.get("error", [])
        if errors:
            raise KrakenPrivateAPIError(f"Kraken API error: {', '.join(errors)}")
        return body.get("result")

    def get_api_key_info(self) -> KrakenKeyInfo:
        result = self._post("GetApiKeyInfo") or {}
        permissions = frozenset(str(value) for value in result.get("permissions", []))
        return KrakenKeyInfo(
            name=str(result.get("apiKeyName") or ""),
            permissions=permissions,
            last_used=result.get("lastUsed"),
        )

    def assert_read_only(self) -> KrakenKeyInfo:
        info = self.get_api_key_info()
        dangerous = sorted(info.permissions & DANGEROUS_PERMISSIONS)
        if dangerous:
            raise KrakenPermissionError(
                "OHM requires a read-only Kraken key; remove permissions: " + ", ".join(dangerous)
            )
        return info

    def get_balance(self) -> dict[str, float]:
        result = self._post("Balance") or {}
        return {str(asset): float(amount) for asset, amount in result.items()}

    def get_open_orders(self) -> dict[str, dict[str, Any]]:
        result = self._post("OpenOrders", {"trades": "true"}) or {}
        rows = result.get("open", {}) if isinstance(result, dict) else {}
        return rows if isinstance(rows, dict) else {}

    def get_trades_history(self, *, start: int | None = None) -> dict[str, dict[str, Any]]:
        params: dict[str, Any] = {"trades": "true"}
        if start is not None:
            params["start"] = int(start)
        result = self._post("TradesHistory", params) or {}
        rows = result.get("trades", {}) if isinstance(result, dict) else {}
        return rows if isinstance(rows, dict) else {}

    def get_open_positions(self) -> dict[str, dict[str, Any]]:
        result = self._post("OpenPositions", {"docalcs": "true"}) or {}
        return result if isinstance(result, dict) else {}
