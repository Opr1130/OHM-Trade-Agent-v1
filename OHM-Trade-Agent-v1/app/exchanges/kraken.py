from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


KRAKEN_API_BASE = "https://api.kraken.com/0/public"


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
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{KRAKEN_API_BASE}/{endpoint}",
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KrakenAPIError(f"Kraken request failed: {exc}") from exc

        payload = response.json()

        errors = payload.get("error", [])
        if errors:
            raise KrakenAPIError(f"Kraken API error: {', '.join(errors)}")

        result = payload.get("result")
        if not isinstance(result, dict):
            raise KrakenAPIError("Kraken response did not contain a valid result")

        return result

    def get_asset_pairs(self) -> dict[str, dict[str, Any]]:
        """Return all online Kraken spot trading pairs."""
        result = self._get("AssetPairs", {})

        return {
            pair_id: details
            for pair_id, details in result.items()
            if isinstance(details, dict)
            and details.get("status") == "online"
        }

    def get_tickers(
        self,
        pairs: list[str],
    ) -> dict[str, dict[str, float]]:
        """Return ticker data for multiple Kraken pairs."""
        if not pairs:
            return {}

        result = self._get(
            "Ticker",
            {"pair": ",".join(pairs)},
        )

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

    def get_ohlc(
        self,
        pair: str,
        interval: int = 60,
        since: int | None = None,
    ) -> list[Candle]:
        params: dict[str, Any] = {
            "pair": pair,
            "interval": interval,
        }

        if since is not None:
            params["since"] = since

        result = self._get("OHLC", params)
        candle_key = next((key for key in result if key != "last"), None)

        if candle_key is None:
            raise KrakenAPIError(f"No OHLC data returned for {pair}")

        candles: list[Candle] = []

        for row in result[candle_key]:
            candles.append(
                Candle(
                    timestamp=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    vwap=float(row[5]),
                    volume=float(row[6]),
                    trade_count=int(row[7]),
                )
            )

        return candles

    def get_pre_trade(self, symbol: str) -> PreTradeBook:
        """Return Kraken's public top-10 aggregated transparency book."""
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

    def get_post_trade(
        self,
        symbol: str,
        count: int = 100,
    ) -> list[PublicTrade]:
        """Return bounded recent public spot trades without authentication."""
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
