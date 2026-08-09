from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.scanner.global_market_context import (
    AVAILABLE as GLOBAL_AVAILABLE,
    UNAVAILABLE as GLOBAL_UNAVAILABLE,
    load_coingecko_global_context,
)
from app.scanner.models import MarketSnapshot
from app.scanner.news_context import (
    AVAILABLE as NEWS_AVAILABLE,
    UNAVAILABLE as NEWS_UNAVAILABLE,
    UNRESOLVED as NEWS_UNRESOLVED,
    validate_finalist_news,
)
from app.scanner.reference_market_validation import (
    AMBIGUOUS,
    UNIQUE,
    UNAVAILABLE as REFERENCE_UNAVAILABLE,
    ReferenceMarketValidation,
    validate_finalist_references,
)
from app.services import cryptopanic
from app.services.coingecko import CoinGeckoAPIError
from app.services.cryptopanic import CryptoPanicAPIError, CryptoPanicClient


NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)


def snapshot(
    symbol="SOLUSD",
    underlying="SOL",
    coingecko_id=None,
    coingecko_name=None,
) -> MarketSnapshot:
    identities = {
        "SOL": ("solana", "Solana"),
        "XBT": ("bitcoin", "Bitcoin"),
        "BTC": ("bitcoin", "Bitcoin"),
        "XDG": ("dogecoin", "Dogecoin"),
        "DOGE": ("dogecoin", "Dogecoin"),
    }
    default_id, default_name = identities.get(
        underlying, (underlying.lower(), underlying)
    )
    candidate = MarketSnapshot(
        symbol=symbol,
        last_price=100,
        ema20=99,
        ema50=98,
        ema200=97,
        rsi=55,
        macd_line=1,
        macd_signal=.5,
        macd_histogram=.5,
        atr=2,
        atr_pct=2,
        volume_ratio=1.2,
        technical_score=85,
        trend="bullish",
        underlying_asset=underlying,
        primary_pair=symbol,
        primary_quote_currency="USD",
        ticker_last=100,
    )
    candidate.independent_market_reference = ReferenceMarketValidation(
        status="CONFIRMED",
        available=True,
        mapping_status=UNIQUE,
        api_mode="KEYLESS",
        coingecko_id=coingecko_id or default_id,
        coingecko_name=coingecko_name or default_name,
        matched_candidate_count=1,
    )
    return candidate


def market_row(symbol="sol", coin_id="solana"):
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": coin_id.title(),
        "current_price": 100,
        "market_cap": 1_000,
        "market_cap_rank": 10,
        "total_volume": 100,
        "price_change_percentage_24h": 1,
        "last_updated": NOW.isoformat(),
    }


def post(
    code="SOL",
    *,
    hours=1,
    post_id=1,
    title="Solana update",
    instrument_title=None,
    instrument_slug=None,
):
    identities = {
        "SOL": ("Solana", "solana"),
        "BTC": ("Bitcoin", "bitcoin"),
        "DOGE": ("Dogecoin", "dogecoin"),
    }
    default_title, default_slug = identities.get(code, (code, code.lower()))
    return {
        "id": post_id,
        "title": title,
        "description": "Structured provider description",
        "published_at": (NOW - timedelta(hours=hours)).isoformat(),
        "kind": "news",
        "source": {"title": "Provider", "domain": "example.com"},
        "instruments": [{
            "code": code,
            "title": instrument_title or default_title,
            "slug": instrument_slug or default_slug,
        }],
        "votes": {"positive": 4, "negative": 1},
    }


def test_valid_coingecko_global_response_is_parsed():
    class Client:
        api_mode = "KEYLESS"
        def get_global_market(self):
            return {
                "total_market_cap": {"usd": 3_000_000},
                "total_volume": {"usd": 100_000},
                "market_cap_percentage": {"btc": 58.2, "eth": 12.1},
                "market_cap_change_percentage_24h_usd": 2.3,
                "updated_at": int(NOW.timestamp()) - 10,
            }
    result = load_coingecko_global_context(client=Client(), now=NOW)
    assert result.status == GLOBAL_AVAILABLE
    assert result.total_market_cap_usd == 3_000_000
    assert result.btc_market_cap_percentage == 58.2
    assert result.market_cap_change_24h_pct == 2.3
    assert result.age_seconds == 10


def test_coingecko_global_api_failure_fails_open():
    class Client:
        api_mode = "KEYLESS"
        def get_global_market(self):
            raise CoinGeckoAPIError("offline")
    result = load_coingecko_global_context(client=Client(), now=NOW)
    assert result.status == GLOBAL_UNAVAILABLE


def test_coingecko_uses_one_finalist_markets_request_plus_one_global_request():
    class Client:
        api_mode = "KEYLESS"
        def __init__(self):
            self.markets_calls = 0
            self.global_calls = 0
        def get_markets_by_symbols(self, symbols):
            self.markets_calls += 1
            return [market_row()]
        def get_global_market(self):
            self.global_calls += 1
            return {"updated_at": int(NOW.timestamp())}
    client = Client()
    validate_finalist_references([snapshot()], 1, client=client, now=NOW)
    load_coingecko_global_context(client=client, now=NOW)
    assert client.markets_calls == 1
    assert client.global_calls == 1


def test_missing_cryptopanic_token_is_unavailable_and_scan_continues():
    candidates = [snapshot()]
    summary = validate_finalist_news(candidates, auth_token=None, now=NOW)
    assert summary.request_count == 0
    assert candidates[0].news_context.status == NEWS_UNAVAILABLE
    assert len(candidates) == 1


def test_news_uses_one_batch_and_normalizes_xbt_xdg():
    class Client:
        def __init__(self): self.calls = []
        def get_posts(self, symbols):
            self.calls.append(symbols)
            return [post("BTC"), post("DOGE", post_id=2)]
    client = Client()
    candidates = [snapshot("XBTUSD", "XBT"), snapshot("XDGUSD", "XDG")]
    summary = validate_finalist_news(candidates, client=client, now=NOW)
    assert summary.request_count == 1
    assert client.calls == [["BTC", "DOGE"]]
    assert all(item.news_context.matched_post_count == 1 for item in candidates)


def test_unique_identity_exact_code_and_title_match_attaches_news():
    candidate = snapshot()
    class Client:
        def get_posts(self, symbols):
            return [post("SOL", instrument_title="Solana", instrument_slug="wrong")]
    validate_finalist_news([candidate], client=Client(), now=NOW)
    assert candidate.news_context.matched_post_count == 1


def test_unique_identity_exact_code_and_slug_match_attaches_news():
    candidate = snapshot()
    class Client:
        def get_posts(self, symbols):
            return [post("SOL", instrument_title="Wrong Asset", instrument_slug="solana")]
    validate_finalist_news([candidate], client=Client(), now=NOW)
    assert candidate.news_context.matched_post_count == 1


def test_same_ticker_wrong_title_and_slug_does_not_attach():
    candidate = snapshot()
    class Client:
        def get_posts(self, symbols):
            return [post(
                "SOL", instrument_title="Different Asset",
                instrument_slug="different-asset",
            )]
    validate_finalist_news([candidate], client=Client(), now=NOW)
    assert candidate.news_context.status == NEWS_AVAILABLE
    assert candidate.news_context.matched_post_count == 0
    assert candidate.news_context.recent_items == ()


def test_exact_identity_normalization_removes_only_formatting():
    candidate = snapshot(
        "PENGUUSD", "PENGU", "pudgy-penguins", "Pudgy Penguins"
    )
    class Client:
        def get_posts(self, symbols):
            return [post(
                "PENGU", instrument_title="Pudgy_Penguins",
                instrument_slug="not-the-id",
            )]
    validate_finalist_news([candidate], client=Client(), now=NOW)
    assert candidate.news_context.matched_post_count == 1


def test_ambiguous_coingecko_identity_is_unresolved_without_request():
    candidate = snapshot()
    candidate.independent_market_reference = ReferenceMarketValidation(
        status=AMBIGUOUS, available=True, mapping_status=AMBIGUOUS,
        api_mode="KEYLESS", matched_candidate_count=2,
    )
    class Client:
        def __init__(self): self.calls = 0
        def get_posts(self, symbols):
            self.calls += 1
            return [post("SOL")]
    client = Client()
    summary = validate_finalist_news([candidate], client=client, now=NOW)
    assert client.calls == 0
    assert summary.request_count == 0
    assert summary.unresolved == 1
    assert candidate.news_context.status == NEWS_UNRESOLVED
    assert candidate.news_context.matched_post_count == 0
    assert candidate.news_context.recent_items == ()


def test_unavailable_coingecko_identity_is_unresolved():
    candidate = snapshot()
    candidate.independent_market_reference = ReferenceMarketValidation(
        status=REFERENCE_UNAVAILABLE, available=False,
        mapping_status=REFERENCE_UNAVAILABLE, api_mode="KEYLESS",
    )
    summary = validate_finalist_news([candidate], client=object(), now=NOW)
    assert summary.request_count == 0
    assert candidate.news_context.status == NEWS_UNRESOLVED
    assert "ticker-only" in candidate.news_context.warnings[0]


def test_ticker_only_collision_cannot_create_news_evidence():
    candidate = snapshot()
    candidate.independent_market_reference = None
    class Client:
        def get_posts(self, symbols):
            raise AssertionError("no request should be made")
    validate_finalist_news([candidate], client=Client(), now=NOW)
    assert candidate.news_context.status == NEWS_UNRESOLVED
    assert candidate.news_context.matched_post_count == 0
    assert candidate.news_context.recent_post_count_6h == 0
    assert candidate.news_context.recent_post_count_24h == 0
    assert candidate.news_context.latest_post_age_seconds is None
    assert candidate.news_context.recent_items == ()


def test_news_uses_exactly_one_batch_for_eight_finalists():
    class Client:
        def __init__(self): self.calls = 0
        def get_posts(self, symbols):
            self.calls += 1
            assert len(symbols) == 8
            return []
    client = Client()
    candidates = [snapshot(f"A{index}USD", f"A{index}") for index in range(8)]
    summary = validate_finalist_news(candidates, client=client, now=NOW)
    assert client.calls == 1
    assert summary.request_count == 1


def test_mixed_identities_batch_only_safe_finalists_once():
    safe = snapshot()
    unsafe = snapshot("FUNUSD", "FUN")
    unsafe.independent_market_reference = ReferenceMarketValidation(
        status=AMBIGUOUS, available=True, mapping_status=AMBIGUOUS,
        api_mode="KEYLESS", matched_candidate_count=2,
    )
    class Client:
        def __init__(self): self.calls = []
        def get_posts(self, symbols):
            self.calls.append(symbols)
            return [post("SOL"), post("FUN", instrument_title="FunFair")]
    client = Client()
    validate_finalist_news([safe, unsafe], client=client, now=NOW)
    assert client.calls == [["SOL"]]
    assert safe.news_context.matched_post_count == 1
    assert unsafe.news_context.status == NEWS_UNRESOLVED


def test_structured_instruments_associate_only_related_news():
    candidate = snapshot()
    class Client:
        def get_posts(self, symbols):
            return [post("SOL"), post("BTC", post_id=2, title="SOL in title only")]
    validate_finalist_news([candidate], client=Client(), now=NOW)
    context = candidate.news_context
    assert context.status == NEWS_AVAILABLE
    assert context.matched_post_count == 1
    assert context.recent_items[0].title == "Solana update"
    assert context.recent_items[0].votes == {"positive": 4, "negative": 1}


def test_news_api_failure_fails_open_but_runtime_error_propagates():
    class APIFailure:
        def get_posts(self, symbols):
            raise CryptoPanicAPIError("offline")
    candidate = snapshot()
    validate_finalist_news([candidate], client=APIFailure(), now=NOW)
    assert candidate.news_context.status == NEWS_UNAVAILABLE

    class Defect:
        def get_posts(self, symbols):
            raise RuntimeError("programming defect")
    with pytest.raises(RuntimeError, match="programming defect"):
        validate_finalist_news([snapshot()], client=Defect(), now=NOW)


def test_news_payload_is_bounded_and_contains_no_secret():
    secret = "cryptopanic-secret"
    class Client:
        def get_posts(self, symbols):
            return [post("SOL", hours=index, post_id=index) for index in range(5)]
    candidate = snapshot()
    validate_finalist_news([candidate], auth_token=secret, client=Client(), now=NOW)
    assert len(candidate.news_context.recent_items) == 3
    serialized = json.dumps(asdict(candidate.news_context))
    assert secret not in serialized


def test_cryptopanic_client_uses_v2_plan_and_keeps_token_out_of_result(monkeypatch):
    secret = "secret-token"
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"results": [post()]}
    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()
    monkeypatch.setattr(cryptopanic.httpx, "get", fake_get)
    rows = CryptoPanicClient(secret, "developer").get_posts(["SOL"])
    assert captured["url"].endswith("/developer/v2/posts/")
    assert captured["params"]["auth_token"] == secret
    assert captured["params"]["currencies"] == "SOL"
    assert captured["params"]["public"] == "true"
    assert secret not in json.dumps(rows)
