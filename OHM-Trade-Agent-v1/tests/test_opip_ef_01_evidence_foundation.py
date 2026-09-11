"""EF-01 evidence-foundation repair — measurement only."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from app.jobs.reconcile_discovery_accountability_evidence import main as reconcile_main
from app.opip.discovery import reconciliation as reconciliation_mod
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
from app.scanner.candidates import MAX_CANDIDATES, MIN_TECHNICAL_SCORE, select_candidates
from app.scanner.directional_candidates import (
    MAX_PER_DIRECTION,
    select_directional_candidates,
)
from app.scanner.models import MarketSnapshot
from app.services.opportunity_accountability import (
    ACCOUNTABILITY_WINNER_DEFINITION,
    AccountabilityPolicy,
    append_accountability_rows,
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
    assert reconstructed_join_key(
        scan_id="SCAN:EF01:JOIN",
        observed_at="2026-09-10T12:00:00Z",
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
    assert report["replica_present"] is False
    assert report["reconciliation_complete"] is False
    assert report["reconciliation_status"] == "EMPTY"
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


def test_production_selection_constants_are_unchanged():
    assert MIN_TECHNICAL_SCORE == 80
    assert MAX_CANDIDATES == 8
    assert MAX_PER_DIRECTION == 5
    assert callable(select_candidates)
    assert callable(select_directional_candidates)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _stage0_row(
    *,
    scan_id: str,
    symbol: str,
    admission: str,
    long_score: int = 85,
    short_score: int = 12,
    exclusion: str | None = None,
):
    observed_at = "2026-09-10T12:00:00+00:00"
    observation_id = observation_join_id(
        scan_id=scan_id,
        scanner_type="BROAD_SEARCH",
        venue_instrument_id=symbol,
        observed_at=observed_at,
    )
    outcome = {
        "ADMITTED": "ADVANCED",
        "RANKED_OUTSIDE_BUDGET": "COARSE_RANK_LIMIT",
        "BELOW_THRESHOLD": "BELOW_THRESHOLD",
        "DATA_UNAVAILABLE": "DATA_UNAVAILABLE",
        "EXCLUDED_MARKET": "EXCLUDED_MARKET",
    }[admission]
    return {
        "observed_at": observed_at,
        "scan_id": scan_id,
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument_id": symbol,
        "venue_instrument": {"venue_instrument_symbol": symbol},
        "outcome": outcome,
        "long_score": long_score,
        "short_score": short_score,
        "advanced_direction": "LONG" if admission == "ADMITTED" else None,
        "metadata": {
            "observation_id": observation_id,
            "production_admission_result": admission,
            "shortlist_selected": admission == "ADMITTED",
            "production_preferred_direction": "LONG",
            "production_exclusion_reason": exclusion,
            "threshold_passed": admission in {"ADMITTED", "RANKED_OUTSIDE_BUDGET"},
        },
    }


def test_shortlist_admission_is_not_inferred_from_funnel():
    finalized = finalize_broad_search_evaluations(
        [_callback(_snapshot(technical_score=85), "SCAN:EF01:SEP", 85, 12)],
        selected=[],
        observed_at=NOW,
        universe_count=8,
    )[0]
    rows = build_accountability_rows(
        screening_rows=[finalized],
        funnel_rows=[],
        snapshot_rows=[
            {
                "snapshot_id": "SNAP:SEP",
                "decision_at_utc": finalized["observed_at"],
                "symbol": "XXBTZUSD",
                "reference_price": 100.0,
            }
        ],
        outcome_rows=[
            {
                "snapshot_id": "SNAP:SEP",
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
    assert long_row["shortlist_admitted"] is False
    assert long_row["production_admission_result"] == "RANKED_OUTSIDE_BUDGET"
    assert long_row["funnel_evidence_present"] is False
    assert long_row["production_selected"] is False


def test_admitted_with_funnel_keeps_legacy_production_selected():
    screening = {
        "observed_at": NOW.isoformat(),
        "scan_id": "SCAN:EF01:FUNNEL",
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument": {"venue_instrument_symbol": "TESTUSD"},
        "outcome": "ADVANCED",
        "long_score": 90,
        "short_score": 20,
        "advanced_direction": "LONG",
        "metadata": {
            "observation_id": observation_join_id(
                scan_id="SCAN:EF01:FUNNEL",
                scanner_type="BROAD_SEARCH",
                venue_instrument_id="TESTUSD",
                observed_at=NOW.isoformat(),
            ),
            "production_admission_result": "ADMITTED",
            "shortlist_selected": True,
            "production_preferred_direction": "LONG",
            "reference_price": 100.0,
            "recent_24h_high": 110.0,
            "recent_24h_low": 90.0,
        },
    }
    rows = build_accountability_rows(
        screening_rows=[screening],
        funnel_rows=[
            {
                "scan_id": "SCAN:EF01:FUNNEL",
                "pair": "TESTUSD",
                "direction": "LONG",
                "decision": "QUALIFIED",
                "terminal_reason_class": "POLICY",
            }
        ],
        snapshot_rows=[
            {
                "snapshot_id": "SNAP:FUNNEL",
                "decision_at_utc": NOW.isoformat(),
                "symbol": "TESTUSD",
                "reference_price": 100.0,
            }
        ],
        outcome_rows=[
            {
                "snapshot_id": "SNAP:FUNNEL",
                "symbol": "TESTUSD",
                "mfe_pct": 6.0,
                "mae_pct": -1.0,
                "window_complete": True,
                "outcome_revision": 1,
            }
        ],
        policy=AccountabilityPolicy(),
    )
    long_row = next(row for row in rows if row["direction"] == "LONG")
    assert long_row["shortlist_admitted"] is True
    assert long_row["funnel_evidence_present"] is True
    assert long_row["production_selected"] is True
    assert long_row["opportunity_classification"] == "CAPTURED_WINNER"


def test_accountability_id_stable_across_ef01_semantic_revision(tmp_path):
    snapshot = _snapshot(technical_score=85)
    finalized = finalize_broad_search_evaluations(
        [_callback(snapshot, "SCAN:EF01:REV", 85, 12)],
        selected=[],
        observed_at=NOW,
        universe_count=8,
    )[0]
    rows = build_accountability_rows(
        screening_rows=[finalized],
        funnel_rows=[],
        snapshot_rows=[
            {
                "snapshot_id": "SNAP:REV",
                "decision_at_utc": finalized["observed_at"],
                "symbol": "XXBTZUSD",
                "reference_price": 100.0,
            }
        ],
        outcome_rows=[
            {
                "snapshot_id": "SNAP:REV",
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
    accountability_id = long_row["accountability_id"]
    legacy = {
        key: value
        for key, value in long_row.items()
        if key
        not in {
            "shortlist_admitted",
            "production_admission_result",
            "production_exclusion_reason",
            "funnel_evidence_present",
        }
    }
    ledger = tmp_path / "accountability.jsonl"
    state = tmp_path / "state.sqlite3"
    first = append_accountability_rows([legacy], path=ledger, state_path=state)
    second = append_accountability_rows([long_row], path=ledger, state_path=state)
    assert first[0]["accountability_id"] == accountability_id
    assert second[0]["accountability_id"] == accountability_id
    assert first[0]["revision"] == 1
    assert second[0]["revision"] == 2
    assert second[0]["shortlist_admitted"] is False
    assert second[0]["funnel_evidence_present"] is False
    history = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["accountability_id"] for row in history] == [
        accountability_id,
        accountability_id,
    ]
    assert len({row["accountability_id"] for row in history}) == 1


def test_reconcile_rows_reports_stage0_counts_and_mismatches():
    admitted = _stage0_row(scan_id="SCAN:A", symbol="AAAUSD", admission="ADMITTED")
    ranked = _stage0_row(
        scan_id="SCAN:R",
        symbol="BBBUSD",
        admission="RANKED_OUTSIDE_BUDGET",
        exclusion="GLOBAL_CAP",
    )
    below = _stage0_row(
        scan_id="SCAN:B",
        symbol="CCCUSD",
        admission="BELOW_THRESHOLD",
        long_score=65,
    )
    unavailable = _stage0_row(
        scan_id="SCAN:U",
        symbol="DDDUSD",
        admission="DATA_UNAVAILABLE",
        long_score=0,
    )
    excluded = _stage0_row(
        scan_id="SCAN:E",
        symbol="EEEUSD",
        admission="EXCLUDED_MARKET",
        long_score=0,
    )
    ranked_obs = ranked["metadata"]["observation_id"]
    admitted_obs = admitted["metadata"]["observation_id"]
    report = reconcile_rows(
        screening_rows=[admitted, ranked, below, unavailable, excluded],
        discovery_rows=[
            {
                "observation_id": ranked_obs,
                "scan_id": "SCAN:R",
                "venue_instrument_id": "BBBUSD",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "production_preferred_direction": "LONG",
                "realized_opportunity_direction": "LONG",
                "market_discovery_opportunity_v1": "WINNER",
                "window_complete": True,
                "outcome_revision": 1,
                "horizons": {"12h": {"long_mfe_pct": 6.0, "window_complete": True}},
            }
        ],
        accountability_rows=[
            {
                "accountability_id": "OA:ranked",
                "observation_id": ranked_obs,
                "scan_id": "SCAN:R",
                "symbol": "BBBUSD",
                "direction": "LONG",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "opportunity_classification": "RANKING_OR_CAP_MISS_CANDIDATE",
                "market_winner": True,
                "outcome_complete": True,
                "funnel_evidence_present": False,
                "revision": 1,
            },
            {
                "accountability_id": "OA:admitted-miss",
                "observation_id": admitted_obs,
                "scan_id": "SCAN:A",
                "symbol": "AAAUSD",
                "direction": "LONG",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "opportunity_classification": "RANKING_OR_CAP_MISS_CANDIDATE",
                "market_winner": True,
                "outcome_complete": True,
                "funnel_evidence_present": False,
                "revision": 1,
            },
        ],
    )
    population = report["stage0_reconciliation"]["population"]
    assert population["ADMITTED"] == 1
    assert population["RANKED_OUTSIDE_BUDGET"] == 1
    assert population["BELOW_THRESHOLD"] == 1
    assert population["DATA_UNAVAILABLE"] == 1
    assert population["EXCLUDED_MARKET"] == 1
    mapping = report["admission_mapping"]
    assert mapping["ranked_out_preferred_winner_correct_rank_cap_miss"] == 1
    assert mapping["admitted_missing_funnel"] == 1
    assert mapping["admitted_incorrectly_mapped_to_rank_cap_miss"] == 1
    assert mapping["admitted_incorrectly_mapped_to_rank_cap_miss_expected"] == 0
    assert report["classification_mismatches"]["count"] >= 1
    assert report["classification_mismatches"]["sample"]
    assert report["overlap_sample"][0]["stage0"] is not None
    assert report["identity_reconciliation"]["shared_observation_id_joins"] >= 1
    assert "resource_usage" in report
    assert report["winner_definitions"]["unifiable"] is False
    assert report["maturity_reconciliation"]["equivalence"] == "DIFFERENT_SCOPE_EXPECTED"


def test_reconcile_rows_fallback_reconstructed_join():
    observation_id = observation_join_id(
        scan_id="SCAN:FB",
        scanner_type="BROAD_SEARCH",
        venue_instrument_id="ETHUSD",
        observed_at="2026-09-10T12:00:00+00:00",
    )
    screening = _stage0_row(
        scan_id="SCAN:FB",
        symbol="ETHUSD",
        admission="RANKED_OUTSIDE_BUDGET",
    )
    report = reconcile_rows(
        screening_rows=[screening],
        discovery_rows=[
            {
                "observation_id": observation_id,
                "scan_id": "SCAN:FB",
                "venue_instrument_id": "ETHUSD",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "production_preferred_direction": "LONG",
                "market_discovery_opportunity_v1": "WINNER",
                "window_complete": True,
                "outcome_revision": 1,
            }
        ],
        accountability_rows=[
            {
                "accountability_id": "OA:fallback",
                "scan_id": "SCAN:FB",
                "symbol": "ETH-USD",
                "direction": "LONG",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "opportunity_classification": "RANKING_OR_CAP_MISS_CANDIDATE",
                "market_winner": True,
                "outcome_complete": True,
                "funnel_evidence_present": False,
                "revision": 1,
            }
        ],
    )
    assert report["identity_reconciliation"]["fallback_reconstructed_joins"] == 1
    assert report["identity_reconciliation"]["shared_observation_id_joins"] == 0


def test_inspect_replica_streams_large_jsonl_without_read_text(tmp_path, monkeypatch):
    source = Path(reconciliation_mod.__file__).read_text(encoding="utf-8")
    assert "read_text(encoding=\"utf-8\").splitlines()" not in source
    assert ".read_bytes(" not in source
    assert "def iter_jsonl_dicts(" in source

    original = Path.read_text

    def guarded(self, *args, **kwargs):
        name = str(self)
        if name.endswith(".jsonl") or name.endswith(".jsonl.gz"):
            raise AssertionError(f"unbounded Path.read_text for JSONL: {self}")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)

    screening_rows = []
    discovery_rows = []
    oa_rows = []
    for index in range(400):
        admission = (
            "ADMITTED"
            if index % 5 == 0
            else "RANKED_OUTSIDE_BUDGET"
            if index % 5 == 1
            else "BELOW_THRESHOLD"
            if index % 5 == 2
            else "DATA_UNAVAILABLE"
            if index % 5 == 3
            else "EXCLUDED_MARKET"
        )
        row = _stage0_row(
            scan_id=f"SCAN:BIG:{index}",
            symbol=f"S{index:04d}USD",
            admission=admission,
            long_score=82 if admission != "BELOW_THRESHOLD" else 65,
        )
        screening_rows.append(row)
        observation_id = row["metadata"]["observation_id"]
        discovery_rows.append(
            {
                "observation_id": observation_id,
                "scan_id": row["scan_id"],
                "venue_instrument_id": row["venue_instrument_id"],
                "observed_at": row["observed_at"],
                "production_preferred_direction": "LONG",
                "market_discovery_opportunity_v1": "WINNER",
                "window_complete": True,
                "outcome_revision": 1,
            }
        )
        oa_rows.append(
            {
                "accountability_id": f"OA:BIG:{index}",
                "observation_id": observation_id,
                "scan_id": row["scan_id"],
                "symbol": row["venue_instrument_id"],
                "direction": "LONG",
                "observed_at": row["observed_at"],
                "opportunity_classification": (
                    "RANKING_OR_CAP_MISS_CANDIDATE"
                    if admission == "RANKED_OUTSIDE_BUDGET"
                    else "MARKET_WINNER_UNVERIFIED_EXECUTABILITY"
                ),
                "market_winner": True,
                "outcome_complete": True,
                "funnel_evidence_present": False,
                "revision": 1,
            }
        )

    _write_jsonl(
        tmp_path / "opip/qualification/screening_evaluations.jsonl",
        screening_rows,
    )
    _write_jsonl(tmp_path / "opip/discovery/forward_outcomes.jsonl", discovery_rows)
    _write_jsonl(tmp_path / "opip/opportunity_accountability.jsonl", oa_rows)

    report = inspect_replica(tmp_path)
    assert report["replica_available"] is True
    assert report["replica_present"] is True
    assert report["reconciliation_complete"] is True
    assert report["reconciliation_status"] == "RECONCILED"
    assert report["stage0_reconciliation"]["population"]["total"] == 400
    assert report["stage0_reconciliation"]["population"]["ADMITTED"] == 80
    assert report["resource_usage"]["used_path_read_text_for_jsonl"] is False
    assert report["resource_usage"]["jsonl_ingestion"] == "streaming_line_iterator"
    assert report["resource_usage"]["processed_physical_rows"]["stage0"] == 400
    assert report["identity_reconciliation"]["shared_observation_id_joins"] == 400


def test_inspect_replica_skips_truncated_and_malformed_jsonl(tmp_path):
    path = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    valid = _stage0_row(scan_id="SCAN:OK", symbol="OKUSD", admission="ADMITTED")
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(valid) + "\n")
        handle.write("{not-json\n")
        handle.write('{"scan_id":"SCAN:TRUNC"')
    report = inspect_replica(tmp_path)
    assert report["stage0_reconciliation"]["population"]["ADMITTED"] == 1
    assert report["resource_usage"]["ingest_stats"]["stage0"]["malformed_rows"] == 1
    assert report["resource_usage"]["ingest_stats"]["stage0"]["truncated_tail_skipped"] == 1


def test_index_cap_exceeded_fail_closes_certification(tmp_path, monkeypatch):
    monkeypatch.setattr(reconciliation_mod, "MAX_INDEX_ROWS_PER_PLANE", 2)
    screening_rows = [
        _stage0_row(scan_id=f"SCAN:CAP:{i}", symbol=f"C{i}USD", admission="ADMITTED")
        for i in range(3)
    ]
    discovery_rows = []
    oa_rows = []
    for row in screening_rows:
        observation_id = row["metadata"]["observation_id"]
        discovery_rows.append(
            {
                "observation_id": observation_id,
                "scan_id": row["scan_id"],
                "venue_instrument_id": row["venue_instrument_id"],
                "observed_at": row["observed_at"],
                "production_preferred_direction": "LONG",
                "market_discovery_opportunity_v1": "WINNER",
                "window_complete": True,
                "outcome_revision": 1,
            }
        )
        oa_rows.append(
            {
                "accountability_id": f"OA:CAP:{row['scan_id']}",
                "observation_id": observation_id,
                "scan_id": row["scan_id"],
                "symbol": row["venue_instrument_id"],
                "direction": "LONG",
                "observed_at": row["observed_at"],
                "opportunity_classification": "MARKET_WINNER_UNVERIFIED_EXECUTABILITY",
                "market_winner": True,
                "outcome_complete": True,
                "production_admission_result": "ADMITTED",
                "production_preferred_direction": "LONG",
                "funnel_evidence_present": False,
                "revision": 1,
            }
        )
    _write_jsonl(
        tmp_path / "opip/qualification/screening_evaluations.jsonl",
        screening_rows,
    )
    _write_jsonl(tmp_path / "opip/discovery/forward_outcomes.jsonl", discovery_rows)
    _write_jsonl(tmp_path / "opip/opportunity_accountability.jsonl", oa_rows)

    report = inspect_replica(tmp_path)
    assert any(report["resource_usage"]["index_cap_exceeded"].values())
    assert report["reconciliation_complete"] is False
    assert report["reconciliation_status"] == "INCOMPLETE"
    assert any(
        item.startswith("INDEX_CAP_EXCEEDED:")
        for item in report["reconciliation_blockers"]
    )

    payload = reconcile_main(tmp_path)
    assert payload["status"] != "OK"
    assert payload["status"] == "INCOMPLETE"
    assert payload["reconciliation_complete"] is False
    consumption = json.loads(
        (tmp_path / ".learning_consumption/reconcile.json").read_text(encoding="utf-8")
    )
    assert consumption["disposition"] != "CONSUMED_OK"
    assert consumption["disposition"] == "FAILED_RETRYABLE"


def test_stage0_only_is_present_but_not_certified(tmp_path):
    row = _stage0_row(scan_id="SCAN:S0", symbol="S0USD", admission="ADMITTED")
    _write_jsonl(tmp_path / "opip/qualification/screening_evaluations.jsonl", [row])
    report = inspect_replica(tmp_path)
    assert report["replica_present"] is True
    assert report["reconciliation_complete"] is False
    assert report["reconciliation_status"] == "INCOMPLETE"
    assert "MISSING_CORE_PLANE:discovery" in report["reconciliation_blockers"]
    assert "MISSING_CORE_PLANE:oa" in report["reconciliation_blockers"]
    assert report["core_planes"]["stage0"] is True
    payload = reconcile_main(tmp_path)
    assert payload["status"] != "OK"
    assert payload["status"] == "INCOMPLETE"


def test_discovery_and_oa_without_stage0_not_certified(tmp_path):
    observation_id = observation_join_id(
        scan_id="SCAN:NO0",
        scanner_type="BROAD_SEARCH",
        venue_instrument_id="NO0USD",
        observed_at="2026-09-10T12:00:00+00:00",
    )
    _write_jsonl(
        tmp_path / "opip/discovery/forward_outcomes.jsonl",
        [
            {
                "observation_id": observation_id,
                "scan_id": "SCAN:NO0",
                "venue_instrument_id": "NO0USD",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "production_preferred_direction": "LONG",
                "market_discovery_opportunity_v1": "WINNER",
                "window_complete": True,
                "outcome_revision": 1,
            }
        ],
    )
    _write_jsonl(
        tmp_path / "opip/opportunity_accountability.jsonl",
        [
            {
                "accountability_id": "OA:NO0",
                "observation_id": observation_id,
                "scan_id": "SCAN:NO0",
                "symbol": "NO0USD",
                "direction": "LONG",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "opportunity_classification": "MARKET_WINNER_UNVERIFIED_EXECUTABILITY",
                "market_winner": True,
                "outcome_complete": True,
                "production_admission_result": "ADMITTED",
                "production_preferred_direction": "LONG",
                "revision": 1,
            }
        ],
    )
    report = inspect_replica(tmp_path)
    assert report["core_planes"]["discovery"] is True
    assert report["core_planes"]["opportunity_accountability"] is True
    assert report["core_planes"]["stage0"] is False
    assert report["reconciliation_complete"] is False
    assert "MISSING_CORE_PLANE:stage0" in report["reconciliation_blockers"]


def test_phase3c_only_not_certified(tmp_path):
    _write_jsonl(
        tmp_path / "phase3c_forward_outcomes.jsonl",
        [
            {
                "snapshot_id": "SNAP:P3",
                "symbol": "P3USD",
                "mfe_pct": 3.0,
                "mae_pct": -1.0,
                "window_complete": True,
                "outcome_revision": 1,
            }
        ],
    )
    report = inspect_replica(tmp_path)
    assert report["replica_present"] is True
    assert report["core_planes"]["phase3c"] is True
    assert report["reconciliation_complete"] is False
    assert report["reconciliation_status"] == "INCOMPLETE"
    assert "MISSING_CORE_PLANE:stage0" in report["reconciliation_blockers"]


def test_stage0_discovery_oa_certified_when_complete(tmp_path):
    row = _stage0_row(scan_id="SCAN:FULL", symbol="FULLUSD", admission="ADMITTED")
    observation_id = row["metadata"]["observation_id"]
    _write_jsonl(tmp_path / "opip/qualification/screening_evaluations.jsonl", [row])
    _write_jsonl(
        tmp_path / "opip/discovery/forward_outcomes.jsonl",
        [
            {
                "observation_id": observation_id,
                "scan_id": row["scan_id"],
                "venue_instrument_id": row["venue_instrument_id"],
                "observed_at": row["observed_at"],
                "production_preferred_direction": "LONG",
                "market_discovery_opportunity_v1": "WINNER",
                "window_complete": True,
                "outcome_revision": 1,
            }
        ],
    )
    _write_jsonl(
        tmp_path / "opip/opportunity_accountability.jsonl",
        [
            {
                "accountability_id": "OA:FULL",
                "observation_id": observation_id,
                "scan_id": row["scan_id"],
                "symbol": row["venue_instrument_id"],
                "direction": "LONG",
                "observed_at": row["observed_at"],
                "opportunity_classification": "MARKET_WINNER_UNVERIFIED_EXECUTABILITY",
                "market_winner": True,
                "outcome_complete": True,
                "production_admission_result": "ADMITTED",
                "production_preferred_direction": "LONG",
                "funnel_evidence_present": False,
                "revision": 1,
            }
        ],
    )
    report = inspect_replica(tmp_path)
    assert report["reconciliation_complete"] is True
    assert report["reconciliation_status"] == "RECONCILED"
    assert report["reconciliation_blockers"] == []
    payload = reconcile_main(tmp_path)
    assert payload["status"] == "OK"
    consumption = json.loads(
        (tmp_path / ".learning_consumption/reconcile.json").read_text(encoding="utf-8")
    )
    assert consumption["disposition"] == "CONSUMED_OK"


def test_incomplete_winner_evidence_never_exact_match():
    """Matching observation_id without comparable winner must not be EXACT_MATCH."""
    observation_id = observation_join_id(
        scan_id="SCAN:INC",
        scanner_type="BROAD_SEARCH",
        venue_instrument_id="INCUSD",
        observed_at="2026-09-10T12:00:00+00:00",
    )
    screening = _stage0_row(scan_id="SCAN:INC", symbol="INCUSD", admission="ADMITTED")
    report = reconcile_rows(
        screening_rows=[screening],
        discovery_rows=[
            {
                "observation_id": observation_id,
                "scan_id": "SCAN:INC",
                "venue_instrument_id": "INCUSD",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "production_preferred_direction": "LONG",
                # Incomplete / non-comparable winner label
                "market_discovery_opportunity_v1": "PENDING",
                "window_complete": False,
                "outcome_revision": 1,
            }
        ],
        accountability_rows=[
            {
                "accountability_id": "OA:INC",
                "observation_id": observation_id,
                "scan_id": "SCAN:INC",
                "symbol": "INCUSD",
                "direction": "LONG",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "opportunity_classification": "PENDING_OUTCOME",
                "market_winner": False,
                "outcome_complete": False,
                "outcome_available": False,
                "production_admission_result": "ADMITTED",
                "production_preferred_direction": "LONG",
                "revision": 1,
            }
        ],
    )
    sample = report["overlap_sample"]
    assert sample
    assert all(item["compatibility"] != "EXACT_MATCH" for item in sample)
    assert any(item["compatibility"] == "SEMANTICALLY_COMPATIBLE" for item in sample)


def test_genuine_stage0_exact_match_requires_compared_admission_dims():
    observation_id = observation_join_id(
        scan_id="SCAN:EX",
        scanner_type="BROAD_SEARCH",
        venue_instrument_id="EXUSD",
        observed_at="2026-09-10T12:00:00+00:00",
    )
    screening = _stage0_row(scan_id="SCAN:EX", symbol="EXUSD", admission="ADMITTED")
    report = reconcile_rows(
        screening_rows=[screening],
        discovery_rows=[
            {
                "observation_id": observation_id,
                "scan_id": "SCAN:EX",
                "venue_instrument_id": "EXUSD",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "production_preferred_direction": "LONG",
                # No market opportunity label — Stage-0 dims only.
                "window_complete": True,
                "outcome_revision": 1,
            }
        ],
        accountability_rows=[
            {
                "accountability_id": "OA:EX",
                "observation_id": observation_id,
                "scan_id": "SCAN:EX",
                "symbol": "EXUSD",
                "direction": "LONG",
                "observed_at": "2026-09-10T12:00:00+00:00",
                "opportunity_classification": "MARKET_WINNER_UNVERIFIED_EXECUTABILITY",
                "market_winner": False,
                "outcome_complete": True,
                "production_admission_result": "ADMITTED",
                "production_preferred_direction": "LONG",
                "revision": 1,
            }
        ],
    )
    sample = report["overlap_sample"]
    assert sample
    assert sample[0]["compatibility"] == "EXACT_MATCH"
    assert sample[0]["via"] == "observation_id"


def test_learning_job_shell_honors_reconcile_disposition_summary():
    runner = (LEARNING / "opip-learning-job.sh").read_text(encoding="utf-8")
    assert 'JOB" == "reconcile"' in runner or "JOB\" == \"reconcile\"" in runner
    assert ".learning_consumption/reconcile.json" in runner
    assert "FAILED_RETRYABLE" in runner
