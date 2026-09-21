"""Kraken health scopes, failure classification and bounded recovery probes.

Owner requirement (Alert v2): when a *real* Kraken connectivity failure occurs,
O'Pip must keep trying to recover automatically and must not interrupt the owner
until recovery has failed repeatedly. That places three obligations here:

1. **Narrow classification.** Connectivity is not a synonym for "degraded".
   Missing credentials, rate limiting, a pricing gap, malformed account state or
   an unsupported asset are distinct classes with distinct policies. Treating
   them as connectivity would fabricate a network outage and would burn seven
   pointless recovery cycles on a deterministic configuration problem.
2. **Scope separation.** A healthy public Ticker call does not prove that private
   read-only position verification recovered, and vice versa. Recovery is proven
   on the same semantic scope that failed.
3. **Bounded, honest probing.** A recovery probe retries with increasing
   backoff and jitter under a delay ceiling, and may drop a demonstrably broken
   pooled connection -- but a recovery cycle is one higher-level probe, never one
   internal HTTP attempt (see :data:`KRAKEN_RECOVERY_CYCLE_SEPARATION`).

This module is transport/provider-health only. It never places, changes,
cancels, confirms or reads an order, and it holds no trading authority.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from app.services.kraken_transport import KrakenPublicTransport


#: One recovery cycle == one higher-level health probe. Low-level retries inside
#: ``KrakenPublicTransport`` (``KRAKEN_PUBLIC_MAX_RETRIES``) are an
#: implementation detail of a single cycle and must never increment the owner's
#: "more than six failed recovery cycles" counter.
KRAKEN_RECOVERY_CYCLE_SEPARATION = (
    "one recovery cycle = one KrakenScopeProbe.run() call; internal transport "
    "retries do not increment the recovery-cycle counter"
)

KRAKEN_RECOVERY_MAX_ATTEMPTS = 3
KRAKEN_RECOVERY_BASE_DELAY_SECONDS = 0.5
KRAKEN_RECOVERY_MAX_DELAY_SECONDS = 8.0
KRAKEN_RECOVERY_JITTER_RATIO = 0.15
KRAKEN_RECOVERY_TIMEOUT_SECONDS = 5.0


class KrakenHealthScope(str, Enum):
    """The semantic surface that failed and must be re-proven on recovery."""

    PUBLIC_CONNECTIVITY = "PUBLIC_CONNECTIVITY"
    READ_ONLY_CONNECTIVITY = "READ_ONLY_CONNECTIVITY"
    READ_ONLY_AUTH = "READ_ONLY_AUTH"
    HELD_ASSET_PRICING = "HELD_ASSET_PRICING"
    POSITION_VERIFICATION = "POSITION_VERIFICATION"
    RATE_LIMIT = "RATE_LIMIT"


class KrakenFailureClass(str, Enum):
    """How a low-level Kraken failure should be governed."""

    CONNECTIVITY = "CONNECTIVITY"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_CONFIG = "AUTH_CONFIG"
    PRICING = "PRICING"
    POSITION_VERIFICATION = "POSITION_VERIFICATION"
    OTHER = "OTHER"


# Rate limiting is checked first: an HTTP 429 must never be read as an outage.
_RATE_LIMIT_MARKERS = (
    "429",
    "too many requests",
    "rate limit",
    "ratelimit",
    "retry-after",
    "retry after",
)

# Deterministic auth/configuration problems. These are not transient, so they
# must not be run through the connectivity recovery loop.
_AUTH_MARKERS = (
    "invalid key",
    "invalid api key",
    "api key is invalid",
    "invalid signature",
    "signature for this request is invalid",
    "permission denied",
    "insufficient permission",
    "permissions are insufficient",
    "credentials are not configured",
    "credential is not configured",
    "missing credential",
    "not configured",
    "revoked",
    "unauthorised",
    "unauthorized",
    "api key not found",
)

# Genuine reachability failures. Only these justify a connectivity incident.
_CONNECTIVITY_MARKERS = (
    "name resolution",
    "nodename nor servname",
    "name or service not known",
    "temporary failure in name resolution",
    "getaddrinfo",
    "gaierror",
    "connection refused",
    "connection reset",
    "connection aborted",
    "connection closed",
    "connect call failed",
    "cannot connect",
    "failed to establish a new connection",
    "network is unreachable",
    "no route to host",
    "server disconnected",
    "remote protocol error",
    "ssl",
    "tls",
    "handshake",
    "certificate verify failed",
    "connecttimeout",
    "connect timeout",
    "read timeout",
    "readtimeout",
    "write timeout",
    "pool timeout",
    "timed out",
    "timeout",
)

_PRICING_MARKERS = (
    "pricing unavailable",
    "stable-quote pricing unavailable",
    "usd/stable-quote",
    "no usd/stable-quote pair",
)

_POSITION_MARKERS = (
    "position protection unavailable",
    "position verification",
    "managed lifecycle verification incomplete",
    "exposure coverage is incomplete",
    "direct snapshot unavailable",
    "account state unavailable",
)


def classify_failure_text(text: str | None) -> KrakenFailureClass:
    """Classify a low-level failure message into a governance class.

    Ordering matters and is part of the contract:

    1. rate limiting (so 429 never becomes an outage),
    2. auth/configuration (so a bad key never becomes a network loop),
    3. pricing coverage,
    4. position/account verification,
    5. genuine connectivity,
    6. everything else.
    """

    haystack = " ".join(str(text or "").split()).lower()
    if not haystack:
        return KrakenFailureClass.OTHER
    if any(marker in haystack for marker in _RATE_LIMIT_MARKERS):
        return KrakenFailureClass.RATE_LIMITED
    if any(marker in haystack for marker in _AUTH_MARKERS):
        return KrakenFailureClass.AUTH_CONFIG
    if any(marker in haystack for marker in _PRICING_MARKERS):
        return KrakenFailureClass.PRICING
    # Connectivity is evaluated before position/account markers: "account state
    # unavailable: ConnectError" IS a reachability failure, and treating it as
    # mere incomplete state would under-report a real outage.
    if any(marker in haystack for marker in _CONNECTIVITY_MARKERS):
        return KrakenFailureClass.CONNECTIVITY
    if any(marker in haystack for marker in _POSITION_MARKERS):
        return KrakenFailureClass.POSITION_VERIFICATION
    return KrakenFailureClass.OTHER


def is_connectivity_failure(text: str | None) -> bool:
    return classify_failure_text(text) is KrakenFailureClass.CONNECTIVITY


@dataclass(frozen=True)
class RecoveryProbeResult:
    """Outcome of exactly one recovery cycle for one scope."""

    scope: KrakenHealthScope
    success: bool
    failure_class: KrakenFailureClass
    attempts: int
    reason: str
    connection_reset: bool = False

    @property
    def attempts_used(self) -> int:
        return self.attempts


class KrakenScopeProbe:
    """Bounded, jittered, cache-bypassing probe for a single health scope.

    The probe owns retry mechanics only. It never decides *when* the owner is
    notified (that is :mod:`app.services.system_incidents`) and never mutates
    trading, protection or paper state.
    """

    def __init__(
        self,
        *,
        max_attempts: int = KRAKEN_RECOVERY_MAX_ATTEMPTS,
        base_delay_seconds: float = KRAKEN_RECOVERY_BASE_DELAY_SECONDS,
        max_delay_seconds: float = KRAKEN_RECOVERY_MAX_DELAY_SECONDS,
        jitter_ratio: float = KRAKEN_RECOVERY_JITTER_RATIO,
        sleeper: Callable[[float], None] | None = None,
        random_source: Callable[[float, float], float] | None = None,
        connection_reset: Callable[[], Any] | None = None,
    ) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay_seconds = max(0.0, float(base_delay_seconds))
        self.max_delay_seconds = max(0.0, float(max_delay_seconds))
        self.jitter_ratio = max(0.0, float(jitter_ratio))
        self._sleep = sleeper or time.sleep
        self._random = random_source or random.uniform
        self._reset = connection_reset

    def _delay_for(self, attempt: int) -> float:
        """Backoff for the retry *after* ``attempt`` failed, plus jitter.

        The returned delay is bounded by ``max_delay_seconds`` so jitter can
        never push a recovery cycle past the configured ceiling.
        """

        exponent = max(0, int(attempt) - 1)
        ceiling = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2**exponent),
        )
        if ceiling <= 0:
            return 0.0
        jitter = self._random(0.0, ceiling * self.jitter_ratio)
        delay = ceiling + max(0.0, float(jitter))
        if self.max_delay_seconds > 0:
            return min(self.max_delay_seconds, delay)
        return delay

    def run(
        self,
        probe: Callable[[], Any],
        *,
        scope: KrakenHealthScope,
    ) -> RecoveryProbeResult:
        """Run one recovery cycle.

        Exactly one call == exactly one owner-visible recovery cycle.
        """

        attempts = 0
        last_class = KrakenFailureClass.OTHER
        last_reason = "no probe attempt was made"
        connection_reset = False

        for attempt in range(1, self.max_attempts + 1):
            attempts = attempt
            if attempt > 1:
                delay = self._delay_for(attempt - 1)
                if delay > 0:
                    self._sleep(delay)
            try:
                probe()
            except Exception as exc:  # noqa: BLE001 - classification below
                last_class = classify_failure_text(str(exc))
                last_reason = f"{type(exc).__name__}: {exc}"
                if (
                    last_class is KrakenFailureClass.CONNECTIVITY
                    and self._reset is not None
                    and not connection_reset
                ):
                    # A demonstrably broken pooled connection may be replaced,
                    # but only for a genuine reachability failure.
                    try:
                        self._reset()
                        connection_reset = True
                    except Exception:
                        pass
                continue
            return RecoveryProbeResult(
                scope=scope,
                success=True,
                failure_class=KrakenFailureClass.OTHER,
                attempts=attempts,
                reason="probe succeeded",
                connection_reset=connection_reset,
            )

        return RecoveryProbeResult(
            scope=scope,
            success=False,
            failure_class=last_class,
            attempts=attempts,
            reason=last_reason,
            connection_reset=connection_reset,
        )


def public_connectivity_probe(
    transport: KrakenPublicTransport | None = None,
) -> Callable[[], dict[str, Any]]:
    """Fresh, cache-bypassing public market-data reachability probe.

    ``Time`` is cheap, requires no parameters and is never served from the TTL
    cache. ``bypass_cache=True`` makes that explicit so a future TTL for
    ``Time`` cannot silently turn this into a cache-hit proof.
    """

    def probe() -> dict[str, Any]:
        from app.services.kraken_transport import get_shared_kraken_transport

        active = transport or get_shared_kraken_transport()
        result = active.request(
            "Time",
            {},
            timeout_seconds=KRAKEN_RECOVERY_TIMEOUT_SECONDS,
            bypass_cache=True,
        )
        if not isinstance(result, dict) or "unixtime" not in result:
            raise RuntimeError("Kraken Time probe returned no server time")
        return result

    return probe


def read_only_connectivity_probe(
    client: Any | None = None,
) -> Callable[[], Any]:
    """Fresh private ***read-only*** account-reachability probe.

    The probe imports the read-only client lazily and asserts read-only
    permissions before any call, so the recovery path can never reach an order
    endpoint even if a future client gains one.
    """

    def probe() -> Any:
        active = client
        if active is None:
            from app.exchanges.kraken_private import KrakenPrivateClient

            active = KrakenPrivateClient()
        if not getattr(active, "enabled", False):
            from app.exchanges.kraken_private import KrakenPrivateAPIError

            raise KrakenPrivateAPIError("Kraken private credentials are not configured")
        active.assert_read_only()
        return active.get_balance()

    return probe


def transport_connection_reset(
    transport: KrakenPublicTransport | None = None,
) -> Callable[[], bool]:
    """Transport-only connection reset callback for connectivity failures."""

    def reset() -> bool:
        from app.services.kraken_transport import get_shared_kraken_transport

        active = transport or get_shared_kraken_transport()
        reset_connection = getattr(active, "reset_connection", None)
        if reset_connection is None:
            return False
        return bool(reset_connection())

    return reset
