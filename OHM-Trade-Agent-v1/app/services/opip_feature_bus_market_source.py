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

from datetime import datetime
from typing import Sequence

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
    """Resolves the eligible USD/USDT universe into instrument versions."""

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
    "KRAKEN_OHLC_SEQUENCE_PREFIX",
    "KRAKEN_OHLC_SOURCE_LABEL",
    "KRAKEN_VENUE",
    "KrakenInstrumentProvider",
    "KrakenMinuteBarFetcher",
    "kraken_minute_source",
]
