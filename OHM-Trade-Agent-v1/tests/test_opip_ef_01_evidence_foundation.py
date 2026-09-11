"""EF-01 evidence-foundation repair — measurement only."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.jobs.reconcile_discovery_accountability_evidence import main as reconcile_main
from app.opip.discovery.admission import (
    finalize_broad_search_evaluations,
    observation_join_id,
)
from app.opip.discovery.reconciliation import (
    DISCOVERY_WINNER_DEFINITION,
    inspect_replica,
    reconcile_rows,
    reconstructed_join_key,
    threshold_authority_report,
)
from app.scanner.candidates import MIN_TECHNICAL_SCORE
from app.scanner.models import MarketSnapshot
from app.services.opportunity_accountability import (
    ACCOUNTABILITY_WINNER_DEFINITION,
    AccountabilityPolicy,
    build_accountability_rows,
)


NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
LEARNING = Path(__file__).resolve().parents[1] / "deploy" / "learning"


def _snapshot(**overrides) -> MarketSnapshot:
    values = dict(
        symbol="BTCUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=98.0,
        ema200=90.0,
        rsi=60.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=2.0,
        technical_score=85,
        trend="bullish",
        primary_pair="XXBTZUSD",
        underlying_asset="BTC",
        ticker_last=100.0,
        recent_24h_high=110.0,
        recent_24h_low=90.0,
        momentum_6h_pct=1.5,
        momentum_24h_pct=3.0,
        momentum_72h_pct=5.0,
        combined_24h_liquidity_usd=1_000_000.0,
        movement_data_status="UNAVAILABLE",
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def _callback(snapshot: MarketSnapshot, scan_id: str, long_score: int, short_score: int):
    from app.opip.discovery.admission import build_callback_evaluation

    return build_callback_evaluation(
        snapshot,
        long_score,
        short_score,
        "LONG",
        observed_at=NOW,
        scan_id=scan_id,
        universe_count=8,
    )


def test_ranked_out_keeps_preferred_direction_and_maps_to_cap_miss():
    snapshot = _snapshot(technical_score=85)
    provisional = _callback(snapshot, "SCAN:EF01:CAP", 85, 12)
    finalized = finalize_broad_search_evaluations(
        [provisional],
        selected=[],
        observed_at=NOW,
        universe_count=8,
    )[0]
    assert finalized["outcome"] == "COARSE_RANK_LIMIT"
    assert finalized["advanced_direction"] is None
    assert finalized["metadata"]["production_preferred_direction"] == "LONG"
    assert finalized["metadata"]["production_admission_result"] == "RANKED_OUTSIDE_BUDGET"

    rows = build_accountability_rows(
        screening_rows=[finalized],
        funnel_rows=[],
        snapshot_rows=[
            {
                "snapshot_id": "SNAP:EF01:CAP",
                "decision_at_utc": finalized["observed_at"],
                "symbol": "XXBTZUSD",
                "reference_price": 100.0,
            }
        ],
        outcome_rows=[
            {
                "snapshot_id": "SNAP:EF01:CAP",
                "symbol": "XXBTZUSD",
                "mfe_pct": 6.0,
                "mae_pct": -1.0,
                "window_complete": True,
                "outcome_revision": 1,
            }
        ],
        policy=AccountabilityPolicy(),
    )
    long_row = next(row for row in rows if row["direction"] == "LONG")
    assert long_row["opportunity_classification"] == "RANKING_OR_CAP_MISS_CANDIDATE"
    assert long_row["observation_id"] == finalized["metadata"]["observation_id"]
    assert long_row["observation_id"] == observation_join_id(
        scan_id="SCAN:EF01:CAP",
        scanner_type="BROAD_SEARCH",
        venue_instrument_id=finalized["venue_instrument_id"],
        observed_at=finalized["observed_at"],
    )


def test_threshold_authorities_share_min_technical_score():
    report = threshold_authority_report()
    assert report["MIN_TECHNICAL_SCORE"] == MIN_TECHNICAL_SCORE == 80
    assert report["AccountabilityPolicy.production_threshold"] == 80.0
    assert report["single_canonical_source"] is True
    assert report["winner_definitions"]["unifiable"] is False
    assert report["winner_definitions"]["v2_01"] == DISCOVERY_WINNER_DEFINITION
    assert report["winner_definitions"]["accountability"] == ACCOUNTABILITY_WINNER_DEFINITION


def test_reconcile_rows_join_on_observation_id_and_report_winner_scope():
    observation_id = observation_join_id(
        scan_id="SCAN:EF01:JOIN",
        scanner_type="BROAD_SEARCH",
        venue_instrument_id="ETHUSD",
        observed_at="2026-09-10T12:00:00+00:00",
    )
    discovery = [
        {
            "observation_id": observation_id,
            "scan_id": "SCAN:EF01:JOIN",
            "venue_instrument_id": "ETHUSD",
            "observed_at": "2026-09-10T12:00:00+00:00",
            "production_preferred_direction": "LONG",
            "realized_opportunity_direction": "LONG",
            "market_discovery_opportunity_v1": "WINNER",
            "window_complete": True,
            "outcome_revision": 1,
            "horizons": {
                "12h": {
                    "long_mfe_pct": 8.0,
                    "short_mfe_pct": -1.0,
                    "long_mae_pct": 1.0,
                    "window_complete": True,
                }
            },
        }
    ]
    accountability = [
        {
            "accountability_id": "OA:join1",
            "observation_id": observation_id,
            "scan_id": "SCAN:EF01:JOIN",
            "symbol": "ETHUSD",
            "direction": "LONG",
            "observed_at": "2026-09-10T12:00:00+00:00",
            "snapshot_id": "SNAP:JOIN",
            "market_winner": True,
            "outcome_complete": True,
            "adverse_excursion_pct": 1.0,
            "opportunity_classification": "RANKING_OR_CAP_MISS_CANDIDATE",
            "revision": 1,
        }
    ]
    phase3c = [
        {
            "snapshot_id": "SNAP:JOIN",
            "mfe_pct": 9.0,
            "mae_pct": 1.0,
            "outcome_revision": 1,
            "window_complete": True,
        }
    ]
    report = reconcile_rows(
        discovery_rows=discovery,
        accountability_rows=accountability,
        phase3c_rows=phase3c,
    )
    counts = report["population_counts"]
    assert counts["matched_observation_id"] == 1
    assert counts["matched"] == 1
    assert counts["discovery_unmatched"] == 0
    assert report["outcome_reconciliation"]["shared_observation_id_count"] == 1
    assert report["outcome_reconciliation"]["mfe_12h_vs_phase3c_24h_abs_pct"]["max"] == 1.0
    assert reconstructed_join_key(
        scan_id="SCAN:EF01:JOIN",
        observed_at="2026-09-10T12:00:00+00:00",
        symbol="ETH-USD",
    ) == "SCAN:EF01:JOIN|2026-09-10T12:00:00+00:00|ETHUSD"


def test_reconcile_job_writes_empty_report_without_replica(tmp_path):
    payload = reconcile_main(tmp_path)
    assert payload["status"] == "EMPTY"
    assert payload["measurement_only"] is True
    assert payload["trade_authority_changed"] is False
    report_path = tmp_path / "opip/discovery/ef01_reconciliation_report.json"
    assert report_path.is_file()
    consumption = tmp_path / ".learning_consumption/reconcile.json"
    assert consumption.is_file()


def test_inspect_replica_marks_missing_files_unavailable(tmp_path):
    report = inspect_replica(tmp_path)
    assert report["replica_available"] is False
    assert report["population_counts"]["discovery_physical_rows"] == 0


def test_learning_job_script_exposes_one_shot_reconcile_without_new_timer():
    runner = (LEARNING / "opip-learning-job.sh").read_text(encoding="utf-8")
    assert "reconcile)" in runner
    assert "app.jobs.reconcile_discovery_accountability_evidence" in runner
    timers = list(LEARNING.glob("*.timer"))
    assert not any("reconcile" in path.name for path in timers)


def test_ef_01_does_not_introduce_signal_quality_scorer():
    quality_score = Path(__file__).resolve().parents[1] / "app" / "opip" / "quality" / "score.py"
    assert not quality_score.exists()
