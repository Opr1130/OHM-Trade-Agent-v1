"""Regression tests for the Binance 2026 WebSocket route split hotfix."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.opip.streaming.config import (
    DEFAULT_BINANCE_MARKET_STREAM_URL,
    StreamingWorkerSettings,
)
from app.services.registry_io import save_json_atomic


def _health(*, binance_trades: int, bybit_trades: int, pair_emissions: int) -> dict:
    return {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_failed": False,
        "provider_states": {
            "BINANCE": "CONNECTED",
            "BYBIT": "CONNECTED",
        },
        "publication": {
            "trade_observations_by_provider": {
                "BINANCE": binance_trades,
                "BYBIT": bybit_trades,
            },
            "pair_emissions": pair_emissions,
        },
        "raw_frames_received": binance_trades + bybit_trades,
        "raw_drop_pct": 0.0,
        "provider_buffer_dropped": 0,
        "store_errors": 0,
        "observation_sink_errors": 0,
        "window_sink_errors": 0,
        "invalid_payload_observations": 0,
        "feature_buckets_dropped": 0,
        "feature_snapshots_dropped": 0,
        "features_persisted": 1,
    }


def test_binance_aggtrade_forceorder_worker_uses_market_route(monkeypatch):
    monkeypatch.delenv("OPIP_STREAMING_BINANCE_URL", raising=False)
    settings = StreamingWorkerSettings()
    assert DEFAULT_BINANCE_MARKET_STREAM_URL == (
        "wss://fstream.binance.com/market/stream"
    )
    assert settings.binance_url == DEFAULT_BINANCE_MARKET_STREAM_URL
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert (
        'OPIP_STREAMING_BINANCE_URL: "wss://fstream.binance.com/market/stream"'
        in compose
    )
    assert 'OPIP_STREAMING_BINANCE_URL: "wss://fstream.binance.com/public/stream"' not in compose


def test_activation_rejects_connected_but_silent_binance(tmp_path, monkeypatch):
    import app.opip.streaming.activation_check as module

    health_path = tmp_path / "health.json"
    latest_path = tmp_path / "latest_features.json"
    monkeypatch.setattr(module, "HEALTH_FILE", health_path)
    monkeypatch.setattr(module, "LATEST_FEATURES_FILE", latest_path)
    save_json_atomic(
        latest_path,
        {"assets": {"bitcoin": {"liquidation_confirmable": False}}},
    )

    save_json_atomic(
        health_path,
        _health(binance_trades=0, bybit_trades=100, pair_emissions=1),
    )
    assert module.main() == 1


def test_activation_requires_real_both_venue_trade_flow_and_pair_emission(
    tmp_path, monkeypatch
):
    import app.opip.streaming.activation_check as module

    health_path = tmp_path / "health.json"
    latest_path = tmp_path / "latest_features.json"
    monkeypatch.setattr(module, "HEALTH_FILE", health_path)
    monkeypatch.setattr(module, "LATEST_FEATURES_FILE", latest_path)
    save_json_atomic(
        latest_path,
        {"assets": {"bitcoin": {"liquidation_confirmable": False}}},
    )

    save_json_atomic(
        health_path,
        _health(binance_trades=10, bybit_trades=100, pair_emissions=0),
    )
    assert module.main() == 1

    save_json_atomic(
        health_path,
        _health(binance_trades=10, bybit_trades=100, pair_emissions=1),
    )
    assert module.main() == 0
