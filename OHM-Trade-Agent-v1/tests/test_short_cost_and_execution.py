import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.exchanges.kraken import BookLevel, PreTradeBook, PublicTrade
from app.scanner.execution_validation import evaluate_execution
from app.scanner.models import MarketSnapshot
from app.scanner.short_execution_quality import short_execution_is_tradeable
from app.services import chief_analyst


def _snapshot(symbol: str, direction: str, *, margin_eligible: bool = False) -> MarketSnapshot:
    bearish = direction == "SHORT"
    return MarketSnapshot(
        symbol=symbol,
        last_price=100.0,
        ema20=102.0 if bearish else 99.0,
        ema50=104.0 if bearish else 98.0,
        ema200=106.0 if bearish else 97.0,
        rsi=45.0 if bearish else 55.0,
        macd_line=-1.0 if bearish else 1.0,
        macd_signal=0.0 if bearish else 0.5,
        macd_histogram=-1.0 if bearish else 0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=90,
        trend="bearish" if bearish else "bullish",
        trade_direction=direction,
        underlying_asset=symbol.removesuffix("USD"),
        primary_pair=symbol,
        primary_quote_currency="USD",
        ticker_last=100.0,
        margin_eligible=margin_eligible,
        margin_validation_status="ELIGIBLE" if margin_eligible else "NOT_REQUIRED",
        margin_venue_symbol=f"{symbol}:BTNL" if margin_eligible else None,
        margin_max_leverage=3.0 if margin_eligible else None,
    )


class _Responses:
    def __init__(self):
        self.calls = 0
        self.kwargs = None

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return SimpleNamespace(
            output_text=json.dumps({
                "market_view": "test",
                "recommended_action": "no_trade",
                "top_candidates": [],
                "summary": "test",
            }),
            usage=None,
        )


def test_mixed_long_short_share_one_chief_call_and_payload(monkeypatch):
    long = _snapshot("SOLUSD", "LONG")
    short = _snapshot("BTCUSD", "SHORT", margin_eligible=True)
    responses = _Responses()
    monkeypatch.setattr(
        chief_analyst,
        "_quality_by_risk_level",
        lambda candidate, equity: ({"low": {"ok": True}}, True),
    )
    monkeypatch.setattr(
        chief_analyst,
        "OpenAI",
        lambda api_key: SimpleNamespace(responses=responses),
    )

    review = chief_analyst.review_candidates([long, short], "model", "key", 2000)
    payload = json.loads(responses.kwargs["input"][1]["content"])

    assert responses.calls == 1
    assert review["chief_eligible_candidates"] == 2
    assert payload["candidate_count"] == 2
    assert {(row["symbol"], row["direction"]) for row in payload["candidates"]} == {
        ("SOLUSD", "LONG"),
        ("BTCUSD", "SHORT"),
    }


def test_ineligible_short_never_reaches_chief(monkeypatch):
    long = _snapshot("SOLUSD", "LONG")
    unsafe_short = _snapshot("BTCUSD", "SHORT", margin_eligible=False)
    responses = _Responses()
    monkeypatch.setattr(
        chief_analyst,
        "_quality_by_risk_level",
        lambda candidate, equity: ({"low": {"ok": True}}, True),
    )
    monkeypatch.setattr(
        chief_analyst,
        "OpenAI",
        lambda api_key: SimpleNamespace(responses=responses),
    )

    chief_analyst.review_candidates([unsafe_short, long], "model", "key", 2000)
    payload = json.loads(responses.kwargs["input"][1]["content"])

    assert responses.calls == 1
    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["symbol"] == "SOLUSD"


def test_chief_payload_is_still_hard_capped_at_eight(monkeypatch):
    candidates = [_snapshot(f"C{i}USD", "LONG") for i in range(12)]
    responses = _Responses()
    monkeypatch.setattr(
        chief_analyst,
        "_quality_by_risk_level",
        lambda candidate, equity: ({"low": {"ok": True}}, True),
    )
    monkeypatch.setattr(
        chief_analyst,
        "OpenAI",
        lambda api_key: SimpleNamespace(responses=responses),
    )

    chief_analyst.review_candidates(candidates, "model", "key", 2000)
    payload = json.loads(responses.kwargs["input"][1]["content"])
    assert responses.calls == 1
    assert payload["candidate_count"] == 8


def test_exact_short_sell_then_buyback_drag_is_measured_and_used():
    bids = [BookLevel(price=99.9 - i * 0.01, quantity=100.0) for i in range(10)]
    asks = [BookLevel(price=100.1 + i * 0.01, quantity=100.0) for i in range(10)]
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    trades = [
        PublicTrade(
            price=100.0,
            quantity=1.0,
            trade_timestamp="2026-08-09T23:59:30+00:00",
        )
    ]
    execution = evaluate_execution(
        PreTradeBook(symbol="BTC/USD", bids=bids, asks=asks),
        validation_notional_usd=2000.0,
        ticker_last=100.0,
        trades=trades,
        now=now,
    )
    candidate = _snapshot("BTCUSD", "SHORT", margin_eligible=True)
    candidate.execution_validation = execution

    assert execution.estimated_visible_short_round_trip_market_drag_pct is not None
    assert execution.estimated_visible_short_round_trip_market_drag_pct > 0
    ok, reasons = short_execution_is_tradeable(candidate)
    assert ok, reasons
