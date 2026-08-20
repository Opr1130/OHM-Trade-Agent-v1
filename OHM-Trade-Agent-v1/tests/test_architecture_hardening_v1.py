import json
from types import SimpleNamespace

import httpx

from app.exchanges.kraken import KrakenClient
from app.scanner.models import MarketSnapshot
from app.services import chief_analyst, chief_runtime_guard
from app.services.kraken_transport import KrakenPublicTransport
from app.services.telegram_notifier import send_telegram_message


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("status", request=request, response=response)

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def _snapshot(symbol="SOLUSD"):
    return MarketSnapshot(
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
        underlying_asset=symbol.removesuffix("USD"),
        primary_pair=symbol,
        primary_quote_currency="USD",
        ticker_last=100,
    )


def test_kraken_transport_cache_avoids_duplicate_network_calls():
    transport = KrakenPublicTransport(requests_per_second=1000, burst=10, max_retries=0)
    fake = _Client([
        _Response({"error": [], "result": {"XXBTZUSD": {"status": "online"}}}),
    ])
    transport._client = fake
    first = transport.request("AssetPairs", {}, timeout_seconds=1)
    second = transport.request("AssetPairs", {}, timeout_seconds=1)
    assert first == second
    assert fake.calls == 1
    assert transport.telemetry_snapshot()["cache_hits"] == 1


def test_kraken_transport_retries_429(monkeypatch):
    transport = KrakenPublicTransport(requests_per_second=1000, burst=10, max_retries=1)
    fake = _Client([
        _Response({}, status_code=429),
        _Response({"error": [], "result": {"ok": {}}}),
    ])
    transport._client = fake
    monkeypatch.setattr("app.services.kraken_transport.time.sleep", lambda value: None)
    monkeypatch.setattr("app.services.kraken_transport.random.uniform", lambda a, b: 0.0)
    result = transport.request("Ticker", {"pair": "BTCUSD"}, timeout_seconds=1)
    assert result == {"ok": {}}
    assert fake.calls == 2
    assert transport.telemetry_snapshot()["retries"] == 1


def test_kraken_client_exposes_transport_telemetry():
    transport = KrakenPublicTransport(requests_per_second=1000, burst=10, max_retries=0)
    client = KrakenClient(transport=transport)
    assert client.transport_telemetry()["configured_rps"] == 1000


def test_chief_api_failure_is_fail_closed(monkeypatch):
    candidate = _snapshot()
    monkeypatch.setattr(
        chief_analyst,
        "OpenAI",
        lambda api_key: SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("outage"))
            )
        ),
    )
    monkeypatch.setattr(chief_analyst, "get_cached_review", lambda fingerprint: None)
    monkeypatch.setattr(chief_analyst, "budget_block_reason", lambda: None)
    monkeypatch.setattr(chief_analyst, "trace_candidate_event", lambda **kwargs: {})
    monkeypatch.setattr(chief_analyst, "store_cached_review", lambda *args, **kwargs: None)
    result = chief_analyst.review_candidates([candidate], "model", "key")
    assert result["recommended_action"] == "no_trade"
    assert result["chief_failure_code"] == "CHIEF_UNAVAILABLE"
    assert result["chief_api_skipped"] is True


def test_chief_budget_limit_is_fail_closed(monkeypatch):
    candidate = _snapshot()
    monkeypatch.setattr(chief_analyst, "get_cached_review", lambda fingerprint: None)
    monkeypatch.setattr(chief_analyst, "budget_block_reason", lambda: "daily OpenAI call budget reached (10/10)")
    monkeypatch.setattr(chief_analyst, "trace_candidate_event", lambda **kwargs: {})
    result = chief_analyst.review_candidates([candidate], "model", "key")
    assert result["recommended_action"] == "no_trade"
    assert result["chief_failure_code"] == "CHIEF_BUDGET_LIMIT"


def test_chief_fingerprint_ignores_small_score_noise():
    payload_a = {
        "candidate_count": 1,
        "market_regime_context": None,
        "candidates": [{
            "symbol": "SOLUSD",
            "direction": "LONG",
            "technical_score": 81,
            "trend": "bullish",
            "rsi": 55.1,
            "volume_ratio": 1.21,
            "market_structure": {"momentum_6h_pct": 3.1, "momentum_24h_pct": 7.2},
        }],
    }
    payload_b = json.loads(json.dumps(payload_a))
    payload_b["candidates"][0]["technical_score"] = 82
    payload_b["candidates"][0]["rsi"] = 55.4
    assert chief_runtime_guard.build_chief_fingerprint(payload_a) == chief_runtime_guard.build_chief_fingerprint(payload_b)


def test_telegram_failure_log_does_not_include_token(monkeypatch, caplog):
    token = "SUPER_SECRET_TOKEN"
    request = httpx.Request("POST", f"https://api.telegram.org/bot{token}/sendMessage")
    response = httpx.Response(500, request=request)

    def fail(*args, **kwargs):
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr("app.services.telegram_notifier.httpx.post", fail)
    assert send_telegram_message(token, "123", "hello") is False
    assert token not in caplog.text
    assert "sendMessage" in caplog.text


def test_private_kraken_client_has_no_order_mutation_methods():
    from app.exchanges.kraken_private import KrakenPrivateClient

    forbidden = {"add_order", "cancel_order", "edit_order", "close_position", "withdraw"}
    names = {name.lower() for name in dir(KrakenPrivateClient)}
    assert forbidden.isdisjoint(names)
