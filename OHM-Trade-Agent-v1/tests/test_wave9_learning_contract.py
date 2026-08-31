import json
from datetime import datetime, timezone

from app.scanner.models import MarketSnapshot
from app.services import trade_outcome_registry
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.trade_feature_snapshot import build_trade_feature_snapshot
from app.services.trade_quality_assessor import assess_trade_quality
from app.services.trade_quality_evidence_registry import record_trade_quality_evidence


def _snapshot():
    row = MarketSnapshot(
        symbol="SOLUSD",
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
        volume_ratio=1.8,
        technical_score=90,
        trend="bullish",
        momentum_6h_pct=3.0,
        momentum_24h_pct=8.0,
        momentum_72h_pct=12.0,
        combined_24h_liquidity_usd=2_000_000.0,
        cross_pair_confirmation_status="CONFIRMED",
        underlying_asset="SOL",
        primary_pair="SOLUSD",
        primary_quote_currency="USD",
        movement_data_status="AVAILABLE",
    )
    row.execution_validation = type(
        "Execution",
        (),
        {
            "status": "VALID",
            "estimated_visible_round_trip_market_drag_pct": 0.2,
        },
    )()
    row.independent_market_reference = type(
        "Reference",
        (),
        {"status": "CONFIRMED", "price_divergence_pct": 0.1},
    )()
    row.news_context = type("News", (), {"status": "AVAILABLE"})()
    row.scheduled_catalyst_context = type(
        "Catalyst",
        (),
        {"status": "UNRESOLVED"},
    )()
    return row


def _plan():
    return EntryExitPlan(
        symbol="SOLUSD",
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="qualified",
        direction="LONG",
    )


def test_quality_evidence_is_point_in_time_and_non_probabilistic(tmp_path):
    decision_at = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    feature_snapshot = build_trade_feature_snapshot(
        _snapshot(),
        decision_at=decision_at,
        episode_id="W9EP:C1",
        candidate_id="C1",
        regime="NEUTRAL",
    )
    assessment = assess_trade_quality(feature_snapshot, _plan())
    path = tmp_path / "quality.jsonl"

    evidence_id = record_trade_quality_evidence(
        feature_snapshot=feature_snapshot,
        assessment=assessment,
        plan=_plan(),
        candidate={
            "direction": "LONG",
            "technical_score": 90,
            "confidence": 84,
            "decision": "alert",
        },
        decision_at=decision_at,
        market_regime="NEUTRAL",
        path=path,
    )

    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["evidence_id"] == evidence_id
    assert row["snapshot_id"] == feature_snapshot.snapshot_id
    assert row["feature_snapshot"]["snapshot_id"] == feature_snapshot.snapshot_id
    assert row["outcome_contract"]["primary_event"] == (
        "TARGET_1_BEFORE_STOP_WITHIN_HORIZON"
    )
    assert row["outcome_contract"]["probability_claimed"] is False
    assert row["measurement_only"] is True
    assert row["trade_authority_changed"] is False


def test_quality_evidence_recording_is_idempotent(tmp_path):
    decision_at = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    feature_snapshot = build_trade_feature_snapshot(
        _snapshot(),
        decision_at=decision_at,
        episode_id="W9EP:IDEMPOTENT",
        candidate_id="IDEMPOTENT",
        regime="NEUTRAL",
    )
    assessment = assess_trade_quality(feature_snapshot, _plan())
    path = tmp_path / "quality.jsonl"
    kwargs = dict(
        feature_snapshot=feature_snapshot,
        assessment=assessment,
        plan=_plan(),
        candidate={"direction": "LONG", "confidence": 84, "decision": "alert"},
        decision_at=decision_at,
        market_regime="NEUTRAL",
        path=path,
    )

    first = record_trade_quality_evidence(**kwargs)
    second = record_trade_quality_evidence(**kwargs)

    assert first == second
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 1


def test_recommendation_links_wave9_quality_to_outcome_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        trade_outcome_registry,
        "OUTCOME_FILE",
        tmp_path / "trade_outcomes.json",
    )
    candidate = {
        "direction": "LONG",
        "confidence": 84,
        "decision": "alert",
        "technical_score": 90,
        "target_attainability_score": 82,
        "profit_rank": 2,
        "profit_rank_score": 88.0,
        "opportunity_rank": 1,
        "capital_efficiency_score": 91.5,
        "hold_proxy_hours": 4.0,
        "net_return_velocity_proxy_pct_per_hour": 1.2,
        "risk_efficiency_ratio": 2.5,
        "feature_snapshot_id": "MLSNAP:abc",
        "trade_quality_evidence_id": "W9Q:def",
        "continuation_score": 81,
        "continuation_decision": "PASS",
        "continuation_evidence_quality": "GOOD",
        "entry_quality_score": 79,
        "entry_quality_decision": "PASS",
        "exhaustion_state": "LOW",
        "trade_quality_actionable": True,
        "economic_qualified": True,
    }

    row = trade_outcome_registry.record_recommendation(
        trade_id="T-1",
        candidate=candidate,
        plan=_plan(),
        action="ENTER_NOW",
    )

    assert row["schema_version"] == 3
    assert row["wave9_extension_version"] == 1
    assert row["feature_snapshot_id"] == "MLSNAP:abc"
    assert row["trade_quality_evidence_id"] == "W9Q:def"
    assert row["opportunity_rank"] == 1
    assert row["capital_efficiency_score"] == 91.5
    assert row["continuation_score"] == 81
    assert row["entry_quality_score"] == 79
    assert row["quality_score_is_probability"] is False
    assert row["outcome_event_definition"] == (
        "TARGET_1_BEFORE_STOP_WITHIN_HORIZON"
    )


def test_calibration_summary_reports_wave9_quality_bins(monkeypatch):
    outcomes = []
    for index in range(30):
        outcomes.append(
            {
                "entered_trade": True,
                "terminal_status": "closed",
                "target_1_observed": index % 2 == 0,
                "target_2_observed": index % 3 == 0,
                "chief_confidence": 84,
                "profit_rank": 2,
                "opportunity_rank": 1,
                "continuation_score": 81,
                "entry_quality_score": 79,
                "capital_efficiency_score": 91.5,
                "direction": "LONG",
            }
        )
    monkeypatch.setattr(trade_outcome_registry, "get_outcomes", lambda: outcomes)

    summary = trade_outcome_registry.calibration_summary()

    assert summary["status"] == "AVAILABLE"
    assert summary["confidence_is_probability"] is False
    assert summary["outcome_event_definition"] == (
        "TARGET_1_BEFORE_STOP_WITHIN_HORIZON"
    )
    assert summary["opportunity_rank_bins"]["1"]["count"] == 30
    assert summary["continuation_score_bins"]["80-89"]["count"] == 30
    assert summary["entry_quality_score_bins"]["70-79"]["count"] == 30
    assert summary["capital_efficiency_score_bins"]["90-99"]["count"] == 30