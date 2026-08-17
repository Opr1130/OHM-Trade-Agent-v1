from datetime import datetime, timezone

import pytest

from app.services.coinalyze_market_intelligence import (
    CoinalyzeClient,
    CoinalyzeMarketIntelligenceProvider,
    CoinalyzeRateLimitError,
)


class FakeCoinalyzeClient:
    def __init__(self) -> None:
        self.market_calls = 0

    def get_future_markets(self):
        self.market_calls += 1
        return [
            {
                "symbol": "BTCUSD_PERP.Z",
                "exchange": "Z",
                "symbol_on_exchange": "BTCUSD",
                "base_asset": "BTC",
                "quote_asset": "USD",
                "is_perpetual": True,
                "margined": "STABLE",
                "has_long_short_ratio_data": True,
            },
            {
                "symbol": "BTCUSDT_PERP.A",
                "exchange": "A",
                "symbol_on_exchange": "BTCUSDT",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "is_perpetual": True,
                "margined": "STABLE",
                "has_long_short_ratio_data": True,
            },
            {
                "symbol": "BTCUSD_PERP.COIN",
                "exchange": "COIN",
                "symbol_on_exchange": "BTCUSD",
                "base_asset": "BTC",
                "quote_asset": "USD",
                "is_perpetual": True,
                "margined": "COIN",
                "has_long_short_ratio_data": True,
            },
        ]

    def get_funding_rates(self, symbols):
        assert symbols == ["BTCUSDT_PERP.A"]
        return [
            {
                "symbol": "BTCUSDT_PERP.A",
                "value": 0.04,
                "update": 1_786_744_800_000,
            }
        ]

    def get_open_interest_history(self, symbols, *, start, end, interval="1hour"):
        assert symbols == ["BTCUSDT_PERP.A"]
        assert end > start
        return [
            {
                "symbol": "BTCUSDT_PERP.A",
                "history": [
                    {"t": start + 300, "c": 100_000_000},
                    {"t": end - 300, "c": 108_000_000},
                ],
            }
        ]

    def get_liquidation_history(self, symbols, *, start, end, interval="1hour"):
        assert symbols == ["BTCUSDT_PERP.A"]
        assert interval == "5min"
        return [
            {
                "symbol": "BTCUSDT_PERP.A",
                "history": [
                    {"t": start + 300, "l": 1_000_000, "s": 500_000},
                    {"t": end - 3_500, "l": 400_000, "s": 100_000},
                    {"t": end - 1_200, "l": 2_000_000, "s": 1_000_000},
                ],
            }
        ]

    def get_long_short_ratio_history(self, symbols, *, start, end, interval="1hour"):
        assert symbols == ["BTCUSDT_PERP.A"]
        return [
            {
                "symbol": "BTCUSDT_PERP.A",
                "history": [
                    {"t": end - 300, "r": 1.25, "l": 55.5, "s": 44.5}
                ],
            }
        ]


def test_provider_normalizes_funding_oi_liquidations_and_long_short_ratio():
    provider = CoinalyzeMarketIntelligenceProvider(client=FakeCoinalyzeClient())

    bundle = provider.fetch("BTCUSD")

    assert len(bundle.derivatives) == 1
    snapshot = bundle.derivatives[0]
    assert snapshot.provider == "coinalyze"
    assert snapshot.symbol == "BTCUSDT_PERP.A"
    assert snapshot.funding_rate_pct == 0.04
    assert snapshot.open_interest_usd == 108_000_000
    assert snapshot.open_interest_change_24h_pct == pytest.approx(8.0)
    assert snapshot.liquidations_1h_usd == 3_500_000
    assert snapshot.liquidations_24h_usd == 5_000_000
    assert snapshot.long_short_ratio == 1.25


def test_provider_calculates_short_window_oi_acceleration_without_extra_call():
    class ShortWindowOI(FakeCoinalyzeClient):
        def get_open_interest_history(self, symbols, *, start, end, interval="1hour"):
            assert interval == "15min"
            history = [{"t": start, "c": 100_000_000}]
            closes = [
                101_000_000,
                101_100_000,
                101_200_000,
                101_300_000,
                101_400_000,
                101_500_000,
                101_600_000,
                101_700_000,
                103_500_000,
            ]
            history.extend(
                {"t": end - (len(closes) - index - 1) * 900, "c": close}
                for index, close in enumerate(closes)
            )
            return [{"symbol": "BTCUSDT_PERP.A", "history": history}]

    snapshot = CoinalyzeMarketIntelligenceProvider(
        client=ShortWindowOI()
    ).fetch("BTCUSD").derivatives[0]

    assert snapshot.open_interest_change_15m_pct == pytest.approx(
        (103_500_000 / 101_700_000 - 1) * 100
    )
    assert snapshot.open_interest_change_1h_pct is not None
    assert snapshot.open_interest_acceleration_zscore is not None


def test_provider_does_not_fabricate_zero_when_trailing_hour_has_no_liquidation_rows():
    class SparseLiquidations(FakeCoinalyzeClient):
        def get_liquidation_history(self, symbols, *, start, end, interval="1hour"):
            assert interval == "5min"
            return [
                {
                    "symbol": "BTCUSDT_PERP.A",
                    "history": [
                        {"t": end - 7_200, "l": 100.0, "s": 50.0},
                    ],
                }
            ]

    provider = CoinalyzeMarketIntelligenceProvider(client=SparseLiquidations())
    snapshot = provider.fetch("BTCUSD").derivatives[0]

    assert snapshot.liquidations_1h_usd is None
    assert snapshot.liquidations_24h_usd == 150.0
    assert "Coinalyze liquidation observations unavailable in trailing 1h" in snapshot.warnings


def test_provider_maps_kraken_xbt_to_btc_and_caches_market_catalog():
    client = FakeCoinalyzeClient()
    provider = CoinalyzeMarketIntelligenceProvider(client=client)

    first = provider.fetch("XBTUSD")
    second = provider.fetch("BTC/USD")

    assert first.derivatives[0].symbol == "BTCUSDT_PERP.A"
    assert second.derivatives[0].symbol == "BTCUSDT_PERP.A"
    assert client.market_calls == 1


def test_provider_returns_empty_bundle_when_asset_has_no_supported_stable_perpetual():
    class NoMarkets(FakeCoinalyzeClient):
        def get_future_markets(self):
            return []

    bundle = CoinalyzeMarketIntelligenceProvider(client=NoMarkets()).fetch("ABCUSD")
    assert bundle.symbol == "ABCUSD"
    assert bundle.derivatives == ()


def test_client_uses_api_key_header_not_query_string(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return []

    def fake_get(url, *, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr(
        "app.services.coinalyze_market_intelligence.httpx.get",
        fake_get,
    )

    CoinalyzeClient("test-secret").get_future_markets()

    assert captured["headers"]["api_key"] == "test-secret"
    assert "api_key" not in captured["params"]
    assert "test-secret" not in captured["url"]


def test_client_surfaces_retry_after_on_rate_limit(monkeypatch):
    class Response:
        status_code = 429
        headers = {"Retry-After": "7"}

        def raise_for_status(self):
            raise AssertionError("429 should be handled before raise_for_status")

        def json(self):
            return {"message": "too many requests"}

    monkeypatch.setattr(
        "app.services.coinalyze_market_intelligence.httpx.get",
        lambda *args, **kwargs: Response(),
    )

    with pytest.raises(CoinalyzeRateLimitError) as exc:
        CoinalyzeClient("test-secret").get_future_markets()

    assert exc.value.retry_after_seconds == 7
