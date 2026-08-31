import json
from datetime import datetime, timezone

from app.services.phase3b_live_structure import Phase3BStructureSample
from app.services.phase3b_shadow_telemetry import (
    build_phase3b_shadow_record,
    record_phase3b_shadow_telemetry,
)
from app.services.signal_scoring import SignalQualityCandidate
from app.services.technical_structure import TechnicalStructureContext

NOW = datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc)


def candidate(**overrides):
    fields = dict(
        version="v1",
        symbol="TESTUSD",
        stage="BREAKOUT_CANDIDATE",
        pattern="REACCELERATION",
        tradeability_score=65,
        pattern_strength_score=80,
        volume_acceleration_score=75,
        persistence_score=70,
        relative_strength_score=90,
        explosion_potential_score=85,
        opportunity_score=78,
        exhaustion_penalty=20,
        exhaustion_band="MODERATE",
        liquidity_24h_usd_approx=750_000.0,
        persistence_scans=5,
        relative_strength_percentile=95.0,
        universe_size=220,
        reasons=(),
        components={
            "near_high": 87.5,
            "window_run_up_pct": 14.0,
        },
    )
    fields.update(overrides)
    return SignalQualityCandidate(**fields)


def structure_sample(**overrides):
    context = TechnicalStructureContext(
        symbol="TESTUSD",
        observed_at=NOW,
        bias="BULLISH",
        last_swing_high=10.4,
        last_swing_low=9.6,
        bullish_break_level=10.0,
        bearish_break_level=None,
        change_of_character=False,
        imbalance_zone_low=9.9,
        imbalance_zone_high=10.0,
        liquidity_sweep="LOW_SWEEP_RECLAIM",
        retest_state="HELD",
        distance_from_breakout_pct=2.0,
        reasons=("BOS bullish close above 10", "retest: HELD"),
    )
    fields = dict(
        symbol="TESTUSD",
        status="AVAILABLE_COMPLETED_KRAKEN_SPOT_OHLC",
        kraken_pair="TEST/USD",
        interval_minutes=15,
        completed_bar_count=96,
        latest_completed_at=NOW,
        context=context,
    )
    fields.update(overrides)
    return Phase3BStructureSample(**fields)


def test_shadow_record_is_measurement_only_and_never_authoritative():
    row = build_phase3b_shadow_record(
        candidate(), reference_prices={"TESTUSD": 1.25}, now=NOW
    )
    assert row.measurement_only is True
    assert row.advisory_only is True
    assert row.affects_ranking is False
    assert row.affects_telegram is False
    assert row.trade_authority_changed is False
    assert row.production_execution_gate_changed is False


def test_same_scan_reference_price_is_preserved():
    row = build_phase3b_shadow_record(
        candidate(symbol="testusd"), reference_prices={"TESTUSD": 42.5}, now=NOW
    )
    assert row.symbol == "TESTUSD"
    assert row.reference_price == 42.5


def test_near_high_component_is_inverted_only_when_informative():
    row = build_phase3b_shadow_record(
        candidate(components={"near_high": 87.5, "window_run_up_pct": 10}),
        reference_prices={"TESTUSD": 10.0},
        now=NOW,
    )
    assert row.inferred_distance_from_24h_high_pct == 1.0

    unknown = build_phase3b_shadow_record(
        candidate(components={"near_high": 0.0}),
        reference_prices={"TESTUSD": 10.0},
        now=NOW,
    )
    assert unknown.inferred_distance_from_24h_high_pct is None


def test_structure_is_explicitly_unavailable_not_fabricated():
    row = build_phase3b_shadow_record(
        candidate(), reference_prices={"TESTUSD": 2.0}, now=NOW
    )
    assert row.structure_status == "UNAVAILABLE_NO_COMPLETED_OHLC_HISTORY"
    assert row.structure_bias is None
    assert row.bullish_break_level is None
    assert row.bearish_break_level is None
    assert row.retest_state is None
    assert row.retest_available is False


def test_completed_ohlc_structure_is_recorded_and_enriches_chase_context():
    sample = structure_sample()
    row = build_phase3b_shadow_record(
        candidate(),
        reference_prices={"TESTUSD": 10.2},
        structure_samples={"TESTUSD": sample},
        now=NOW,
    )
    assert row.schema_version == 2
    assert row.structure_status == "AVAILABLE_COMPLETED_KRAKEN_SPOT_OHLC"
    assert row.structure_pair == "TEST/USD"
    assert row.structure_interval_minutes == 15
    assert row.structure_completed_bars == 96
    assert row.structure_bias == "BULLISH"
    assert row.bullish_break_level == 10.0
    assert row.breakout_level_used_for_chase == 10.0
    assert row.retest_state == "HELD"
    assert row.retest_available is True
    assert row.liquidity_sweep == "LOW_SWEEP_RECLAIM"
    assert row.structure_reasons


def test_shadow_score_uses_only_point_in_time_available_inputs():
    low = build_phase3b_shadow_record(
        candidate(persistence_scans=1, exhaustion_penalty=0, components={"near_high": 0.0}),
        reference_prices={"TESTUSD": 10.0},
        now=NOW,
    )
    higher = build_phase3b_shadow_record(
        candidate(persistence_scans=6, exhaustion_penalty=35, components={"near_high": 100.0}),
        reference_prices={"TESTUSD": 10.0},
        now=NOW,
    )
    assert higher.chase_risk_score > low.chase_risk_score


def test_suppressed_candidates_are_preserved(tmp_path):
    target = tmp_path / "phase3b.jsonl"
    rows = [
        candidate(symbol="AAAUSD", stage="SUPPRESSED"),
        candidate(symbol="BBBUSD", stage="BREAKOUT_CANDIDATE"),
    ]
    written = record_phase3b_shadow_telemetry(
        rows,
        reference_prices={"AAAUSD": 1.0, "BBBUSD": 2.0},
        path=target,
        now=NOW,
    )
    assert written == 2
    payloads = [json.loads(line) for line in target.read_text().splitlines()]
    assert any(row["symbol"] == "AAAUSD" and row["suppressed"] for row in payloads)


def test_writer_is_fail_soft(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("file")
    target = blocker / "phase3b.jsonl"
    assert record_phase3b_shadow_telemetry([candidate()], path=target, now=NOW) == 0
