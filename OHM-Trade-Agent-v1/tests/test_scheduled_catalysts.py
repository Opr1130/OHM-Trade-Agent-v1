from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.scanner.models import MarketSnapshot
from app.scanner.reference_market_validation import (
    AMBIGUOUS,
    CONFIRMED,
    UNIQUE,
    ReferenceMarketValidation,
)
from app.scanner.scheduled_catalysts import (
    AVAILABLE,
    NO_UPCOMING_EVENTS,
    RESOLVED,
    UNAVAILABLE,
    UNRESOLVED,
    parse_scheduled_event,
    validate_scheduled_catalysts,
)
from app.services import coinmarketcal
from app.services.coinmarketcal import CoinMarketCalAPIError, CoinMarketCalClient


NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)


def snapshot(symbol="SOLUSD", underlying="SOL", coin_id="solana", name="Solana"):
    item = MarketSnapshot(
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
    )
    item.independent_market_reference = ReferenceMarketValidation(
        status=CONFIRMED,
        available=True,
        mapping_status=UNIQUE,
        api_mode="KEYLESS",
        coingecko_id=coin_id,
        coingecko_name=name,
        matched_candidate_count=1,
    )
    return item


def ambiguous_snapshot():
    item = snapshot()
    item.independent_market_reference = ReferenceMarketValidation(
        status=AMBIGUOUS,
        available=True,
        mapping_status=AMBIGUOUS,
        api_mode="KEYLESS",
        matched_candidate_count=2,
    )
    return item


def coin_row(symbol="SOL", name="Solana", slug="solana"):
    return {"symbol": symbol, "name": name, "slug": slug}


def event_row(
    slug="solana",
    *,
    event_id=1,
    days=1,
    estimated=False,
):
    return {
        "id": event_id,
        "title": "Protocol event",
        "date": (NOW + timedelta(days=days)).isoformat(),
        "dateEnd": (NOW + timedelta(days=days + 1)).isoformat(),
        "dateType": "date" if not estimated else "month",
        "isEstimated": estimated,
        "displayedDate": "August 2026" if estimated else "9 Aug 2026",
        "categories": [{"name": "Release"}],
        "coins": [{"slug": slug}],
        "impact": "medium",
        "impactSummary": "Scheduled provider evidence",
    }


class Client:
    def __init__(self, coins=None, events=None):
        self.coins = coins if coins is not None else [coin_row()]
        self.events = events if events is not None else [event_row()]
        self.coin_calls = []
        self.event_calls = []
    def get_coins(self, query):
        self.coin_calls.append(query)
        return self.coins
    def get_events(self, slugs, from_time, to_time):
        self.event_calls.append((slugs, from_time, to_time))
        return self.events


def test_missing_key_is_unavailable_for_unique_identity(tmp_path):
    candidate = snapshot()
    summary = validate_scheduled_catalysts(
        [candidate], cache_path=tmp_path / "cache.json", now=NOW
    )
    assert candidate.scheduled_catalyst_context.status == UNAVAILABLE
    assert summary.events_request_count == 0


def test_unique_identity_resolves_safely_and_uses_one_events_batch(tmp_path):
    client = Client()
    sleeps = []
    candidates = [
        snapshot(),
        snapshot("BTCUSD", "BTC", "bitcoin", "Bitcoin"),
    ]
    client.coins = [coin_row(), coin_row("BTC", "Bitcoin", "bitcoin")]
    client.events = [event_row(), event_row("bitcoin", event_id=2)]
    summary = validate_scheduled_catalysts(
        candidates,
        api_key="key",
        client=client,
        cache_path=tmp_path / "cache.json",
        now=NOW,
        sleep=sleeps.append,
    )
    assert all(item.scheduled_catalyst_context.mapping_status == RESOLVED for item in candidates)
    assert all(item.scheduled_catalyst_context.status == AVAILABLE for item in candidates)
    assert len(client.coin_calls) == 2
    assert sleeps == [1.0]
    assert len(client.event_calls) == 1
    assert set(client.event_calls[0][0]) == {"solana", "bitcoin"}
    assert summary.events_request_count == 1


def test_ambiguous_coingecko_identity_is_unresolved_without_guess(tmp_path):
    candidate = ambiguous_snapshot()
    client = Client()
    validate_scheduled_catalysts(
        [candidate], api_key="key", client=client,
        cache_path=tmp_path / "cache.json", now=NOW,
    )
    context = candidate.scheduled_catalyst_context
    assert context.status == UNRESOLVED
    assert context.coinmarketcal_slug is None
    assert client.coin_calls == []
    assert client.event_calls == []


def test_multiple_plausible_coinmarketcal_matches_are_unresolved(tmp_path):
    candidate = snapshot()
    client = Client(coins=[
        coin_row(slug="solana"),
        coin_row(slug="solana-network"),
    ])
    validate_scheduled_catalysts(
        [candidate], api_key="key", client=client,
        cache_path=tmp_path / "cache.json", now=NOW,
    )
    assert candidate.scheduled_catalyst_context.status == UNRESOLVED
    assert client.event_calls == []


def test_mapping_cache_reused_without_repeated_lookup(tmp_path):
    cache_path = tmp_path / "cache.json"
    first_client = Client(events=[])
    first = snapshot()
    validate_scheduled_catalysts(
        [first], api_key="key", client=first_client,
        cache_path=cache_path, now=NOW,
    )
    second_client = Client(events=[])
    second = snapshot()
    validate_scheduled_catalysts(
        [second], api_key="key", client=second_client,
        cache_path=cache_path, now=NOW,
    )
    assert first_client.coin_calls == ["SOL"]
    assert second_client.coin_calls == []
    assert len(second_client.event_calls) == 1
    assert second.scheduled_catalyst_context.status == NO_UPCOMING_EVENTS


def test_estimated_event_has_no_exact_countdown_but_exact_event_does():
    estimated = parse_scheduled_event(event_row(estimated=True), NOW)
    exact = parse_scheduled_event(event_row(estimated=False), NOW)
    assert estimated.is_estimated
    assert estimated.displayed_date == "August 2026"
    assert estimated.time_to_event_seconds is None
    assert exact.time_to_event_seconds == 24 * 3600


def test_api_failure_fails_open_but_runtime_error_propagates(tmp_path):
    class APIFailure(Client):
        def get_coins(self, query):
            raise CoinMarketCalAPIError("offline")
    candidate = snapshot()
    validate_scheduled_catalysts(
        [candidate], api_key="key", client=APIFailure(),
        cache_path=tmp_path / "one.json", now=NOW,
    )
    assert candidate.scheduled_catalyst_context.status == UNAVAILABLE

    class Defect(Client):
        def get_coins(self, query):
            raise RuntimeError("programming defect")
    with pytest.raises(RuntimeError, match="programming defect"):
        validate_scheduled_catalysts(
            [snapshot()], api_key="key", client=Defect(),
            cache_path=tmp_path / "two.json", now=NOW,
        )


def test_events_failure_fails_open_with_resolved_mapping(tmp_path):
    class APIFailure(Client):
        def get_events(self, slugs, from_time, to_time):
            raise CoinMarketCalAPIError("offline")
    candidate = snapshot()
    validate_scheduled_catalysts(
        [candidate], api_key="key", client=APIFailure(),
        cache_path=tmp_path / "cache.json", now=NOW,
    )
    context = candidate.scheduled_catalyst_context
    assert context.status == UNAVAILABLE
    assert context.mapping_status == RESOLVED
    assert context.coinmarketcal_slug == "solana"


def test_catalyst_payload_is_bounded_and_key_is_never_exposed(tmp_path):
    secret = "coinmarketcal-secret"
    client = Client(events=[event_row(event_id=index, days=index + 1) for index in range(5)])
    candidate = snapshot()
    validate_scheduled_catalysts(
        [candidate], api_key=secret, client=client,
        cache_path=tmp_path / "cache.json", now=NOW,
    )
    assert candidate.scheduled_catalyst_context.event_count_next_7d == 5
    assert len(candidate.scheduled_catalyst_context.events) == 3
    assert secret not in json.dumps(asdict(candidate.scheduled_catalyst_context))


def test_coinmarketcal_client_uses_v2_and_api_key_header(monkeypatch):
    secret = "key-secret"
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"data": [], "meta": {}}
    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()
    monkeypatch.setattr(coinmarketcal.httpx, "get", fake_get)
    rows = CoinMarketCalClient(secret).get_coins("SOL")
    assert rows == []
    assert captured["url"] == "https://api.coinmarketcal.com/v2/coins"
    assert captured["params"] == {"q": "SOL", "limit": 200}
    assert captured["headers"] == {
        "x-api-key": secret,
        "Accept": "application/json",
    }
