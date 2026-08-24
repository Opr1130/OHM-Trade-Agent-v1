from datetime import datetime, timedelta, timezone

from app.services.signal_features import ObservationSnapshot
from app.services.signal_quality_phase2 import (
    OutcomeLabel,
    ReplayDetection,
    ReplayObservation,
    build_phase2_report,
    label_outcomes,
    reconstruct_scan_frames,
)

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _snapshot(at, price, *, notional=2_000_000.0):
    return ObservationSnapshot(
        observed_at=at,
        last_price=price,
        volume_24h=notional / price,
        notional_24h_usd_approx=notional,
        high_24h=price,
        low_24h=min(100.0, price),
        lift_from_24h_low_pct=(price / min(100.0, price) - 1.0) * 100.0,
        distance_from_24h_high_pct=0.0,
    )


def _obs(minutes, price, symbol="TESTUSD"):
    at = BASE + timedelta(minutes=minutes)
    return ReplayObservation(at, symbol, _snapshot(at, price))


def test_fixed_grid_neutralises_dense_event_sampling():
    sparse = [_obs(1, 100), _obs(59, 101)]
    dense = sparse + [_obs(11, 100.1), _obs(12, 100.2), _obs(13, 100.3), _obs(14, 100.4)]

    sparse_frames = reconstruct_scan_frames(sparse, interval_seconds=600, max_carry_seconds=3600)
    dense_frames = reconstruct_scan_frames(dense, interval_seconds=600, max_carry_seconds=3600)

    # Event density may alter values inside a bucket, but never the number or
    # timestamps of reconstructed runtime scans.
    assert [at for at, _ in sparse_frames] == [at for at, _ in dense_frames]


def test_reconstruction_is_past_only_and_retimestamps_carried_state():
    rows = [_obs(1, 100), _obs(25, 120)]
    frames = reconstruct_scan_frames(rows, interval_seconds=600, max_carry_seconds=3600)

    at_10, frame_10 = frames[0]
    at_20, frame_20 = frames[1]
    at_30, frame_30 = frames[2]

    assert frame_10["TESTUSD"].last_price == 100
    assert frame_20["TESTUSD"].last_price == 100
    assert frame_30["TESTUSD"].last_price == 120
    assert frame_20["TESTUSD"].observed_at == at_20
    assert frame_30["TESTUSD"].observed_at == at_30


def test_carry_forward_expires_instead_of_becoming_permanent_history():
    frames = reconstruct_scan_frames([_obs(1, 100)], interval_seconds=600, max_carry_seconds=900)
    assert "TESTUSD" in frames[0][1]
    assert "TESTUSD" not in frames[-1][1] or len(frames) == 1


def test_outcomes_use_only_future_rows_and_classify_major_moves():
    rows = [_obs(0, 100), _obs(60, 105), _obs(120, 151), _obs(180, 405)]
    labels = label_outcomes(rows, horizon_hours=24)
    first = next(row for row in labels if row.start_at == BASE)

    assert first.max_future_price == 405
    assert first.max_move_pct == 305.0
    assert first.outcome_class == "MOVE_300_PLUS"
    assert first.event_sampled_proxy is True


def test_phase2_report_is_advisory_and_never_auto_tunes():
    label = OutcomeLabel(
        symbol="TESTUSD",
        start_at=BASE,
        reference_price=100.0,
        max_future_price=150.0,
        max_move_pct=50.0,
        outcome_class="MOVE_50",
    )
    detection = ReplayDetection(
        observed_at=BASE + timedelta(minutes=10),
        symbol="TESTUSD",
        stage="EARLY_BUILDING",
        reference_price=104.0,
        opportunity_score=60,
        explosion_potential_score=70,
        tradeability_score=90,
        pattern_strength_score=70,
        persistence_scans=1,
        exhaustion_penalty=0,
    )
    report = build_phase2_report([], [detection], [label], horizon_hours=24)

    assert report["methodology"]["no_lookahead"] is True
    assert report["methodology"]["event_sampling_bias_corrected"] is True
    assert report["methodology"]["automatic_tuning_applied"] is False
    assert report["methodology"]["production_thresholds_changed"] is False
    assert report["methodology"]["advisory_only"] is True
    assert report["major_move_metrics"]["MOVE_50"]["detected_windows"] == 1
    assert report["major_move_metrics"]["MOVE_50"]["detected_before_plus_5_pct"] == 1


def test_future_detection_is_not_used_before_its_timestamp():
    label = OutcomeLabel(
        symbol="TESTUSD",
        start_at=BASE,
        reference_price=100.0,
        max_future_price=130.0,
        max_move_pct=30.0,
        outcome_class="MOVE_20",
    )
    detection = ReplayDetection(
        observed_at=BASE - timedelta(minutes=10),
        symbol="TESTUSD",
        stage="EARLY_BUILDING",
        reference_price=99.0,
        opportunity_score=60,
        explosion_potential_score=60,
        tradeability_score=80,
        pattern_strength_score=70,
        persistence_scans=1,
        exhaustion_penalty=0,
    )
    report = build_phase2_report([], [detection], [label])
    assert report["major_move_metrics"]["MOVE_20"]["detected_windows"] == 0
