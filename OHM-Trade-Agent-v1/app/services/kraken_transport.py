from __future__ import annotations

import copy
import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx


logger = logging.getLogger(__name__)

KRAKEN_PUBLIC_BASE = "https://api.kraken.com/0/public"

#: Worst-case retry/backoff policy of THIS transport, exposed as constants so a
#: caller that must fit a whole request+retry+backoff sequence inside a wall-clock
#: deadline can derive its per-attempt timeout from the policy actually applied
#: instead of assuming ``budget / attempts`` (which ignores backoff entirely).
KRAKEN_RETRY_BACKOFF_BASE_SECONDS = 0.25
KRAKEN_RETRY_BACKOFF_CAP_SECONDS = 2.0
KRAKEN_RETRY_BACKOFF_JITTER_FRACTION = 0.15

#: ``httpx`` applies one float timeout to connect, read, write and pool
#: SEPARATELY, so a single stalled attempt (a connect that exhausts its timeout
#: followed by a read that exhausts its own) can consume about twice the float.
#: A deadline-aware caller divides the remaining budget by this factor to bound
#: ONE attempt rather than assuming the float is a total-attempt bound.
KRAKEN_ATTEMPT_PHASE_BOUND = 2.0

#: Floor for a deadline-derived attempt timeout: below this the request cannot
#: realistically complete, so the caller stops instead of starting doomed work.
KRAKEN_MIN_DEADLINE_ATTEMPT_SECONDS = 0.2


class KrakenTransportError(RuntimeError):
    """Raised when the shared Kraken public transport cannot complete a request."""


class KrakenTransportDeadlineExceeded(KrakenTransportError):
    """The request (and its full retry/backoff sequence) could not fit the deadline.

    Raised by a deadline-aware caller INSTEAD of starting a request/retry that
    cannot complete inside the declared budget, so a producer always regains
    control in time to record a durable disposition rather than being killed.
    """


def retry_backoff_seconds(attempt: int) -> float:
    """The nominal backoff before ``attempt`` (1-based), before jitter."""
    return min(
        KRAKEN_RETRY_BACKOFF_CAP_SECONDS,
        KRAKEN_RETRY_BACKOFF_BASE_SECONDS * (2 ** (max(1, int(attempt)) - 1)),
    )


def retry_backoff_worst_case_seconds(max_retries: int) -> float:
    """Worst-case total backoff+jitter for a full retry sequence.

    Mirrors ``request``'s own sleeps exactly (``backoff + uniform(0, backoff*0.15)``),
    so this is the backoff component of the enforceable
    ``timeout*attempts + backoff <= budget`` invariant.
    """
    retries = max(0, int(max_retries))
    return sum(
        retry_backoff_seconds(attempt) * (1.0 + KRAKEN_RETRY_BACKOFF_JITTER_FRACTION)
        for attempt in range(1, retries + 1)
    )


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

    def _acquire_budget(self, deadline_monotonic: float | None = None) -> bool:
        """Take one rate-budget token, optionally bounded by an absolute deadline.

        Returns ``True`` when a token was taken. With a deadline, returns
        ``False`` rather than waiting past it, so a caller can stop instead of
        blocking until its process bound kills it.
        """
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
                    return True
                sleep_for = (1.0 - self._tokens) / self.requests_per_second
                self._metrics["rate_wait_seconds"] += sleep_for
            wait = min(max(sleep_for, 0.001), 1.0)
            if deadline_monotonic is not None and (
                time.monotonic() + wait > deadline_monotonic
            ):
                return False
            time.sleep(wait)

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
        bypass_cache: bool = False,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        """Perform one public request, optionally bypassing the TTL cache.

        ``bypass_cache`` exists for health/recovery probes: a TTL cache hit is
        not evidence that provider connectivity recovered, so a recovery probe
        must reach the network. Ordinary callers keep the cached default.

        ``deadline_monotonic`` (an absolute ``time.monotonic()`` instant) makes
        the WHOLE request -- every attempt, every inter-attempt backoff/jitter
        sleep and every rate-limit wait -- obey one caller budget, so the
        enforceable bound is

            complete request + all retries + all retry backoff <= deadline

        rather than ``timeout * attempts``. A retry is never started when its
        worst-case cost cannot fit the remaining budget, and the per-attempt
        timeout is itself clamped by the remaining budget divided by
        :data:`KRAKEN_ATTEMPT_PHASE_BOUND` (one float timeout bounds connect and
        read separately in ``httpx``). The default ``None`` preserves the
        pre-existing behaviour exactly for callers that declare no deadline.
        """
        key = self._cache_key(endpoint, params)
        if not bypass_cache:
            cached = self._cached(key)
            if cached is not None:
                return cached

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                backoff = retry_backoff_seconds(attempt)
                jitter = random.uniform(
                    0.0, backoff * KRAKEN_RETRY_BACKOFF_JITTER_FRACTION
                )
                if deadline_monotonic is not None and (
                    time.monotonic() + backoff + jitter > deadline_monotonic
                ):
                    last_error = KrakenTransportDeadlineExceeded(
                        f"deadline exhausted before retry {attempt} of "
                        f"{self.max_retries} for {endpoint}"
                    )
                    break
                with self._lock:
                    self._metrics["retries"] += 1
                time.sleep(backoff + jitter)

            if deadline_monotonic is None:
                self._acquire_budget()
                attempt_timeout = float(timeout_seconds)
            else:
                if not self._acquire_budget(deadline_monotonic=deadline_monotonic):
                    last_error = KrakenTransportDeadlineExceeded(
                        f"deadline exhausted before attempt {attempt + 1} for {endpoint}"
                    )
                    break
                remaining = deadline_monotonic - time.monotonic()
                attempt_timeout = min(
                    float(timeout_seconds), remaining / KRAKEN_ATTEMPT_PHASE_BOUND
                )
                if attempt_timeout < KRAKEN_MIN_DEADLINE_ATTEMPT_SECONDS:
                    last_error = KrakenTransportDeadlineExceeded(
                        f"insufficient budget ({remaining:.3f}s) for attempt "
                        f"{attempt + 1} for {endpoint}"
                    )
                    break

            try:
                with self._lock:
                    self._metrics["network_calls"] += 1
                response = self._client.get(
                    f"{KRAKEN_PUBLIC_BASE}/{endpoint}",
                    params=params,
                    timeout=attempt_timeout,
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
        if isinstance(last_error, KrakenTransportDeadlineExceeded):
            raise last_error
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

    def reset_connection(self) -> bool:
        """Recreate the pooled HTTP client after a genuine low-level failure.

        This is transport hygiene only. It never places, changes, cancels,
        confirms, or even reads an order, and it cannot reach a trading
        endpoint: the transport is the public market-data plane.

        A failed close of the previous client is not fatal (the pooled socket is
        discarded either way), but it is reported rather than silently swallowed
        so recovery telemetry stays truthful.
        """

        try:
            with self._lock:
                previous = self._client
                self._client = httpx.Client()
            try:
                previous.close()
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                logger.warning(
                    "Kraken public transport close failed during reset: %s: %s",
                    type(exc).__name__,
                    exc,
                )
            return True
        except Exception:
            return False


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
