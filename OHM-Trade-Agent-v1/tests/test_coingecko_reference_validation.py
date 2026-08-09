from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.scanner.models import MarketSnapshot
from app.scanner.reference_market_validation import (
    AMBIGUOUS,
    CONFIRMED,
    MATERIAL_DIVERGENCE,
    STALE,
    UNAVAILABLE,
    evaluate_reference_market,
    validate_finalist_references,
)
from app.services import chief_analyst, coingecko
from app.services.coingecko import CoinGeckoAPIError, CoinGeckoClient


NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)


def snapshot(symbol="SOLUSD", underlying="SOL", quote="USD", price=100):
    return MarketSnapshot(
        symbol=symbol, last_price=price, ema20=99, ema50=95, ema200=90,
        rsi=58, macd_line=1, macd_signal=.5, macd_histogram=.5,
        atr=2, atr_pct=2, volume_ratio=1.5, technical_score=90,
        trend="bullish", underlying_asset=underlying, primary_pair=symbol,
        primary_quote_currency=quote, ticker_last=price,
    )


def row(
    symbol="sol", coin_id="solana", name="Solana", price=100,
    rank=5, market_cap=50_000, age=10,
):
    return {
        "id": coin_id, "symbol": symbol, "name": name,
        "current_price": price, "market_cap": market_cap,
        "market_cap_rank": rank, "total_volume": 10_000,
        "price_change_percentage_24h": 2.5,
        "last_updated": (NOW - timedelta(seconds=age)).isoformat().replace("+00:00", "Z"),
    }


def evaluate(candidate=None, rows=None, rate=1):
    return evaluate_reference_market(
        candidate or snapshot(), rows or [row()], rate, "KEYLESS", NOW
    )


def test_unique_symbol_maps_directly_case_insensitive():
    result = evaluate(rows=[row(symbol="SoL")])
    assert result.status == CONFIRMED
    assert result.mapping_status == "UNIQUE"
    assert result.coingecko_id == "solana"
    assert result.matched_candidate_count == 1


def test_alias_xbt_normalizes_to_btc():
    result = evaluate(snapshot("XBTUSD", "XBT", price=100), [row("btc", "bitcoin", "Bitcoin")])
    assert result.coingecko_id == "bitcoin"


def test_multiple_matches_choose_best_rank_and_mark_ambiguous():
    result = evaluate(rows=[
        row(coin_id="small-sol", rank=500, market_cap=5_000),
        row(coin_id="solana", rank=5, market_cap=50_000),
    ])
    assert result.status == AMBIGUOUS
    assert result.mapping_status == AMBIGUOUS
    assert result.coingecko_id == "solana"
    assert result.matched_candidate_count == 2


def test_multiple_unranked_matches_choose_highest_market_cap():
    result = evaluate(rows=[
        row(coin_id="small", rank=None, market_cap=1),
        row(coin_id="large", rank=None, market_cap=10),
    ])
    assert result.coingecko_id == "large"
    assert result.status == AMBIGUOUS


def test_missing_symbol_is_unavailable():
    result = evaluate(rows=[row(symbol="btc")])
    assert result.status == UNAVAILABLE
    assert not result.available


def test_usd_pair_price_comparison():
    result = evaluate(rows=[row(price=102)])
    assert result.kraken_normalized_price_usd == 100
    assert result.absolute_price_difference_usd == 2
    assert result.price_divergence_pct == 2


def test_usdt_only_uses_live_conversion_not_parity():
    candidate = snapshot("ABCUSDT", "ABC", "USDT", 100)
    result = evaluate(candidate, [row("abc", "abc", "ABC", price=99)], rate=.99)
    assert result.kraken_normalized_price_usd == pytest.approx(99)
    assert result.price_divergence_pct == pytest.approx(0)


def test_stale_reference_semantics():
    assert evaluate(rows=[row(age=301)]).status == STALE


def test_material_divergence_semantics():
    assert evaluate(rows=[row(price=106)]).status == MATERIAL_DIVERGENCE


def test_api_failure_is_unavailable_and_candidates_continue():
    class Client:
        api_mode = "KEYLESS"
        def get_markets_by_symbols(self, symbols):
            raise CoinGeckoAPIError("offline")
    candidates = [snapshot(), snapshot("BTCUSD", "BTC")]
    summary = validate_finalist_references(candidates, 1, client=Client(), now=NOW)
    assert summary.unavailable == 2
    assert len(candidates) == 2
    assert all(item.independent_market_reference.status == UNAVAILABLE for item in candidates)


def test_unexpected_runtime_error_propagates():
    class Client:
        api_mode = "KEYLESS"
        def get_markets_by_symbols(self, symbols):
            raise RuntimeError("programming defect")
    candidate = snapshot()
    with pytest.raises(RuntimeError, match="programming defect"):
        validate_finalist_references([candidate], 1, client=Client(), now=NOW)
    assert candidate.independent_market_reference is None


def test_api_key_header_is_used_but_never_returned(monkeypatch):
    secret = "secret-demo-key"
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return [row()]
    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return Response()
    monkeypatch.setattr(coingecko.httpx, "get", fake_get)
    client = CoinGeckoClient(api_key=secret)
    rows = client.get_markets_by_symbols(["SOL"])
    result = evaluate_reference_market(snapshot(), rows, 1, client.api_mode, NOW)
    assert captured["headers"] == {"x-cg-demo-api-key": secret}
    assert secret not in json.dumps(asdict(result))
    assert secret not in repr(result)


def test_one_batch_call_for_maximum_eight_finalists():
    class Client:
        api_mode = "KEYLESS"
        def __init__(self): self.calls = []
        def get_markets_by_symbols(self, symbols):
            self.calls.append(symbols)
            return [row(symbol=f"a{i}", coin_id=f"a{i}") for i in range(8)]
    client = Client()
    candidates = [snapshot(f"A{i}USD", f"A{i}") for i in range(8)]
    summary = validate_finalist_references(candidates, 1, client=client, now=NOW)
    assert summary.requested == 8
    assert len(client.calls) == 1
    assert len(client.calls[0]) == 8


def test_chief_receives_reference_in_exactly_one_responses_call(monkeypatch):
    candidate = snapshot()
    candidate.independent_market_reference = evaluate()
    class Responses:
        calls = 0
        payload = None
        def create(self, **kwargs):
            self.calls += 1
            self.payload = json.loads(kwargs["input"][1]["content"])
            return SimpleNamespace(output_text=json.dumps({
                "market_view": "", "recommended_action": "no_trade",
                "top_candidates": [], "summary": "",
            }))
    responses = Responses()
    monkeypatch.setattr(
        chief_analyst, "OpenAI",
        lambda api_key: SimpleNamespace(responses=responses),
    )
    chief_analyst.review_candidates([candidate], "model", "key")
    assert responses.calls == 1
    reference = responses.payload["candidates"][0]["independent_market_reference"]
    assert reference["coingecko_id"] == "solana"
    assert reference["status"] == CONFIRMED


def test_no_kraken_private_or_order_endpoint_added():
    source = __import__("pathlib").Path("app/services/coingecko.py").read_text()
    assert "/private/" not in source
    assert "AddOrder" not in source
