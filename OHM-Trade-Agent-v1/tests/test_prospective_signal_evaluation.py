import json

from app.services.prospective_signal_evaluation import (
    MIN_PROSPECTIVE_BUCKET_SAMPLES,
    build_movement_discovery_comparison,
    build_precursor_watch_comparison,
)


HORIZONS = ("1h", "4h", "12h", "24h")


def test_precursor_watch_requires_and_reports_same_cohort_thirty_sample_baseline(tmp_path):
    precursor_file = tmp_path / "precursors.jsonl"
    outcome_file = tmp_path / "explosion_outcomes.jsonl"
    precursors = []
    outcomes = []
    for label, watch, mfe in (("W", True, 8.0), ("N", False, 4.0)):
        for index in range(MIN_PROSPECTIVE_BUCKET_SAMPLES):
            observation_id = f"{label}{index}"
            precursors.append({
                "record_type": "EXPLOSION_PRECURSOR",
                "observation_id": observation_id,
                "phase": "DORMANT",
                "watch": watch,
                "score": 50 if watch else 20,
                "evidence_count": 4 if watch else 1,
            })
            for horizon in HORIZONS:
                outcomes.append({
                    "record_type": "EXPLOSION_OUTCOME",
                    "observation_id": observation_id,
                    "phase": "DORMANT",
                    "horizon": horizon,
                    "mfe_pct": mfe,
                    "mae_pct": -2.0,
                    "hit_2pct": True,
                    "hit_5pct": watch,
                    "hit_10pct": False,
                    "hit_20pct": False,
                })
    precursor_file.write_text("".join(json.dumps(row) + "\n" for row in precursors))
    outcome_file.write_text("".join(json.dumps(row) + "\n" for row in outcomes))

    report = build_precursor_watch_comparison(
        outcome_file=outcome_file,
        precursor_file=precursor_file,
    )
    assert report["status"] == "READY_FOR_HUMAN_REVIEW"
    assert report["same_cohort_baseline"] is True
    assert report["auto_promotion_allowed"] is False
    assert report["minimum_samples_per_bucket"] == 30
    assert report["horizons"]["4h"]["watch"]["samples"] == 30
    assert report["horizons"]["4h"]["no_watch"]["samples"] == 30
    assert report["horizons"]["4h"]["mfe_delta_pct"] == 4.0
    assert report["horizons"]["4h"]["hit_5_delta"] == 1.0


def test_movement_ready_vs_watch_and_liquidity_bands_require_thirty_samples(tmp_path):
    detection_file = tmp_path / "movement_detections.jsonl"
    outcome_file = tmp_path / "movement_outcomes.jsonl"
    detections = []
    outcomes = []
    for label, stage, eligible, mfe, liquidity in (
        ("R", "READY", True, 9.0, 2_000_000.0),
        ("W", "WATCH", False, 4.0, 100_000.0),
    ):
        for index in range(MIN_PROSPECTIVE_BUCKET_SAMPLES):
            detection_id = f"{label}{index}"
            detections.append({
                "record_type": "DETECTION",
                "detection_id": detection_id,
                "stage": stage,
                "alert_eligible": eligible,
                "liquidity_24h_usd_approx": liquidity,
                "entry_recommendation": "BREAKOUT_ENTRY_POSSIBLE" if eligible else "WATCH",
            })
            for horizon in HORIZONS:
                outcomes.append({
                    "record_type": "OUTCOME",
                    "detection_id": detection_id,
                    "horizon": horizon,
                    "mfe_pct": mfe,
                    "mae_pct": -2.0,
                    "hit_5pct": stage == "READY",
                    "hit_10pct": False,
                    "hit_20pct": False,
                })
    detection_file.write_text("".join(json.dumps(row) + "\n" for row in detections))
    outcome_file.write_text("".join(json.dumps(row) + "\n" for row in outcomes))

    report = build_movement_discovery_comparison(
        outcome_file=outcome_file,
        detection_file=detection_file,
    )
    assert report["status"] == "READY_FOR_HUMAN_REVIEW"
    assert report["auto_promotion_allowed"] is False
    assert report["minimum_samples_per_bucket"] == 30
    four_hour = report["horizons"]["4h"]
    assert four_hour["ready"]["samples"] == 30
    assert four_hour["watch"]["samples"] == 30
    assert four_hour["ready_mfe_delta_pct"] == 5.0
    assert four_hour["ready_hit_5_delta"] == 1.0
    assert four_hour["liquidity_bands"]["1M_10M"]["samples"] == 30
    assert four_hour["liquidity_bands"]["50K_250K"]["samples"] == 30
