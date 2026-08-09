from datetime import datetime, timezone
import json
from types import SimpleNamespace

from app.jobs import scan_opportunities
from app.scanner.execution_validation import unavailable_execution
from app.scanner.global_market_context import CoinGeckoGlobalContext
from app.scanner.market_regime import MarketRegimeContext
from app.scanner.market_scanner import ScanResult, SecondaryConfirmationSummary
from app.scanner.models import MarketSnapshot
from app.scanner.news_context import NewsContext, NewsValidationSummary
from app.scanner.reference_market_validation import ReferenceMarketValidation
from app.scanner.scheduled_catalysts import (
    ScheduledCatalystContext,
    ScheduledCatalystSummary,
)
from app.services import chief_analyst


def snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="SOLUSD",
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
        underlying_asset="SOL",
        primary_pair="SOLUSD",
        primary_quote_currency="USD",
        ticker_last=100,
    )


def regime() -> MarketRegimeContext:
    return MarketRegimeContext(
        status="AVAILABLE",
        sample_size=200,
        regime="NEUTRAL",
        breadth_score=50,
        pct_above_ema20=50,
        pct_above_ema50=50,
        pct_above_ema200=50,
        pct_positive_momentum_24h=50,
        pct_positive_momentum_72h=50,
        pct_bullish_trend=50,
        median_momentum_24h_pct=0,
        median_momentum_72h_pct=0,
    )


def global_context() -> CoinGeckoGlobalContext:
    return CoinGeckoGlobalContext(
        status="AVAILABLE",
        api_mode="KEYLESS",
        btc_market_cap_percentage=58,
        market_cap_change_24h_pct=1.2,
    )


def test_chief_receives_all_context_in_exactly_one_responses_call(monkeypatch):
    candidate = snapshot()
    candidate.news_context = NewsContext(
        status="AVAILABLE",
        matched_post_count=0,
        recent_post_count_6h=0,
        recent_post_count_24h=0,
        latest_post_age_seconds=None,
    )
    candidate.scheduled_catalyst_context = ScheduledCatalystContext(
        status="NO_UPCOMING_EVENTS",
        mapping_status="RESOLVED",
        coinmarketcal_slug="solana",
        event_count_next_7d=0,
        nearest_event=None,
    )
    class Responses:
        def __init__(self):
            self.calls = 0
            self.payload = None
        def create(self, **kwargs):
            self.calls += 1
            self.payload = json.loads(kwargs["input"][1]["content"])
            return SimpleNamespace(output_text=json.dumps({
                "market_view": "",
                "recommended_action": "no_trade",
                "top_candidates": [],
                "summary": "",
            }))
    responses = Responses()
    monkeypatch.setattr(
        chief_analyst,
        "OpenAI",
        lambda api_key: SimpleNamespace(responses=responses),
    )
    chief_analyst.review_candidates(
        [candidate], "model", "key",
        market_regime_context=regime(),
        coingecko_global_context=global_context(),
    )
    assert responses.calls == 1
    assert responses.payload["market_regime_context"]["ohm_breadth"]["sample_size"] == 200
    assert responses.payload["market_regime_context"]["coingecko_global"]["btc_market_cap_percentage"] == 58
    chief_candidate = responses.payload["candidates"][0]
    assert chief_candidate["news_context"]["status"] == "AVAILABLE"
    assert chief_candidate["scheduled_catalyst_context"]["mapping_status"] == "RESOLVED"


def test_chief_receives_unresolved_news_without_fabricated_items(monkeypatch):
    candidate = snapshot()
    candidate.news_context = NewsContext(
        status="UNRESOLVED",
        matched_post_count=0,
        recent_post_count_6h=0,
        recent_post_count_24h=0,
        latest_post_age_seconds=None,
        warnings=("ticker-only attribution refused",),
    )
    class Responses:
        def __init__(self):
            self.calls = 0
            self.payload = None
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
    news = responses.payload["candidates"][0]["news_context"]
    assert responses.calls == 1
    assert news["status"] == "UNRESOLVED"
    assert news["matched_post_count"] == 0
    assert news["recent_items"] == []
    assert "ticker-only attribution" in chief_analyst.SYSTEM_PROMPT


def test_pipeline_evidence_order_precedes_single_chief_call(monkeypatch, tmp_path):
    candidate = snapshot()
    calls = []
    settings = SimpleNamespace(
        openai_model="model",
        openai_api_key="key",
        account_equity=2_000,
        coingecko_api_key=None,
        cryptopanic_auth_token=None,
        cryptopanic_api_plan="developer",
        coinmarketcal_api_key=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    monkeypatch.setattr(scan_opportunities, "get_settings", lambda: settings)
    monkeypatch.setattr(
        scan_opportunities,
        "scan_market",
        lambda limit: ScanResult([candidate], 1, 1, 0, 0, [], []),
    )
    monkeypatch.setattr(
        scan_opportunities,
        "evaluate_market_regime",
        lambda snapshots: calls.append("regime") or regime(),
    )
    monkeypatch.setattr(scan_opportunities, "select_candidates", lambda items: items)
    monkeypatch.setattr(
        scan_opportunities,
        "confirm_secondary_markets",
        lambda *args: SecondaryConfirmationSummary(0, 0, 0),
    )
    def execution(candidates, *args):
        candidates[0].execution_validation = unavailable_execution("test")
        return candidates
    monkeypatch.setattr(scan_opportunities, "deep_validate_candidates", execution)

    def references(candidates, *args, **kwargs):
        calls.append("reference")
        candidates[0].independent_market_reference = ReferenceMarketValidation(
            status="UNAVAILABLE", available=False, mapping_status="UNAVAILABLE",
            api_mode="KEYLESS",
        )
        return SimpleNamespace(
            requested=1, available=0, unavailable=1, ambiguous=0,
            api_mode="KEYLESS",
        )
    monkeypatch.setattr(scan_opportunities, "validate_finalist_references", references)
    monkeypatch.setattr(
        scan_opportunities,
        "load_coingecko_global_context",
        lambda **kwargs: calls.append("global") or global_context(),
    )
    def news(candidates, **kwargs):
        calls.append("news")
        candidates[0].news_context = NewsContext(
            "UNAVAILABLE", 0, 0, 0, None
        )
        return NewsValidationSummary(1, 0, 1, 0)
    monkeypatch.setattr(scan_opportunities, "validate_finalist_news", news)
    def catalysts(candidates, **kwargs):
        calls.append("catalysts")
        candidates[0].scheduled_catalyst_context = ScheduledCatalystContext(
            "UNRESOLVED", "UNRESOLVED", None, 0, None
        )
        return ScheduledCatalystSummary(1, 0, 1, 0, 0, 0)
    monkeypatch.setattr(scan_opportunities, "validate_scheduled_catalysts", catalysts)
    def chief(*args, **kwargs):
        calls.append("chief")
        assert kwargs["market_regime_context"].sample_size == 200
        assert kwargs["coingecko_global_context"].status == "AVAILABLE"
        return {"top_candidates": [], "summary": ""}
    monkeypatch.setattr(scan_opportunities, "review_candidates", chief)
    monkeypatch.setattr(scan_opportunities, "qualified_alerts", lambda review: [])

    scan_opportunities.main()
    assert calls == ["regime", "reference", "global", "news", "catalysts", "chief"]
    assert calls.count("chief") == 1


def test_no_private_kraken_execution_or_leverage_was_added():
    paths = [
        "app/scanner/market_regime.py",
        "app/scanner/global_market_context.py",
        "app/scanner/news_context.py",
        "app/scanner/scheduled_catalysts.py",
        "app/services/cryptopanic.py",
        "app/services/coinmarketcal.py",
    ]
    source = "\n".join(__import__("pathlib").Path(path).read_text() for path in paths)
    assert "/private/" not in source
    assert "AddOrder" not in source
    assert "leverage" not in source.lower()
    assert "margin" not in source.lower()
