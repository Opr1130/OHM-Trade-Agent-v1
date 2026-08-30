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
import app.services.opip_ml_evidence_capture as capture_module
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


def _canonical_row(
    *,
    decision_at: datetime = NOW,
    scan_source: str = "LIVE_OPPORTUNITY_SCAN",
):
    return build_canonical_episode_snapshots(
        [_observation()],
        candidates=(),
        decision_at=decision_at,
        signal_quality_enabled=False,
        scan_source=scan_source,
    )[0]


def _drain_stub(**_kwargs):
    return SimpleNamespace(
        processed=0,
        duplicates=0,
        malformed=0,
        stopped_on_error=False,
        error_type=None,
    )


def _published_chunks(snapshot_dir: Path) -> list[Path]:
    return sorted(snapshot_dir.glob("chunk-*.jsonl.gz"))


def _read_chunk(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_canonical_seed_preserves_source_specific_price_semantics():
    opportunity = _canonical_row(scan_source="LIVE_OPPORTUNITY_SCAN")
    opportunity_features = opportunity["ml_feature_seed"]["feature_values"]
    assert opportunity_features["reference_price"] == pytest.approx(100.5)
    assert opportunity_features["ticker_last"] == pytest.approx(100.5)
    assert opportunity_features["completed_close"] == pytest.approx(100.0)

    full_market = _canonical_row(scan_source="LIVE_FULL_MARKET")
    full_market_features = full_market["ml_feature_seed"]["feature_values"]
    assert full_market_features["reference_price"] == pytest.approx(100.0)
    assert full_market_features["ticker_last"] == pytest.approx(100.0)
    assert full_market_features["completed_close"] is None


def test_full_market_seed_preserves_raw_volume_liquidity_and_range_aliases():
    observation = _observation()
    observation.primary_24h_liquidity_usd = None
    observation.combined_24h_liquidity_usd = None
    observation.recent_24h_high = None
    observation.recent_24h_low = None
    observation.notional_24h_usd_approx = 2_750_000.0
    observation.high_24h = 111.0
    observation.low_24h = 89.0

    row = build_canonical_episode_snapshots(
        [observation],
        candidates=(),
        decision_at=NOW,
        signal_quality_enabled=False,
        scan_source="LIVE_FULL_MARKET",
    )[0]
    features = row["ml_feature_seed"]["feature_values"]

    assert features["volume_24h"] == pytest.approx(1000.0)
    assert features["primary_24h_liquidity_usd"] == pytest.approx(2_750_000.0)
    assert features["combined_24h_liquidity_usd"] == pytest.approx(2_750_000.0)
    assert features["recent_24h_high"] == pytest.approx(111.0)
    assert features["recent_24h_low"] == pytest.approx(89.0)
    assert features["completed_close"] is None


def test_canonical_seed_captures_raw_features_without_deterministic_outputs():
    row = _canonical_row()
    seed = row["ml_feature_seed"]
    features = seed["feature_values"]

    assert seed["availability_basis"] == "CONSERVATIVE_DECISION_BOUNDARY"
    assert seed["deterministic_outputs_excluded"] is True
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


def test_capture_worker_writes_atomic_compressed_chunk_and_advances_byte_checkpoint(
    tmp_path, monkeypatch
):
    evidence = tmp_path / "p1_evidence.jsonl"
    snapshot_dir = tmp_path / "ml-snapshots"
    checkpoint = tmp_path / "checkpoint.json"
    dead = tmp_path / "dead.jsonl"
    health = tmp_path / "health.json"
    evidence.write_text(json.dumps(_canonical_row()) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        capture_module,
        "drain_outbox_to_evidence_ledger",
        _drain_stub,
    )

    summary = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_dir=snapshot_dir,
        checkpoint_path=checkpoint,
        dead_letter_path=dead,
        health_path=health,
        enabled=True,
    )

    assert summary.processed == 1
    assert summary.temporal_violations == 0
    assert summary.next_line == 1
    assert summary.byte_offset == evidence.stat().st_size
    saved = json.loads(checkpoint.read_text())
    assert saved["next_line"] == 1
    assert saved["byte_offset"] == evidence.stat().st_size

    chunks = _published_chunks(snapshot_dir)
    assert len(chunks) == 1
    rows = _read_chunk(chunks[0])
    assert len(rows) == 1
    wrapper = rows[0]
    assert wrapper["record_type"] == "OPIP_ML_FEATURE_SNAPSHOT"
    assert wrapper["canonical_snapshot_id"].startswith("SNAP:")
    assert wrapper["ml_snapshot_id"].startswith("MLSNAP:")
    assert wrapper["feature_snapshot"]["max_visible_at_utc"] == NOW.isoformat()
    assert wrapper["affects_live_decisions"] is False
    assert wrapper["trade_authority_changed"] is False
    assert not list(snapshot_dir.glob("*.tmp"))


def test_legacy_canonical_row_is_not_backfilled_with_invented_timestamps(
    tmp_path, monkeypatch
):
    row = _canonical_row()
    row.pop("ml_feature_seed")
    evidence = tmp_path / "p1_evidence.jsonl"
    snapshot_dir = tmp_path / "ml-snapshots"
    checkpoint = tmp_path / "checkpoint.json"
    health = tmp_path / "health.json"
    evidence.write_text(json.dumps(row) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        capture_module,
        "drain_outbox_to_evidence_ledger",
        _drain_stub,
    )

    summary = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_dir=snapshot_dir,
        checkpoint_path=checkpoint,
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=health,
        enabled=True,
    )

    assert summary.processed == 0
    assert summary.legacy_without_seed == 1
    assert summary.next_line == 1
    assert not snapshot_dir.exists()


def test_custom_evidence_path_does_not_consume_production_p1_outbox(
    tmp_path, monkeypatch
):
    evidence = tmp_path / "isolated-evidence.jsonl"
    called = False

    def drain(**_kwargs):
        nonlocal called
        called = True
        return _drain_stub()

    monkeypatch.setattr(
        capture_module,
        "drain_outbox_to_evidence_ledger",
        drain,
    )

    summary = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_dir=tmp_path / "ml-snapshots",
        checkpoint_path=tmp_path / "checkpoint.json",
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=tmp_path / "health.json",
        enabled=True,
    )

    assert called is False
    assert summary.p1_drained == 0


def test_retry_after_checkpoint_loss_reuses_atomic_chunk_without_duplicate(
    tmp_path, monkeypatch
):
    evidence = tmp_path / "p1_evidence.jsonl"
    snapshot_dir = tmp_path / "ml-snapshots"
    checkpoint = tmp_path / "checkpoint.json"
    health = tmp_path / "health.json"
    evidence.write_text(json.dumps(_canonical_row()) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        capture_module,
        "drain_outbox_to_evidence_ledger",
        _drain_stub,
    )

    first = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_dir=snapshot_dir,
        checkpoint_path=checkpoint,
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=health,
        enabled=True,
    )
    assert first.processed == 1
    assert len(_published_chunks(snapshot_dir)) == 1

    # Simulate a lost checkpoint after the atomic chunk rename was durable.
    checkpoint.write_text(
        json.dumps({"schema_version": 1, "next_line": 0, "byte_offset": 0}),
        encoding="utf-8",
    )
    second = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_dir=snapshot_dir,
        checkpoint_path=checkpoint,
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=health,
        enabled=True,
    )

    assert second.processed == 0
    assert second.duplicate_snapshots_skipped == 1
    assert len(_published_chunks(snapshot_dir)) == 1
    assert json.loads(checkpoint.read_text())["next_line"] == 1


def test_interrupted_unpublished_temp_chunk_does_not_poison_future_capture(
    tmp_path, monkeypatch
):
    evidence = tmp_path / "p1_evidence.jsonl"
    snapshot_dir = tmp_path / "ml-snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / ".interrupted.jsonl.gz.tmp").write_bytes(b"truncated-gzip")
    evidence.write_text(json.dumps(_canonical_row()) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        capture_module,
        "drain_outbox_to_evidence_ledger",
        _drain_stub,
    )

    summary = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_dir=snapshot_dir,
        checkpoint_path=tmp_path / "checkpoint.json",
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=tmp_path / "health.json",
        enabled=True,
    )

    assert summary.processed == 1
    chunks = _published_chunks(snapshot_dir)
    assert len(chunks) == 1
    assert len(_read_chunk(chunks[0])) == 1


def test_second_capture_starts_from_durable_byte_offset_not_ledger_origin(
    tmp_path, monkeypatch
):
    evidence = tmp_path / "p1_evidence.jsonl"
    snapshot_dir = tmp_path / "ml-snapshots"
    checkpoint = tmp_path / "checkpoint.json"
    health = tmp_path / "health.json"
    first_row = _canonical_row()
    evidence.write_text(json.dumps(first_row) + "\n", encoding="utf-8")
    first_size = evidence.stat().st_size

    monkeypatch.setattr(
        capture_module,
        "drain_outbox_to_evidence_ledger",
        _drain_stub,
    )

    first = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_dir=snapshot_dir,
        checkpoint_path=checkpoint,
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=health,
        enabled=True,
    )
    assert first.byte_offset == first_size

    second_row = _canonical_row(decision_at=NOW + timedelta(minutes=1))
    with evidence.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(second_row) + "\n")

    original_reader = capture_module._read_complete_batch
    observed = {}

    def read_from_checkpoint(path, *, next_line, byte_offset, batch_limit):
        observed["byte_offset"] = byte_offset
        return original_reader(
            path,
            next_line=next_line,
            byte_offset=byte_offset,
            batch_limit=batch_limit,
        )

    monkeypatch.setattr(capture_module, "_read_complete_batch", read_from_checkpoint)
    second = capture_ml_production_evidence(
        evidence_path=evidence,
        snapshot_dir=snapshot_dir,
        checkpoint_path=checkpoint,
        dead_letter_path=tmp_path / "dead.jsonl",
        health_path=health,
        enabled=True,
    )

    assert observed["byte_offset"] == first_size
    assert second.batch_rows == 1
    assert second.processed == 1
    assert len(_published_chunks(snapshot_dir)) == 2


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
