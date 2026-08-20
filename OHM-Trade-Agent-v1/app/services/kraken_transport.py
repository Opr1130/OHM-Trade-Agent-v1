from __future__ import annotations

import copy
import json
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx


KRAKEN_PUBLIC_BASE = "https://api.kraken.com/0/public"


class KrakenTransportError(RuntimeError):
    """Raised when the shared Kraken public transport cannot complete a request."""


@dataclass
class _CacheEntry:
    expires_at: float
    value: dict[str, Any]


class KrakenPublicTransport:
    """Shared Kraken public transport with bounded retry, rate budget and TTL cache.

    The transport changes delivery mechanics only. It does not alter any OHM
    market, qualification, risk, execution or notification threshold.
    """

    DEFAULT_TTLS = {
        "AssetPairs": 300.0,
        "Ticker": 3.0,
        "OHLC": 20.0,
        "PreTrade": 2.0,
        "PostTrade": 2.0,
    }

    def __init__(
        self,
        *,
        requests_per_second: float | None = None,
        burst: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.requests_per_second = max(
            0.25,
            float(requests_per_second or os.getenv("KRAKEN_PUBLIC_RPS", "4.0")),
        )
        self.burst = max(1, int(burst or os.getenv("KRAKEN_PUBLIC_BURST", "4")))
        self.max_retries = max(
            0,
            int(max_retries if max_retries is not None else os.getenv("KRAKEN_PUBLIC_MAX_RETRIES", "2")),
        )
        self._client = httpx.Client()
        self._lock = threading.RLock()
        self._tokens = float(self.burst)
        self._last_refill = time.monotonic()
        self._cache: dict[str, _CacheEntry] = {}
        self._metrics = {
            "network_calls": 0,
            "cache_hits": 0,
            "retries": 0,
            "failures": 0,
            "rate_wait_seconds": 0.0,
        }

    def _cache_ttl(self, endpoint: str) -> float:
        env_key = f"KRAKEN_CACHE_TTL_{endpoint.upper()}_SECONDS"
        try:
            return max(0.0, float(os.getenv(env_key, str(self.DEFAULT_TTLS.get(endpoint, 0.0)))))
        except ValueError:
            return float(self.DEFAULT_TTLS.get(endpoint, 0.0))

    @staticmethod
    def _cache_key(endpoint: str, params: dict[str, Any]) -> str:
        return f"{endpoint}:{json.dumps(params, sort_keys=True, separators=(',', ':'), default=str)}"

    def _cached(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._cache.pop(key, None)
                return None
            self._metrics["cache_hits"] += 1
            return copy.deepcopy(entry.value)

    def _store(self, key: str, value: dict[str, Any], ttl: float) -> None:
        if ttl <= 0:
            return
        with self._lock:
            self._cache[key] = _CacheEntry(
                expires_at=time.monotonic() + ttl,
                value=copy.deepcopy(value),
            )

    def _acquire_budget(self) -> None:
        while True:
            sleep_for = 0.0
            with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - self._last_refill)
                self._tokens = min(
                    float(self.burst),
                    self._tokens + elapsed * self.requests_per_second,
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                sleep_for = (1.0 - self._tokens) / self.requests_per_second
                self._metrics["rate_wait_seconds"] += sleep_for
            time.sleep(min(max(sleep_for, 0.001), 1.0))

    @staticmethod
    def _retryable_api_error(errors: list[Any]) -> bool:
        text = " ".join(str(item).lower() for item in errors)
        return "too many requests" in text or "rate limit" in text or "service unavailable" in text

    def request(
        self,
        endpoint: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        key = self._cache_key(endpoint, params)
        cached = self._cached(key)
        if cached is not None:
            return cached

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                with self._lock:
                    self._metrics["retries"] += 1
                backoff = min(2.0, 0.25 * (2 ** (attempt - 1)))
                time.sleep(backoff + random.uniform(0.0, backoff * 0.15))

            self._acquire_budget()
            try:
                with self._lock:
                    self._metrics["network_calls"] += 1
                response = self._client.get(
                    f"{KRAKEN_PUBLIC_BASE}/{endpoint}",
                    params=params,
                    timeout=timeout_seconds,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise KrakenTransportError(
                        f"Kraken public HTTP {response.status_code} for {endpoint}"
                    )
                response.raise_for_status()
                payload = response.json()
                errors = payload.get("error", [])
                if errors:
                    if self._retryable_api_error(errors) and attempt < self.max_retries:
                        last_error = KrakenTransportError(
                            f"Kraken API retryable error for {endpoint}: {', '.join(map(str, errors))}"
                        )
                        continue
                    raise KrakenTransportError(
                        f"Kraken API error for {endpoint}: {', '.join(map(str, errors))}"
                    )
                result = payload.get("result")
                if not isinstance(result, dict):
                    raise KrakenTransportError(
                        f"Kraken response did not contain a valid result for {endpoint}"
                    )
                self._store(key, result, self._cache_ttl(endpoint))
                return copy.deepcopy(result)
            except (httpx.HTTPError, ValueError, KrakenTransportError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break

        with self._lock:
            self._metrics["failures"] += 1
        raise KrakenTransportError(
            f"Kraken public request failed for {endpoint}: {type(last_error).__name__}: {last_error}"
        ) from last_error

    def telemetry_snapshot(self) -> dict[str, float | int]:
        with self._lock:
            return {
                **self._metrics,
                "cache_entries": len(self._cache),
                "configured_rps": self.requests_per_second,
                "configured_burst": self.burst,
                "configured_max_retries": self.max_retries,
            }

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


_SHARED_TRANSPORT: KrakenPublicTransport | None = None
_SHARED_LOCK = threading.Lock()


def get_shared_kraken_transport() -> KrakenPublicTransport:
    global _SHARED_TRANSPORT
    with _SHARED_LOCK:
        if _SHARED_TRANSPORT is None:
            _SHARED_TRANSPORT = KrakenPublicTransport()
        return _SHARED_TRANSPORT


def reset_shared_kraken_transport_for_tests() -> None:
    global _SHARED_TRANSPORT
    with _SHARED_LOCK:
        if _SHARED_TRANSPORT is not None:
            try:
                _SHARED_TRANSPORT._client.close()
            except Exception:
                pass
        _SHARED_TRANSPORT = None
