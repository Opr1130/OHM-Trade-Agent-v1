from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.kraken_transport import (
    KrakenPublicTransport,
    KrakenTransportError,
    get_shared_kraken_transport,
)


class KrakenAPIError(RuntimeError):
    """Raised when Kraken returns an API or network error."""


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    vwap: float
    volume: float
    trade_count: int


@dataclass(frozen=True)
class BookLevel:
    price: float
    quantity: float
    publication_timestamp: str | None = None


@dataclass(frozen=True)
class PreTradeBook:
    symbol: str
    bids: list[BookLevel]
    asks: list[BookLevel]


@dataclass(frozen=True)
class PublicTrade:
    price: float
    quantity: float
    trade_timestamp: str
    publication_timestamp: str | None = None


class KrakenClient:
    def __init__(
        self,
        timeout_seconds: float = 15.0,
        *,
        transport: KrakenPublicTransport | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport or get_shared_kraken_transport()

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.transport.request(
                endpoint,
                params,
                timeout_seconds=self.timeout_seconds,
            )
        except KrakenTransportError as exc:
            raise KrakenAPIError(str(exc)) from exc

    def transport_telemetry(self) -> dict[str, float | int]:
        return self.transport.telemetry_snapshot()

    def get_asset_pairs(
        self,
        execution_venue: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Return online Kraken pairs, optionally scoped to an execution venue."""
        params: dict[str, Any] = {}
        if execution_venue:
            params["execution_venue"] = execution_venue
        result = self._get("AssetPairs", params)
        return {
            pair_id: details
            for pair_id, details in result.items()
            if isinstance(details, dict)
            and details.get("status", "online") == "online"
        }

    def get_tickers(self, pairs: list[str]) -> dict[str, dict[str, float]]:
        if not pairs:
            return {}
        result = self._get("Ticker", {"pair": ",".join(pairs)})
        tickers: dict[str, dict[str, float]] = {}
        for pair_name, ticker in result.items():
            tickers[pair_name] = {
                "ask": float(ticker["a"][0]),
                "bid": float(ticker["b"][0]),
                "last": float(ticker["c"][0]),
                "volume_today": float(ticker["v"][0]),
                "volume_24h": float(ticker["v"][1]),
                "high_today": float(ticker["h"][0]),
                "high_24h": float(ticker["h"][1]),
                "low_today": float(ticker["l"][0]),
                "low_24h": float(ticker["l"][1]),
            }
        return tickers

    def get_ticker(self, pair: str) -> dict[str, float]:
        result = self._get("Ticker", {"pair": pair})
        if not result:
            raise KrakenAPIError(f"No ticker data returned for {pair}")
        ticker = next(iter(result.values()))
        return {
            "ask": float(ticker["a"][0]),
            "bid": float(ticker["b"][0]),
            "last": float(ticker["c"][0]),
            "volume_today": float(ticker["v"][0]),
            "volume_24h": float(ticker["v"][1]),
            "high_today": float(ticker["h"][0]),
            "high_24h": float(ticker["h"][1]),
            "low_today": float(ticker["l"][0]),
            "low_24h": float(ticker["l"][1]),
        }

    def get_ohlc(self, pair: str, interval: int = 60, since: int | None = None) -> list[Candle]:
        params: dict[str, Any] = {"pair": pair, "interval": interval}
        if since is not None:
            params["since"] = since
        result = self._get("OHLC", params)
        candle_key = next((key for key in result if key != "last"), None)
        if candle_key is None:
            raise KrakenAPIError(f"No OHLC data returned for {pair}")
        return [
            Candle(
                timestamp=int(row[0]), open=float(row[1]), high=float(row[2]),
                low=float(row[3]), close=float(row[4]), vwap=float(row[5]),
                volume=float(row[6]), trade_count=int(row[7]),
            )
            for row in result[candle_key]
        ]

    def get_pre_trade(self, symbol: str) -> PreTradeBook:
        result = self._get("PreTrade", {"symbol": symbol})
        return PreTradeBook(
            symbol=str(result.get("symbol", symbol)),
            bids=[self._book_level(item) for item in result.get("bids", [])[:10]],
            asks=[self._book_level(item) for item in result.get("asks", [])[:10]],
        )

    @staticmethod
    def _book_level(item: dict[str, Any]) -> BookLevel:
        return BookLevel(
            price=float(item["price"]),
            quantity=float(item["qty"]),
            publication_timestamp=item.get("publication_ts"),
        )

    def get_post_trade(self, symbol: str, count: int = 100) -> list[PublicTrade]:
        result = self._get("PostTrade", {"symbol": symbol, "count": count})
        return [
            PublicTrade(
                price=float(item["price"]),
                quantity=float(item["quantity"]),
                trade_timestamp=str(item["trade_ts"]),
                publication_timestamp=item.get("publication_ts"),
            )
            for item in result.get("trades", [])
        ]
