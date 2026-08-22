from datetime import datetime, timedelta, timezone

import pytest

from app.exchanges.kraken import Candle
from app.services import profitability_learning as learning
from app.services import shadow_learning as shadow


def test_shadow_learning_records_multi_horizon_directional_outcomes(monkeypatch, tmp_path):
    monkeypatch.setattr(shadow, "SHADOW_FILE", tmp_path / "shadow.json")
    monkeypatch.setattr(shadow, "LOCK_FILE", tmp_path / ".shadow.lock")
    start = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    shadow.record_shadow_candidate(
        symbol="ABCUSD",
        direction="LONG",
        decision="WAIT",
        reference_price=100.0,
        market_regime="RISK_OFF",
        observed_at=start.isoformat(),
    )

    class FakeClient:
        def get_ohlc(self, pair, interval=5, since=None):
            assert pair == "ABCUSD"
            assert interval == 5
            # Kraken timestamps bars by OPEN. These bars close exactly at the
            # 5m, 15m and 30m due horizons respectively.
            return [
                Candle(int(start.timestamp()), 100, 105, 100, 105, 105, 10, 10),
                Candle(int((start + timedelta(minutes=10)).timestamp()), 105, 106, 104, 105, 105, 10, 10),
                Candle(int((start + timedelta(minutes=25)).timestamp()), 105, 106, 104, 105, 105, 10, 10),
            ]

    result = shadow.observe_due_shadows(client=FakeClient(), now=start + timedelta(minutes=31))
    assert result["status"] == "OK"
    assert result["prices_requested"] == 1
    assert result["observations_added"] == 3
    row = shadow.get_shadow_records()[0]
    assert row["observations"]["5m"]["directional_move_pct"] == pytest.approx(5.0)
    assert row["observations"]["15m"]["directional_move_pct"] == pytest.approx(5.0)
    assert row["observations"]["30m"]["directional_move_pct"] == pytest.approx(5.0)
    assert row["observations"]["5m"]["observed_at"] == (start + timedelta(minutes=5)).isoformat()
    assert "1h" not in row["observations"]


def test_shadow_learning_deduplicates_same_decision_within_four_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(shadow, "SHADOW_FILE", tmp_path / "shadow.json")
    monkeypatch.setattr(shadow, "LOCK_FILE", tmp_path / ".shadow.lock")
    start = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    first = shadow.record_shadow_candidate(
        symbol="ABCUSD",
        direction="LONG",
        decision="MARGIN_REJECT",
        reference_price=100.0,
        market_regime="RISK_OFF",
        source="margin_eligibility_gate",
        observed_at=start.isoformat(),
    )
    duplicate = shadow.record_shadow_candidate(
        symbol="ABCUSD",
        direction="LONG",
        decision="MARGIN_REJECT",
        reference_price=101.0,
        market_regime="RISK_OFF",
        source="margin_eligibility_gate",
        observed_at=(start + timedelta(hours=1)).isoformat(),
    )

    assert len(shadow.get_shadow_records()) == 1
    assert duplicate["record_key"] == first["record_key"]
    assert duplicate["deduplicated"] is True


def test_shadow_learning_records_new_sample_after_cooldown_or_regime_change(monkeypatch, tmp_path):
    monkeypatch.setattr(shadow, "SHADOW_FILE", tmp_path / "shadow.json")
    monkeypatch.setattr(shadow, "LOCK_FILE", tmp_path / ".shadow.lock")
    start = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    base = dict(
        symbol="ABCUSD",
        direction="LONG",
        decision="WAIT",
        reference_price=100.0,
        source="chief_review",
    )
    shadow.record_shadow_candidate(
        **base,
        market_regime="RISK_OFF",
        observed_at=start.isoformat(),
    )
    shadow.record_shadow_candidate(
        **base,
        market_regime="RISK_ON",
        observed_at=(start + timedelta(hours=1)).isoformat(),
    )
    shadow.record_shadow_candidate(
        **base,
        market_regime="RISK_OFF",
        observed_at=(start + timedelta(hours=4)).isoformat(),
    )

    assert len(shadow.get_shadow_records()) == 3


def _outcome(i, *, direction="LONG", regime="RISK_ON", pnl=1.0):
    return {
        "trade_id": f"T{i}",
        "entered_trade": True,
        "terminal_status": "closed",
        "direction": direction,
        "market_regime": regime,
        "net_pnl": pnl,
        "mfe_pct": 2.0 if pnl > 0 else 0.1,
        "mae_pct": 0.5 if pnl > 0 else 3.0,
        "stop_observed": pnl <= 0,
    }


def test_profitability_profile_uses_actual_net_pnl_and_bounded_weights(monkeypatch, tmp_path):
    monkeypatch.setattr(learning, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(learning, "LOCK_FILE", tmp_path / ".profile.lock")
    outcomes = []
    for i in range(30):
        outcomes.append(_outcome(i, direction="LONG", regime="RISK_ON", pnl=2.0))
    for i in range(30, 60):
        outcomes.append(_outcome(i, direction="SHORT", regime="RISK_OFF", pnl=-1.0))

    profile = learning.build_profitability_profile(outcomes=outcomes, executions=[], shadows=[], persist=True)
    assert profile["paid_ai_calls"] == 0
    assert profile["trade_calibration"]["status"] == "CALIBRATED"
    assert profile["trade_calibration"]["minimum_bucket_samples"] == 30
    assert profile["weights"]["direction:LONG"] == pytest.approx(1.15)
    assert profile["weights"]["direction:SHORT"] == pytest.approx(0.85)
    assert profile["loss_learning"]["losing_trades"] == 30
    assert profile["loss_learning"]["potentially_avoidable_losses"] == 30
    assert profile["guardrails"]["minimum_bucket_samples"] == 30
    assert profile["guardrails"]["hard_risk_rules_mutable"] is False


def test_shadow_quality_counts_correct_rejects_and_missed_profit():
    shadows = [
        {"decision": "WAIT", "observations": {"24h": {"directional_move_pct": 4.0}}},
        {"decision": "REJECT", "observations": {"24h": {"directional_move_pct": -3.0}}},
        {"decision": "ENTER_NOW", "observations": {"24h": {"directional_move_pct": 2.0}}},
    ]
    result = learning.build_profitability_profile(outcomes=[], executions=[], shadows=shadows, persist=False)
    metrics = result["shadow_learning"]
    assert metrics["samples"] == 3
    assert metrics["missed_profitable"] == 1
    assert metrics["correct_rejections"] == 1
    assert metrics["decision_accuracy_pct"] == pytest.approx(66.67)


def test_timing_learning_compares_profitable_and_losing_fills():
    outcomes = [_outcome(1, pnl=10.0), _outcome(2, pnl=-5.0)]
    executions = [
        {"trade_id": "T1", "idea_to_order_seconds": 30.0, "order_to_first_fill_seconds": 20.0, "fill_vs_limit_pct": 0.1, "holding_seconds": 3600.0, "quantity": 10.0, "fill_price": 10.0},
        {"trade_id": "T2", "idea_to_order_seconds": 300.0, "order_to_first_fill_seconds": 200.0, "fill_vs_limit_pct": 1.0, "holding_seconds": 7200.0, "quantity": 10.0, "fill_price": 10.0},
    ]
    result = learning.build_profitability_profile(outcomes=outcomes, executions=executions, shadows=[], persist=False)
    timing = result["timing_learning"]
    assert timing["samples"] == 2
    assert timing["avg_idea_to_order_seconds_winners"] == 30.0
    assert timing["avg_idea_to_order_seconds_losers"] == 300.0
    assert timing["avg_order_to_fill_seconds_winners"] == 20.0
    assert timing["avg_order_to_fill_seconds_losers"] == 200.0
