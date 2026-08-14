from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from app.backtesting.models import Candle
from app.exchanges.kraken import KrakenClient


class OHLCClient(Protocol):
    def get_ohlc(self, pair: str, interval: int = 60, since: int | None = None): ...


def load_kraken_candles(
    pair: str,
    *,
    interval: int = 60,
    since: int | None = None,
    client: OHLCClient | None = None,
    drop_open_candle: bool = True,
) -> list[Candle]:
    """Load Kraken OHLC into the deterministic replay model.

    Kraken's public OHLC response includes the currently forming candle. By
    default it is excluded so a backtest cannot consume information that was
    not final at decision time. A client may be injected for deterministic
    tests and offline research.
    """
    if not pair:
        raise ValueError("pair is required")
    if interval <= 0:
        raise ValueError("interval must be positive")

    source = (client or KrakenClient()).get_ohlc(pair, interval=interval, since=since)
    if drop_open_candle and source:
        source = source[:-1]

    candles = [
        Candle(
            timestamp=datetime.fromtimestamp(int(row.timestamp), tz=timezone.utc),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in source
    ]
    candles.sort(key=lambda candle: candle.timestamp)
    return candles
