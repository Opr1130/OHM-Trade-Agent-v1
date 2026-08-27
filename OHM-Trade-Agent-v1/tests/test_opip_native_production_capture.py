from datetime import datetime, timezone
from types import SimpleNamespace

from app.jobs import scan_opportunities
from app.services.canonical_episode_capture import build_canonical_episode_snapshots


NOW = datetime(2026, 8, 27, 5, 0, tzinfo=timezone.utc)


def test_native_market_snapshot_maps_to_canonical_episode_fields():
    snapshot = SimpleNamespace(
        symbol="BTCUSD",
        underlying_asset="BTC",
        kraken_public_symbol="BTC/USD",
        last_price=65000.0,
        combined_24h_liquidity_usd=25000000.0,
        primary_24h_liquidity_usd=20000000.0,
        recent_24h_high=67000.0,
        recent_24h_low=62000.0,
        distance_to_24h_high_pct=2.985,
    )

    rows = build_canonical_episode_snapshots(
        [snapshot],
        candidates=(),
        decision_at=NOW,
        signal_quality_enabled=False,
        scan_source="LIVE_OPPORTUNITY_SCAN",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "BTCUSD"
    assert row["base_asset"] == "BTC"
    assert row["kraken_public_symbol"] == "BTC/USD"
    assert row["reference_price"] == 65000.0
    assert row["liquidity_24h_usd_approx"] == 25000000.0
    assert row["high_24h"] == 67000.0
    assert row["low_24h"] == 62000.0
    assert row["distance_from_24h_high_pct"] == 2.985
    assert row["scan_source"] == "LIVE_OPPORTUNITY_SCAN"
    assert row["decision_status"] == "NOT_SCORED"
    assert row["measurement_only"] is True
    assert row["affects_ranking"] is False
    assert row["affects_telegram"] is False
    assert row["affects_pending_setup"] is False
    assert row["trade_authority_changed"] is False


def test_production_scan_capture_hook_uses_native_snapshots_and_is_fail_soft(monkeypatch):
    snapshot = SimpleNamespace(symbol="ETHUSD", last_price=3000.0)
    scan = SimpleNamespace(snapshots=[snapshot])
    calls = []

    def capture(observations, **kwargs):
        calls.append((list(observations), kwargs))
        return 1

    monkeypatch.setattr(
        scan_opportunities,
        "append_canonical_episode_snapshots",
        capture,
    )

    written = scan_opportunities._capture_native_scan_cohort(
        scan,
        decision_at=NOW,
    )

    assert written == 1
    assert calls[0][0] == [snapshot]
    assert calls[0][1]["candidates"] == ()
    assert calls[0][1]["signal_quality_enabled"] is False
    assert calls[0][1]["scan_source"] == "LIVE_OPPORTUNITY_SCAN"

    def explode(*args, **kwargs):
        raise RuntimeError("disk unavailable")

    monkeypatch.setattr(
        scan_opportunities,
        "append_canonical_episode_snapshots",
        explode,
    )

    assert scan_opportunities._capture_native_scan_cohort(
        scan,
        decision_at=NOW,
    ) == 0
