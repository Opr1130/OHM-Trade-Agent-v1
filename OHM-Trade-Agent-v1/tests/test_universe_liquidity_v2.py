import json
from types import SimpleNamespace

import pytest

from app.scanner import market_scanner
from app.scanner.candidates import MAX_CANDIDATES, select_candidates
from app.scanner.cross_pair_confirmation import (
    DIVERGENCE,
    MIXED,
    SINGLE_MARKET,
    STRONG_CONFIRMATION,
    UNAVAILABLE,
    evaluate_cross_pair_confirmation,
)
from app.scanner.models import MarketSnapshot
from app.scanner.universe import (
    DEFAULT_UNIQUE_ASSET_LIMIT,
    UniverseAsset,
    UniverseBuildResult,
    build_kraken_asset_universe,
)
from app.services import chief_alert_notifier, chief_analyst
from app.services.entry_exit_advisor import EntryExitPlan
from app.scanner.execution_validation import unavailable_execution
from app.scanner.market_data_validation import MarketDataValidation


def pair(base, quote, *, altname=None):
    kraken_base = {"BTC": "XBT", "DOGE": "XDG"}.get(base, base)
    return {
        "altname": altname or f"{kraken_base}{quote}",
        "wsname": f"{kraken_base}/{quote}",
        "quote": "ZUSD" if quote == "USD" else quote,
    }


def ticker(last, volume):
    return {"last": last, "bid": last - 0.1, "ask": last + 0.1, "volume_24h": volume}


class FakeKraken:
    def __init__(self, pairs, tickers):
        self.pairs = pairs
        self.tickers = tickers
        self.ticker_calls = []

    def get_asset_pairs(self):
        return self.pairs

    def get_tickers(self, pair_ids):
        self.ticker_calls.append(list(pair_ids))
        return {
            pair_id: self.tickers[pair_id]
            for pair_id in pair_ids
            if pair_id in self.tickers
        }


def build(pairs, tickers, limit=200):
    client = FakeKraken(pairs, tickers)
    return build_kraken_asset_universe(client, limit), client


def snapshot(symbol="SOLUSD", **overrides):
    values = dict(
        symbol=symbol, last_price=100.0, ema20=99.0, ema50=95.0, ema200=90.0,
        rsi=58.0, macd_line=1.0, macd_signal=0.5, macd_histogram=0.5,
        atr=2.0, atr_pct=2.0, volume_ratio=1.5, technical_score=90,
        trend="bullish", momentum_6h_pct=1.0, momentum_24h_pct=2.0,
        momentum_72h_pct=3.0, underlying_asset="SOL", primary_pair="SOLUSD",
        secondary_pair="SOLUSDT", primary_quote_currency="USD",
        primary_24h_liquidity_usd=90_000_000,
        secondary_24h_liquidity_usd=170_000_000,
        combined_24h_liquidity_usd=260_000_000, liquidity_rank=1,
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def test_usd_only_asset_appears_once_and_is_primary():
    result, _ = build(
        {"XXBTZUSD": pair("BTC", "USD")},
        {"XXBTZUSD": ticker(50_000, 100)},
    )
    assert len(result.assets) == 1
    assert result.assets[0].base_asset == "BTC"
    assert result.assets[0].primary_pair == "BTCUSD"
    assert result.assets[0].secondary_pair is None


def test_usdt_only_asset_uses_conversion_and_is_primary():
    result, _ = build(
        {
            "XYZUSDT": pair("XYZ", "USDT"),
            "USDTZUSD": pair("USDT", "USD"),
        },
        {
            "XYZUSDT": ticker(2.0, 1_000),
            "USDTZUSD": ticker(0.999, 1_000_000),
        },
    )
    asset = result.assets[0]
    assert asset.base_asset == "XYZ"
    assert asset.primary_pair == "XYZUSDT"
    assert asset.usdt_24h_notional_usd_equivalent == 1_998.0


def test_dual_market_combines_liquidity_without_duplicate_asset():
    result, client = build(
        {
            "SOLUSD": pair("SOL", "USD"),
            "SOLUSDT": pair("SOL", "USDT"),
            "USDTZUSD": pair("USDT", "USD"),
        },
        {
            "SOLUSD": ticker(90.0, 1_000_000),
            "SOLUSDT": ticker(85.0, 2_000_000),
            "USDTZUSD": ticker(1.01, 1_000_000),
        },
    )
    assert len(result.assets) == 1
    sol = result.assets[0]
    assert sol.combined_24h_notional_usd == 90_000_000 + 171_700_000
    assert sol.primary_pair == "SOLUSD"
    assert sol.secondary_pair == "SOLUSDT"
    assert sol.primary_quote_currency == "USD"
    assert sum(len(call) for call in client.ticker_calls) == 3


def test_liquidity_ranking_and_usd_tie_break_are_deterministic():
    result, _ = build(
        {
            "AAAUSD": pair("AAA", "USD"),
            "BBBUSD": pair("BBB", "USD"),
            "BBBUSDT": pair("BBB", "USDT"),
            "USDTZUSD": pair("USDT", "USD"),
        },
        {
            "AAAUSD": ticker(9, 100),
            "BBBUSD": ticker(5, 100),
            "BBBUSDT": ticker(5, 100),
            "USDTZUSD": ticker(1, 1_000),
        },
    )
    assert [asset.base_asset for asset in result.assets] == ["BBB", "AAA"]
    bbb = result.assets[0]
    assert bbb.primary_pair == "BBBUSD"
    assert bbb.secondary_pair == "BBBUSDT"


def test_usd_remains_canonical_when_usdt_is_far_more_liquid():
    result, _ = build(
        {
            "SOLUSD": pair("SOL", "USD"),
            "SOLUSDT": pair("SOL", "USDT"),
            "USDTZUSD": pair("USDT", "USD"),
        },
        {
            "SOLUSD": ticker(100, 1_000),
            "SOLUSDT": ticker(100, 100_000),
            "USDTZUSD": ticker(1, 1_000),
        },
    )
    assert result.assets[0].primary_pair == "SOLUSD"
    assert result.assets[0].secondary_pair == "SOLUSDT"


def test_liquidity_reversal_across_scans_does_not_change_pair_identity():
    pairs = {
        "SOLUSD": pair("SOL", "USD"),
        "SOLUSDT": pair("SOL", "USDT"),
        "USDTZUSD": pair("USDT", "USD"),
    }
    usd_led, _ = build(pairs, {
        "SOLUSD": ticker(100, 100_000),
        "SOLUSDT": ticker(100, 1_000),
        "USDTZUSD": ticker(1, 1_000),
    })
    usdt_led, _ = build(pairs, {
        "SOLUSD": ticker(100, 1_000),
        "SOLUSDT": ticker(100, 100_000),
        "USDTZUSD": ticker(1, 1_000),
    })
    assert usd_led.assets[0].primary_pair == "SOLUSD"
    assert usdt_led.assets[0].primary_pair == "SOLUSD"
    assert usd_led.assets[0].secondary_pair == "SOLUSDT"
    assert usdt_led.assets[0].secondary_pair == "SOLUSDT"


def test_secondary_usdt_liquidity_can_improve_rank_without_becoming_primary():
    result, _ = build(
        {
            "AAAUSD": pair("AAA", "USD"),
            "BBBUSDT": pair("BBB", "USDT"),
            "BBBUSD": pair("BBB", "USD"),
            "USDTZUSD": pair("USDT", "USD"),
        },
        {
            "AAAUSD": ticker(1, 1_000),
            "BBBUSD": ticker(1, 600),
            "BBBUSDT": ticker(1, 600),
            "USDTZUSD": ticker(1, 1_000),
        },
    )
    assert [asset.base_asset for asset in result.assets] == ["BBB", "AAA"]
    assert result.assets[0].primary_pair == "BBBUSD"
    assert result.assets[0].combined_24h_notional_usd == 1_200


def test_ticker_returned_under_kraken_altname_is_resolved():
    class AltnameClient(FakeKraken):
        def get_tickers(self, pair_ids):
            self.ticker_calls.append(list(pair_ids))
            return {"XBTUSD": ticker(50_000, 100)}

    client = AltnameClient(
        {"XXBTZUSD": pair("BTC", "USD")},
        {},
    )
    result = build_kraken_asset_universe(client)
    assert result.assets[0].base_asset == "BTC"
    assert result.assets[0].primary_pair == "BTCUSD"
    assert result.assets[0].primary_kraken_symbol == "BTC/USD"
    assert result.assets[0].primary_ticker_last == 50_000


def test_doge_lifecycle_normalization_preserves_kraken_public_symbol():
    result, _ = build(
        {"XXDGZUSD": pair("DOGE", "USD")},
        {"XXDGZUSD": ticker(0.2, 1_000)},
    )
    assert result.assets[0].base_asset == "DOGE"
    assert result.assets[0].primary_pair == "DOGEUSD"
    assert result.assets[0].primary_kraken_symbol == "DOGE/USD"


def test_inverse_usdt_conversion_is_normalized():
    result, _ = build(
        {
            "XYZUSDT": pair("XYZ", "USDT"),
            "ZUSDUSDT": pair("USD", "USDT"),
        },
        {
            "XYZUSDT": ticker(10, 100),
            "ZUSDUSDT": ticker(1.002, 1_000_000),
        },
    )
    assert result.usdt_usd_rate == 1 / 1.002
    assert result.assets[0].usdt_24h_notional_usd_equivalent == 1000 / 1.002


def test_missing_conversion_keeps_usd_and_does_not_fabricate_rate():
    result, _ = build(
        {
            "AAAUSD": pair("AAA", "USD"),
            "XYZUSDT": pair("XYZ", "USDT"),
        },
        {
            "AAAUSD": ticker(10, 100),
            "XYZUSDT": ticker(100, 1_000_000),
        },
    )
    assert result.usdt_usd_rate is None
    assert [asset.base_asset for asset in result.assets] == ["AAA"]
    assert result.warnings


def test_stablecoin_bases_excluded_usdt_quote_allowed_and_leverage_excluded():
    result, _ = build(
        {
            "USDTZUSD": pair("USDT", "USD"),
            "USDCZUSD": pair("USDC", "USD"),
            "SOLUSDT": pair("SOL", "USDT"),
            "SOL3LUSD": pair("SOL3L", "USD", altname="SOL3LUSD"),
        },
        {
            "USDTZUSD": ticker(1, 1_000_000),
            "USDCZUSD": ticker(1, 1_000_000),
            "SOLUSDT": ticker(100, 1_000),
            "SOL3LUSD": ticker(10, 1_000),
        },
    )
    assert [asset.base_asset for asset in result.assets] == ["SOL"]
    assert result.assets[0].usdt_pair == "SOLUSDT"


def test_limit_applies_after_pair_deduplication_to_unique_assets():
    pairs = {"USDTZUSD": pair("USDT", "USD")}
    tickers = {"USDTZUSD": ticker(1, 1_000_000)}
    for index in range(205):
        base = f"A{index:03d}"
        pairs[f"{base}USD"] = pair(base, "USD")
        pairs[f"{base}USDT"] = pair(base, "USDT")
        tickers[f"{base}USD"] = ticker(1, 1_000 - index)
        tickers[f"{base}USDT"] = ticker(1, 1_000 - index)
    result, _ = build(pairs, tickers, DEFAULT_UNIQUE_ASSET_LIMIT)
    assert result.unique_underlying_assets == 205
    assert result.selected_liquid_assets == 200
    assert len({asset.base_asset for asset in result.assets}) == 200
    assert result.ticker_batch_requests == 5


def test_cross_pair_states():
    primary = snapshot()
    strong_secondary = snapshot("SOLUSDT", volume_ratio=2.0)
    assert evaluate_cross_pair_confirmation(
        primary, strong_secondary
    ).status == STRONG_CONFIRMATION

    mixed_secondary = snapshot(
        "SOLUSDT", momentum_6h_pct=-1.0, momentum_24h_pct=2.0,
        trend="neutral", volume_ratio=0.9,
    )
    assert evaluate_cross_pair_confirmation(primary, mixed_secondary).status == MIXED

    divergent = snapshot(
        "SOLUSDT", momentum_6h_pct=-1.0, momentum_24h_pct=-2.0,
        trend="bearish", volume_ratio=0.7,
    )
    assert evaluate_cross_pair_confirmation(primary, divergent).status == DIVERGENCE

    primary.secondary_pair = None
    assert evaluate_cross_pair_confirmation(primary, None).status == SINGLE_MARKET


def test_cross_pair_price_normalization_and_divergence_context(monkeypatch):
    primary = snapshot(last_price=150)
    secondary = snapshot("SOLUSDT", last_price=150.1)
    monkeypatch.setattr(
        market_scanner, "analyze_symbol", lambda symbol: ("ok", secondary, None)
    )
    market_scanner.confirm_secondary_markets([primary], usdt_usd_rate=0.999)
    assert primary.cross_pair_price_status == "NORMAL"
    assert primary.cross_pair_price_divergence_pct == pytest.approx(
        abs(150.1 * 0.999 - 150) / 150 * 100
    )


def test_cross_pair_price_warning_material_and_missing_conversion(monkeypatch):
    primary = snapshot(last_price=100)
    secondary = snapshot("SOLUSDT", last_price=101)
    monkeypatch.setattr(
        market_scanner, "analyze_symbol", lambda symbol: ("ok", secondary, None)
    )
    market_scanner.confirm_secondary_markets([primary], usdt_usd_rate=1)
    assert primary.cross_pair_price_status == "WARNING"
    secondary.last_price = 104
    market_scanner.confirm_secondary_markets([primary], usdt_usd_rate=1)
    assert primary.cross_pair_price_status == "MATERIAL_DIVERGENCE"
    missing = snapshot(last_price=100)
    market_scanner.confirm_secondary_markets([missing], usdt_usd_rate=None)
    assert missing.cross_pair_price_divergence_pct is None
    assert missing.cross_pair_price_status == "UNAVAILABLE"


def test_secondary_ohlc_only_for_max_eight_finalists_and_failure_is_safe(monkeypatch):
    snapshots = [
        snapshot(
            symbol=f"A{index}USD", underlying_asset=f"A{index}",
            primary_pair=f"A{index}USD", secondary_pair=f"A{index}USDT",
            technical_score=100 - index,
        )
        for index in range(12)
    ]
    finalists = select_candidates(snapshots, min_score=0)
    calls = []

    def fake_analyze(symbol, universe_asset=None):
        calls.append(symbol)
        if symbol == "A0USDT":
            return "fail", None, "failure"
        return "ok", snapshot(symbol), None

    monkeypatch.setattr(market_scanner, "analyze_symbol", fake_analyze)
    summary = market_scanner.confirm_secondary_markets(finalists)
    assert len(finalists) == MAX_CANDIDATES == 8
    assert len(calls) == 8
    assert summary.failed == 1
    assert finalists[0].cross_pair_confirmation_status == UNAVAILABLE


def test_scan_analyzes_only_one_primary_pair_per_unique_asset(monkeypatch):
    assets = [
        UniverseAsset(
            base_asset="SOL", usd_pair="SOLUSD", usdt_pair="SOLUSDT",
            primary_pair="SOLUSD", secondary_pair="SOLUSDT",
            primary_quote_currency="USD",
            usdt_24h_notional_usd_equivalent=200,
            usd_24h_notional_usd=100,
            combined_24h_notional_usd=300, liquidity_rank=1,
        ),
        UniverseAsset(
            base_asset="BTC", usd_pair="BTCUSD", primary_pair="BTCUSD",
            primary_quote_currency="USD", usd_24h_notional_usd=250,
            combined_24h_notional_usd=250, liquidity_rank=2,
        ),
    ]
    universe = UniverseBuildResult(
        assets=assets, eligible_usd_markets=2, eligible_usdt_markets=1,
        unique_underlying_assets=2, selected_liquid_assets=2,
        usdt_usd_rate=1.0, warnings=[], ticker_batch_requests=1,
    )
    calls = []

    monkeypatch.setattr(market_scanner, "KrakenClient", lambda: object())
    monkeypatch.setattr(
        market_scanner, "build_kraken_asset_universe",
        lambda client, limit: universe,
    )

    def fake_analyze(symbol, universe_asset=None):
        calls.append(symbol)
        item = snapshot(
            symbol, underlying_asset=universe_asset.base_asset,
            primary_pair=symbol, secondary_pair=universe_asset.secondary_pair,
        )
        return "ok", item, None

    monkeypatch.setattr(market_scanner, "analyze_symbol", fake_analyze)
    result = market_scanner.scan_market(limit=200)
    assert sorted(calls) == ["BTCUSD", "SOLUSD"]
    assert result.requested == result.analyzed == 2
    assert len({item.underlying_asset for item in result.snapshots}) == 2


def test_one_underlying_asset_is_one_chief_payload_and_one_api_call(monkeypatch):
    first = snapshot("SOLUSD")
    first.market_data_validation = MarketDataValidation(
        status="PASS", qualified=True, warnings=[], rejection_reasons=[],
        candle_count=200, latest_candle_timestamp=1,
        latest_candle_age_seconds=10, duplicate_timestamp_count=0,
        gap_count=0, largest_gap_seconds=0, invalid_ohlc_count=0,
        non_finite_value_count=0, ticker_last=100,
        latest_ohlc_close=100, ticker_vs_ohlc_difference_pct=0,
        suspicious_spike_detected=False,
    )
    first.execution_validation = unavailable_execution("test context")
    duplicate = snapshot("SOLUSDT", primary_pair="SOLUSDT")

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
    chief_analyst.review_candidates([first, duplicate], "model", "key", 2_000)
    assert responses.calls == 1
    assert responses.payload["candidate_count"] == 1
    context = responses.payload["candidates"][0]["liquidity_context"]
    assert context["underlying_asset"] == "SOL"
    assert context["primary_pair"] == "SOLUSD"
    assert context["secondary_pair"] == "SOLUSDT"
    assert context["primary_quote_currency"] == "USD"
    assert context["combined_24h_liquidity_usd"] == 260_000_000
    assert responses.payload["candidates"][0]["market_data_quality"]["status"] == "PASS"
    execution = responses.payload["candidates"][0]["execution_evidence"]
    assert execution["status"] == "UNAVAILABLE"
    assert execution["book_coverage_status"] == "UNAVAILABLE"
    assert "execution_quality" not in responses.payload["candidates"][0]


def _actionable_plan(symbol):
    return EntryExitPlan(
        symbol=symbol, valid_now=True, entry_style="pullback_or_retest",
        entry_low=99, entry_high=100, chase_limit=101, stop_price=96,
        target_1=106, target_2=110, reward_to_risk_1=2,
        reward_to_risk_2=3, risk_level="low", reason="Test plan",
    )


def test_telegram_dual_market_uses_stable_usd_market_identity():
    message = chief_alert_notifier.format_trade_plan(
        {
            "underlying_asset": "SOL", "primary_pair": "SOLUSD",
            "secondary_pair": "SOLUSDT", "primary_quote_currency": "USD",
            "confidence": 90, "decision": "alert",
        },
        _actionable_plan("SOLUSD"),
        "Summary",
    )
    assert "Market: SOLUSD" in message
    assert "Market: SOLUSDT" not in message
    assert "no automatic USD conversion" not in message


def test_telegram_usdt_only_market_is_explicitly_quoted_without_conversion():
    message = chief_alert_notifier.format_trade_plan(
        {
            "underlying_asset": "XYZ", "primary_pair": "XYZUSDT",
            "secondary_pair": None, "primary_quote_currency": "USDT",
            "confidence": 90, "decision": "alert",
        },
        _actionable_plan("XYZUSDT"),
        "Summary",
    )
    assert "Market: XYZUSDT" in message
    assert "USDT availability required" in message
    assert "no automatic USD conversion" in message
