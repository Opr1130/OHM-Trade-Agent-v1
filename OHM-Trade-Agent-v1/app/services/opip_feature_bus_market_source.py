"""Kraken venue wiring for the PR3 feature-bus pilot.

This lives outside ``app/opip`` deliberately: no O'Pip module may import an
exchange transport, and that invariant is enforced by
``tests/test_opip_decision_safety_v1.py``. The feature bus therefore consumes
market data through the injected ``MinuteBarFetcher`` contract, and all Kraken
specifics stop here.

Public read-only endpoints only. Nothing in this module can place, modify,
cancel, or confirm an order.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Sequence

import httpx

from app.exchanges.kraken import Candle, KrakenAPIError, KrakenClient
from app.opip.contracts.identity import InstrumentVersion
from app.opip.market.instruments import (
    InstrumentVersionRegistry,
    kraken_descriptors,
)
from app.opip.market.observations import IntervalRow
from app.opip.market.source import PolledMinuteBarSource

KRAKEN_VENUE = "kraken"
KRAKEN_OHLC_SOURCE_LABEL = "kraken_public_ohlc"
KRAKEN_OHLC_SEQUENCE_PREFIX = "kraken-ohlc"

#: The capture producers run inside a one-minute slot and rely on their own
#: internal budget being the graceful stop, with the cron ``timeout`` as final
#: containment ONLY. The shared Kraken public transport defaults to a 15s
#: per-request timeout with up to 2 retries, so ONE stalled request can consume
#: ~45s+backoff -- the whole pass budget -- and the producer is then killed by its
#: process bound before it can record any disposition (a silent evidence drop).
#: These values size a pass-scoped per-attempt timeout so the transport's FULL
#: retry+backoff sequence still fits a single acquisition wave. Request semantics
#: and the transport's retry policy are unchanged; nothing here grants authority.
CAPTURE_WAVE_BUDGET_SECONDS = 15.0

#: Mirrors ``KrakenPublicTransport``'s own default so the derived timeout matches
#: the retry policy actually applied (see ``KRAKEN_PUBLIC_MAX_RETRIES``).
DEFAULT_KRAKEN_PUBLIC_MAX_RETRIES = 2

#: Floor: never derive a timeout so small that a healthy request is aborted.
MIN_CAPTURE_REQUEST_TIMEOUT_SECONDS = 1.0

#: ONE token-bucket refill step the shared transport may wait for before an
#: attempt. The transport's own rate limiter uses at most a 1.0s sleep per step.
CAPTURE_RATE_LIMIT_WAIT_ALLOWANCE_SECONDS = 1.0


def _resolved_max_retries(max_retries: int | None) -> int:
    if max_retries is not None:
        return max(0, int(max_retries))
    return max(
        0, int(os.getenv("KRAKEN_PUBLIC_MAX_RETRIES", str(DEFAULT_KRAKEN_PUBLIC_MAX_RETRIES)))
    )


def capture_request_timeout_seconds(
    *,
    wave_budget_seconds: float = CAPTURE_WAVE_BUDGET_SECONDS,
    max_retries: int | None = None,
) -> float:
    """Per-attempt timeout so a FULL retry sequence fits one capture wave.

    ``wave_budget / attempts`` is NOT a wall-clock bound: it ignores the
    transport's inter-attempt backoff+jitter, its rate-limiter wait, and the fact
    that ``httpx`` applies one float timeout to connect and read separately (so a
    single attempt can consume about twice the float). This derivation subtracts
    every one of those costs first:

        attempts * attempt_timeout * KRAKEN_ATTEMPT_PHASE_BOUND
            + retries * rate_wait_allowance
            + retry_backoff_worst_case_seconds(retries)
        <= wave_budget_seconds

    so the resulting per-attempt timeout makes the enforceable invariant true by
    construction (see ``capture_worst_case_request_seconds``, which the wave gate
    re-checks before starting any wave).
    """
    from app.services.kraken_transport import (
        KRAKEN_ATTEMPT_PHASE_BOUND,
        retry_backoff_worst_case_seconds,
    )

    retries = _resolved_max_retries(max_retries)
    attempts = max(1, retries + 1)
    overhead = (
        retries * CAPTURE_RATE_LIMIT_WAIT_ALLOWANCE_SECONDS
        + retry_backoff_worst_case_seconds(retries)
    )
    available = float(wave_budget_seconds) - overhead
    if available <= 0.0:  # pragma: no cover - only for a nonsensical tiny budget
        return MIN_CAPTURE_REQUEST_TIMEOUT_SECONDS
    return max(
        MIN_CAPTURE_REQUEST_TIMEOUT_SECONDS,
        available / (attempts * KRAKEN_ATTEMPT_PHASE_BOUND),
    )


def capture_worst_case_request_seconds(
    *,
    wave_budget_seconds: float = CAPTURE_WAVE_BUDGET_SECONDS,
    max_retries: int | None = None,
) -> float:
    """Worst-case wall clock of ONE request whose attempts all time out.

    ``attempts * attempt_timeout * phase_bound + backoff + rate_wait_allowance``.
    A producer must not start any upstream operation whose worst-case bounded cost
    cannot fit in the remaining acquisition budget; this is that cost.
    """
    from app.services.kraken_transport import (
        KRAKEN_ATTEMPT_PHASE_BOUND,
        retry_backoff_worst_case_seconds,
    )

    retries = _resolved_max_retries(max_retries)
    attempts = max(1, retries + 1)
    per_attempt = capture_request_timeout_seconds(
        wave_budget_seconds=wave_budget_seconds, max_retries=retries
    )
    return (
        attempts * per_attempt * KRAKEN_ATTEMPT_PHASE_BOUND
        + retries * CAPTURE_RATE_LIMIT_WAIT_ALLOWANCE_SECONDS
        + retry_backoff_worst_case_seconds(retries)
    )


def capture_kraken_client(
    *,
    wave_budget_seconds: float = CAPTURE_WAVE_BUDGET_SECONDS,
    deadline_monotonic: float | None = None,
) -> KrakenClient:
    """A Kraken public client whose FULL retry sequence fits one capture wave.

    Public read-only endpoints only: this can read market data and can never
    place, modify, cancel, or confirm an order. ``deadline_monotonic`` additionally
    bounds the client's whole lifetime by an absolute pass deadline, so even a
    pathological retry sequence cannot outlive the pass that declared it.
    """
    return KrakenClient(
        timeout_seconds=capture_request_timeout_seconds(
            wave_budget_seconds=wave_budget_seconds
        ),
        deadline_monotonic=deadline_monotonic,
    )


def classify_capture_error(error: BaseException) -> str:
    """Bounded classification token for one acquisition failure disposition.

    Walks the exception chain (bounded) so a failure wrapped by ``KrakenAPIError``
    is still classified as ``DEADLINE_EXHAUSTED`` or ``REQUEST_TIMEOUT`` rather
    than being reported as a generic failure. Observability only: it changes no
    control flow and grants no authority.
    """
    from app.services.kraken_transport import KrakenTransportDeadlineExceeded

    link: BaseException | None = error
    for _ in range(8):
        if link is None:
            break
        if isinstance(link, KrakenTransportDeadlineExceeded):
            return "DEADLINE_EXHAUSTED"
        if isinstance(link, httpx.TimeoutException):
            return "REQUEST_TIMEOUT"
        link = link.__cause__ or link.__context__
    return "ACQUISITION_FAILURE"


def _rows_from_candles(candles: Sequence[Candle]) -> list[IntervalRow]:
    return [
        IntervalRow(
            interval_start_epoch=int(candle.timestamp),
            open=float(candle.open),
            high=float(candle.high),
            low=float(candle.low),
            close=float(candle.close),
            volume=float(candle.volume),
            vwap=float(candle.vwap),
            trade_count=int(candle.trade_count),
        )
        for candle in candles
    ]


class KrakenMinuteBarFetcher:
    """One public OHLC request per call. No retry, backoff, or scheduling."""

    def __init__(self, client: KrakenClient | None = None) -> None:
        self._client = client or KrakenClient()

    def __call__(
        self,
        venue_instrument_id: str,
        *,
        interval_minutes: int,
        since_epoch: int | None,
    ) -> list[IntervalRow]:
        candles = self._client.get_ohlc(
            venue_instrument_id, interval=interval_minutes, since=since_epoch
        )
        return _rows_from_candles(candles)


def kraken_minute_source(
    client: KrakenClient | None = None,
    *,
    interval_seconds: int = 60,
) -> PolledMinuteBarSource:
    """Pilot one-minute Kraken source. Manual measurement use only."""
    return PolledMinuteBarSource(
        KrakenMinuteBarFetcher(client),
        venue=KRAKEN_VENUE,
        source_label=KRAKEN_OHLC_SOURCE_LABEL,
        sequence_prefix=KRAKEN_OHLC_SEQUENCE_PREFIX,
        interval_seconds=interval_seconds,
        transport_errors=(KrakenAPIError,),
    )


class KrakenInstrumentProvider:
    """Resolves the eligible USD/USDT universe into instrument versions.

    Pass a registry hydrated from durable ``market.instrument_version.recorded``
    payloads (via ``reconstruct_instrument_version_registry``) so process
    restarts do not forget version history. Newly minted versions must be
    committed through the feature-bus publisher before dependent observations.
    """

    def __init__(
        self,
        client: KrakenClient | None = None,
        *,
        registry: InstrumentVersionRegistry | None = None,
    ) -> None:
        self._client = client or KrakenClient()
        self.registry = registry or InstrumentVersionRegistry()

    def refresh(self, *, observed_at_utc: datetime) -> list[InstrumentVersion]:
        descriptors = kraken_descriptors(self._client.get_asset_pairs())
        return self.registry.observe_all(descriptors, observed_at_utc=observed_at_utc)


__all__ = [
    "CAPTURE_RATE_LIMIT_WAIT_ALLOWANCE_SECONDS",
    "CAPTURE_WAVE_BUDGET_SECONDS",
    "DEFAULT_KRAKEN_PUBLIC_MAX_RETRIES",
    "MIN_CAPTURE_REQUEST_TIMEOUT_SECONDS",
    "KRAKEN_OHLC_SEQUENCE_PREFIX",
    "KRAKEN_OHLC_SOURCE_LABEL",
    "KRAKEN_VENUE",
    "KrakenInstrumentProvider",
    "KrakenMinuteBarFetcher",
    "capture_kraken_client",
    "capture_request_timeout_seconds",
    "capture_worst_case_request_seconds",
    "classify_capture_error",
    "kraken_minute_source",
]
