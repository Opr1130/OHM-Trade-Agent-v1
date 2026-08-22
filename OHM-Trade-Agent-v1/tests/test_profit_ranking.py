from types import SimpleNamespace

import pytest

from app.jobs import scan_opportunities
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.market_scanner import ScanResult, SecondaryConfirmationSummary
from app.scanner.models import MarketSnapshot
from app.scanner.reference_market_validation import ReferenceMarketValidation
from app.services import chief_alert_notifier
from app.services.economic_quality_gate import EconomicGateResult
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.profit_ranking import (
    QualifiedOpportunity,
    evaluate_profit_ranking,
    rank_profit_opportunities,
)
from app.services.target_attainability import TargetAttainabilityResult


def _snapshot(
    symbol: str = "AAAUSD",
    *,
    technical_score: int = 90,
    drag: float | None = 0.25,
    book_coverage_status: str = "COMPLETE",
    recent_trade_status: str = "FRESH",
    reference_status: str = "CONFIRMED",
    mapping_status: str = "UNIQUE",
    cross_pair_status: str = "CONFIRMED",
) -> MarketSnapshot:
    result = MarketSnapshot(
        symbol=symbol,
        last_price=100.0,
        ema20=99.0,
        ema50=95.0,
        ema200=90.0,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=technical_score,
        trend="bullish",
        recent_24h_high=120.0,
        recent_72h_high=130.0,
        rolling_24h_upside_median_pct=6.0,
        rolling_24h_upside_p75_pct=8.0,
        rolling_24h_upside_p90_pct=10.0,
        rolling_72h_upside_median_pct=10.0,
        rolling_72h_upside_p75_pct=14.0,
        rolling_72h_upside_p90_pct=18.0,
        underlying_asset=symbol.removesuffix("USD"),
        primary_pair=symbol,
        primary_quote_currency="USD",
        combined_24h_liquidity_usd=1_000_000.0,
        cross_pair_confirmation_status=cross_pair_status,
        ticker_last=100.0,
    )
    result.execution_validation = ExecutionValidation(
        status="VALID",
        book_coverage_status=book_coverage_status,
        warnings=[],
        validation_notional_usd=2_000.0,
        estimated_visible_round_trip_market_drag_pct=drag,
        recent_trade_status=recent_trade_status,
    )
    result.independent_market_reference = ReferenceMarketValidation(
        status=reference_status,
        available=mapping_status == "UNIQUE",
        mapping_status=mapping_status,
        api_mode="KEYLESS",
        coingecko_id=("asset" if mapping_status == "UNIQUE" else None),
        coingecko_name=("Asset" if mapping_status == "UNIQUE" else None),
        matched_candidate_count=1,
        reference_price_usd=100.0,
        kraken_normalized_price_usd=100.0,
        price_divergence_pct=0.0,
        age_seconds=20.0,
        market_cap_rank=10,
    )
    return result


def _target(
    symbol: str = "AAAUSD",
    *,
    score: int = 80,
    qualified: bool = True,
) -> TargetAttainabilityResult:
    return TargetAttainabilityResult(
        symbol=symbol,
        qualified=qualified,
        target_1_move_pct=5.0,
        target_2_move_pct=7.0,
        target_1_atr_multiple=3.0,
        target_2_atr_multiple=4.5,
        clearance_to_24h_resistance_pct=5.0,
        clearance_to_72h_resistance_pct=8.0,
        momentum_context="aligned_positive",
        volatility_context="normal",
        attainability_score=score,
        strengths=["supported"],
        warnings=[],
        rejection_reasons=[] if qualified else ["target rejected"],
    )


def _economic(
    *,
    target_2_move_pct: float = 7.0,
    qualified: bool = True,
) -> EconomicGateResult:
    return EconomicGateResult(
        qualified=qualified,
        entry_reference=100.0,
        stop_pct=3.0,
        target_1_move_pct=5.0,
        target_2_move_pct=target_2_move_pct,
        recommended_capital=2_000.0,
        target_1_gross_profit=100.0,
        target_2_gross_profit=140.0,
        estimated_costs=12.0,
        target_1_net_profit=88.0,
        target_2_net_profit=128.0,
        rejection_reason=None if qualified else "economic rejected",
    )


def _plan(symbol: str = "AAAUSD", *, valid_now: bool = True) -> EntryExitPlan:
    return EntryExitPlan(
        symbol=symbol,
        valid_now=valid_now,
        entry_style="pullback_or_retest" if valid_now else "wait_for_pullback",
        entry_low=99.0,
        entry_high=101.0,
        chase_limit=102.0,
        stop_price=97.0,
        target_1=105.0,
        target_2=107.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="test plan",
    )


def _opportunity(
    symbol: str,
    *,
    confidence: int = 80,
    target_score: int = 80,
    technical_score: int = 90,
    drag: float | None = 0.25,
    target_2_move_pct: float = 7.0,
) -> QualifiedOpportunity:
    return QualifiedOpportunity(
        alert={
            "symbol": symbol,
            "risk_level": "low",
            "confidence": confidence,
            "decision": "alert",
        },
        snapshot=_snapshot(
            symbol,
            technical_score=technical_score,
            drag=drag,
        ),
        plan=_plan(symbol),
        target_quality=_target(symbol, score=target_score),
        economic_quality=_economic(target_2_move_pct=target_2_move_pct),
    )


def test_total_and_component_score_arithmetic():
    result = evaluate_profit_ranking(_snapshot(), _target(), _economic())
    assert result.economic_opportunity_score == 24.50
    assert result.target_quality_score == 20.00
    assert result.execution_quality_score == 17.00
    assert result.technical_quality_score == 9.00
    assert result.evidence_quality_score == 10.00
    assert result.total_score == 80.50


def test_economic_component_caps_at_35_for_ten_percent_or_more():
    ten = evaluate_profit_ranking(_snapshot(), _target(), _economic(target_2_move_pct=10))
    above = evaluate_profit_ranking(_snapshot(), _target(), _economic(target_2_move_pct=40))
    assert ten.economic_opportunity_score == 35.0
    assert above.economic_opportunity_score == 35.0


@pytest.mark.parametrize(("attainability", "expected"), [(0, 0.0), (80, 20.0), (100, 25.0)])
def test_target_component_conversion(attainability, expected):
    result = evaluate_profit_ranking(
        _snapshot(), _target(score=attainability), _economic()
    )
    assert result.target_quality_score == expected


@pytest.mark.parametrize(
    ("drag", "expected"),
    [(0.0, 12.0), (0.25, 9.0), (0.50, 6.0), (1.0, 0.0), (4.0, 0.0), (None, 3.0)],
)
def test_execution_drag_points(drag, expected):
    snapshot = _snapshot(
        drag=drag,
        book_coverage_status="OTHER",
        recent_trade_status="OTHER",
    )
    result = evaluate_profit_ranking(snapshot, _target(), _economic())
    assert result.execution_quality_score == expected


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [("COMPLETE", 5.0), ("PARTIAL", 3.0), ("INSUFFICIENT", 1.0), ("OTHER", 0.0)],
)
def test_book_coverage_points(coverage, expected):
    snapshot = _snapshot(
        drag=1.0,
        book_coverage_status=coverage,
        recent_trade_status="OTHER",
    )
    result = evaluate_profit_ranking(snapshot, _target(), _economic())
    assert result.execution_quality_score == expected


@pytest.mark.parametrize(
    ("trade_status", "expected"),
    [("FRESH", 3.0), ("WARN", 1.5), ("UNAVAILABLE", 0.0), ("OTHER", 0.0)],
)
def test_recent_trade_evidence_points(trade_status, expected):
    snapshot = _snapshot(
        drag=1.0,
        book_coverage_status="OTHER",
        recent_trade_status=trade_status,
    )
    result = evaluate_profit_ranking(snapshot, _target(), _economic())
    assert result.execution_quality_score == expected


@pytest.mark.parametrize(("technical", "expected"), [(0, 0.0), (87, 8.7), (100, 10.0), (150, 10.0)])
def test_technical_score_conversion(technical, expected):
    result = evaluate_profit_ranking(
        _snapshot(technical_score=technical), _target(), _economic()
    )
    assert result.technical_quality_score == expected


@pytest.mark.parametrize(
    ("status", "mapping", "expected"),
    [
        ("CONFIRMED", "UNIQUE", 6.0),
        ("WARN", "UNIQUE", 4.0),
        ("STALE", "UNIQUE", 2.0),
        ("MATERIAL_DIVERGENCE", "UNIQUE", 1.0),
        ("CONFIRMED", "AMBIGUOUS", 0.0),
        ("CONFIRMED", "UNAVAILABLE", 0.0),
    ],
)
def test_reference_evidence_scoring(status, mapping, expected):
    snapshot = _snapshot(
        reference_status=status,
        mapping_status=mapping,
        cross_pair_status="OTHER",
    )
    result = evaluate_profit_ranking(snapshot, _target(), _economic())
    assert result.evidence_quality_score == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [("CONFIRMED", 4.0), ("MIXED", 2.0), ("SINGLE_MARKET", 1.0), ("OTHER", 0.0)],
)
def test_cross_pair_evidence_scoring(status, expected):
    snapshot = _snapshot(mapping_status="AMBIGUOUS", cross_pair_status=status)
    result = evaluate_profit_ranking(snapshot, _target(), _economic())
    assert result.evidence_quality_score == expected


def test_total_score_is_clamped_to_100():
    snapshot = _snapshot(technical_score=500, drag=0.0)
    result = evaluate_profit_ranking(
        snapshot,
        _target(score=500),
        _economic(target_2_move_pct=500.0),
    )
    assert result.total_score == 100.0


def test_deterministic_tie_order_uses_symbol_ascending_last():
    ranked = rank_profit_opportunities([
        _opportunity("ZZZUSD"),
        _opportunity("AAAUSD"),
    ])
    assert [item.profit_ranking.symbol for item in ranked] == ["AAAUSD", "ZZZUSD"]


def test_exact_ties_apply_target_drag_and_technical_breakers_in_order():
    target_tie = rank_profit_opportunities([
        _opportunity(
            "ZHIGHUSD",
            target_score=81,
            target_2_move_pct=6.928571428571429,
        ),
        _opportunity("ALOWUSD", target_score=80, target_2_move_pct=7.0),
    ])
    drag_tie = rank_profit_opportunities([
        _opportunity("ZLOWDRAGUSD", drag=0.25, target_2_move_pct=6.142857142857143),
        _opportunity("AHIGHDRAGUSD", drag=0.50, target_2_move_pct=7.0),
    ])
    technical_tie = rank_profit_opportunities([
        _opportunity(
            "ZHIGHTECHUSD",
            technical_score=91,
            target_2_move_pct=6.9714285714285715,
        ),
        _opportunity("ALOWTECHUSD", technical_score=90, target_2_move_pct=7.0),
    ])
    assert target_tie[0].profit_ranking.symbol == "ZHIGHUSD"
    assert drag_tie[0].profit_ranking.symbol == "ZLOWDRAGUSD"
    assert technical_tie[0].profit_ranking.symbol == "ZHIGHTECHUSD"


def test_missing_drag_sorts_after_known_drag_on_otherwise_tied_score():
    ranked = rank_profit_opportunities([
        _opportunity("MISSINGUSD", drag=None),
        _opportunity("KNOWNUSD", drag=0.75),
    ])
    assert [item.profit_ranking.symbol for item in ranked] == ["KNOWNUSD", "MISSINGUSD"]


def test_ai_confidence_does_not_alter_rank():
    ranked = rank_profit_opportunities([
        _opportunity("ZZZUSD", confidence=99),
        _opportunity("AAAUSD", confidence=10),
    ])
    assert ranked[0].profit_ranking.symbol == "AAAUSD"


def test_news_unavailable_does_not_change_score():
    snapshot = _snapshot()
    before = evaluate_profit_ranking(snapshot, _target(), _economic())
    snapshot.news_context = SimpleNamespace(status="UNAVAILABLE")
    after = evaluate_profit_ranking(snapshot, _target(), _economic())
    assert before.total_score == after.total_score


def test_catalyst_unresolved_does_not_change_score():
    snapshot = _snapshot()
    before = evaluate_profit_ranking(snapshot, _target(), _economic())
    snapshot.scheduled_catalyst_context = SimpleNamespace(status="UNRESOLVED")
    after = evaluate_profit_ranking(snapshot, _target(), _economic())
    assert before.total_score == after.total_score


def test_market_regime_does_not_change_candidate_score():
    snapshot = _snapshot()
    before = evaluate_profit_ranking(snapshot, _target(), _economic())
    snapshot.market_regime = "RISK_OFF"
    after = evaluate_profit_ranking(snapshot, _target(), _economic())
    assert before.total_score == after.total_score


def _configure_pipeline(monkeypatch, specifications):
    snapshots = [_snapshot(item["symbol"]) for item in specifications]
    events: list[str] = []
    ranked_symbols: list[str] = []
    sent: list[dict] = []
    settings = SimpleNamespace(
        openai_model="model",
        openai_api_key="key",
        account_equity=2_000.0,
        coingecko_api_key=None,
        cryptopanic_auth_token=None,
        cryptopanic_api_plan="developer",
        coinmarketcal_api_key=None,
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )
    monkeypatch.setattr(scan_opportunities, "get_settings", lambda: settings)
    monkeypatch.setattr(
        scan_opportunities,
        "scan_market",
        lambda limit: ScanResult(snapshots, len(snapshots), len(snapshots), 0, 0, [], []),
    )
    monkeypatch.setattr(
        scan_opportunities,
        "evaluate_market_regime",
        lambda items: SimpleNamespace(
            sample_size=len(items),
            regime="NEUTRAL",
            breadth_score=50.0,
            pct_above_ema20=50.0,
            pct_above_ema50=50.0,
            pct_above_ema200=50.0,
            pct_positive_momentum_24h=50.0,
            pct_positive_momentum_72h=50.0,
            pct_bullish_trend=50.0,
        ),
    )
    monkeypatch.setattr(scan_opportunities, "select_candidates", lambda items: items)
    monkeypatch.setattr(
        scan_opportunities,
        "confirm_secondary_markets",
        lambda *args: SecondaryConfirmationSummary(0, 0, 0),
    )
    monkeypatch.setattr(
        scan_opportunities,
        "deep_validate_candidates",
        lambda candidates, *args: candidates,
    )
    monkeypatch.setattr(
        scan_opportunities,
        "validate_finalist_references",
        lambda candidates, *args, **kwargs: SimpleNamespace(
            requested=len(candidates),
            available=len(candidates),
            unavailable=0,
            ambiguous=0,
            api_mode="KEYLESS",
        ),
    )
    monkeypatch.setattr(
        scan_opportunities,
        "load_coingecko_global_context",
        lambda **kwargs: SimpleNamespace(
            status="UNAVAILABLE",
            market_cap_change_24h_pct=None,
            btc_market_cap_percentage=None,
        ),
    )

    def attach_news(candidates, **kwargs):
        for candidate in candidates:
            candidate.news_context = SimpleNamespace(
                status="UNAVAILABLE",
                recent_post_count_24h=0,
                recent_post_count_6h=0,
                latest_post_age_seconds=None,
            )
        return SimpleNamespace(
            requested=len(candidates), available=0, unavailable=len(candidates), unresolved=0
        )

    def attach_catalysts(candidates, **kwargs):
        for candidate in candidates:
            candidate.scheduled_catalyst_context = SimpleNamespace(
                status="UNRESOLVED",
                mapping_status="UNRESOLVED",
                coinmarketcal_slug=None,
                event_count_next_7d=0,
                nearest_event=None,
            )
        return SimpleNamespace(
            requested=len(candidates), available=0, unresolved=len(candidates), unavailable=0
        )

    monkeypatch.setattr(scan_opportunities, "validate_finalist_news", attach_news)
    monkeypatch.setattr(
        scan_opportunities, "validate_scheduled_catalysts", attach_catalysts
    )
    alerts = [
        {
            "symbol": item["symbol"],
            "risk_level": "low",
            "confidence": item.get("confidence", 80),
            "decision": "alert",
        }
        for item in specifications
    ]

    def chief(*args, **kwargs):
        events.append("chief")
        return {"top_candidates": alerts, "summary": "summary"}

    monkeypatch.setattr(scan_opportunities, "review_candidates", chief)
    monkeypatch.setattr(
        scan_opportunities, "qualified_alerts", lambda review: review["top_candidates"]
    )
    monkeypatch.setattr(
        scan_opportunities,
        "build_entry_exit_plan",
        lambda snapshot, risk: _plan(snapshot.symbol),
    )
    by_symbol = {item["symbol"]: item for item in specifications}

    def target_gate(plan, snapshot):
        events.append(f"target:{plan.symbol}")
        specification = by_symbol[plan.symbol]
        return _target(
            plan.symbol,
            score=specification.get("target_score", 80),
            qualified=specification.get("target_pass", True),
        )

    def economic_gate(plan, available_capital, **kwargs):
        assert kwargs["max_capital_fraction"] == pytest.approx(
            scan_opportunities.PRODUCTION_MAX_CAPITAL_FRACTION
        )
        events.append(f"economic:{plan.symbol}")
        specification = by_symbol[plan.symbol]
        return _economic(
            target_2_move_pct=specification.get("move", 7.0),
            qualified=specification.get("economic_pass", True),
        )

    monkeypatch.setattr(
        scan_opportunities, "evaluate_target_attainability", target_gate
    )
    monkeypatch.setattr(scan_opportunities, "evaluate_economic_quality", economic_gate)
    real_rank = rank_profit_opportunities

    def rank_all(opportunities):
        events.append(f"rank:{len(opportunities)}")
        ranked_symbols.extend(item.snapshot.symbol for item in opportunities)
        return real_rank(opportunities)

    monkeypatch.setattr(scan_opportunities, "rank_profit_opportunities", rank_all)

    def send(**kwargs):
        events.append(f"send:{kwargs['plan'].symbol}")
        sent.append(kwargs["candidate"].copy())
        return True

    monkeypatch.setattr(scan_opportunities, "send_trade_plan", send)
    return events, ranked_symbols, sent


def test_pipeline_ranks_only_gate_survivors_before_dispatch_and_sends_all(monkeypatch):
    events, ranked_symbols, sent = _configure_pipeline(
        monkeypatch,
        [
            {"symbol": "TARGETREJECTUSD", "target_pass": False},
            {"symbol": "ECONREJECTUSD", "economic_pass": False},
            {"symbol": "THIRDUSD", "move": 5.0},
            {"symbol": "FIRSTUSD", "move": 10.0},
            {"symbol": "SECONDUSD", "move": 7.0},
        ],
    )
    scan_opportunities.main()

    assert "TARGETREJECTUSD" not in ranked_symbols
    assert "ECONREJECTUSD" not in ranked_symbols
    assert set(ranked_symbols) == {"FIRSTUSD", "SECONDUSD", "THIRDUSD"}
    first_send_index = next(i for i, value in enumerate(events) if value.startswith("send:"))
    assert events.index("rank:3") < first_send_index
    assert events.index("target:THIRDUSD") < first_send_index
    assert events.index("economic:SECONDUSD") < first_send_index
    assert [item["symbol"] for item in sent] == ["FIRSTUSD", "SECONDUSD", "THIRDUSD"]
    assert [item["profit_rank"] for item in sent] == [1, 2, 3]
    assert all("profit_rank_score" in item for item in sent)
    assert events.count("chief") == 1


def test_zero_survivor_scan_prints_clean_ranking_summary(monkeypatch, capsys):
    events, ranked_symbols, sent = _configure_pipeline(
        monkeypatch,
        [{"symbol": "REJECTEDUSD", "target_pass": False}],
    )
    scan_opportunities.main()
    output = capsys.readouterr().out
    assert "===== OHM PROFIT RANKING =====" in output
    assert "Qualified survivors: 0" in output
    assert ranked_symbols == []
    assert sent == []
    assert "rank:0" in events


def test_telegram_includes_optional_rank_and_score():
    message = chief_alert_notifier.format_trade_plan(
        {
            "symbol": "AAAUSD",
            "decision": "alert",
            "confidence": 80,
            "profit_rank": 2,
            "profit_rank_score": 78.421,
        },
        _plan(),
        "summary",
    )
    assert "OHM Opportunity Rank: #2" in message
    assert "Profit Rank Score: 78.42/100" in message
    assert "% probability" not in message


def test_telegram_remains_backward_compatible_without_rank_fields():
    message = chief_alert_notifier.format_trade_plan(
        {"symbol": "AAAUSD", "decision": "alert", "confidence": 80},
        _plan(),
        "summary",
    )
    assert "OHM Opportunity Rank" not in message
    assert "Profit Rank Score" not in message


def test_telegram_dedup_key_is_unchanged_by_rank_alone():
    first = {"confidence": 80, "profit_rank": 1, "profit_rank_score": 90.0}
    second = {"confidence": 80, "profit_rank": 8, "profit_rank_score": 40.0}
    assert chief_alert_notifier._alert_state_key(first, _plan()) == (
        chief_alert_notifier._alert_state_key(second, _plan())
    )
