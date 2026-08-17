from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from time import monotonic
from typing import Any

import httpx

from app.services.market_intelligence_models import (
    AVAILABLE,
    UNAVAILABLE,
    DerivativesSnapshot,
    MarketIntelligenceBundle,
)


COINALYZE_API_BASE = "https://api.coinalyze.net/v1"
COINALYZE_MAX_SYMBOLS_PER_REQUEST = 20
COINALYZE_RATE_LIMIT_UNITS_PER_MINUTE = 40

# Coinalyze exchange codes for the default representative derivatives venues.
# A = Binance, 6 = Bybit, 3 = OKX. The order is deliberate: prefer a broad,
# liquid major venue before falling back to any other supported stable perpetual.
DEFAULT_PREFERRED_EXCHANGES: tuple[str, ...] = ("A", "6", "3")


class CoinalyzeAPIError(RuntimeError):
    """Raised for expected Coinalyze network/API/response failures."""


class CoinalyzeRateLimitError(CoinalyzeAPIError):
    def __init__(self, retry_after_seconds: int | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        suffix = (
            f"; retry after {retry_after_seconds}s"
            if retry_after_seconds is not None
            else ""
        )
        super().__init__(f"Coinalyze rate limit reached{suffix}")


class CoinalyzeClient:
    """Thin client for the official Coinalyze v1 API.

    Authentication is kept in the request header so credentials never appear in
    URLs, logs, or persisted market-intelligence records.
    """

    def __init__(self, api_key: str, timeout_seconds: float = 15.0) -> None:
        if not api_key.strip():
            raise ValueError("Coinalyze API key is required")
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            response = httpx.get(
                f"{COINALYZE_API_BASE}{path}",
                params=params or {},
                headers={
                    "api_key": self.api_key,
                    "Accept": "application/json",
                },
                timeout=self.timeout_seconds,
            )
            if response.status_code == 429:
                retry_after: int | None = None
                raw_retry = response.headers.get("Retry-After")
                if raw_retry:
                    try:
                        retry_after = int(float(raw_retry))
                    except ValueError:
                        retry_after = None
                raise CoinalyzeRateLimitError(retry_after)
            response.raise_for_status()
            payload = response.json()
        except CoinalyzeRateLimitError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise CoinalyzeAPIError(f"Coinalyze {path} request failed") from exc

        if not isinstance(payload, list):
            raise CoinalyzeAPIError(f"Coinalyze {path} response was not a list")
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _symbols_param(symbols: list[str]) -> str:
        normalized = [item.strip() for item in symbols if item and item.strip()]
        if not normalized:
            raise ValueError("at least one Coinalyze symbol is required")
        if len(normalized) > COINALYZE_MAX_SYMBOLS_PER_REQUEST:
            raise ValueError(
                f"Coinalyze supports at most {COINALYZE_MAX_SYMBOLS_PER_REQUEST} symbols per request"
            )
        return ",".join(normalized)

    def get_future_markets(self) -> list[dict[str, Any]]:
        return self._get("/future-markets")

    def get_funding_rates(self, symbols: list[str]) -> list[dict[str, Any]]:
        return self._get(
            "/funding-rate",
            {"symbols": self._symbols_param(symbols)},
        )

    def get_open_interest_history(
        self,
        symbols: list[str],
        *,
        start: int,
        end: int,
        interval: str = "1hour",
    ) -> list[dict[str, Any]]:
        return self._get(
            "/open-interest-history",
            {
                "symbols": self._symbols_param(symbols),
                "interval": interval,
                "from": start,
                "to": end,
                "convert_to_usd": "true",
            },
        )

    def get_liquidation_history(
        self,
        symbols: list[str],
        *,
        start: int,
        end: int,
        interval: str = "1hour",
    ) -> list[dict[str, Any]]:
        return self._get(
            "/liquidation-history",
            {
                "symbols": self._symbols_param(symbols),
                "interval": interval,
                "from": start,
                "to": end,
                "convert_to_usd": "true",
            },
        )

    def get_long_short_ratio_history(
        self,
        symbols: list[str],
        *,
        start: int,
        end: int,
        interval: str = "1hour",
    ) -> list[dict[str, Any]]:
        return self._get(
            "/long-short-ratio-history",
            {
                "symbols": self._symbols_param(symbols),
                "interval": interval,
                "from": start,
                "to": end,
            },
        )


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _history_for_symbol(
    payload: list[dict[str, Any]],
    symbol: str,
) -> list[dict[str, Any]]:
    for item in payload:
        if item.get("symbol") != symbol:
            continue
        history = item.get("history")
        if isinstance(history, list):
            return [row for row in history if isinstance(row, dict)]
    return []


def _current_for_symbol(
    payload: list[dict[str, Any]],
    symbol: str,
) -> dict[str, Any] | None:
    for item in payload:
        if item.get("symbol") == symbol:
            return item
    return None


def _history_timestamp(row: dict[str, Any]) -> datetime | None:
    timestamp = _as_float(row.get("t"))
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _current_timestamp(row: dict[str, Any]) -> datetime | None:
    timestamp_ms = _as_float(row.get("update"))
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)


def _liquidation_value_usd(row: dict[str, Any]) -> float:
    return float(_as_float(row.get("l")) or 0.0) + float(
        _as_float(row.get("s")) or 0.0
    )


def _ordered_history_closes(
    history: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    for row in history:
        timestamp = _as_float(row.get("t"))
        close = _as_float(row.get("c"))
        if timestamp is None or close is None or close <= 0:
            continue
        rows.append((timestamp, close))
    return sorted(rows)


def _history_change_pct(
    history: list[dict[str, Any]],
    *,
    seconds: int,
) -> float | None:
    rows = _ordered_history_closes(history)
    if len(rows) < 2:
        return None
    latest_time, latest_close = rows[-1]
    cutoff = latest_time - seconds
    references = [item for item in rows[:-1] if item[0] <= cutoff]
    if not references:
        return None
    reference_time, reference_close = references[-1]
    # Do not label a many-hour gap as a 15m or 1h observation.
    if latest_time - reference_time > seconds * 2:
        return None
    return ((latest_close / reference_close) - 1.0) * 100.0


def _latest_change_zscore(
    history: list[dict[str, Any]],
) -> float | None:
    rows = _ordered_history_closes(history)
    changes = [
        ((current / previous) - 1.0) * 100.0
        for (_, previous), (_, current) in zip(rows, rows[1:])
        if previous > 0
    ]
    if len(changes) < 8:
        return None
    deviation = pstdev(changes)
    if deviation <= 0:
        return 0.0
    return (changes[-1] - mean(changes)) / deviation


class CoinalyzeMarketIntelligenceProvider:
    """Normalize Coinalyze futures evidence into Wave 8 derivatives context.

    The default deliberately uses one stable-margined perpetual market per
    asset. Four symbol-call units are consumed for a fully enriched asset:
    funding, 15m-granularity open-interest history, liquidation history and long/short
    ratio history. This keeps enrichment of a short finalist list within the
    official 40-symbol-call/minute free-tier budget.
    """

    name = "coinalyze"
    estimated_symbol_call_units_per_asset = 4

    _QUOTE_PRIORITY = {"USDT": 0, "USDC": 1, "USD": 2}
    _ASSET_ALIASES = {
        "XBT": "BTC",
        "XDG": "DOGE",
    }
    _KNOWN_QUOTES = (
        "USDT",
        "USDC",
        "USD",
        "EUR",
        "GBP",
        "CAD",
        "AUD",
        "JPY",
        "BTC",
        "ETH",
    )

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: CoinalyzeClient | None = None,
        preferred_exchanges: tuple[str, ...] | None = None,
        max_markets_per_asset: int = 1,
        market_cache_ttl_seconds: int = 3600,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("Coinalyze API key is required")
            client = CoinalyzeClient(api_key)
        if max_markets_per_asset < 1:
            raise ValueError("max_markets_per_asset must be at least 1")

        self.client = client
        exchange_order = (
            DEFAULT_PREFERRED_EXCHANGES
            if preferred_exchanges is None
            else preferred_exchanges
        )
        self.preferred_exchanges = tuple(item.upper() for item in exchange_order)
        self.max_markets_per_asset = max_markets_per_asset
        self.market_cache_ttl_seconds = market_cache_ttl_seconds
        self._market_cache: list[dict[str, Any]] | None = None
        self._market_cache_loaded_at = 0.0

    def _future_markets(self) -> list[dict[str, Any]]:
        now = monotonic()
        if (
            self._market_cache is None
            or now - self._market_cache_loaded_at >= self.market_cache_ttl_seconds
        ):
            self._market_cache = self.client.get_future_markets()
            self._market_cache_loaded_at = now
        return self._market_cache

    @classmethod
    def _base_asset(cls, symbol: str) -> str:
        normalized = "".join(ch for ch in symbol.upper() if ch.isalnum())
        for quote in cls._KNOWN_QUOTES:
            if normalized.endswith(quote) and len(normalized) > len(quote):
                normalized = normalized[: -len(quote)]
                break
        return cls._ASSET_ALIASES.get(normalized, normalized)

    def _market_sort_key(self, market: dict[str, Any]) -> tuple[int, int, str]:
        exchange = str(market.get("exchange") or "").upper()
        try:
            exchange_rank = self.preferred_exchanges.index(exchange)
        except ValueError:
            exchange_rank = len(self.preferred_exchanges) + 1
        quote = str(market.get("quote_asset") or "").upper()
        quote_rank = self._QUOTE_PRIORITY.get(quote, len(self._QUOTE_PRIORITY) + 1)
        return exchange_rank, quote_rank, str(market.get("symbol") or "")

    def _select_markets(self, symbol: str) -> list[dict[str, Any]]:
        base_asset = self._base_asset(symbol)
        eligible = [
            market
            for market in self._future_markets()
            if str(market.get("base_asset") or "").upper() == base_asset
            and market.get("is_perpetual") is True
            and str(market.get("margined") or "").upper() == "STABLE"
            and str(market.get("quote_asset") or "").upper() in self._QUOTE_PRIORITY
            and isinstance(market.get("symbol"), str)
        ]
        return sorted(eligible, key=self._market_sort_key)[: self.max_markets_per_asset]

    def _snapshot(
        self,
        market: dict[str, Any],
        *,
        now: datetime,
    ) -> DerivativesSnapshot:
        api_symbol = str(market["symbol"])
        end = int(now.timestamp())
        start = int((now - timedelta(hours=24)).timestamp())
        warnings: list[str] = []
        observed_at: list[datetime] = []

        selected_exchange = str(market.get("exchange") or "").upper()
        if self.preferred_exchanges and selected_exchange not in self.preferred_exchanges:
            warnings.append(
                f"Coinalyze fallback exchange selected: {selected_exchange or 'UNKNOWN'}"
            )

        funding_payload = self.client.get_funding_rates([api_symbol])
        funding_row = _current_for_symbol(funding_payload, api_symbol)
        funding_rate = _as_float(funding_row.get("value")) if funding_row else None
        if funding_row:
            funding_ts = _current_timestamp(funding_row)
            if funding_ts:
                observed_at.append(funding_ts)
        if funding_rate is None:
            warnings.append("Coinalyze funding rate unavailable")

        oi_payload = self.client.get_open_interest_history(
            [api_symbol],
            start=start,
            end=end,
            interval="15min",
        )
        oi_history = _history_for_symbol(oi_payload, api_symbol)
        open_interest_usd: float | None = None
        oi_change_15m_pct: float | None = None
        oi_change_1h_pct: float | None = None
        oi_change_24h_pct: float | None = None
        oi_acceleration_zscore: float | None = None
        if oi_history:
            first_close = _as_float(oi_history[0].get("c"))
            last_close = _as_float(oi_history[-1].get("c"))
            open_interest_usd = last_close
            oi_change_15m_pct = _history_change_pct(
                oi_history,
                seconds=15 * 60,
            )
            oi_change_1h_pct = _history_change_pct(
                oi_history,
                seconds=60 * 60,
            )
            oi_acceleration_zscore = _latest_change_zscore(oi_history)
            if first_close is not None and first_close > 0 and last_close is not None:
                oi_change_24h_pct = ((last_close / first_close) - 1.0) * 100.0
            latest_ts = _history_timestamp(oi_history[-1])
            if latest_ts:
                observed_at.append(latest_ts)
        else:
            warnings.append("Coinalyze open-interest history unavailable")

        liquidation_payload = self.client.get_liquidation_history(
            [api_symbol],
            start=start,
            end=end,
            interval="5min",
        )
        liquidation_history = _history_for_symbol(liquidation_payload, api_symbol)
        liquidations_1h_usd: float | None = None
        liquidations_24h_usd: float | None = None
        if liquidation_history:
            cutoff_1h = end - 60 * 60
            rows_24h: list[dict[str, Any]] = []
            rows_1h: list[dict[str, Any]] = []
            for row in liquidation_history:
                timestamp = _as_float(row.get("t"))
                if timestamp is None:
                    continue
                if start <= timestamp <= end:
                    rows_24h.append(row)
                    if timestamp >= cutoff_1h:
                        rows_1h.append(row)

            if rows_24h:
                liquidations_24h_usd = sum(
                    _liquidation_value_usd(row) for row in rows_24h
                )
            if rows_1h:
                liquidations_1h_usd = sum(
                    _liquidation_value_usd(row) for row in rows_1h
                )
            else:
                warnings.append(
                    "Coinalyze liquidation observations unavailable in trailing 1h"
                )

            timestamped_rows = [
                row for row in liquidation_history if _history_timestamp(row) is not None
            ]
            if timestamped_rows:
                latest_row = max(
                    timestamped_rows,
                    key=lambda row: float(_as_float(row.get("t")) or 0.0),
                )
                latest_ts = _history_timestamp(latest_row)
                if latest_ts:
                    observed_at.append(latest_ts)
        else:
            warnings.append("Coinalyze liquidation history unavailable")

        long_short_ratio: float | None = None
        if market.get("has_long_short_ratio_data") is True:
            ratio_payload = self.client.get_long_short_ratio_history(
                [api_symbol],
                start=start,
                end=end,
            )
            ratio_history = _history_for_symbol(ratio_payload, api_symbol)
            if ratio_history:
                long_short_ratio = _as_float(ratio_history[-1].get("r"))
                latest_ts = _history_timestamp(ratio_history[-1])
                if latest_ts:
                    observed_at.append(latest_ts)
            else:
                warnings.append("Coinalyze long/short ratio history unavailable")
        else:
            warnings.append("Coinalyze market does not publish long/short ratio data")

        any_evidence = any(
            value is not None
            for value in (
                funding_rate,
                open_interest_usd,
                oi_change_15m_pct,
                oi_change_1h_pct,
                oi_change_24h_pct,
                oi_acceleration_zscore,
                liquidations_1h_usd,
                liquidations_24h_usd,
                long_short_ratio,
            )
        )
        if not observed_at:
            warnings.append("Coinalyze provider timestamps unavailable")

        return DerivativesSnapshot(
            symbol=api_symbol,
            provider=self.name,
            status=AVAILABLE if any_evidence else UNAVAILABLE,
            observed_at=max(observed_at) if observed_at else now,
            funding_rate_pct=funding_rate,
            open_interest_usd=open_interest_usd,
            open_interest_change_15m_pct=oi_change_15m_pct,
            open_interest_change_1h_pct=oi_change_1h_pct,
            open_interest_change_24h_pct=oi_change_24h_pct,
            open_interest_acceleration_zscore=oi_acceleration_zscore,
            liquidations_1h_usd=liquidations_1h_usd,
            liquidations_24h_usd=liquidations_24h_usd,
            long_short_ratio=long_short_ratio,
            warnings=tuple(warnings),
        )

    def fetch(self, symbol: str) -> MarketIntelligenceBundle:
        markets = self._select_markets(symbol)
        if not markets:
            return MarketIntelligenceBundle(symbol=symbol)

        now = datetime.now(timezone.utc)
        snapshots = tuple(self._snapshot(market, now=now) for market in markets)
        return MarketIntelligenceBundle(symbol=symbol, derivatives=snapshots)
