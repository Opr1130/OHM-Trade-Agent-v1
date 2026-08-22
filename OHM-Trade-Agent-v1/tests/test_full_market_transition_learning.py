import json
from datetime import datetime, timedelta, timezone

import pytest

from app.exchanges.kraken import Candle
from app.services.full_market_observation import MarketTransition
from app.services.full_market_transition_learning import (
    build_full_market_transition_summary,
    capture_full_market_transitions,
    observe_due_full_market_transition_outcomes,
)


def _transition(reference_price=100.0):
    return MarketTransition(
        version="full-market-observation-v1",
        symbol="FASTUSD",
        pattern="REACCELERATION",
        score=82,
        price_change_since_prior_pct=2.0,
        lift_change_since_prior_pct=3.0,
        lift_from_24h_low_pct=8.0,
        distance_from_24h_high_pct=1.0,
        liquidity_24h_usd_approx=1_000_000.0,
        alert_tier="DEEP_REVIEW",
        reference_price=reference_price,
    )


def test_transition_capture_has_immutable_identity_and_cooldown(tmp_path):
    detections = tmp_path / "detections.jsonl"
    state = tmp_path / "capture.json"
    now = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)

    assert capture_full_market_transitions(
        [_transition()], now=now, detection_file=detections, state_file=state
    ) == 1
    assert capture_full_market_transitions(
        [_transition()], now=now + timedelta(minutes=30), detection_file=detections, state_file=state
    ) == 0
    assert capture_full_market_transitions(
        [_transition()], now=now + timedelta(hours=1), detection_file=detections, state_file=state
    ) == 1

    rows = [json.loads(line) for line in detections.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["detection_id"] != rows[1]["detection_id"]
    assert len(rows[0]["detection_id"]) == 24
    assert rows[0]["reference_price"] == 100.0
    assert rows[0]["shadow_only"] is True
    assert rows[0]["trade_authority_changed"] is False


def test_transition_outcome_uses_only_candles_fully_closed_by_horizon(tmp_path):
    detections = tmp_path / "detections.jsonl"
    capture_state = tmp_path / "capture.json"
    outcomes = tmp_path / "outcomes.jsonl"
    outcome_state = tmp_path / "outcome_state.json"
    detected = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)
    capture_full_market_transitions(
        [_transition()], now=detected, detection_file=detections, state_file=capture_state
    )

    candles = [
        Candle(int((detected + timedelta(minutes=15)).timestamp()), 100, 103, 99, 102, 101, 10, 20),
        Candle(int((detected + timedelta(minutes=30)).timestamp()), 102, 106, 95, 101, 101, 12, 25),
        Candle(int((detected + timedelta(minutes=45)).timestamp()), 101, 104, 100, 103, 102, 9, 18),
        # Opens exactly at the 1h horizon and therefore must not contribute.
        Candle(int((detected + timedelta(hours=1)).timestamp()), 103, 150, 50, 140, 120, 99, 99),
    ]

    class Client:
        def get_ohlc(self, symbol, interval=15, since=None):
            assert symbol == "FASTUSD"
            assert interval == 15
            return candles

    result = observe_due_full_market_transition_outcomes(
        now=detected + timedelta(hours=1, minutes=1),
        client=Client(),
        detection_file=detections,
        outcome_file=outcomes,
        state_file=outcome_state,
    )
    assert result["outcomes_added"] == 1
    row = json.loads(outcomes.read_text().strip())
    assert row["horizon"] == "1h"
    assert row["close_return_pct"] == pytest.approx(3.0)
    assert row["mfe_pct"] == pytest.approx(6.0)
    assert row["mae_pct"] == pytest.approx(-5.0)
    assert row["hit_5pct"] is True
    assert row["hit_10pct"] is False
    assert row["sample_candles"] == 3


def test_transition_summary_requires_thirty_samples_per_bucket(tmp_path):
    outcomes = tmp_path / "outcomes.jsonl"
    rows = []
    for index in range(30):
        rows.append({
            "record_type": "FULL_MARKET_TRANSITION_OUTCOME",
            "detection_id": f"D{index}",
            "symbol": "FASTUSD",
            "pattern": "REACCELERATION",
            "score": 82,
            "score_bucket": 80,
            "alert_tier": "DEEP_REVIEW",
            "horizon": "4h",
            "mfe_pct": 8.0,
            "mae_pct": -3.0,
            "hit_2pct": True,
            "hit_5pct": True,
            "hit_10pct": False,
            "hit_20pct": False,
        })
    outcomes.write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = build_full_market_transition_summary(outcome_file=outcomes)
    bucket = summary["buckets"]["REACCELERATION|DEEP_REVIEW|4h"]
    assert summary["status"] == "EVIDENCE_READY"
    assert summary["auto_promotion_allowed"] is False
    assert bucket["samples"] == 30
    assert bucket["sufficient_samples"] is True
    assert bucket["hit_5pct_rate"] == 1.0
