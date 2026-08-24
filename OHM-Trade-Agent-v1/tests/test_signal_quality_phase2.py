"""Signal Quality Phase 2 — replay, episode and evaluation invariants.

These tests pin the three properties the analysis depends on: no lookahead,
post-detection-only credit, and one episode per explosive run. They are
deterministic and offline; no test reaches the network.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.signal_features import ObservationSnapshot
from app.services.signal_quality_phase2 import (
    FORENSIC_THRESHOLDS,
    CandidateRow,
    EpisodeConfig,
    IngestionResult,
    KrakenPublicOhlcProvider,
    NullOhlcProvider,
    OhlcCandle,
    Phase2Config,
    SymbolTimeline,
    build_all_episodes,
    build_episodes,
    build_phase2_report,
    build_timelines,
    chronological_split,
    evaluate_detections,
    evaluate_episode_detection,
    missed_winner_snapshots,
    read_observations,
    reconstruct_scan_frames,
    replay_signal_quality,
    run_phase2_replay,
    validate_episodes_with_ohlc,
    ReplayObservation,
)

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)
HORIZON = timedelta(hours=24)


def _snapshot(at, price, *, notional=2_000_000.0, low=None, high=None):
    low = min(100.0, price) if low is None else low
    high = price if high is None else high
    return ObservationSnapshot(
        observed_at=at,
        last_price=price,
        volume_24h=notional / price,
        notional_24h_usd_approx=notional,
        high_24h=high,
        low_24h=low,
        lift_from_24h_low_pct=(price / low - 1.0) * 100.0 if low > 0 else 0.0,
        distance_from_24h_high_pct=max(0.0, (high - price) / price * 100.0),
    )


def _obs(minutes, price, symbol="TESTUSD", **kwargs):
    at = BASE + timedelta(minutes=minutes)
    return ReplayObservation(at, symbol, _snapshot(at, price, **kwargs))


def _detection(minutes, price, *, symbol="TESTUSD", stage="EARLY_BUILDING", explosion=70,
               opportunity=60, liquidity=2_000_000.0, persistence=2, exhaustion=0):
    return CandidateRow(
        scan_at=BASE + timedelta(minutes=minutes),
        symbol=symbol,
        stage=stage,
        price=price,
        imputed_input=False,
        opportunity_score=opportunity,
        explosion_potential_score=explosion,
        tradeability_score=90,
        pattern_strength_score=70,
        volume_acceleration_score=60,
        relative_strength_score=80,
        persistence_scans=persistence,
        exhaustion_penalty=exhaustion,
        liquidity_24h_usd_approx=liquidity,
        pattern="REACCELERATION",
        reasons=(),
    )


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def test_ingestion_rejects_malformed_rows_without_failing(tmp_path):
    path = tmp_path / "obs.jsonl"
    path.write_text(
        "\n".join([
            '{"record_type":"FULL_MARKET_OBSERVATION","observed_at":"2026-08-01T00:00:00+00:00",'
            '"symbol":"AUSD","last_price":10,"volume_24h":100,"notional_24h_usd_approx":1000,'
            '"high_24h":10,"low_24h":9,"lift_from_24h_low_pct":11.1,"distance_from_24h_high_pct":0}',
            "not json at all",
            '{"record_type":"SOMETHING_ELSE"}',
            '{"record_type":"FULL_MARKET_OBSERVATION","observed_at":"nope","symbol":"BUSD"}',
            '{"record_type":"FULL_MARKET_OBSERVATION","observed_at":"2026-08-01T00:10:00+00:00",'
            '"symbol":"CUSD","last_price":0,"volume_24h":1,"notional_24h_usd_approx":1,'
            '"high_24h":1,"low_24h":1,"lift_from_24h_low_pct":0,"distance_from_24h_high_pct":0}',
        ]),
        encoding="utf-8",
    )
    result = read_observations(path)

    assert len(result.observations) == 1
    assert result.observations[0].symbol == "AUSD"
    assert result.rejected_lines == 4
    assert result.total_lines == 5


def test_missing_observation_file_is_not_an_error(tmp_path):
    result = read_observations(tmp_path / "absent.jsonl")
    assert result.observations == []
    assert result.total_lines == 0


# ---------------------------------------------------------------------------
# Event-sampling neutrality and no-lookahead
# ---------------------------------------------------------------------------


def test_fixed_grid_neutralises_dense_event_sampling():
    """Scan count must depend on elapsed time, never on event density."""
    sparse = [_obs(1, 100), _obs(59, 101)]
    dense = sparse + [_obs(11, 100.1), _obs(12, 100.2), _obs(13, 100.3), _obs(14, 100.4)]

    sparse_frames = reconstruct_scan_frames(sparse, interval_seconds=600, max_carry_seconds=3600)
    dense_frames = reconstruct_scan_frames(dense, interval_seconds=600, max_carry_seconds=3600)

    assert [f.scan_at for f in sparse_frames] == [f.scan_at for f in dense_frames]
    assert len(sparse_frames) == len(dense_frames)


def test_no_future_row_can_enter_an_earlier_frame():
    rows = [_obs(1, 100), _obs(25, 120)]
    frames = reconstruct_scan_frames(rows, interval_seconds=600, max_carry_seconds=3600)
    by_time = {f.scan_at: f for f in frames}

    at_10 = BASE + timedelta(minutes=10)
    at_20 = BASE + timedelta(minutes=20)
    at_30 = BASE + timedelta(minutes=30)

    # The 120 print at minute 25 is invisible until the 30-minute scan.
    assert by_time[at_10].cells["TESTUSD"].snapshot.last_price == 100
    assert by_time[at_20].cells["TESTUSD"].snapshot.last_price == 100
    assert by_time[at_30].cells["TESTUSD"].snapshot.last_price == 120

    for frame in frames:
        for cell in frame.cells.values():
            assert cell.source_at <= frame.scan_at


def test_carried_cells_are_flagged_as_imputed():
    rows = [_obs(10, 100)]
    frames = reconstruct_scan_frames(rows, interval_seconds=600, max_carry_seconds=3600)

    # The observation lands exactly on the 10-minute boundary, so that scan
    # sees it as observed rather than carried.
    first = next(f for f in frames if "TESTUSD" in f.cells)
    assert first.scan_at == BASE + timedelta(minutes=10)
    assert first.cells["TESTUSD"].imputed is False
    assert first.observed_count == 1

    later = [f for f in frames if f.scan_at > first.scan_at and "TESTUSD" in f.cells]
    assert later
    for frame in later:
        assert frame.cells["TESTUSD"].imputed is True
        assert frame.imputed_count == 1
        assert frame.observed_count == 0


def test_stale_carry_expires():
    rows = [_obs(10, 100), _obs(200, 100)]
    frames = reconstruct_scan_frames(rows, interval_seconds=600, max_carry_seconds=1200)

    gap = [f for f in frames if timedelta(minutes=40) <= (f.scan_at - BASE) <= timedelta(minutes=180)]
    assert gap
    assert all("TESTUSD" not in f.cells for f in gap)


def test_quiet_carry_forward_creates_no_momentum_and_no_persistence():
    """A flat carried price must break a chain, never extend one."""
    rows = [_obs(10, 100)]
    frames = reconstruct_scan_frames(rows, interval_seconds=600, max_carry_seconds=3600)
    detections, _ = replay_signal_quality(frames, config=Phase2Config())

    assert detections == []


def test_repeated_carried_scans_do_not_manufacture_persistence():
    """Dense events inside a bucket must not out-score sparse ones."""
    sparse = [_obs(0, 100), _obs(10, 102), _obs(20, 104), _obs(30, 106)]
    dense = list(sparse)
    for minute, price in ((2, 100.5), (4, 101), (6, 101.5), (12, 102.5), (14, 103)):
        dense.append(_obs(minute, price))

    config = Phase2Config()
    sparse_det, _ = replay_signal_quality(
        reconstruct_scan_frames(sparse, interval_seconds=600, max_carry_seconds=3600), config=config
    )
    dense_det, _ = replay_signal_quality(
        reconstruct_scan_frames(dense, interval_seconds=600, max_carry_seconds=3600), config=config
    )

    sparse_max = max((row.persistence_scans for row in sparse_det), default=0)
    dense_max = max((row.persistence_scans for row in dense_det), default=0)
    assert dense_max == sparse_max


def test_relative_strength_universe_uses_only_symbols_present_at_that_scan():
    rows = [
        _obs(0, 100, symbol="AUSD"), _obs(10, 102, symbol="AUSD"),
        _obs(0, 50, symbol="BUSD"), _obs(10, 51, symbol="BUSD"),
        # CUSD only appears much later and must not influence earlier scans.
        _obs(600, 10, symbol="CUSD"), _obs(610, 20, symbol="CUSD"),
    ]
    frames = reconstruct_scan_frames(rows, interval_seconds=600, max_carry_seconds=1800)
    early = [f for f in frames if f.scan_at <= BASE + timedelta(minutes=20)]

    for frame in early:
        assert "CUSD" not in frame.cells


# ---------------------------------------------------------------------------
# Forward index
# ---------------------------------------------------------------------------


def test_forward_maxima_are_strictly_after_the_query_time():
    timeline = SymbolTimeline([_obs(0, 100), _obs(10, 150), _obs(20, 120)])

    # At minute 10 the 150 print is contemporaneous, not future.
    maxima = timeline.forward_maxima(
        [BASE, BASE + timedelta(minutes=10), BASE + timedelta(minutes=20)], HORIZON
    )
    assert maxima[0] == 150
    assert maxima[1] == 120
    assert maxima[2] is None


def test_forward_maxima_respect_the_horizon():
    timeline = SymbolTimeline([_obs(0, 100), _obs(60, 110), _obs(60 * 30, 500)])
    maxima = timeline.forward_maxima([BASE], timedelta(hours=2))
    assert maxima[0] == 110


def test_incomplete_forward_window_is_reported():
    timeline = SymbolTimeline([_obs(0, 100), _obs(60, 110)])
    assert timeline.has_complete_window(BASE, HORIZON) is False
    assert timeline.has_complete_window(BASE - timedelta(hours=48), timedelta(hours=1)) is True


# ---------------------------------------------------------------------------
# Episode model
# ---------------------------------------------------------------------------


def _run_timeline(points):
    return SymbolTimeline([_obs(minute, price) for minute, price in points])


def test_one_explosive_run_is_one_episode():
    """The core de-duplication guarantee.

    A continuous run sampled densely must not become many overlapping winners.
    """
    points = [(i * 10, 100 + i * 10) for i in range(40)]  # 100 -> 490, dense
    points += [(400 + i * 10, 480 - i * 30) for i in range(1, 10)]  # retrace
    timeline = SymbolTimeline([_obs(m, p) for m, p in points])

    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())

    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.baseline_price == 100
    assert episode.peak_price == pytest.approx(490)
    assert episode.outcome_class == "MOVE_300_PLUS"


def test_two_separated_runs_are_two_episodes():
    points = [(0, 100), (10, 130), (20, 90)]           # run 1 up then full retrace
    points += [(600, 90), (610, 125), (620, 80)]        # run 2 after a reset
    timeline = SymbolTimeline([_obs(m, p) for m, p in points])

    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())
    assert len(episodes) == 2


def test_episode_records_threshold_crossing_times_in_order():
    points = [(0, 100), (10, 105), (20, 125), (30, 160), (40, 210), (50, 320), (60, 420)]
    timeline = SymbolTimeline([_obs(m, p) for m, p in points])

    episode = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())[0]

    assert episode.baseline_price == 100
    crossings = episode.crossings
    for threshold in (20.0, 50.0, 100.0, 200.0, 300.0):
        assert threshold in crossings
    ordered = [crossings[t] for t in (20.0, 50.0, 100.0, 200.0, 300.0)]
    assert ordered == sorted(ordered)
    assert crossings[20.0] == BASE + timedelta(minutes=20)
    assert crossings[200.0] == BASE + timedelta(minutes=50)
    assert crossings[300.0] == BASE + timedelta(minutes=60)


def test_move_class_boundaries():
    cases = {
        25.0: "MOVE_20_50",
        60.0: "MOVE_50_100",
        140.0: "MOVE_100_200",
        250.0: "MOVE_200_300",
        410.0: "MOVE_300_PLUS",
    }
    for gain, expected in cases.items():
        timeline = _run_timeline([(0, 100), (10, 100 * (1 + gain / 100))])
        episode = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())[0]
        assert episode.outcome_class == expected


def test_a_quiet_market_produces_no_episode():
    timeline = _run_timeline([(i * 10, 100 + (i % 2)) for i in range(20)])
    assert build_episodes(timeline, "TESTUSD", config=EpisodeConfig()) == []


# ---------------------------------------------------------------------------
# Post-detection evaluation: the central correctness property
# ---------------------------------------------------------------------------


def test_detection_after_the_peak_receives_no_success_credit():
    """A move that already happened is not a prediction."""
    timeline = _run_timeline([(0, 100), (10, 200), (20, 105), (30, 104)])
    episode = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())[0]

    late = _detection(20, 105.0)  # after the peak at minute 10
    result = evaluate_episode_detection(episode, [late])

    assert result.first_detection is None
    assert result.detected_before.get(20.0) is False
    assert result.detected_before.get(100.0) is False


def test_detection_after_a_threshold_does_not_count_as_before_it():
    # +20% crosses at minute 10; +50% not until minute 30.
    timeline = _run_timeline([(0, 100), (10, 125), (20, 130), (30, 160), (40, 210)])
    episode = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())[0]

    after_20 = _detection(20, 130.0)
    result = evaluate_episode_detection(episode, [after_20])

    assert episode.crossings[20.0] == BASE + timedelta(minutes=10)
    assert episode.crossings[50.0] == BASE + timedelta(minutes=30)
    # Too late for the move it already missed, still early for the next one.
    assert result.detected_before[20.0] is False
    assert result.detected_before[50.0] is True


def test_detection_exactly_at_the_crossing_does_not_count_as_before():
    timeline = _run_timeline([(0, 100), (10, 125), (20, 200)])
    episode = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())[0]

    at_crossing = _detection(10, 125.0)
    result = evaluate_episode_detection(episode, [at_crossing])
    assert result.detected_before[20.0] is False


def test_detection_before_plus_5_counts_correctly():
    timeline = _run_timeline([(0, 100), (10, 103), (20, 125), (30, 190)])
    episode = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())[0]

    early = _detection(10, 103.0)
    result = evaluate_episode_detection(episode, [early])

    assert result.detected_before[5.0] is True
    assert result.detected_before[20.0] is True
    assert result.first_detection is early
    assert result.move_completed_fraction_pct == pytest.approx(3 / 90 * 100, abs=1e-6)


def test_forward_return_is_measured_from_the_detection_not_the_window():
    """The bug this harness exists to prevent.

    The spike completes before the detection. Judged from the detection's own
    timestamp its forward return is negative, so it must be a failure - not a
    +100% winner inherited from an overlapping window.
    """
    observations = [_obs(0, 100), _obs(10, 200), _obs(20, 100), _obs(30, 98)]
    timelines = build_timelines(observations)
    late = _detection(20, 100.0)

    outcome = evaluate_detections([late], timelines, horizon=HORIZON)[0]

    assert outcome.forward_max_return_pct == pytest.approx(-2.0)
    assert outcome.bucket == "FAIL_LT_5"
    assert outcome.reached_20 is False


def test_forward_return_credits_a_genuine_post_detection_move():
    observations = [_obs(0, 100), _obs(10, 102), _obs(60, 260)]
    timelines = build_timelines(observations)
    early = _detection(10, 102.0)

    outcome = evaluate_detections([early], timelines, horizon=HORIZON)[0]

    assert outcome.forward_max_return_pct == pytest.approx((260 / 102 - 1) * 100)
    assert outcome.bucket == "MOVE_50_PLUS"
    assert outcome.reached_20 is True


def test_failed_breakout_is_classified_as_a_false_positive():
    observations = [_obs(0, 100), _obs(10, 101), _obs(120, 102), _obs(240, 99)]
    timelines = build_timelines(observations)
    detection = _detection(10, 101.0)

    outcome = evaluate_detections([detection], timelines, horizon=HORIZON)[0]

    assert outcome.forward_max_return_pct < 5.0
    assert outcome.bucket == "FAIL_LT_5"


# ---------------------------------------------------------------------------
# Missed-winner forensics
# ---------------------------------------------------------------------------


def test_missed_winner_snapshot_captures_scores_before_each_threshold():
    timeline = _run_timeline([(0, 100), (10, 104), (20, 125), (30, 200)])
    episode = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())[0]

    audit = [
        _detection(0, 100.0, stage="SUPPRESSED", explosion=10, opportunity=12),
        _detection(10, 104.0, stage="SUPPRESSED", explosion=30, opportunity=25),
        _detection(20, 125.0, stage="EARLY_BUILDING", explosion=55, opportunity=58),
    ]
    report = missed_winner_snapshots(episode, audit)

    assert report["symbol"] == "TESTUSD"
    assert report["marks"]["before_plus_20"]["scan_at"] == (BASE + timedelta(minutes=10)).isoformat()
    assert report["marks"]["before_plus_20"]["explosion_potential_score"] == 30
    assert report["marks"]["before_plus_20"]["stage"] == "SUPPRESSED"
    # Every forensic mark that the episode actually crossed is represented.
    for threshold in FORENSIC_THRESHOLDS:
        if threshold in episode.crossings:
            assert f"before_plus_{int(threshold)}" in report["marks"]


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------


def test_chronological_split_is_time_ordered_not_random():
    moments = [BASE + timedelta(minutes=10 * i) for i in range(11)]
    cutoff = chronological_split(moments, 0.6)

    assert cutoff == BASE + timedelta(minutes=60)
    assert all(m <= cutoff for m in moments[:7])
    assert all(m > cutoff for m in moments[7:])


def test_chronological_split_handles_empty_input():
    assert chronological_split([], 0.6) is None


# ---------------------------------------------------------------------------
# OHLC provider
# ---------------------------------------------------------------------------


class FixtureOhlcProvider:
    """Deterministic offline provider; no network access."""

    def __init__(self, candles):
        self._candles = candles
        self.calls = []

    def fetch(self, symbol, start_at, end_at):
        self.calls.append((symbol, start_at, end_at))
        return [c for c in self._candles if start_at <= c.start_at <= end_at]


def test_ohlc_validation_records_a_higher_intraperiod_peak():
    timeline = _run_timeline([(0, 100), (10, 125), (20, 118)])
    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())
    provider = FixtureOhlcProvider([
        OhlcCandle(BASE + timedelta(minutes=10), high=180.0, low=100.0, close=125.0),
    ])

    validated = validate_episodes_with_ohlc(episodes, provider)

    assert validated[0].ohlc_validated is True
    assert validated[0].ohlc_peak_return_pct == pytest.approx(80.0)
    # The event-sampled peak is preserved so undercounting stays visible.
    assert validated[0].peak_return_pct == pytest.approx(25.0)


def test_null_provider_validates_nothing():
    timeline = _run_timeline([(0, 100), (10, 130)])
    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())
    validated = validate_episodes_with_ohlc(episodes, NullOhlcProvider())
    assert validated[0].ohlc_validated is False


def test_kraken_ohlc_provider_uses_only_the_public_client_and_fails_soft():
    class Boom:
        def get_ohlc(self, *args, **kwargs):
            raise RuntimeError("network down")

    provider = KrakenPublicOhlcProvider(Boom())
    assert provider.fetch("TESTUSD", BASE, BASE + timedelta(hours=1)) == []


def test_kraken_ohlc_provider_never_references_a_private_path():
    import app.services.signal_quality_phase2 as module

    source = open(module.__file__, encoding="utf-8").read()
    for forbidden in (
        "kraken_private",
        "add_order",
        "AddOrder",
        "confirm_entry",
        "register_trade",
        "execution_validation",
        "kraken_position_verification",
        "telegram_notifier",
        "send_telegram",
        "save_json_atomic",
    ):
        assert forbidden not in source
    # The only Kraken surface referenced is the public OHLC read.
    assert "get_ohlc" in source
    assert "KrakenClient" in source


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _report_for(observations, detections, *, config=None):
    config = config or Phase2Config()
    timelines = build_timelines(observations)
    episodes = build_all_episodes(timelines, config=config.episodes)
    outcomes = evaluate_detections(detections, timelines, horizon=config.horizon)
    ingestion = IngestionResult(observations=observations, total_lines=len(observations), rejected_lines=0)
    frames = reconstruct_scan_frames(
        observations,
        interval_seconds=config.scan_interval_seconds,
        max_carry_seconds=config.max_carry_seconds,
    )
    from app.services.signal_quality_phase2 import episode_sensitivity, measure_persistence_gap_drift

    return build_phase2_report(
        ingestion, frames, detections, list(detections), episodes, outcomes,
        config=config,
        carry_fidelity=measure_persistence_gap_drift(observations, frames),
        sensitivity=episode_sensitivity(timelines, base=config.episodes),
    )


def test_report_is_provisional_and_advisory_only():
    observations = [_obs(0, 100), _obs(10, 104), _obs(30, 200)]
    report = _report_for(observations, [_detection(10, 104.0)])

    assert report["status"] == "PROVISIONAL_EVENT_SAMPLED_REPLAY"
    method = report["methodology"]
    assert method["no_lookahead"] is True
    assert method["event_sampling_bias_corrected"] is True
    assert method["detection_evaluated_only_after_its_own_timestamp"] is True
    assert method["automatic_tuning_applied"] is False
    assert method["production_thresholds_changed"] is False
    assert method["advisory_only"] is True
    assert method["telegram_messages_sent"] == 0
    assert method["private_kraken_calls"] == 0
    assert any("OHLC_VALIDATION_ABSENT" in w for w in report["warnings"])


def test_report_counts_one_episode_for_one_run():
    observations = [_obs(i * 10, 100 + i * 12) for i in range(12)]
    report = _report_for(observations, [])

    assert report["coverage"]["episodes"] == 1
    assert report["coverage"]["major_move_episodes"] == 1
    assert report["detection_metrics_by_threshold_cohort"]["cohorts"]["GE_20"]["episodes"] == 1
    assert report["detection_metrics_by_threshold_cohort"]["cohorts"]["GE_20"]["missed_episodes"] == 1


def test_report_reports_imputed_cell_share():
    observations = [_obs(0, 100), _obs(60, 101)]
    report = _report_for(observations, [])
    coverage = report["coverage"]

    assert coverage["imputed_cells"] > 0
    assert coverage["observed_cells"] > 0
    assert 0 < coverage["imputed_cell_pct"] <= 100


def test_report_warns_when_there_is_nothing_to_replay():
    report = _report_for([], [])
    assert any("NO_OBSERVATION_ROWS" in w for w in report["warnings"])


def test_report_separates_calibration_from_validation():
    observations = [_obs(i * 10, 100 + i) for i in range(200)]
    detections = [_detection(i * 10, 100 + i) for i in range(0, 100, 10)]
    report = _report_for(observations, detections)

    out_of_sample = report["out_of_sample"]
    assert out_of_sample["split"] == "chronological"
    assert out_of_sample["calibration_detections"] + out_of_sample["validation_detections"] <= len(detections)
    assert any("VALIDATION_SAMPLE_SMALL" in w for w in report["warnings"])


def test_run_phase2_replay_on_missing_file_is_safe_and_warns(tmp_path):
    report = run_phase2_replay(observation_file=tmp_path / "nope.jsonl")

    assert report["coverage"]["observation_rows"] == 0
    assert report["coverage"]["detections"] == 0
    assert report["methodology"]["advisory_only"] is True
    assert any("NO_OBSERVATION_ROWS" in w for w in report["warnings"])


def test_end_to_end_replay_reads_only_and_writes_nothing(tmp_path):
    path = tmp_path / "obs.jsonl"
    lines = []
    for index in range(30):
        moment = BASE + timedelta(minutes=10 * index)
        price = 100 + index * 5
        lines.append(
            '{"record_type":"FULL_MARKET_OBSERVATION","observed_at":"%s","symbol":"AUSD",'
            '"last_price":%s,"volume_24h":1000,"notional_24h_usd_approx":%s,'
            '"high_24h":%s,"low_24h":100,"lift_from_24h_low_pct":%s,'
            '"distance_from_24h_high_pct":0}'
            % (moment.isoformat(), price, price * 1000, price, (price / 100 - 1) * 100)
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())

    report = run_phase2_replay(observation_file=path)

    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert report["coverage"]["observation_rows"] == 30
    assert report["coverage"]["episodes"] >= 1
    assert report["methodology"]["production_thresholds_changed"] is False


def test_replay_does_not_mutate_production_settings():
    """Phase 2 must not touch the live Phase 1 configuration."""
    from app.core.config import Settings

    settings = Settings(webhook_secret="test-webhook-secret")
    before = (
        settings.signal_quality_v1_enabled,
        settings.signal_quality_early_alerts_enabled,
        settings.signal_quality_min_liquidity_usd,
        settings.signal_quality_breakout_opportunity,
    )
    _report_for([_obs(0, 100), _obs(10, 130)], [_detection(0, 100.0)])
    after = (
        settings.signal_quality_v1_enabled,
        settings.signal_quality_early_alerts_enabled,
        settings.signal_quality_min_liquidity_usd,
        settings.signal_quality_breakout_opportunity,
    )
    assert before == after


# ---------------------------------------------------------------------------
# Reconstruction fidelity: how wrong were the carried values?
# ---------------------------------------------------------------------------


def test_carry_error_bounds_match_phase_1():
    """The stated bound must track the runtime that produces it.

    These constants justify last-observation-carried-forward. If Phase 1's
    persistence thresholds change and these do not, the report would advertise
    a bound the data no longer satisfies.
    """
    from app.services import full_market_observation as runtime
    from app.services import signal_quality_phase2 as phase2

    assert phase2.CARRY_PRICE_DRIFT_BOUND_PCT == runtime.MIN_PERSIST_PRICE_CHANGE_PCT
    assert phase2.CARRY_LIFT_DRIFT_BOUND_PCT == runtime.MIN_PERSIST_LIFT_CHANGE_PCT
    assert phase2.CARRY_HIGH_DISTANCE_DRIFT_BOUND_PCT == runtime.MIN_PERSIST_HIGH_DISTANCE_CHANGE_PCT
    assert phase2.CARRY_NOTIONAL_RATIO_BOUND == runtime.MIN_NOTIONAL_RATIO_CHANGE
    assert phase2.CARRY_HEARTBEAT_SECONDS == runtime.HEARTBEAT_SECONDS


def test_carry_fidelity_measures_realised_drift():
    from app.services.signal_quality_phase2 import measure_persistence_gap_drift

    # Quiet carry then a 0.5% move: within the 1% persist bound.
    observations = [_obs(0, 100.0), _obs(40, 100.5)]
    frames = reconstruct_scan_frames(observations, interval_seconds=600, max_carry_seconds=3600)
    fidelity = measure_persistence_gap_drift(observations, frames)

    assert fidelity["imputed_cells_measured"] > 0
    assert fidelity["exceeded_bound"] == 0
    assert fidelity["drift_to_next_persisted_observation_pct"]["median"] == pytest.approx(0.5)
    assert fidelity["theoretical_price_drift_bound_pct"] == 1.0


def test_carry_fidelity_flags_drift_past_the_bound():
    from app.services.signal_quality_phase2 import measure_persistence_gap_drift

    observations = [_obs(0, 100.0), _obs(40, 130.0)]
    frames = reconstruct_scan_frames(observations, interval_seconds=600, max_carry_seconds=3600)
    fidelity = measure_persistence_gap_drift(observations, frames)

    assert fidelity["exceeded_bound"] > 0
    assert fidelity["exceeded_bound_pct"] > 0


def test_unresolvable_carry_is_counted_not_assumed_zero():
    """A carry with no later observation has unknowable drift."""
    from app.services.signal_quality_phase2 import measure_persistence_gap_drift

    observations = [_obs(0, 100.0)]
    frames = reconstruct_scan_frames(observations, interval_seconds=600, max_carry_seconds=3600)
    fidelity = measure_persistence_gap_drift(observations, frames)

    assert fidelity["imputed_cells_unresolved"] > 0
    assert fidelity["imputed_cells_measured"] == 0


def test_report_includes_reconstruction_fidelity():
    observations = [_obs(0, 100), _obs(40, 100.4), _obs(80, 140)]
    report = _report_for(observations, [])
    fidelity = report["reconstruction_drift_proxy"]

    assert "theoretical_price_drift_bound_pct" in fidelity
    assert "drift_to_next_persisted_observation_pct" in fidelity


def test_high_carry_drift_raises_a_warning():
    observations = [_obs(0, 100), _obs(40, 200), _obs(80, 400)]
    config = Phase2Config()
    timelines = build_timelines(observations)
    frames = reconstruct_scan_frames(
        observations, interval_seconds=config.scan_interval_seconds,
        max_carry_seconds=config.max_carry_seconds,
    )
    from app.services.signal_quality_phase2 import measure_persistence_gap_drift

    report = build_phase2_report(
        IngestionResult(observations, len(observations), 0),
        frames, [], [], build_all_episodes(timelines, config=config.episodes), [],
        config=config,
        carry_fidelity=measure_persistence_gap_drift(observations, frames),
    )
    assert any("PERSISTENCE_GAP_DRIFT_HIGH" in w for w in report["warnings"])


# ---------------------------------------------------------------------------
# Episode parameter sensitivity
# ---------------------------------------------------------------------------


def test_episode_sensitivity_sweeps_trigger_and_retrace():
    from app.services.signal_quality_phase2 import episode_sensitivity

    observations = [_obs(i * 10, 100 * (1.03 ** i)) for i in range(40)]
    timelines = build_timelines(observations)
    sweep = episode_sensitivity(timelines, base=EpisodeConfig())

    assert len(sweep) == 9
    assert sum(row["is_default"] for row in sweep) == 1
    default = next(row for row in sweep if row["is_default"])
    assert default["trigger_pct"] == EpisodeConfig().trigger_pct
    assert default["close_retrace_pct"] == EpisodeConfig().close_retrace_pct
    for row in sweep:
        assert row["episodes"] >= 0
        assert isinstance(row["episodes_by_class"], dict)


def test_lower_trigger_never_finds_fewer_episodes():
    """Sanity property: loosening the trigger cannot hide runs."""
    from app.services.signal_quality_phase2 import episode_sensitivity

    observations = []
    for sym in ("AUSD", "BUSD"):
        price = 100.0
        for i in range(60):
            price *= 1.02 if i < 30 else 0.99
            observations.append(_obs(i * 10, price, symbol=sym))
    timelines = build_timelines(observations)
    sweep = episode_sensitivity(timelines, base=EpisodeConfig(), retraces=(30.0,))

    by_trigger = {row["trigger_pct"]: row["episodes"] for row in sweep}
    assert by_trigger[15.0] >= by_trigger[30.0]


def test_report_publishes_the_sensitivity_sweep():
    observations = [_obs(i * 10, 100 * (1.03 ** i)) for i in range(30)]
    report = _report_for(observations, [])
    block = report["episode_parameter_sensitivity"]

    assert block["default"]["trigger_pct"] == EpisodeConfig().trigger_pct
    assert isinstance(block["sweep"], list)


def test_parameter_sensitive_episode_counts_raise_a_warning():
    """A capture rate that moves with the priors must be flagged as such.

    Each tooth rises ~50% then retraces ~38%. A 20%/30% close threshold splits
    every tooth into its own episode; a 50% threshold cannot close them and
    merges the lot into one run - an 8x swing in how many "winners" exist.
    """
    from app.services.signal_quality_phase2 import episode_sensitivity

    config = Phase2Config()
    observations = []
    price = 100.0
    minute = 0
    for _ in range(8):
        for _ in range(5):
            price *= 1.0845
            observations.append(_obs(minute, price))
            minute += 10
        for _ in range(5):
            price *= 0.9075
            observations.append(_obs(minute, price))
            minute += 10

    timelines = build_timelines(observations)
    sweep = episode_sensitivity(timelines, base=config.episodes)
    counts = [row["episodes"] for row in sweep]
    assert min(counts) > 0
    assert max(counts) / min(counts) >= 3.0

    report = build_phase2_report(
        IngestionResult(observations, len(observations), 0),
        [], [], [], build_all_episodes(timelines, config=config.episodes), [],
        config=config, sensitivity=sweep,
    )
    assert any("EPISODE_COUNT_PARAMETER_SENSITIVE" in w for w in report["warnings"])


# ---------------------------------------------------------------------------
# Offline OHLC cache
# ---------------------------------------------------------------------------


def test_cached_ohlc_provider_reads_a_local_file(tmp_path):
    from app.services.signal_quality_phase2 import CachedOhlcProvider

    path = tmp_path / "ohlc.jsonl"
    path.write_text("\n".join([
        '{"symbol":"TESTUSD","start_at":"2026-08-01T00:10:00+00:00","high":180,"low":100,"close":125}',
        '{"symbol":"TESTUSD","start_at":"2026-08-01T00:20:00+00:00","high":150,"low":110,"close":118}',
        "malformed",
        '{"symbol":"TESTUSD","start_at":"bad-time","high":1,"low":1,"close":1}',
        '{"symbol":"OTHERUSD","start_at":"2026-08-01T00:10:00+00:00","high":9,"low":1,"close":5}',
    ]), encoding="utf-8")

    provider = CachedOhlcProvider(path)
    rows = provider.fetch("TESTUSD", BASE, BASE + timedelta(hours=1))

    assert [r.high for r in rows] == [180.0, 150.0]
    assert provider.fetch("MISSINGUSD", BASE, BASE + timedelta(hours=1)) == []


def test_cached_provider_validates_episodes_offline(tmp_path):
    from app.services.signal_quality_phase2 import CachedOhlcProvider

    path = tmp_path / "ohlc.jsonl"
    path.write_text(
        '{"symbol":"TESTUSD","start_at":"2026-08-01T00:10:00+00:00","high":180,"low":100,"close":125}',
        encoding="utf-8",
    )
    timeline = _run_timeline([(0, 100), (10, 125), (20, 118)])
    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())

    validated = validate_episodes_with_ohlc(episodes, CachedOhlcProvider(path))

    assert validated[0].ohlc_validated is True
    assert validated[0].ohlc_peak_return_pct == pytest.approx(80.0)


def test_missing_cache_file_is_safe(tmp_path):
    from app.services.signal_quality_phase2 import CachedOhlcProvider

    provider = CachedOhlcProvider(tmp_path / "absent.jsonl")
    assert provider.fetch("TESTUSD", BASE, BASE + timedelta(hours=1)) == []


def test_write_ohlc_cache_round_trips(tmp_path):
    from app.services.signal_quality_phase2 import CachedOhlcProvider, write_ohlc_cache

    timeline = _run_timeline([(0, 100), (10, 125), (20, 118)])
    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())
    source = FixtureOhlcProvider([
        OhlcCandle(BASE + timedelta(minutes=10), high=180.0, low=100.0, close=125.0),
    ])
    path = tmp_path / "cache.jsonl"

    written = write_ohlc_cache(episodes, source, path)
    assert written == 1

    reloaded = CachedOhlcProvider(path).fetch("TESTUSD", BASE, BASE + timedelta(hours=1))
    assert reloaded[0].high == 180.0


def test_full_ohlc_coverage_still_does_not_claim_cross_validation(tmp_path):
    """Even complete peak coverage validates only peak magnitude."""
    from app.services.signal_quality_phase2 import CachedOhlcProvider

    path = tmp_path / "obs.jsonl"
    lines = []
    for index in range(12):
        moment = BASE + timedelta(minutes=10 * index)
        price = 100 * (1.06 ** index)
        lines.append(
            '{"record_type":"FULL_MARKET_OBSERVATION","observed_at":"%s","symbol":"AUSD",'
            '"last_price":%s,"volume_24h":1000,"notional_24h_usd_approx":%s,'
            '"high_24h":%s,"low_24h":100,"lift_from_24h_low_pct":%s,'
            '"distance_from_24h_high_pct":0}'
            % (moment.isoformat(), price, price * 1000, price, (price / 100 - 1) * 100)
        )
    path.write_text("\n".join(lines), encoding="utf-8")

    cache = tmp_path / "ohlc.jsonl"
    cache.write_text(
        '{"symbol":"AUSD","start_at":"2026-08-01T00:30:00+00:00","high":900,"low":100,"close":300}',
        encoding="utf-8",
    )

    report = run_phase2_replay(observation_file=path, ohlc_provider=CachedOhlcProvider(cache))
    block = report["ohlc_validation"]

    # Every episode covered...
    assert block["coverage_pct"] == 100.0
    assert block["status"] == "COMPLETE_OHLC_PEAK_COMPARISON"
    # ...and the report is still provisional, because timing and class were
    # never recomputed from OHLC.
    assert report["status"] == "PROVISIONAL_EVENT_SAMPLED_REPLAY"
    assert block["fully_validated_metrics"] == []
    assert any("crossings" in m for m in block["not_validated_metrics"])
    assert any("outcome_class" in m for m in block["not_validated_metrics"])
    assert any("OHLC_PEAK_COMPARISON_ONLY" in w for w in report["warnings"])
    assert "OHLC-validated episode peaks" not in report["methodology"]["outcome_source"]


# ---------------------------------------------------------------------------
# HIGH 1 — incomplete forward windows must not reach any precision denominator
# ---------------------------------------------------------------------------


def _late_and_early_outcomes():
    """One complete-window detection and one truncated-window detection.

    The late detection has a future print (so it is measurable) but the data
    ends well before its 24h horizon closes, which is exactly the case that
    previously leaked into precision.
    """
    observations = [
        _obs(0, 100.0),
        _obs(60, 102.0),
        _obs(60 * 24 + 60, 101.0),   # closes the early detection's horizon
        _obs(60 * 24 + 120, 400.0),  # strong partial move for the late one
    ]
    timelines = build_timelines(observations)
    early = _detection(0, 100.0)
    late = _detection(60 * 24 + 60, 101.0)
    outcomes = evaluate_detections([early, late], timelines, horizon=HORIZON)
    return observations, timelines, outcomes, early, late


def test_incomplete_window_is_measurable_but_not_judged():
    _, _, outcomes, _, late = _late_and_early_outcomes()
    late_outcome = next(o for o in outcomes if o.detection.scan_at == late.scan_at)

    # It has a forward return - the old rule would have admitted it.
    assert late_outcome.forward_max_return_pct is not None
    assert late_outcome.forward_max_return_pct > 100.0
    assert late_outcome.window_complete is False


def _report_from_outcomes(observations, detections):
    config = Phase2Config()
    timelines = build_timelines(observations)
    episodes = build_all_episodes(timelines, config=config.episodes)
    outcomes = evaluate_detections(detections, timelines, horizon=config.horizon)
    frames = reconstruct_scan_frames(
        observations,
        interval_seconds=config.scan_interval_seconds,
        max_carry_seconds=config.max_carry_seconds,
    )
    return build_phase2_report(
        IngestionResult(observations, len(observations), 0),
        frames, detections, list(detections), episodes, outcomes, config=config,
    )


def test_incomplete_detection_with_a_strong_partial_move_is_excluded_from_precision():
    observations, _, _, early, late = _late_and_early_outcomes()
    report = _report_from_outcomes(observations, [early, late])
    fp = report["false_positives"]

    # Only the complete-window detection is judged.
    assert fp["judged_detections"] == 1
    assert fp["excluded_incomplete_window"] == 1
    # The +300% partial move did not inflate the +20% reach rate.
    assert fp["reached_plus_20_pct"] == 0.0
    assert "window_complete" in fp["judged_population_rule"]


def test_incomplete_detection_with_no_move_is_excluded_from_the_failure_rate():
    observations = [
        _obs(0, 100.0),
        _obs(60, 108.0),
        _obs(60 * 24 + 60, 130.0),   # early detection reaches +30%
        _obs(60 * 24 + 70, 130.0),   # late detection: flat, window truncated
    ]
    early = _detection(0, 100.0)
    late = _detection(60 * 24 + 60, 130.0)
    report = _report_from_outcomes(observations, [early, late])
    fp = report["false_positives"]

    assert fp["judged_detections"] == 1
    assert fp["excluded_incomplete_window"] == 1
    # The flat truncated detection did not drag the failure rate up.
    assert fp["failed_to_reach_plus_5_pct"] == 0.0


def test_calibration_and_validation_exclude_incomplete_windows():
    observations, _, _, early, late = _late_and_early_outcomes()
    report = _report_from_outcomes(observations, [early, late])
    split = report["out_of_sample"]

    assert split["calibration_detections"] + split["validation_detections"] == 1


def test_winner_versus_failed_distributions_exclude_incomplete_windows():
    observations, _, _, early, late = _late_and_early_outcomes()
    report = _report_from_outcomes(observations, [early, late])
    block = report["winner_vs_failed_breakout"]

    # The truncated +300% row must not appear as a "winner".
    assert block["eventual_major_mover_detections"] == 0
    assert block["failed_breakout_detections"] == 1


def test_precision_tables_exclude_incomplete_windows():
    observations, _, _, early, late = _late_and_early_outcomes()
    report = _report_from_outcomes(observations, [early, late])
    fp = report["false_positives"]

    for table in (
        "precision_by_stage",
        "precision_by_explosion_bucket",
        "precision_by_opportunity_bucket",
        "precision_by_liquidity_band",
    ):
        judged = sum(row["judged"] for row in fp[table].values())
        assert judged == 1, table


def test_incomplete_detections_remain_reported():
    observations, _, _, early, late = _late_and_early_outcomes()
    report = _report_from_outcomes(observations, [early, late])

    assert report["coverage"]["detections_with_incomplete_forward_window"] == 1
    assert report["false_positives"]["excluded_incomplete_window"] == 1
    assert report["false_positives"]["excluded_incomplete_bucket_counts"]
    assert any("INCOMPLETE_FORWARD_WINDOWS" in w for w in report["warnings"])


# ---------------------------------------------------------------------------
# HIGH 2 — OHLC coverage must not upgrade the report
# ---------------------------------------------------------------------------


def _ten_episodes_one_covered():
    from app.services.signal_quality_phase2 import build_ohlc_validation_block

    episodes = []
    for index in range(10):
        timeline = _run_timeline([(0, 100), (10, 130), (20, 118)])
        episodes.extend(build_episodes(timeline, f"S{index}USD", config=EpisodeConfig()))
    provider = FixtureOhlcProvider([
        OhlcCandle(BASE + timedelta(minutes=10), high=200.0, low=100.0, close=130.0),
    ])
    # Only the first symbol resolves in the fixture.
    covered = validate_episodes_with_ohlc(episodes[:1], provider) + list(episodes[1:])
    return covered, build_ohlc_validation_block


def test_one_covered_episode_cannot_upgrade_the_whole_report():
    episodes, build_block = _ten_episodes_one_covered()
    block = build_block(episodes, provider_name="FixtureOhlcProvider")

    assert block["episodes_requested"] == 10
    assert block["episodes_with_candles"] == 1
    assert block["coverage_pct"] == 10.0
    assert block["status"] == "PARTIAL_OHLC_PEAK_COMPARISON"
    assert block["status"] != "COMPLETE_OHLC_PEAK_COMPARISON"


def test_partial_coverage_is_reported_explicitly_in_the_report():
    from app.services.signal_quality_phase2 import build_ohlc_validation_block

    episodes, _ = _ten_episodes_one_covered()
    config = Phase2Config()
    report = build_phase2_report(
        IngestionResult([], 0, 0), [], [], [], episodes, [],
        config=config,
        ohlc_validation=build_ohlc_validation_block(episodes, provider_name="Fixture"),
    )

    assert report["status"] == "PROVISIONAL_EVENT_SAMPLED_REPLAY"
    assert report["ohlc_validation"]["coverage_pct"] == 10.0
    assert any("OHLC_COVERAGE_PARTIAL" in w for w in report["warnings"])


def test_ohlc_peak_comparison_does_not_imply_threshold_validation():
    """Peak magnitude is compared; crossings and class are untouched."""
    timeline = _run_timeline([(0, 100), (10, 125), (20, 118)])
    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())
    before = episodes[0]
    provider = FixtureOhlcProvider([
        OhlcCandle(BASE + timedelta(minutes=10), high=400.0, low=100.0, close=125.0),
    ])

    after = validate_episodes_with_ohlc(episodes, provider)[0]

    # OHLC says +300%; the episode's own class and timing are unchanged.
    assert after.ohlc_peak_return_pct == pytest.approx(300.0)
    assert after.peak_return_pct == before.peak_return_pct
    assert after.outcome_class == before.outcome_class
    assert after.peak_at == before.peak_at
    assert after.crossings == before.crossings


def test_no_ohlc_data_leaves_the_report_provisional():
    from app.services.signal_quality_phase2 import build_ohlc_validation_block

    timeline = _run_timeline([(0, 100), (10, 130)])
    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())
    block = build_ohlc_validation_block(episodes, provider_name="none")
    report = build_phase2_report(
        IngestionResult([], 0, 0), [], [], [], episodes, [],
        config=Phase2Config(), ohlc_validation=block,
    )

    assert block["status"] == "NO_OHLC_VALIDATION"
    assert report["status"] == "PROVISIONAL_EVENT_SAMPLED_REPLAY"
    assert any("OHLC_VALIDATION_ABSENT" in w for w in report["warnings"])


def test_no_cross_validated_status_constant_exists():
    """The overclaiming status must be unreachable, not merely unused."""
    import app.services.signal_quality_phase2 as module

    assert not hasattr(module, "REPORT_STATUS_OHLC_VALIDATED")
    source = open(module.__file__, encoding="utf-8").read()
    assert "OHLC_CROSS_VALIDATED_REPLAY" not in source


# ---------------------------------------------------------------------------
# MEDIUM 1 — drift terminology
# ---------------------------------------------------------------------------


def test_drift_metric_disclaims_being_reconstruction_error():
    from app.services.signal_quality_phase2 import measure_persistence_gap_drift

    observations = [_obs(0, 100.0), _obs(40, 100.5)]
    frames = reconstruct_scan_frames(observations, interval_seconds=600, max_carry_seconds=3600)
    block = measure_persistence_gap_drift(observations, frames)

    assert block["metric"] == "drift_to_next_persisted_observation"
    assert block["is_point_in_time_reconstruction_error"] is False
    assert "not to the true contemporaneous price" in block["interpretation"]
    assert "unknowable" in block["interpretation"]
    assert "drift_to_next_persisted_observation_pct" in block
    assert "realised_drift_to_next_observation_pct" not in block
    assert "exceeded_bound_caveat" in block


def test_report_drift_block_is_named_as_a_proxy():
    observations = [_obs(0, 100), _obs(40, 100.4), _obs(80, 140)]
    report = _report_for(observations, [])

    assert "reconstruction_drift_proxy" in report
    assert "reconstruction_fidelity" not in report
    assert report["reconstruction_drift_proxy"]["is_point_in_time_reconstruction_error"] is False


# ---------------------------------------------------------------------------
# MEDIUM 2 — cumulative cohorts vs exclusive classes
# ---------------------------------------------------------------------------


def _mixed_magnitude_observations():
    """Four symbols peaking at roughly +30, +70, +150 and +400 percent."""
    observations = []
    for symbol, peak in (("AUSD", 1.3), ("BUSD", 1.7), ("CUSD", 2.5), ("DUSD", 5.0)):
        steps = 12
        for index in range(steps + 1):
            price = 100.0 * (peak ** (index / steps))
            observations.append(_obs(index * 10, price, symbol=symbol))
        observations.append(_obs((steps + 1) * 10, 100.0 * peak * 0.5, symbol=symbol))
    return observations


def test_cumulative_cohorts_overlap_and_exclusive_classes_do_not():
    observations = _mixed_magnitude_observations()
    report = _report_for(observations, [])

    cohorts = report["detection_metrics_by_threshold_cohort"]["cohorts"]
    classes = report["detection_metrics_by_exclusive_class"]["classes"]
    total_episodes = report["coverage"]["episodes"]

    # Cumulative: every cohort key is GE_, and counts are non-increasing.
    assert set(cohorts) == {"GE_20", "GE_50", "GE_100", "GE_200", "GE_300"}
    counts = [cohorts[k]["episodes"] for k in ("GE_20", "GE_50", "GE_100", "GE_200", "GE_300")]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 4  # all four qualify as >= +20%

    # Exclusive: each episode counted once, and the bands sum to the total.
    assert sum(row["episodes"] for row in classes.values()) == total_episodes
    assert classes["MOVE_20_50"]["episodes"] == 1
    assert classes["MOVE_50_100"]["episodes"] == 1
    assert classes["MOVE_100_200"]["episodes"] == 1
    assert classes["MOVE_300_PLUS"]["episodes"] == 1


def test_counting_semantics_are_documented_in_the_report():
    report = _report_for(_mixed_magnitude_observations(), [])

    assert "CUMULATIVE" in report["detection_metrics_by_threshold_cohort"]["counting"]
    assert "Do not sum" in report["detection_metrics_by_threshold_cohort"]["counting"]
    assert "EXCLUSIVE" in report["detection_metrics_by_exclusive_class"]["counting"]


def test_episode_outcome_class_uses_the_exclusive_vocabulary():
    """One name, one meaning: episodes_by_class and the exclusive metrics agree."""
    report = _report_for(_mixed_magnitude_observations(), [])
    by_class = report["coverage"]["episodes_by_class"]
    classes = report["detection_metrics_by_exclusive_class"]["classes"]

    for name, count in by_class.items():
        assert name in classes
        assert classes[name]["episodes"] == count


# ---------------------------------------------------------------------------
# Cache hardening
# ---------------------------------------------------------------------------


def test_cache_writer_refuses_to_truncate_a_non_cache_file(tmp_path):
    """A mistyped path must not destroy a production registry."""
    from app.services.signal_quality_phase2 import OhlcCacheTargetError, write_ohlc_cache

    registry = tmp_path / "trade_outcomes.json"
    original = '{"record_type":"TRADE_OUTCOME","symbol":"BTCUSD"}'
    registry.write_text(original, encoding="utf-8")

    timeline = _run_timeline([(0, 100), (10, 130)])
    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())

    with pytest.raises(OhlcCacheTargetError):
        write_ohlc_cache(episodes, FixtureOhlcProvider([]), registry)

    assert registry.read_text(encoding="utf-8") == original


def test_cache_writer_may_replace_an_existing_cache(tmp_path):
    from app.services.signal_quality_phase2 import write_ohlc_cache

    path = tmp_path / "ohlc.jsonl"
    path.write_text(
        '{"symbol":"OLDUSD","start_at":"2026-08-01T00:00:00+00:00","high":1,"low":1,"close":1}',
        encoding="utf-8",
    )
    timeline = _run_timeline([(0, 100), (10, 130)])
    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())
    provider = FixtureOhlcProvider([
        OhlcCandle(BASE + timedelta(minutes=10), high=180.0, low=100.0, close=130.0),
    ])

    assert write_ohlc_cache(episodes, provider, path) == 1
    assert "OLDUSD" not in path.read_text(encoding="utf-8")


def test_cache_writer_deduplicates_overlapping_candles(tmp_path):
    """Overlapping episodes on one symbol must not multiply the cache."""
    from app.services.signal_quality_phase2 import write_ohlc_cache

    timeline = _run_timeline([(0, 100), (10, 130), (20, 90), (600, 90), (610, 125), (620, 80)])
    episodes = build_episodes(timeline, "TESTUSD", config=EpisodeConfig())
    assert len(episodes) >= 2

    candle = OhlcCandle(BASE + timedelta(minutes=10), high=180.0, low=100.0, close=130.0)

    class AlwaysSame:
        def fetch(self, symbol, start_at, end_at):
            return [candle]

    path = tmp_path / "ohlc.jsonl"
    written = write_ohlc_cache(episodes, AlwaysSame(), path)

    assert written == 1
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_cache_reader_counts_rejected_and_duplicate_rows(tmp_path):
    from app.services.signal_quality_phase2 import CachedOhlcProvider

    path = tmp_path / "ohlc.jsonl"
    row = '{"symbol":"TESTUSD","start_at":"2026-08-01T00:10:00+00:00","high":180,"low":100,"close":125}'
    path.write_text("\n".join([row, row, "nonsense", '["not","a","dict"]']), encoding="utf-8")

    provider = CachedOhlcProvider(path)

    assert len(provider.fetch("TESTUSD", BASE, BASE + timedelta(hours=1))) == 1
    assert provider.duplicate_rows == 1
    assert provider.rejected_rows == 2


def test_cache_reader_normalises_symbol_case(tmp_path):
    from app.services.signal_quality_phase2 import CachedOhlcProvider

    path = tmp_path / "ohlc.jsonl"
    path.write_text(
        '{"symbol":"testusd","start_at":"2026-08-01T00:10:00+00:00","high":180,"low":100,"close":125}',
        encoding="utf-8",
    )
    provider = CachedOhlcProvider(path)

    assert len(provider.fetch("TESTUSD", BASE, BASE + timedelta(hours=1))) == 1
    assert len(provider.fetch("testusd", BASE, BASE + timedelta(hours=1))) == 1
