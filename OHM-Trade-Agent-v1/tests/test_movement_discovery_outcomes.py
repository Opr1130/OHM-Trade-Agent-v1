import json
from datetime import datetime, timedelta, timezone

from app.exchanges.kraken import Candle
from app.scanner.models import MarketSnapshot
from app.services.movement_discovery_learning_capture import capture_movement_detections
from app.services.movement_discovery_outcomes import (
    HORIZONS,
    MAX_COMPLETED_KEYS,
    MAX_SOURCE_DETECTIONS,
    _read_detections,
    observe_due_movement_discovery_outcomes,
)
from app.services.movement_discovery_v2 import CoarseMover, evaluate_early_mover


def _snapshot():
    return MarketSnapshot(
        symbol="FASTUSD",
        last_price=100.0,
        ema20=99,
        ema50=98,
        ema200=97,
        rsi=60,
        macd_line=1,
        macd_signal=.5,
        macd_histogram=.5,
        atr=2,
        atr_pct=2,
        volume_ratio=2.0,
        technical_score=90,
        trend="bullish",
        movement_data_status="AVAILABLE",
        movement_volume_ratio=2.0,
        confirmed_price_change_1h_pct=2.0,
        momentum_6h_pct=5.0,
        momentum_24h_pct=9.0,
        distance_to_24h_high_pct=1.0,
    )


def _coarse():
    return CoarseMover(
        "FAST",
        "FASTUSD",
        "FAST/USD",
        100.0,
        10000,
        1_000_000,
        101.0,
        90.0,
        11.1,
        1.0,
        60.0,
    )


def _signal():
    signal = evaluate_early_mover(_snapshot(), _coarse())
    assert signal is not None
    return signal


def test_capture_adds_timestamp_reference_and_deduplicates_bucket(tmp_path):
    now = datetime(2026, 8, 20, 12, 5, tzinfo=timezone.utc)
    detections = tmp_path / "detections.jsonl"
    state = tmp_path / "state.json"
    signal = _signal()
    assert capture_movement_detections(
        [signal], [_coarse()], now=now, detection_file=detections, state_file=state
    ) == 1
    assert capture_movement_detections(
        [signal], [_coarse()], now=now + timedelta(minutes=10), detection_file=detections, state_file=state
    ) == 0
    row = json.loads(detections.read_text().strip())
    assert row["record_type"] == "DETECTION"
    assert row["observed_at"] == now.isoformat()
    assert row["reference_price"] == 100.0
    assert len(row["detection_id"]) == 24
    assert row["shadow_only"] is True


class _FakeKraken:
    def __init__(self, candles):
        self.candles = candles
        self.calls = []

    def get_ohlc(self, symbol, interval=60, since=None):
        self.calls.append((symbol, interval, since))
        return self.candles


class _NeverCallKraken:
    def get_ohlc(self, *args, **kwargs):
        raise AssertionError("completed ledger outcome should suppress a second market-data fetch")


def test_outcome_observer_uses_only_candles_fully_inside_horizon(tmp_path):
    detected = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
    detections = tmp_path / "detections.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "state.json"
    detections.write_text(json.dumps({
        "record_type": "DETECTION",
        "detection_id": "abc123",
        "symbol": "FASTUSD",
        "direction": "LONG",
        "observed_at": detected.isoformat(),
        "reference_price": 100.0,
        "stage": "READY",
        "entry_recommendation": "BREAKOUT_ENTRY_POSSIBLE",
        "momentum_state": "ACCELERATING",
        "continuation_confidence": 80,
        "entry_quality": 70,
        "relative_volume": 2.0,
        "liquidity_24h_usd_approx": 1_000_000,
        "version": "movement-discovery-v2.1",
    }) + "\n")
    candles = [
        Candle(int((detected + timedelta(minutes=15)).timestamp()), 100, 103, 99, 102, 101, 10, 20),
        Candle(int((detected + timedelta(minutes=30)).timestamp()), 102, 106, 95, 101, 101, 12, 25),
        Candle(int((detected + timedelta(minutes=45)).timestamp()), 101, 104, 100, 103, 102, 9, 18),
        # This candle opens exactly at the 1h horizon and therefore extends
        # beyond it. It must not contribute its close/high/low to the 1h label.
        Candle(int((detected + timedelta(hours=1)).timestamp()), 103, 105, 102, 104, 103, 8, 15),
    ]
    result = observe_due_movement_discovery_outcomes(
        now=detected + timedelta(hours=1, minutes=1),
        client=_FakeKraken(candles),
        detection_file=detections,
        outcome_file=outcomes,
        state_file=state,
    )
    assert result["observations_added"] == 1
    row = json.loads(outcomes.read_text().strip())
    assert row["horizon"] == "1h"
    assert row["close_return_pct"] == 3.0
    assert row["mfe_pct"] == 6.0
    assert row["mae_pct"] == -5.0
    assert row["hit_5pct"] is True
    assert row["hit_10pct"] is False
    assert row["sample_candles"] == 3


def test_outcome_observer_never_labels_future_horizon(tmp_path):
    detected = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
    detections = tmp_path / "detections.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "state.json"
    detections.write_text(json.dumps({
        "record_type": "DETECTION",
        "detection_id": "future",
        "symbol": "FASTUSD",
        "direction": "LONG",
        "observed_at": detected.isoformat(),
        "reference_price": 100.0,
    }) + "\n")
    result = observe_due_movement_discovery_outcomes(
        now=detected + timedelta(minutes=30),
        client=_FakeKraken([]),
        detection_file=detections,
        outcome_file=outcomes,
        state_file=state,
    )
    assert result["observations_added"] == 0
    assert result["pending_horizons"] == 4
    assert not outcomes.exists()


def test_legacy_detection_without_timestamp_is_not_backfilled(tmp_path):
    detections = tmp_path / "detections.jsonl"
    detections.write_text(json.dumps({"record_type": "DETECTION", "symbol": "FASTUSD"}) + "\n")
    result = observe_due_movement_discovery_outcomes(
        now=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        client=_FakeKraken([]),
        detection_file=detections,
        outcome_file=tmp_path / "outcomes.jsonl",
        state_file=tmp_path / "state.json",
    )
    assert result["legacy_unobservable"] == 1
    assert result["observations_added"] == 0


def test_recent_detection_window_is_bounded(tmp_path):
    detections = tmp_path / "detections.jsonl"
    detections.write_text(
        "".join(
            json.dumps({"record_type": "DETECTION", "detection_id": f"D{index}"}) + "\n"
            for index in range(5)
        ),
        encoding="utf-8",
    )

    rows = _read_detections(detections, limit=2)

    assert [row["detection_id"] for row in rows] == ["D3", "D4"]


def test_completion_index_capacity_covers_entire_source_window():
    assert MAX_COMPLETED_KEYS >= MAX_SOURCE_DETECTIONS * len(HORIZONS)


def test_outcome_observer_rehydrates_completion_from_existing_ledger(tmp_path):
    detected = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
    detections = tmp_path / "detections.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "state.json"
    detections.write_text(json.dumps({
        "record_type": "DETECTION",
        "detection_id": "already-done",
        "symbol": "FASTUSD",
        "direction": "LONG",
        "observed_at": detected.isoformat(),
        "reference_price": 100.0,
    }) + "\n", encoding="utf-8")
    outcomes.write_text(json.dumps({
        "record_type": "OUTCOME",
        "detection_id": "already-done",
        "horizon": "1h",
    }) + "\n", encoding="utf-8")

    result = observe_due_movement_discovery_outcomes(
        now=detected + timedelta(hours=1, minutes=1),
        client=_NeverCallKraken(),
        detection_file=detections,
        outcome_file=outcomes,
        state_file=state,
    )

    assert result["observations_added"] == 0
    assert result["completion_index_migrated"] is True
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["completion_index_version"] == 2
    assert "already-done:1h" in saved["completed"]
