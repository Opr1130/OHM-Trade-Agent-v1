"""Phase 3A forward decision telemetry: dark-by-default, fail-soft, one-directional.

These tests pin the three safety properties from SIGNAL_QUALITY_PHASE3A.md:
the flag defaults off and writes nothing while off; a write failure never
raises past ``record_decision_telemetry``; and a written record carries no
execution authority regardless of its contents.
"""

import json

import pytest

from app.core.config import Settings
from app.services.decision_telemetry import (
    DecisionTelemetryRecord,
    build_telemetry_record,
    record_decision_telemetry,
)
from app.services.signal_scoring import STAGE_SUPPRESSED, SignalQualityCandidate


BASE_SETTINGS = {"webhook_secret": "test-webhook-secret"}


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
        reasons=("volume accelerating", "holding near highs"),
        components={},
    )
    fields.update(overrides)
    return SignalQualityCandidate(**fields)


def test_flag_off_writes_nothing(tmp_path):
    settings = _settings(decision_telemetry_v1_enabled=False)
    target = tmp_path / "telemetry.jsonl"

    written = record_decision_telemetry([_candidate()], settings=settings, path=target)

    assert written == 0
    assert not target.exists()


def test_flag_on_writes_one_line_per_candidate(tmp_path):
    settings = _settings(decision_telemetry_v1_enabled=True, signal_quality_v1_enabled=True)
    target = tmp_path / "telemetry.jsonl"
    candidates = [_candidate(symbol="AAAUSD"), _candidate(symbol="BBBUSD")]
    reference_prices = {"AAAUSD": 12.5, "BBBUSD": 0.003}

    written = record_decision_telemetry(
        candidates, settings=settings, reference_prices=reference_prices, path=target
    )

    assert written == 2
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert row["symbol"] == "AAAUSD"
    assert row["price"] == pytest.approx(12.5)
    assert row["schema_version"] == 1
    assert row["scan_source"] == "LIVE"


def test_suppressed_candidates_are_still_recorded(tmp_path):
    """Telemetry must retain the full scored universe, suppressed rows
    included - narrowing it to alert-worthy candidates would make future
    false-positive/false-negative analysis impossible from this data source.
    """
    settings = _settings(decision_telemetry_v1_enabled=True, signal_quality_v1_enabled=True)
    target = tmp_path / "telemetry.jsonl"
    candidates = [
        _candidate(symbol="AAAUSD", stage=STAGE_SUPPRESSED),
        _candidate(symbol="BBBUSD", stage="BREAKOUT_CANDIDATE"),
    ]
    assert candidates[0].suppressed is True

    written = record_decision_telemetry(candidates, settings=settings, path=target)

    assert written == 2
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").strip().splitlines()]
    suppressed_row = next(row for row in rows if row["symbol"] == "AAAUSD")
    assert suppressed_row["suppressed"] is True
    assert suppressed_row["stage"] == STAGE_SUPPRESSED


def test_empty_candidate_list_writes_nothing(tmp_path):
    settings = _settings(decision_telemetry_v1_enabled=True)
    target = tmp_path / "telemetry.jsonl"

    written = record_decision_telemetry([], settings=settings, path=target)

    assert written == 0
    assert not target.exists()


def test_write_failure_is_fail_soft(tmp_path):
    settings = _settings(decision_telemetry_v1_enabled=True)
    # A path whose parent cannot be created (a file, not a directory) forces
    # the write to fail; record_decision_telemetry must swallow it.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    target = blocker / "telemetry.jsonl"

    written = record_decision_telemetry([_candidate()], settings=settings, path=target)

    assert written == 0


def test_record_carries_no_execution_authority():
    settings = _settings(decision_telemetry_v1_enabled=True)
    record = build_telemetry_record(_candidate(), settings=settings)

    assert record.advisory_only is True
    assert record.weights_are_calibrated is False
    assert record.trade_authority_changed is False
    assert record.production_execution_gate_changed is False


def test_price_is_none_when_no_reference_price_is_available():
    settings = _settings(decision_telemetry_v1_enabled=True)
    record = build_telemetry_record(_candidate(), settings=settings)

    assert record.price is None


def test_price_is_read_from_the_reference_prices_mapping():
    """The primary price path: the same-scan price
    FullMarketResult.signal_quality_reference_prices already holds for this
    symbol - not a value read off the candidate, not a new lookup.
    """
    settings = _settings(decision_telemetry_v1_enabled=True)
    candidate = _candidate(symbol="TESTUSD")

    record = build_telemetry_record(
        candidate, settings=settings, reference_prices={"TESTUSD": 42.5}
    )

    assert record.price == pytest.approx(42.5)


def test_reference_prices_mapping_is_keyed_by_upper_cased_symbol():
    settings = _settings(decision_telemetry_v1_enabled=True)
    candidate = _candidate(symbol="testusd")

    record = build_telemetry_record(
        candidate, settings=settings, reference_prices={"TESTUSD": 42.5}
    )

    assert record.symbol == "TESTUSD"
    assert record.price == pytest.approx(42.5)


def test_price_missing_from_mapping_falls_back_to_opportunistic_attribute():
    settings = _settings(decision_telemetry_v1_enabled=True)
    candidate = _candidate(symbol="OTHERUSD")
    # SignalQualityCandidate carries no reference_price field today; simulate
    # a future candidate object that does, to pin the opportunistic fallback.
    object.__setattr__(candidate, "reference_price", 1.2345)

    record = build_telemetry_record(
        candidate, settings=settings, reference_prices={"TESTUSD": 42.5}
    )

    assert record.price == pytest.approx(1.2345)


def test_reference_prices_mapping_takes_priority_over_opportunistic_attribute():
    settings = _settings(decision_telemetry_v1_enabled=True)
    candidate = _candidate(symbol="TESTUSD")
    object.__setattr__(candidate, "reference_price", 999.0)

    record = build_telemetry_record(
        candidate, settings=settings, reference_prices={"TESTUSD": 42.5}
    )

    assert record.price == pytest.approx(42.5)


def test_record_round_trips_key_fields():
    settings = _settings(decision_telemetry_v1_enabled=True, signal_quality_v1_enabled=True)
    candidate = _candidate(stage="ACTIONABLE_REVIEW", persistence_scans=5)

    record = build_telemetry_record(candidate, settings=settings)

    assert record.stage == "ACTIONABLE_REVIEW"
    assert record.persistence_scans == 5
    assert record.signal_quality_enabled is True
    assert isinstance(record, DecisionTelemetryRecord)
    payload = record.as_dict()
    assert payload["reasons"] == list(candidate.reasons)
