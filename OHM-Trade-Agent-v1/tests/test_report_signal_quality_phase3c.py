import json
from datetime import datetime, timezone

from app.jobs.report_signal_quality_phase3c import build_report


NOW = datetime(2026, 8, 24, 21, 30, tzinfo=timezone.utc)


def test_offline_report_job_writes_non_authoritative_report(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    phase3b = tmp_path / "phase3b.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    output = tmp_path / "report.json"

    snapshots.write_text(
        json.dumps(
            {
                "snapshot_id": "S1",
                "decision_at_utc": NOW.isoformat(),
                "symbol": "BTCUSD",
                "candidate_rank": 1,
                "reference_price": 100.0,
                "liquidity_24h_usd_approx": 5_000_000,
                "stage": "BREAKOUT_CANDIDATE",
                "opportunity_score": 80,
                "tradeability_score": 75,
                "explosion_potential_score": 78,
                "persistence_scans": 3,
                "exhaustion_penalty": 5,
                "suppressed": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    phase3b.write_text(
        json.dumps(
            {
                "symbol": "BTCUSD",
                "recorded_at": NOW.isoformat(),
                "structure_bias": "BULLISH",
                "structure_status": "AVAILABLE_COMPLETED_KRAKEN_SPOT_OHLC",
                "retest_state": "HELD",
                "chase_risk_score": 20,
                "chase_risk_band": "LOW",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    outcomes.write_text(
        json.dumps(
            {
                "symbol": "BTCUSD",
                "reference_at": NOW.isoformat(),
                "episode_id": "E1",
                "horizon_returns_pct": {"15m": 1.0, "30m": 2.0, "60m": 2.5, "4h": 4.0},
                "mfe_pct": 6.0,
                "max_adverse_excursion_pct": -1.0,
                "window_complete": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_report(
        snapshot_path=snapshots,
        phase3b_path=phase3b,
        outcomes_path=outcomes,
        report_path=output,
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert report["episodes"] == 1
    assert persisted["score_is_probability"] is False
    assert persisted["auto_promotion_allowed"] is False
    assert persisted["trade_authority_changed"] is False
