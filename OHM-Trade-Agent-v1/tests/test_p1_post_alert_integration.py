from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import decision_telemetry


NOW = datetime(2026, 8, 24, 21, 30, tzinfo=timezone.utc)


def candidate():
    return SimpleNamespace(
        symbol="TESTUSD",
        universe_size=200,
        stage="BREAKOUT_CANDIDATE",
        pattern="REACCELERATION",
        opportunity_score=78,
        explosion_potential_score=74,
        tradeability_score=72,
        pattern_strength_score=80,
        volume_acceleration_score=70,
        relative_strength_score=88,
        persistence_scans=3,
        exhaustion_penalty=10,
        exhaustion_band="LOW",
        relative_strength_percentile=95.0,
        liquidity_24h_usd_approx=2_000_000.0,
        suppressed=False,
        reasons=(),
        components={},
    )


def settings():
    return SimpleNamespace(signal_quality_v1_enabled=True)


def test_post_alert_path_enqueues_before_external_ohlc_and_preserves_timestamp(monkeypatch):
    order = []
    seen = {}

    def enqueue(rows, *, reference_prices=None, decision_at, **kwargs):
        order.append("outbox")
        seen["outbox_time"] = decision_at
        return len(list(rows))

    def collect(rows, *, decision_at, **kwargs):
        order.append("ohlc")
        seen["ohlc_time"] = decision_at
        return {}

    def shadow(rows, *, reference_prices=None, structure_samples=None, now=None, **kwargs):
        order.append("shadow_write")
        seen["shadow_time"] = now
        return len(list(rows))

    monkeypatch.setattr(decision_telemetry, "append_live_scan_snapshots", enqueue)
    monkeypatch.setattr(decision_telemetry, "collect_phase3b_live_structure", collect)
    monkeypatch.setattr(decision_telemetry, "record_phase3b_shadow_telemetry", shadow)

    result = decision_telemetry.record_phase3b_shadow_for_decision(
        [candidate()],
        settings=settings(),
        reference_prices={"TESTUSD": 10.5},
        decision_at=NOW,
    )

    assert result == 1
    assert order == ["outbox", "ohlc", "shadow_write"]
    assert seen["outbox_time"] is NOW
    assert seen["ohlc_time"] is NOW
    assert seen["shadow_time"] is NOW


def test_outbox_failure_is_fail_soft_and_does_not_block_phase3b(monkeypatch):
    calls = []

    def failing(*args, **kwargs):
        calls.append("outbox")
        raise OSError("disk")

    def collect(*args, **kwargs):
        calls.append("ohlc")
        return {}

    def shadow(rows, **kwargs):
        calls.append("shadow")
        return len(list(rows))

    monkeypatch.setattr(decision_telemetry, "append_live_scan_snapshots", failing)
    monkeypatch.setattr(decision_telemetry, "collect_phase3b_live_structure", collect)
    monkeypatch.setattr(decision_telemetry, "record_phase3b_shadow_telemetry", shadow)

    result = decision_telemetry.record_phase3b_shadow_for_decision(
        [candidate()], settings=settings(), decision_at=NOW
    )
    assert result == 1
    assert calls == ["outbox", "ohlc", "shadow"]
