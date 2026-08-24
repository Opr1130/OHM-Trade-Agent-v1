import inspect
from datetime import datetime, timezone

from app.core.config import Settings
from app.jobs import scan_movers
from app.services import decision_telemetry
from app.services.signal_scoring import SignalQualityCandidate


BASE_SETTINGS = {"webhook_secret": "test-webhook-secret"}


def _settings(**overrides):
    return Settings(**BASE_SETTINGS, **overrides)


def _candidate(symbol="TESTUSD"):
    return SignalQualityCandidate(
        version="v1",
        symbol=symbol,
        stage="BREAKOUT_CANDIDATE",
        pattern="REACCELERATION",
        tradeability_score=70,
        pattern_strength_score=80,
        volume_acceleration_score=75,
        persistence_score=70,
        relative_strength_score=90,
        explosion_potential_score=85,
        opportunity_score=78,
        exhaustion_penalty=10,
        exhaustion_band="LOW",
        liquidity_24h_usd_approx=2_000_000.0,
        persistence_scans=3,
        relative_strength_percentile=95.0,
        universe_size=200,
        reasons=(),
        components={},
    )


def test_phase3b_shadow_call_occurs_after_telegram_send_sites():
    source = inspect.getsource(scan_movers.main)
    post_shadow = source.rindex("_maybe_record_phase3b_shadow(")

    assert source.index("scan_early_movers()") < post_shadow
    assert source.rindex("send_telegram_message_with_id(") < post_shadow
    assert source.rindex("record_opportunity_alert(") < post_shadow


def test_phase3a_recording_never_invokes_kraken_ohlc(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Phase 3A telemetry must not perform OHLC I/O")

    monkeypatch.setattr(
        decision_telemetry, "collect_phase3b_live_structure", forbidden
    )
    target = tmp_path / "phase3a.jsonl"
    written = decision_telemetry.record_decision_telemetry(
        [_candidate()],
        settings=_settings(decision_telemetry_v1_enabled=True),
        path=target,
    )

    assert written == 1
    assert target.exists()


def test_post_alert_shadow_preserves_original_decision_timestamp(monkeypatch):
    original = datetime(2026, 8, 24, 18, 7, tzinfo=timezone.utc)
    seen = {}

    def fake_collect(rows, *, decision_at, client=None):
        seen["collector_decision_at"] = decision_at
        seen["rows"] = list(rows)
        return {}

    def fake_write(rows, *, reference_prices=None, structure_samples=None, path=None, now=None):
        seen["writer_now"] = now
        seen["reference_prices"] = reference_prices
        seen["structure_samples"] = structure_samples
        return len(list(rows))

    monkeypatch.setattr(
        decision_telemetry, "collect_phase3b_live_structure", fake_collect
    )
    monkeypatch.setattr(
        decision_telemetry, "record_phase3b_shadow_telemetry", fake_write
    )

    written = decision_telemetry.record_phase3b_shadow_for_decision(
        [_candidate()],
        settings=_settings(signal_quality_v1_enabled=True),
        reference_prices={"TESTUSD": 10.5},
        decision_at=original,
    )

    assert written == 1
    assert seen["collector_decision_at"] is original
    assert seen["writer_now"] is original
    assert seen["reference_prices"] == {"TESTUSD": 10.5}
    assert seen["structure_samples"] == {}


def test_post_alert_shadow_is_fail_soft_when_ohlc_collection_raises(monkeypatch):
    original = datetime(2026, 8, 24, 18, 7, tzinfo=timezone.utc)
    seen = {}

    def failing_collect(*args, **kwargs):
        raise TimeoutError("Kraken timeout")

    def fake_write(rows, *, reference_prices=None, structure_samples=None, path=None, now=None):
        seen["structure_samples"] = structure_samples
        return len(list(rows))

    monkeypatch.setattr(
        decision_telemetry, "collect_phase3b_live_structure", failing_collect
    )
    monkeypatch.setattr(
        decision_telemetry, "record_phase3b_shadow_telemetry", fake_write
    )

    written = decision_telemetry.record_phase3b_shadow_for_decision(
        [_candidate()],
        settings=_settings(signal_quality_v1_enabled=True),
        decision_at=original,
    )

    assert written == 1
    assert seen["structure_samples"] == {}
