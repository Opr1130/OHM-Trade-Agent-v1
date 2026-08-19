from datetime import datetime, timedelta, timezone

from app.scanner.models import MarketSnapshot
from app.scanner.reference_market_validation import (
    AMBIGUOUS,
    CONFIRMED,
    PRICE_DISAMBIGUATED,
    evaluate_reference_market,
)


NOW = datetime(2026, 8, 19, 4, 35, tzinfo=timezone.utc)


def _snapshot(price=25.0):
    return MarketSnapshot(
        symbol="LINKUSD",
        last_price=price,
        ema20=24,
        ema50=23,
        ema200=20,
        rsi=58,
        macd_line=1,
        macd_signal=.5,
        macd_histogram=.5,
        atr=1,
        atr_pct=4,
        volume_ratio=3.0,
        technical_score=90,
        trend="bullish",
        underlying_asset="LINK",
        primary_pair="LINKUSD",
        primary_quote_currency="USD",
        ticker_last=price,
    )


def _row(coin_id, name, price):
    return {
        "id": coin_id,
        "symbol": "link",
        "name": name,
        "current_price": price,
        "market_cap": 1000,
        "market_cap_rank": 10,
        "total_volume": 100,
        "price_change_percentage_24h": 1.0,
        "last_updated": (NOW - timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
    }


def test_duplicate_ticker_is_price_disambiguated_only_when_unique_match_is_clear():
    result = evaluate_reference_market(
        _snapshot(25.0),
        [
            _row("chainlink", "Chainlink", 25.10),
            _row("other-link", "Other Link", 2.0),
            _row("link-two", "Link Two", 0.25),
        ],
        1.0,
        "KEYLESS",
        NOW,
    )
    assert result.status == CONFIRMED
    assert result.mapping_status == PRICE_DISAMBIGUATED
    assert result.coingecko_id == "chainlink"
    assert result.matched_candidate_count == 3
    assert result.price_divergence_pct < 1.0
    assert "uniquely matched Kraken" in result.warnings[0]


def test_duplicate_ticker_stays_ambiguous_when_two_prices_are_plausible():
    result = evaluate_reference_market(
        _snapshot(25.0),
        [
            _row("chainlink", "Chainlink", 25.10),
            _row("other-link", "Other Link", 25.30),
        ],
        1.0,
        "KEYLESS",
        NOW,
    )
    assert result.status == AMBIGUOUS
    assert result.mapping_status == AMBIGUOUS
    assert result.coingecko_id is None


def test_duplicate_ticker_stays_ambiguous_when_best_price_is_not_close_enough():
    result = evaluate_reference_market(
        _snapshot(25.0),
        [
            _row("candidate-a", "Candidate A", 26.0),
            _row("candidate-b", "Candidate B", 40.0),
        ],
        1.0,
        "KEYLESS",
        NOW,
    )
    assert result.status == AMBIGUOUS
    assert result.coingecko_id is None
