"""Sequence 4 BUILD 4.5 production shadow integration tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.streaming.contract import EvidenceQualityState, StreamProvider
from app.opip.streaming.feature_accumulator import StreamingFeatureSnapshot
from app.opip.streaming.read_model import read_streaming_shadow_status
from app.opip.streaming.runtime import StreamingRuntime
from app.opip.streaming.store import StreamingShadowStore
from app.opip.streaming.worker import _parse_symbols
from app.services.registry_io import load_json, save_json_atomic
from tests.test_opip_streaming_runtime_v1 import (
    FakeAdapter,
    NOW as RUNTIME_NOW,
    _queued,
    _raw,
    _runtime_config,
)
from app.opip.streaming.adapter import QueuedRawFrame


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _feature(asset="bitcoin", start=NOW):
    return StreamingFeatureSnapshot(
        canonical_asset_id=asset,
        window_start_utc=start,
        window_end_utc=start + timedelta(seconds=15),
        cvd_signed_notional_usd=1000.0,
        per_venue_cvd_notional_usd={"BINANCE": 600.0, "BYBIT": 400.0},
        venue_agreement="ALIGNED_POSITIVE",
        evidence_quality="COMPLETE",
        degradations=(),
        liquidation_long_notional_usd=0.0,
        liquidation_short_notional_usd=0.0,
        liquidation_unknown_notional_usd=0.0,
        liquidation_sync_state="INSUFFICIENT_EVIDENCE",
        liquidation_venues=(),
        liquidation_evidence_quality="INCOMPLETE",
        liquidation_degradations=("EMPTY_WINDOW",),
        liquidation_confirmable=False,
    )


def test_shadow_store_rotates_hourly_and_preserves_latest_assets(tmp_path):
    store = StreamingShadowStore(base_dir=tmp_path, retention_hours=72)
    assert store.append_features([_feature("bitcoin")]) == 1
    assert store.append_features([_feature("ethereum")]) == 1

    files = sorted(tmp_path.glob("features-*.jsonl"))
    assert len(files) == 1
    assert files[0].name == "features-20260830T12.jsonl"
    assert len(files[0].read_text(encoding="utf-8").splitlines()) == 2

    latest = load_json(tmp_path / "latest_features.json")
    assert set(latest["assets"]) == {"bitcoin", "ethereum"}
    assert not list(tmp_path.glob("*raw*"))


def test_shadow_store_prunes_only_expired_hour_files(tmp_path):
    store = StreamingShadowStore(base_dir=tmp_path, retention_hours=72)
    old = tmp_path / "features-20260825T00.jsonl"
    recent = tmp_path / "features-20260830T11.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    recent.write_text("{}\n", encoding="utf-8")
    removed = store.prune(now_utc=NOW)
    assert removed == 1
    assert not old.exists()
    assert recent.exists()


def test_read_model_is_explicitly_non_authoritative(tmp_path):
    health = tmp_path / "health.json"
    telemetry = tmp_path / "telemetry.json"
    latest = tmp_path / "latest.json"
    save_json_atomic(health, {"status": "RUNNING"})
    save_json_atomic(telemetry, {"runtime": {"frames_processed": 10}})
    save_json_atomic(latest, {"assets": {"bitcoin": {}}})
    result = read_streaming_shadow_status(
        health_path=health,
        telemetry_path=telemetry,
        latest_features_path=latest,
    )
    assert result["authoritative"] is False
    assert result["can_trade"] is False
    assert result["can_change_policy"] is False


def test_healthcheck_requires_fresh_running_heartbeat(tmp_path, monkeypatch):
    import app.opip.streaming.healthcheck as module

    path = tmp_path / "health.json"
    monkeypatch.setattr(module, "HEALTH_FILE", path)
    save_json_atomic(
        path,
        {
            "status": "RUNNING",
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "runtime_failed": False,
        },
    )
    assert module.main() == 0
    save_json_atomic(
        path,
        {
            "status": "RUNNING",
            "updated_at_utc": (
                datetime.now(timezone.utc) - timedelta(minutes=2)
            ).isoformat(),
            "runtime_failed": False,
        },
    )
    assert module.main() == 1


def test_worker_initial_universe_is_explicitly_bound():
    assert _parse_symbols("BTCUSDT,ETHUSDT,SOLUSDT") == (
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
    )
    with pytest.raises(ValueError):
        _parse_symbols("BTCUSDT,XRPUSDT")


def test_queue_drop_is_attributed_to_matching_windows():
    adapter = FakeAdapter([])
    runtime = StreamingRuntime(
        {StreamProvider.BINANCE: adapter},
        config=_runtime_config(queue_maxsize=1),
        utc_now=lambda: RUNTIME_NOW + timedelta(milliseconds=10),
    )
    runtime._accepting = True
    runtime._epochs[StreamProvider.BINANCE] = 0

    first = _queued(epoch=0)
    second = QueuedRawFrame(
        provider=StreamProvider.BINANCE,
        frame=_raw(2),
        connection_id="binance-0",
        reconnect_epoch=0,
        received_monotonic=2.0,
        ingest_timestamp_utc=RUNTIME_NOW,
    )
    assert runtime._enqueue_if_current(first) is True
    assert runtime._enqueue_if_current(second) is False

    normalized = adapter.normalize(first)
    assert runtime._record_windows(
        normalized,
        now_utc=RUNTIME_NOW + timedelta(milliseconds=10),
    ) is True
    assert runtime._windows
    assert all(
        window.dropped_frame_count == 1
        for window in runtime._windows.values()
    )


def test_late_after_seal_never_reaches_feature_acceptance_and_degrades_quality():
    notices = []
    adapter = FakeAdapter([])
    runtime = StreamingRuntime(
        {StreamProvider.BINANCE: adapter},
        config=_runtime_config(
            sealed_window_retention_seconds=1.0,
            window_grace_seconds=0.001,
        ),
        utc_now=lambda: RUNTIME_NOW,
        sealed_window_sink=notices.append,
    )
    runtime._epochs[StreamProvider.BINANCE] = 0

    first = _queued(epoch=0)
    normalized = adapter.normalize(first)
    assert runtime._record_windows(
        normalized,
        now_utc=RUNTIME_NOW + timedelta(milliseconds=10),
    ) is True
    final_end = max(
        window.bounds.end_utc for window in runtime._windows.values()
    )
    seal_at = final_end + timedelta(milliseconds=2)
    runtime._seal_and_prune(seal_at)
    assert all(notice.window_seconds == 1 for notice in notices)
    notices.clear()

    late = QueuedRawFrame(
        provider=StreamProvider.BINANCE,
        frame=_raw(2),
        connection_id="binance-0",
        reconnect_epoch=0,
        received_monotonic=2.0,
        ingest_timestamp_utc=RUNTIME_NOW,
    )
    late_normalized = adapter.normalize(late)
    assert runtime._record_windows(
        late_normalized,
        now_utc=seal_at,
    ) is False

    runtime._seal_and_prune(
        final_end + timedelta(seconds=2)
    )
    notices_15s = [
        notice for notice in notices if notice.window_seconds == 15
    ]
    assert len(notices_15s) == 1
    assert (
        notices_15s[0].quality.state
        is EvidenceQualityState.DEGRADED
    )


def test_far_future_provider_timestamp_fails_closed():
    adapter = FakeAdapter([])
    runtime = StreamingRuntime(
        {StreamProvider.BINANCE: adapter},
        config=_runtime_config(provider_future_tolerance_seconds=1.0),
        utc_now=lambda: RUNTIME_NOW,
    )
    queued = _queued(epoch=0)
    normalized = adapter.normalize(queued)
    future_envelope = replace(
        normalized.envelope,
        provider_timestamp_utc=RUNTIME_NOW + timedelta(seconds=10),
    )
    future_normalized = normalized.__class__(
        envelope=future_envelope,
        sequence=normalized.sequence,
    )
    with pytest.raises(ValueError):
        runtime._record_windows(future_normalized, now_utc=RUNTIME_NOW)


def test_activation_check_requires_live_cross_venue_feature(tmp_path, monkeypatch):
    import app.opip.streaming.activation_check as module

    health = tmp_path / "health.json"
    latest = tmp_path / "latest_features.json"
    monkeypatch.setattr(module, "HEALTH_FILE", health)
    monkeypatch.setattr(module, "LATEST_FEATURES_FILE", latest)

    save_json_atomic(
        health,
        {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "runtime_failed": False,
            "provider_states": {
                "BINANCE": "CONNECTED",
                "BYBIT": "CONNECTED",
            },
            "raw_frames_received": 100,
            "raw_drop_pct": 0.0,
            "store_errors": 0,
            "observation_sink_errors": 0,
            "window_sink_errors": 0,
            "feature_buckets_dropped": 0,
            "feature_snapshots_dropped": 0,
            "features_persisted": 1,
        },
    )
    save_json_atomic(
        latest,
        {
            "assets": {
                "bitcoin": {
                    "liquidation_confirmable": False,
                }
            }
        },
    )
    assert module.main() == 0

    save_json_atomic(
        health,
        {
            **load_json(health),
            "provider_states": {
                "BINANCE": "CONNECTED",
                "BYBIT": "BACKOFF",
            },
        },
    )
    assert module.main() == 1


def test_activation_check_blocks_high_drop_or_confirming_liquidation(
    tmp_path, monkeypatch
):
    import app.opip.streaming.activation_check as module

    health = tmp_path / "health.json"
    latest = tmp_path / "latest_features.json"
    monkeypatch.setattr(module, "HEALTH_FILE", health)
    monkeypatch.setattr(module, "LATEST_FEATURES_FILE", latest)

    base = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_failed": False,
        "provider_states": {
            "BINANCE": "CONNECTED",
            "BYBIT": "CONNECTED",
        },
        "raw_frames_received": 100,
        "raw_drop_pct": 1.01,
        "store_errors": 0,
        "observation_sink_errors": 0,
        "window_sink_errors": 0,
        "feature_buckets_dropped": 0,
        "feature_snapshots_dropped": 0,
        "features_persisted": 1,
    }
    save_json_atomic(health, base)
    save_json_atomic(
        latest,
        {"assets": {"bitcoin": {"liquidation_confirmable": False}}},
    )
    assert module.main() == 1

    base["raw_drop_pct"] = 0.0
    save_json_atomic(health, base)
    save_json_atomic(
        latest,
        {"assets": {"bitcoin": {"liquidation_confirmable": True}}},
    )
    assert module.main() == 1


def test_drop_after_seal_degrades_retained_window_and_next_window():
    notices = []
    adapter = FakeAdapter([])
    runtime = StreamingRuntime(
        {StreamProvider.BINANCE: adapter},
        config=_runtime_config(
            queue_maxsize=1,
            sealed_window_retention_seconds=2.0,
            window_grace_seconds=0.001,
            window_seconds=(15,),
        ),
        utc_now=lambda: RUNTIME_NOW,
        sealed_window_sink=notices.append,
    )
    runtime._accepting = True
    runtime._epochs[StreamProvider.BINANCE] = 0

    first = _queued(epoch=0)
    normalized = adapter.normalize(first)
    assert runtime._record_windows(
        normalized,
        now_utc=RUNTIME_NOW + timedelta(milliseconds=10),
    ) is True
    window = next(iter(runtime._windows.values()))
    runtime._seal_and_prune(
        window.bounds.end_utc + timedelta(milliseconds=2)
    )
    assert next(iter(runtime._windows.values())).sealed is True

    assert runtime._enqueue_if_current(first) is True
    dropped = QueuedRawFrame(
        provider=StreamProvider.BINANCE,
        frame=_raw(2),
        connection_id="binance-0",
        reconnect_epoch=0,
        received_monotonic=2.0,
        ingest_timestamp_utc=RUNTIME_NOW,
    )
    assert runtime._enqueue_if_current(dropped) is False
    retained = next(iter(runtime._windows.values()))
    assert retained.dropped_frame_count == 1
    assert runtime._pending_drops

    runtime._seal_and_prune(
        window.bounds.end_utc + timedelta(seconds=3)
    )
    notices_15s = [
        notice for notice in notices if notice.window_seconds == 15
    ]
    assert len(notices_15s) == 1
    assert notices_15s[0].quality.state is EvidenceQualityState.INCOMPLETE


def test_feature_persistence_retry_is_idempotent_after_latest_write_failure(
    tmp_path, monkeypatch
):
    import app.opip.streaming.store as module

    store = StreamingShadowStore(base_dir=tmp_path, retention_hours=72)
    real_save = module.save_json_atomic
    calls = {"count": 0}

    def fail_latest_once(path, data, *, mode=None):
        calls["count"] += 1
        if path.name == "latest_features.json" and calls["count"] == 1:
            raise OSError("simulated latest read-model failure")
        return real_save(path, data, mode=mode)

    monkeypatch.setattr(module, "save_json_atomic", fail_latest_once)
    row = _feature("bitcoin")

    with pytest.raises(OSError):
        store.append_features([row])

    hourly = next(tmp_path.glob("features-*.jsonl"))
    first_lines = hourly.read_text(encoding="utf-8").splitlines()
    assert len(first_lines) == 1
    assert '"feature_id":"SF1:' in first_lines[0]

    assert store.append_features([row]) == 1
    second_lines = hourly.read_text(encoding="utf-8").splitlines()
    assert second_lines == first_lines
    latest = load_json(tmp_path / "latest_features.json")
    assert latest["assets"]["bitcoin"]["feature_id"].startswith("SF1:")


def test_feature_persistence_deduplicates_same_logical_window_in_one_batch(tmp_path):
    store = StreamingShadowStore(base_dir=tmp_path, retention_hours=72)
    row = _feature("bitcoin")
    assert store.append_features([row, row]) == 2
    hourly = next(tmp_path.glob("features-*.jsonl"))
    assert len(hourly.read_text(encoding="utf-8").splitlines()) == 1
