from app.services.coinalyze_market_intelligence import (
    CoinalyzeMarketIntelligenceProvider,
    DEFAULT_PREFERRED_EXCHANGES,
)


class MarketCatalogClient:
    def get_future_markets(self):
        return [
            {
                "symbol": "PERP_SOL_USDT.W",
                "exchange": "W",
                "symbol_on_exchange": "PERP_SOL_USDT",
                "base_asset": "SOL",
                "quote_asset": "USDT",
                "is_perpetual": True,
                "margined": "STABLE",
                "has_long_short_ratio_data": False,
            },
            {
                "symbol": "SOLUSDT.6",
                "exchange": "6",
                "symbol_on_exchange": "SOLUSDT",
                "base_asset": "SOL",
                "quote_asset": "USDT",
                "is_perpetual": True,
                "margined": "STABLE",
                "has_long_short_ratio_data": True,
            },
            {
                "symbol": "SOLUSDT.A",
                "exchange": "A",
                "symbol_on_exchange": "SOLUSDT",
                "base_asset": "SOL",
                "quote_asset": "USDT",
                "is_perpetual": True,
                "margined": "STABLE",
                "has_long_short_ratio_data": True,
            },
            {
                "symbol": "SOL-USDT-SWAP.3",
                "exchange": "3",
                "symbol_on_exchange": "SOL-USDT-SWAP",
                "base_asset": "SOL",
                "quote_asset": "USDT",
                "is_perpetual": True,
                "margined": "STABLE",
                "has_long_short_ratio_data": True,
            },
        ]


def test_default_venue_order_prefers_binance_before_bybit_okx_and_fallbacks():
    assert DEFAULT_PREFERRED_EXCHANGES == ("A", "6", "3")

    provider = CoinalyzeMarketIntelligenceProvider(client=MarketCatalogClient())

    selected = provider._select_markets("SOLUSD")

    assert len(selected) == 1
    assert selected[0]["exchange"] == "A"
    assert selected[0]["symbol"] == "SOLUSDT.A"


def test_explicit_venue_order_overrides_default():
    provider = CoinalyzeMarketIntelligenceProvider(
        client=MarketCatalogClient(),
        preferred_exchanges=("6", "A", "3"),
    )

    selected = provider._select_markets("SOLUSD")

    assert selected[0]["exchange"] == "6"
