from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.phase3c_outcomes import (
    assign_signal_episode_ids,
    build_forward_outcome_labels,
)
from app.services.signal_quality_phase2 import ReplayObservation


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snapshot(snapshot_id, minute, *, stage="BREAKOUT_CANDIDATE", suppressed=False, symbol="TESTUSD"):
    return {
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "decision_at_utc": (BASE + timedelta(minutes=minute)).isoformat(),
        "reference_price": 10.0,
        "stage": stage,
        "suppressed": suppressed,
    }


def observation(minute, price, symbol="TESTUSD"):
    return ReplayObservation(
        observed_at=BASE + timedelta(minutes=minute),
        symbol=symbol,
        snapshot=SimpleNamespace(last_price=float(price)),
    )


def test_signal_episode_groups_contiguous_alerts_and_suppressed_row_breaks_episode():
    rows = [
        snapshot("S1", 0),
        snapshot("S2", 10),
        snapshot("S3", 20, stage="SUPPRESSED", suppressed=True),
        snapshot("S4", 30),
    ]
    assigned = assign_signal_episode_ids(rows, continuity_seconds=1500)
    assert assigned["S1"] == assigned["S2"]
    assert "S3" not in assigned
    assert assigned["S4"] != assigned["S1"]


def test_signal_episode_gap_larger_than_continuity_starts_new_episode():
    rows = [snapshot("S1", 0), snapshot("S2", 30)]
    assigned = assign_signal_episode_ids(rows, continuity_seconds=1500)
    assert assigned["S1"] != assigned["S2"]


def test_forward_labels_keep_non_major_move_signal_episode_for_false_positive_analysis():
    snapshots = [snapshot("S1", 0)]
    observations = [
        observation(0, 10.0),
        observation(15, 10.1),
        observation(30, 10.0),
        observation(60, 9.9),
        observation(240, 10.0),
        observation(24 * 60, 10.1),
    ]
    labels = build_forward_outcome_labels(snapshots, observations)
    assert len(labels) == 1
    row = labels[0]
    assert row["signal_episode_id"].startswith("SIG:")
    assert row["episode_id"] == row["signal_episode_id"]
    assert row["move_episode_id"] is None
    assert row["within_major_move_episode"] is False
    assert row["offline_label_only"] is True
    assert row["affects_ranking"] is False


def test_forward_labels_map_phase2_major_move_episode_separately():
    snapshots = [snapshot("S1", 10)]
    observations = [
        observation(0, 10.0),
        observation(10, 10.5),
        observation(20, 12.1),  # opens >=20% Phase-2 move episode
        observation(30, 13.0),
        observation(60, 9.0),   # retrace closes episode
        observation(240, 9.2),
        observation(24 * 60, 9.3),
    ]
    row = build_forward_outcome_labels(snapshots, observations)[0]
    assert row["signal_episode_id"].startswith("SIG:")
    assert row["move_episode_id"].startswith("MOVE:")
    assert row["within_major_move_episode"] is True
    assert row["outcome_source"].startswith("PROVISIONAL_EVENT_SAMPLED")
