"""End-to-end O'Pip funnel behaviour over a real production scan.

Only I/O boundaries are stubbed: the market fetch, the OpenAI transport, the
Telegram send, the paper bridges and the learning writers. Every deterministic
gate - the Chief pre-AI viability prefilter, the recommendation gate, target
attainability, economic quality and entry/exit planning - runs for real, so the
funnel records genuine production verdicts and the shadow engine genuinely
re-derives them from the same evidence.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.jobs import scan_opportunities
from app.opip.decision import store
from app.opip.decision.explanations import build_zero_trade_explanation
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.market_scanner import ScanResult
from app.scanner.models import MarketSnapshot
from app.scanner.reference_market_validation import ReferenceMarketValidation
from app.services import chief_analyst


VIABLE = "viable"
NO_ECONOMIC_ROOM = "no_economic_room"
NO_TARGET_ROOM = "no_target_room"


def _snapshot(symbol: str, shape: str = VIABLE, price: float = 100.0):
    """Build a realistic snapshot in one of three deterministic-gate shapes."""
    result = MarketSnapshot(
        symbol=symbol, last_price=price, ema20=price * 0.99, ema50=price * 0.95,
        ema200=price * 0.90, rsi=55.0, macd_line=1.0, macd_signal=0.5,
        macd_histogram=0.5, atr=price * 0.02, atr_pct=2.0, volume_ratio=1.5,
        technical_score=90, trend="bullish",
        recent_24h_high=price * 1.20, recent_24h_low=price * 0.94,
        recent_72h_high=price * 1.30, recent_72h_low=price * 0.88,
        momentum_6h_pct=1.0, momentum_24h_pct=3.0, momentum_72h_pct=7.0,
        distance_to_24h_high_pct=16.0, distance_to_72h_high_pct=23.0,
        realized_range_24h_pct=16.0, realized_range_72h_pct=28.0,
        average_hourly_range_24h_pct=1.0, average_hourly_range_72h_pct=1.1,
        rolling_24h_range_median_pct=8.0, rolling_24h_range_p75_pct=10.0,
        rolling_24h_range_p90_pct=12.0, rolling_72h_range_median_pct=12.0,
        rolling_72h_range_p75_pct=16.0, rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=6.0, rolling_24h_upside_p75_pct=8.0,
        rolling_24h_upside_p90_pct=10.0, rolling_72h_upside_median_pct=10.0,
        rolling_72h_upside_p75_pct=14.0, rolling_72h_upside_p90_pct=18.0,
        underlying_asset=symbol.removesuffix("USD"), primary_pair=symbol,
        primary_quote_currency="USD", combined_24h_liquidity_usd=5_000_000.0,
        primary_24h_liquidity_usd=5_000_000.0,
        cross_pair_confirmation_status="CONFIRMED", ticker_last=price,
    )
    if shape == NO_ECONOMIC_ROOM:
        # Real volatility collapse: targets become too small to clear costs.
        result.atr = price * 0.0005
        result.atr_pct = 0.05
    elif shape == NO_TARGET_ROOM:
        # No headroom above and no historical precedent for the move.
        result.recent_24h_high = price * 1.002
        result.recent_72h_high = price * 1.003
        result.distance_to_24h_high_pct = 0.2
        result.distance_to_72h_high_pct = 0.3
        result.rolling_24h_upside_median_pct = 0.4
        result.rolling_24h_upside_p75_pct = 0.5
        result.rolling_24h_upside_p90_pct = 0.6
        result.rolling_72h_upside_median_pct = 0.5
        result.rolling_72h_upside_p75_pct = 0.6
        result.rolling_72h_upside_p90_pct = 0.7
        result.atr = price * 0.003
        result.atr_pct = 0.3

    result.execution_validation = ExecutionValidation(
        status="VALID", book_coverage_status="COMPLETE", warnings=[],
        validation_notional_usd=2_000.0, spread_bps=6.0,
        estimated_visible_round_trip_market_drag_pct=0.25,
        recent_trade_status="FRESH",
    )
    result.independent_market_reference = ReferenceMarketValidation(
        status="CONFIRMED", available=True, mapping_status="UNIQUE",
        api_mode="KEYLESS", coingecko_id="asset", coingecko_name="Asset",
        matched_candidate_count=1, reference_price_usd=price,
        kraken_normalized_price_usd=price, price_divergence_pct=0.0,
    )
    return result


class _FakeOpenAI:
    """Minimal stand-in for the OpenAI transport used by the Chief review."""

    def __init__(self, payload):
        self._payload = payload
        self.responses = SimpleNamespace(create=self._create)

    def __call__(self, *args, **kwargs):
        return self

    def _create(self, **kwargs):
        return SimpleNamespace(
            output_text=json.dumps(self._payload),
            usage=None,
        )


def _chief_payload(top_candidates):
    return {
        "market_view": "neutral",
        "recommended_action": "alert" if top_candidates else "no_trade",
        "top_candidates": top_candidates,
        "summary": "test summary",
    }


def _install_scan(monkeypatch, tmp_path, snapshots, *, openai=None):
    """Stub only I/O boundaries; leave every deterministic gate real."""
    settings = SimpleNamespace(
        openai_model="model", openai_api_key="key", account_equity=10_000.0,
        coingecko_api_key=None, cryptopanic_auth_token=None,
        cryptopanic_api_plan="developer", coinmarketcal_api_key=None,
        telegram_bot_token=None, telegram_chat_id=None, telegram_enabled=False,
        price_movement_mode="off", max_margin_leverage=3.0,
        paper_trade_capital_per_trade=1_000.0, paper_trade_max_hold_hours=24,
        paper_trade_pending_ttl_hours=24, paper_trade_starting_equity=10_000.0,
        paper_trade_max_positions=3, tradingview_v2_enabled=False,
    )
    monkeypatch.setattr(scan_opportunities, "get_settings", lambda: settings)
    monkeypatch.setattr(
        scan_opportunities, "scan_market",
        lambda limit: ScanResult(
            snapshots, len(snapshots), len(snapshots), 0, 0, [], []
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "evaluate_market_regime",
        lambda items: SimpleNamespace(
            sample_size=len(items), regime="NEUTRAL", breadth_score=50.0,
            pct_above_ema20=50.0, pct_above_ema50=50.0, pct_above_ema200=50.0,
            pct_positive_momentum_24h=50.0, pct_positive_momentum_72h=50.0,
            pct_bullish_trend=50.0,
        ),
    )
    monkeypatch.setattr(scan_opportunities, "select_candidates", lambda items: items)
    monkeypatch.setattr(
        scan_opportunities, "validate_short_margin_eligibility",
        lambda candidates, **kwargs: SimpleNamespace(
            requested=0, eligible=0, ineligible=0, unavailable=0
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "confirm_secondary_markets",
        lambda *args: SimpleNamespace(requested=0, analyzed=0, failed=0),
    )
    monkeypatch.setattr(
        scan_opportunities, "deep_validate_candidates",
        lambda candidates, *args: candidates,
    )
    monkeypatch.setattr(
        scan_opportunities, "validate_finalist_references",
        lambda candidates, *args, **kwargs: SimpleNamespace(
            requested=len(candidates), available=len(candidates), unavailable=0,
            ambiguous=0, api_mode="KEYLESS",
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "load_coingecko_global_context",
        lambda **kwargs: SimpleNamespace(
            status="UNAVAILABLE", market_cap_change_24h_pct=None,
            btc_market_cap_percentage=None,
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "validate_finalist_news",
        lambda candidates, **kwargs: SimpleNamespace(
            requested=len(candidates), available=0, unavailable=len(candidates),
            unresolved=0,
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "validate_scheduled_catalysts",
        lambda candidates, **kwargs: SimpleNamespace(
            requested=len(candidates), available=0, unresolved=len(candidates),
            unavailable=0,
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "enrich_finalist_market_intelligence",
        lambda candidates, regime: SimpleNamespace(
            candidates=candidates, evidence=[], assessments={},
            chief_market_regime_context=None,
        ),
    )
    monkeypatch.setattr(scan_opportunities, "send_trade_plan", lambda **kwargs: True)
    monkeypatch.setattr(
        scan_opportunities, "_prepare_qualified_lineage",
        lambda ranked, **kwargs: (len(list(ranked)), 0),
    )
    monkeypatch.setattr(
        scan_opportunities, "_publish_freqtrade_paper_opportunities",
        lambda ranked, **kwargs: (0, 0),
    )
    monkeypatch.setattr(
        scan_opportunities, "_maybe_enroll_paper_opportunities",
        lambda ranked, **kwargs: (0, 0),
    )
    monkeypatch.setattr(
        scan_opportunities, "_capture_native_scan_cohort", lambda scan, **kwargs: 0
    )
    monkeypatch.setattr(scan_opportunities, "_paper_trade_enabled_safe", lambda: False)

    # Keep the real Chief review, but isolate its transport and its writers.
    monkeypatch.setattr(chief_analyst, "OpenAI", openai or _FakeOpenAI(_chief_payload([])))
    monkeypatch.setattr(chief_analyst, "capture_prefilter_rejection", lambda *a, **k: True)
    monkeypatch.setattr(
        chief_analyst, "capture_chief_review_decisions",
        lambda *a, **k: {"captured": 0, "qualified_alerts_deferred": 0,
                         "not_selected": 0, "unmatched": 0},
    )
    monkeypatch.setattr(chief_analyst, "append_usage_record", lambda **kwargs: {})
    monkeypatch.setattr(chief_analyst, "budget_block_reason", lambda: None)
    monkeypatch.setattr(chief_analyst, "get_cached_review", lambda fingerprint: None)
    monkeypatch.setattr(chief_analyst, "store_cached_review", lambda *a, **k: None)

    monkeypatch.setattr(store, "FUNNEL_EVENTS_FILE", tmp_path / "funnel_events.jsonl")
    monkeypatch.setattr(store, "SCAN_SUMMARIES_FILE", tmp_path / "scan_summaries.jsonl")
    monkeypatch.setattr(store, "DEAD_LETTER_FILE", tmp_path / "dead.jsonl")
    monkeypatch.setenv("OPIP_FUNNEL_TELEMETRY_ENABLED", "true")
    return settings


def _summary(tmp_path) -> dict:
    rows = store.read_jsonl(tmp_path / "scan_summaries.jsonl")
    assert rows, "the scan must persist exactly one summary"
    return rows[-1]


def _assert_equivalent(summary):
    """The engine must reproduce both the verdict and the attribution."""
    comparison = summary["shadow_comparison"]
    assert comparison["comparable_comparisons"] == comparison["total_comparisons"]
    assert comparison["divergences"] == 0, comparison["divergence_reasons"]
    assert comparison["terminal_gate_divergences"] == 0, (
        comparison["terminal_gate_divergence_reasons"]
    )
    assert comparison["opip_engine_authoritative"] is False


# ------------------------------------------------------------------ tests ---


def test_deterministic_prefilter_zero_trade_scan_is_fully_attributed(
    monkeypatch, tmp_path, capsys
):
    snapshots = [
        _snapshot("RAYUSD", NO_ECONOMIC_ROOM),
        _snapshot("AAAUSD", NO_TARGET_ROOM),
        _snapshot("BBBUSD", NO_ECONOMIC_ROOM),
    ]
    _install_scan(monkeypatch, tmp_path, snapshots)
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["funnel"]["entered"] == 3
    assert summary["funnel"]["qualified"] == 0
    assert summary["funnel"]["rejected_by_policy"] == 3
    assert summary["funnel"]["operationally_unresolved"] == 0
    assert summary["invariant_holds"] is True
    assert summary["terminal"]["dominant_terminal_gate"] == "DETERMINISTIC_QUALITY"
    assert summary["terminal"]["top_reasons"] == {"DETERMINISTIC_VIABILITY_FAILED": 3}
    assert summary["terminal"]["reason_classes"]["POLICY"] == 3
    assert summary["terminal"]["reason_classes"]["OPERATIONAL"] == 0
    assert summary["ai_stage_reached"] is False
    assert (
        summary["ai_stage"]["invocation_status"] == "SKIPPED_NO_ELIGIBLE_CANDIDATES"
    )
    _assert_equivalent(summary)

    output = capsys.readouterr().out
    assert "O'Pip Qualification Summary" in output
    assert "Directional candidates: 3" in output
    assert "Rejected by policy: 3" in output
    assert "Terminal stage: DETERMINISTIC_QUALITY" in output
    assert "AI stage reached: NO" in output


def test_funnel_events_are_joinable_and_version_stamped(monkeypatch, tmp_path):
    snapshots = [
        _snapshot("RAYUSD", NO_ECONOMIC_ROOM),
        _snapshot("AAAUSD", NO_ECONOMIC_ROOM),
    ]
    _install_scan(monkeypatch, tmp_path, snapshots)
    scan_opportunities.main()

    summary = _summary(tmp_path)
    events = store.read_funnel_events_for_scan(
        summary["scan_id"], path=tmp_path / "funnel_events.jsonl"
    )
    assert len(events) == 2
    for event in events:
        assert event["scan_id"] == summary["scan_id"]
        assert event["cohort_id"] == summary["cohort_id"]
        assert event["episode_id"]
        assert event["candidate_id"].startswith("OPIPC:")
        assert event["strategy_version"] == summary["strategy_version"]
        assert event["gate_policy_version"] == summary["gate_policy_version"]
        assert event["gate_policy_fingerprint"] == summary["gate_policy_fingerprint"]
        assert event["decision"] == "REJECTED"
        assert event["legacy_decision"] == "REJECTED"
        assert event["gate_results"]
        assert event["market_type"] == "SPOT"
    assert len({event["candidate_id"] for event in events}) == 2


def test_qualified_scan_records_a_qualified_terminal_state(monkeypatch, tmp_path):
    snapshots = [_snapshot("RAYUSD", VIABLE)]
    _install_scan(
        monkeypatch, tmp_path, snapshots,
        openai=_FakeOpenAI(
            _chief_payload(
                [
                    {
                        "symbol": "RAYUSD", "direction": "LONG", "rank": 1,
                        "confidence": 95, "risk_level": "low",
                        "decision": "alert", "reason": "clean continuation",
                    }
                ]
            )
        ),
    )
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["funnel"]["entered"] == 1
    assert summary["funnel"]["qualified"] == 1
    assert summary["funnel"]["rejected_by_policy"] == 0
    assert summary["invariant_holds"] is True
    assert summary["ai_stage_reached"] is True
    assert summary["ai_stage"]["invoked"] is True
    assert summary["ai_stage"]["confidence_summary"]["max"] == 95
    _assert_equivalent(summary)


def test_ai_outage_is_reported_as_an_operational_failure(monkeypatch, tmp_path):
    class _Broken:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("connection refused")

    snapshots = [_snapshot("RAYUSD", VIABLE)]
    _install_scan(monkeypatch, tmp_path, snapshots, openai=_Broken())
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["funnel"]["operational_failures"] == 1
    assert summary["funnel"]["rejected_by_policy"] == 0
    assert summary["invariant_holds"] is True
    assert summary["terminal"]["top_reasons"] == {"AI_SERVICE_UNAVAILABLE": 1}
    assert summary["terminal"]["reason_classes"]["OPERATIONAL"] == 1
    assert summary["ai_stage"]["unavailable"] is True
    assert summary["ai_stage"]["budget_exhausted"] is False
    assert summary["ai_stage"]["failure_type"] == "RuntimeError"
    assert summary["ai_stage_reached"] is True
    _assert_equivalent(summary)


def test_ai_budget_suppression_is_not_an_operational_failure(monkeypatch, tmp_path):
    snapshots = [_snapshot("RAYUSD", VIABLE)]
    _install_scan(monkeypatch, tmp_path, snapshots)
    monkeypatch.setattr(
        chief_analyst, "budget_block_reason",
        lambda: "daily OpenAI call budget reached (12/12)",
    )
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["terminal"]["top_reasons"] == {"AI_BUDGET_LIMIT": 1}
    assert summary["terminal"]["reason_classes"]["BUDGET"] == 1
    assert summary["funnel"]["operational_failures"] == 0
    assert summary["funnel"]["rejected_by_policy"] == 1
    assert summary["ai_stage"]["budget_exhausted"] is True
    assert summary["ai_stage"]["unavailable"] is False
    _assert_equivalent(summary)


def test_ai_returning_nothing_is_distinct_from_never_asking(monkeypatch, tmp_path):
    snapshots = [_snapshot("RAYUSD", VIABLE)]
    _install_scan(monkeypatch, tmp_path, snapshots)
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["terminal"]["top_reasons"] == {"AI_RETURNED_NO_CANDIDATES": 1}
    assert summary["terminal"]["reason_classes"]["MODEL"] == 1
    assert summary["ai_stage"]["invoked"] is True
    assert summary["ai_stage"]["unavailable"] is False
    assert summary["ai_stage"]["budget_exhausted"] is False
    assert summary["ai_stage"]["invocation_status"] == "SUCCEEDED"
    _assert_equivalent(summary)


def test_low_confidence_is_attributed_to_the_recommendation_gate(
    monkeypatch, tmp_path
):
    snapshots = [_snapshot("RAYUSD", VIABLE)]
    _install_scan(
        monkeypatch, tmp_path, snapshots,
        openai=_FakeOpenAI(
            _chief_payload(
                [
                    {
                        "symbol": "RAYUSD", "direction": "LONG", "rank": 1,
                        "confidence": 70, "risk_level": "low",
                        "decision": "alert", "reason": "close but not clear",
                    }
                ]
            )
        ),
    )
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["terminal"]["top_reasons"] == {"AI_CONFIDENCE_BELOW_THRESHOLD": 1}
    assert summary["terminal"]["dominant_terminal_gate"] == "RECOMMENDATION_GATE"
    assert summary["terminal"]["reason_classes"]["MODEL"] == 1
    assert summary["ai_stage"]["confidence_summary"]["count"] == 1
    assert summary["ai_stage"]["confidence_summary"]["max"] == 70
    assert summary["ai_stage"]["confidence_summary"]["calibrated_probability"] is False
    nearest = summary["nearest_misses"][0]
    assert nearest["gate"] == "RECOMMENDATION_GATE"
    assert nearest["threshold"] == 85
    assert nearest["measured_value"] == 70
    _assert_equivalent(summary)


def test_chief_watch_verdict_is_not_reported_as_low_confidence(monkeypatch, tmp_path):
    snapshots = [_snapshot("RAYUSD", VIABLE)]
    _install_scan(
        monkeypatch, tmp_path, snapshots,
        openai=_FakeOpenAI(
            _chief_payload(
                [
                    {
                        "symbol": "RAYUSD", "direction": "LONG", "rank": 1,
                        "confidence": 95, "risk_level": "low",
                        "decision": "watch", "reason": "wait for the retest",
                    }
                ]
            )
        ),
    )
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["terminal"]["top_reasons"] == {"AI_DECISION_WATCH": 1}
    _assert_equivalent(summary)


def test_zero_trade_read_model_answers_the_operator_question(monkeypatch, tmp_path):
    snapshots = [
        _snapshot("RAYUSD", NO_ECONOMIC_ROOM),
        _snapshot("AAAUSD", NO_ECONOMIC_ROOM),
    ]
    _install_scan(monkeypatch, tmp_path, snapshots)
    scan_opportunities.main()

    explanation = build_zero_trade_explanation(
        summaries_path=tmp_path / "scan_summaries.jsonl",
        events_path=tmp_path / "funnel_events.jsonl",
        telemetry_enabled=True,
        now=datetime.now(timezone.utc) + timedelta(seconds=1),
        include_candidates=True,
    )
    assert explanation["state"] == "FRESH"
    assert explanation["qualified"] == 0
    assert explanation["directional_candidates"] == 2
    assert explanation["candidates_analyzed"] == 2
    assert explanation["technical_candidates"] == 2
    assert explanation["long_candidates"] == 2
    assert explanation["short_candidates"] == 0
    assert explanation["dominant_terminal_gate"] == "DETERMINISTIC_QUALITY"
    assert explanation["ai_invoked"] is False
    assert explanation["ai_unavailable"] is False
    assert explanation["ai_budget_exhausted"] is False
    assert explanation["operational_failure_state"] == "NONE"
    assert explanation["funnel_invariant_holds"] is True
    assert explanation["paper_admission_eligible"] == 0
    assert len(explanation["candidates"]) == 2
    assert "DETERMINISTIC_QUALITY" in explanation["explanation"]


def test_scan_with_no_technical_candidates_still_explains_itself(
    monkeypatch, tmp_path, capsys
):
    _install_scan(monkeypatch, tmp_path, [])
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["funnel"]["entered"] == 0
    assert summary["invariant_holds"] is True
    assert "Directional candidates: 0" in capsys.readouterr().out


def test_telemetry_disabled_scan_writes_nothing_but_still_prints(
    monkeypatch, tmp_path, capsys
):
    snapshots = [_snapshot("RAYUSD", NO_ECONOMIC_ROOM)]
    _install_scan(monkeypatch, tmp_path, snapshots)
    monkeypatch.setenv("OPIP_FUNNEL_TELEMETRY_ENABLED", "false")
    scan_opportunities.main()

    assert not (tmp_path / "scan_summaries.jsonl").exists()
    assert not (tmp_path / "funnel_events.jsonl").exists()
    assert "O'Pip Qualification Summary" in capsys.readouterr().out


def test_review_without_stage_evidence_leaves_candidates_unresolved_not_rejected(
    monkeypatch, tmp_path
):
    """A caller that bypasses the Chief cannot silently look like a rejection.

    Several existing tests (and any future caller) monkeypatch
    ``review_candidates`` to return a bare review dict with no O'Pip stage
    evidence. The funnel must then report those candidates as operationally
    unresolved - it genuinely does not know why they stopped - rather than
    inventing a policy rejection they never received.
    """
    snapshots = [_snapshot("RAYUSD", VIABLE)]
    _install_scan(monkeypatch, tmp_path, snapshots)
    monkeypatch.setattr(
        scan_opportunities, "review_candidates",
        lambda *a, **k: {"top_candidates": [], "summary": "bare review"},
    )
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["funnel"]["entered"] == 1
    assert summary["funnel"]["rejected_by_policy"] == 0
    assert summary["funnel"]["incomplete"] == 1
    assert summary["funnel"]["operationally_unresolved"] == 1
    assert summary["invariant_holds"] is True
    assert summary["terminal"]["top_reasons"] == {"FUNNEL_INCOMPLETE": 1}
    assert summary["ai_stage"]["invocation_status"] == "NOT_REACHED"
    assert summary["ai_stage_reached"] is False


def test_scan_context_reports_the_selected_shortlist_not_the_survivors(
    monkeypatch, tmp_path
):
    """Attrition belongs in the funnel counters, not in the shortlist size."""
    snapshots = [
        _snapshot("RAYUSD", NO_ECONOMIC_ROOM),
        _snapshot("AAAUSD", NO_ECONOMIC_ROOM),
        _snapshot("BBBUSD", NO_ECONOMIC_ROOM),
    ]
    _install_scan(monkeypatch, tmp_path, snapshots)
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["scan"]["technical_candidates"] == 3
    assert summary["scan"]["analyzed"] == 3
    assert summary["funnel"]["entered"] == 3


def test_nearest_miss_is_reported_for_the_dominant_terminal_gate(
    monkeypatch, tmp_path, capsys
):
    """The most common stop must still answer 'how close was it?'."""
    snapshots = [
        _snapshot("RAYUSD", NO_ECONOMIC_ROOM),
        _snapshot("ADAUSD", NO_TARGET_ROOM),
    ]
    _install_scan(monkeypatch, tmp_path, snapshots)
    scan_opportunities.main()

    summary = _summary(tmp_path)
    assert summary["terminal"]["dominant_terminal_gate"] == "DETERMINISTIC_QUALITY"
    nearest = summary["nearest_misses"]
    assert nearest, "the dominant terminal gate must produce a nearest miss"
    assert nearest[0]["gate"] == "DETERMINISTIC_QUALITY"
    assert nearest[0]["distance_from_threshold_pct"] is not None
    # Sorted by absolute distance from the bar.
    distances = [row["distance_from_threshold_pct"] for row in nearest]
    assert distances == sorted(distances)
    assert "Distance from threshold:" in capsys.readouterr().out


def test_nearest_miss_quotes_the_binding_metric_not_a_passing_one(
    monkeypatch, tmp_path
):
    """An economically-rejected candidate must not be scored on its target."""
    snapshots = [_snapshot("RAYUSD", NO_ECONOMIC_ROOM)]
    _install_scan(monkeypatch, tmp_path, snapshots)
    scan_opportunities.main()

    events = store.read_funnel_events_for_scan(
        _summary(tmp_path)["scan_id"], path=tmp_path / "funnel_events.jsonl"
    )
    terminal = next(
        result
        for result in events[0]["gate_results"]
        if result["gate"] == "DETERMINISTIC_QUALITY"
    )
    assert terminal["metadata"]["target_qualified_any"] is True
    assert terminal["metadata"]["economic_qualified_any"] is False
    assert terminal["metadata"]["binding_metric"] == "ECONOMIC_NET_PROFIT_AT_TARGET_2"
    assert terminal["threshold"] == 75.0
    assert terminal["measured_value"] < 75.0
