from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.opip.ml.temporal import TemporalIntegrityError
from app.services.canonical_episode_capture import build_canonical_episode_snapshots
from app.services.opip_ml_evidence_capture import (
    build_ml_snapshot_from_canonical,
    capture_ml_production_evidence,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _observation(symbol: str = "BTCUSD"):
    return SimpleNamespace(
        symbol=symbol,
        base_asset="BTC",
        underlying_asset="BTC",
        kraken_public_symbol="XXBTZUSD",
        ticker_last=100.5,
        ticker_bid=100.4,
        ticker_ask=100.6,
        last_price=100.0,
        volume_24h=1000.0,
        primary_24h_liquidity_usd=2_000_000.0,
        secondary_24h_liquidity_usd=500_000.0,
        combined_24h_liquidity_usd=2_500_000.0,
        recent_24h_high=110.0,
        recent_24h_low=90.0,
        recent_72h_high=120.0,
        recent_72h_low=80.0,
        ema20=101.0,
        ema50=99.0,
        ema200=95.0,
        rsi=58.0,
        macd_line=1.2,
        macd_signal=1.0,
        macd_histogram=0.2,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.4,
        trend="bullish",
        movement_timeframe="1H",
        movement_data_status="AVAILABLE",
        bollinger_bandwidth_pct=4.0,
        bollinger_bandwidth_percentile=35.0,
        atr_percentile=40.0,
        movement_volume_ratio=1.2,
        confirmed_price_change_1h_pct=1.5,
        momentum_6h_pct=3.0,
        momentum_24h_pct=5.0,
        momentum_72h_pct=9.0,
        distance_to_24h_high_pct=8.0,
        distance_to_72h_high_pct=16.0,
        distance_to_24h_low_pct=11.0,
        distance_to_72h_low_pct=25.0,
        realized_range_24h_pct=20.0,
        realized_range_72h_pct=40.0,
        average_hourly_range_24h_pct=1.0,
        average_hourly_range_72h_pct=1.2,
        rolling_24h_range_median_pct=10.0,
        rolling_24h_range_p75_pct=14.0,
        rolling_24h_range_p90_pct=18.0,
        rolling_72h_range_median_pct=25.0,
        rolling_72h_range_p75_pct=30.0,
        rolling_72h_range_p90_pct=35.0,
        rolling_24h_upside_median_pct=5.0,
        rolling_24h_upside_p75_pct=8.0,
        rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0,
        rolling_72h_upside_p75_pct=18.0,
        rolling_72h_upside_p90_pct=25.0,
        rolling_24h_downside_median_pct=4.0,
        rolling_24h_downside_p75_pct=7.0,
        rolling_24h_downside_p90_pct=10.0,
        rolling_72h_downside_median_pct=10.0,
        rolling_72h_downside_p75_pct=15.0,
        rolling_72h_downside_p90_pct=20.0,
        cross_pair_confirmation_status="CONFIRMED",
        cross_pair_price_status="AVAILABLE",
        cross_pair_price_divergence_pct=0.2,
    )


def _canonical_row():
    return build_canonical_episode_snapshots(
        [_observation()],
        candidates=(),
        decision_at=NOW,
        signal_quality_enabled=False,
        scan_source="LIVE_OPPORTUNITY_SCAN",
    )[0]


def _drain_stub(**_kwargs):
    return SimpleNamespace(
        processed=0,
        duplicates=0,
        malformed=0,
        stopped_on_error=False,
        error_type=None,
    )


def test_canonical_seed_captures_raw_features_without_deterministic_outputs():
    row = _canonical_row()
    seed = row["ml_feature_seed"]
    features = seed["feature_values"]

    assert seed["availability_basis"] == "CONSERVATIVE_DECISION_BOUNDARY"
    assert seed["deterministic_outputs_excluded"] is True
    assert features["reference_price"] == pytest.approx(100.5)
    assert features["completed_close"] == pytest.approx(100.0)
    assert features["rsi"] == pytest.approx(58.0)
    assert features["momentum_24h_pct"] == pytest.approx(5.0)
    assert features["ticker_bid"] == pytest.approx(100.4)
    assert features["ticker_ask"] == pytest.approx(100.6)
    assert "opportunity_score" not in features
    assert "decision_status" not in features
    assert "suppressed" not in features


def test_ml_snapshot_uses_conservative_decision_visibility_and_no_source_fabrication():
    row = _canonical_row()
    snapshot = build_ml_snapshot_from_canonical(row)

    assert snapshot.direction == "NONE"
    assert snapshot.lane == "PRODUCTION_SHADOW"
    assert snapshot.decision_at_utc == NOW
    assert snapshot.max_visible_at_utc == NOW
    assert all(item.availability.source_at_utc is None for item in snapshot.features)
    assert all(item.availability.ingested_at_utc == NOW for item in snapshot.features)
    assert all(item.availability.visible_at_utc == NOW for item in snapshot.features)
    assert "opportunity_score" not in snapshot.ml_feature_mapping()
    assert "decision_status" not in snapshot.ml_feature_mapping()


def test_ml_snapshot_rejects_seed_visible_after_decision():
    row = _canonical_row()
    row["ml_feature_seed"]["availability"]["visible_at_utc"] = (
        NOW + timedelta(seconds=1)
    ).isoformat()

    with pytest.raises(TemporalIntegrityError):
        build_ml_snapshot_from_canonical(row)


def test_capture_worker_writes_compressed_snapshot_and_advances_checkpoint(
    tmp_path, monkeypatch
):
    evidence = tmp_path / "p1_evidence.jsonl"
    snapshot_file = tmp_path / "ml.jsonl.gz"
    checkpoint = tmp_path / "checkpoint.json"
    dead = tmp_path / "dead.jsonl"
    health = tmp_path / "health.json"
    evidence.write_text(json.dumps(_canonical_row()) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.services.opip_ml_evidence_capture.drain_outbox_to_evidence_ledger",
        _drain_stub,
    )

    summary = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_path=snapshot_file,
        checkpoint_path=checkpoint,
        dead_letter_path=dead,
        health_path=health,
        enabled=True,
    )

    assert summary.processed == 1
    assert summary.temporal_violations == 0
    assert summary.next_line == 1
    assert json.loads(checkpoint.read_text())["next_line"] == 1

    with gzip.open(snapshot_file, "rt", encoding="utf-8") as handle:
        wrapper = json.loads(handle.readline())
    assert wrapper["record_type"] == "OPIP_ML_FEATURE_SNAPSHOT"
    assert wrapper["canonical_snapshot_id"].startswith("SNAP:")
    assert wrapper["ml_snapshot_id"].startswith("MLSNAP:")
    assert wrapper["feature_snapshot"]["max_visible_at_utc"] == NOW.isoformat()
    assert wrapper["affects_live_decisions"] is False
    assert wrapper["trade_authority_changed"] is False


def test_legacy_canonical_row_is_not_backfilled_with_invented_timestamps(
    tmp_path, monkeypatch
):
    row = _canonical_row()
    row.pop("ml_feature_seed")
    evidence = tmp_path / "p1_evidence.jsonl"
    snapshot_file = tmp_path / "ml.jsonl.gz"
    checkpoint = tmp_path / "checkpoint.json"
    health = tmp_path / "health.json"
    evidence.write_text(json.dumps(row) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.services.opip_ml_evidence_capture.drain_outbox_to_evidence_ledger",
        _drain_stub,
    )

    summary = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_path=snapshot_file,
        checkpoint_path=checkpoint,
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=health,
        enabled=True,
    )

    assert summary.processed == 0
    assert summary.legacy_without_seed == 1
    assert summary.next_line == 1
    assert not snapshot_file.exists()


def test_custom_evidence_path_is_forwarded_to_p1_drain(tmp_path, monkeypatch):
    evidence = tmp_path / "isolated-evidence.jsonl"
    captured = {}

    def drain(**kwargs):
        captured.update(kwargs)
        return _drain_stub()

    monkeypatch.setattr(
        "app.services.opip_ml_evidence_capture.drain_outbox_to_evidence_ledger",
        drain,
    )

    capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_path=tmp_path / "ml.jsonl.gz",
        checkpoint_path=tmp_path / "checkpoint.json",
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=tmp_path / "health.json",
        enabled=True,
    )

    assert captured["evidence_path"] == evidence


def test_retry_after_checkpoint_loss_does_not_duplicate_snapshot(
    tmp_path, monkeypatch
):
    evidence = tmp_path / "p1_evidence.jsonl"
    snapshot_file = tmp_path / "ml.jsonl.gz"
    checkpoint = tmp_path / "checkpoint.json"
    health = tmp_path / "health.json"
    evidence.write_text(json.dumps(_canonical_row()) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.services.opip_ml_evidence_capture.drain_outbox_to_evidence_ledger",
        _drain_stub,
    )

    first = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_path=snapshot_file,
        checkpoint_path=checkpoint,
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=health,
        enabled=True,
    )
    assert first.processed == 1

    # Simulate a lost checkpoint after the compressed snapshot was durable.
    checkpoint.write_text(json.dumps({"next_line": 0}), encoding="utf-8")
    second = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_path=snapshot_file,
        checkpoint_path=checkpoint,
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=health,
        enabled=True,
    )

    assert second.processed == 0
    assert second.duplicate_snapshots_skipped == 1
    assert json.loads(checkpoint.read_text())["next_line"] == 1
    with gzip.open(snapshot_file, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    assert len(rows) == 1


def test_ml_capture_service_has_no_exchange_order_or_position_imports():
    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "opip_ml_evidence_capture.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = ("kraken", "order", "position", "telegram", "execution")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.lower() for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [str(node.module or "").lower()]
        else:
            continue
        for name in names:
            assert not any(fragment in name for fragment in forbidden), name
