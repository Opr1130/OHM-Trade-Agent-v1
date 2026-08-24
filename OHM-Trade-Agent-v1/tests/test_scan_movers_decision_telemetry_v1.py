"""Phase 3A call-site wiring in scan_movers.py.

These tests pin that the Phase 3A call site is fail-soft and carries the
immutable decision timestamp without performing Phase 3B OHLC work.
"""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.core.config import Settings
from app.jobs.scan_movers import _maybe_record_decision_telemetry
from app.services.full_market_observation import FullMarketResult
from app.services.signal_scoring import SignalQualityCandidate


BASE_SETTINGS = {"webhook_secret": "test-webhook-secret"}
DECISION_AT = datetime(2026, 8, 24, 18, 7, tzinfo=timezone.utc)


def _settings(**overrides):
    return Settings(**BASE_SETTINGS, **overrides)


def _candidate(**overrides) -> SignalQualityCandidate:
    fields = dict(
        version="v1",
        symbol="testusd",
        stage="BREAKOUT_CANDIDATE",
        pattern="ACCELERATION",
        tradeability_score=70,
        pattern_strength_score=65,
        volume_acceleration_score=80,
        persistence_score=60,
        relative_strength_score=55,
        explosion_potential_score=75,
        opportunity_score=68,
        exhaustion_penalty=0,
        exhaustion_band="NONE",
        liquidity_24h_usd_approx=1_500_000.0,
        persistence_scans=3,
        relative_strength_percentile=0.82,
        universe_size=240,
        reasons=("volume accelerating",),
        components={},
    )
    fields.update(overrides)
    return SignalQualityCandidate(**fields)


def _full_market(**overrides) -> FullMarketResult:
    fields = dict(
        observed_markets=10,
        persisted_events=0,
        transition_alerts=(),
        signal_quality_enabled=True,
        signal_quality_candidates=(_candidate(),),
        signal_quality_reference_prices={"TESTUSD": 55.5},
    )
    fields.update(overrides)
    return FullMarketResult(**fields)


def test_none_full_market_is_a_noop(tmp_path, monkeypatch):
    settings = _settings(decision_telemetry_v1_enabled=True)
    monkeypatch.setattr(
        "app.jobs.scan_movers.record_decision_telemetry",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    _maybe_record_decision_telemetry(None, settings, decision_at=DECISION_AT)


def test_both_flags_off_by_default_writes_nothing(monkeypatch, tmp_path):
    settings = _settings()
    assert settings.decision_telemetry_v1_enabled is False
    assert settings.signal_quality_v1_enabled is False

    calls = []
    from app.services.decision_telemetry import record_decision_telemetry as real_record

    def spy(*args, **kwargs):
        calls.append(kwargs.get("path"))
        return real_record(*args, **kwargs, path=tmp_path / "telemetry.jsonl")

    monkeypatch.setattr("app.jobs.scan_movers.record_decision_telemetry", spy)

    _maybe_record_decision_telemetry(
        _full_market(), settings, decision_at=DECISION_AT
    )

    assert calls
    assert not (tmp_path / "telemetry.jsonl").exists()


def test_internal_failure_never_escapes_the_call_site(monkeypatch, capsys):
    settings = _settings(decision_telemetry_v1_enabled=True)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("app.jobs.scan_movers.record_decision_telemetry", boom)

    _maybe_record_decision_telemetry(
        _full_market(), settings, decision_at=DECISION_AT
    )

    out = capsys.readouterr().out
    assert "Decision telemetry: fail-soft" in out


def test_enabled_flags_write_through_to_disk(tmp_path, monkeypatch):
    settings = _settings(
        decision_telemetry_v1_enabled=True, signal_quality_v1_enabled=True
    )
    target = tmp_path / "telemetry.jsonl"

    from app.services.decision_telemetry import record_decision_telemetry as real_record

    monkeypatch.setattr(
        "app.jobs.scan_movers.record_decision_telemetry",
        lambda candidates, *, settings, reference_prices=None, now=None: real_record(
            candidates,
            settings=settings,
            reference_prices=reference_prices,
            path=target,
            now=now,
        ),
    )

    full_market = _full_market()
    _maybe_record_decision_telemetry(
        full_market, settings, decision_at=DECISION_AT
    )

    assert target.exists()
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert full_market == replace(full_market)


def test_reference_prices_flow_from_full_market_to_the_written_record(tmp_path):
    """The exact same-scan price and decision time must reach Phase 3A."""
    import json

    settings = _settings(
        decision_telemetry_v1_enabled=True, signal_quality_v1_enabled=True
    )
    target = tmp_path / "telemetry.jsonl"
    full_market = _full_market(
        signal_quality_reference_prices={"TESTUSD": 55.5}
    )

    from app.services import decision_telemetry as telemetry_module

    original_default = telemetry_module.DEFAULT_TELEMETRY_FILE
    telemetry_module.DEFAULT_TELEMETRY_FILE = target
    try:
        _maybe_record_decision_telemetry(
            full_market, settings, decision_at=DECISION_AT
        )
    finally:
        telemetry_module.DEFAULT_TELEMETRY_FILE = original_default

    row = json.loads(target.read_text(encoding="utf-8").strip().splitlines()[0])
    assert row["symbol"] == "TESTUSD"
    assert row["price"] == pytest.approx(55.5)
    assert row["recorded_at"] == DECISION_AT.isoformat()
