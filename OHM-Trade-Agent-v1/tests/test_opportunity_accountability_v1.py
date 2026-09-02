from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path

from app.services.dashboard_read_model import _opportunity_accountability_snapshot
from app.services.opportunity_accountability import (
    AccountabilityPolicy,
    append_accountability_rows,
    build_accountability_rows,
    build_accountability_summary,
    build_accountability_summary_from_state,
    build_incremental_from_outcomes,
)
from app.services.signal_timing_v2 import STANDARD_HORIZONS


NOW = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)


def _screening(
    *,
    score_long=90,
    score_short=20,
    outcome="ADVANCED",
    advanced_direction="LONG",
    symbol="TESTUSD",
    scan_id="SCAN:1",
):
    return {
        "observed_at": NOW.isoformat(),
        "scan_id": scan_id,
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument": {"venue_instrument_symbol": symbol},
        "outcome": outcome,
        "long_score": score_long,
        "short_score": score_short,
        "advanced_direction": advanced_direction,
        "metadata": {
            "reference_price": 100.0,
            "recent_24h_high": 110.0,
            "recent_24h_low": 90.0,
            "measurement_only": True,
        },
    }


def _snapshot(symbol="TESTUSD", snapshot_id="SNAP:1"):
    return {
        "decision_at_utc": NOW.isoformat(),
        "symbol": symbol,
        "snapshot_id": snapshot_id,
        "episode_id": "EP:1",
        "reference_price": 100.0,
        "high_24h": 105.0,
        "low_24h": 90.0,
    }


def _outcome(
    *,
    snapshot_id="SNAP:1",
    symbol="TESTUSD",
    mfe=6.0,
    mae=-1.0,
    revision=1,
):
    return {
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "reference_at": NOW.isoformat(),
        "reference_price": 100.0,
        "canonical_episode_id": "EP:1",
        "mfe_pct": mfe,
        "mae_pct": mae,
        "time_to_mfe_seconds": 600,
        "time_to_mae_seconds": 300,
        "horizon_returns_pct": {"15m": 2.5, "60m": 4.0, "12h": 5.0},
        "window_complete": True,
        "outcome_revision": revision,
    }


def _funnel(
    *,
    decision="REJECTED",
    symbol="TESTUSD",
    scan_id="SCAN:1",
    reason_class="POLICY",
    terminal_gate="DETERMINISTIC_QUALITY",
    signal_id=None,
):
    return {
        "scan_id": scan_id,
        "pair": symbol,
        "direction": "LONG",
        "decision_at_utc": NOW.isoformat(),
        "decision": decision,
        "first_terminal_gate": terminal_gate,
        "terminal_reason_code": "DETERMINISTIC_VIABILITY_FAILED",
        "terminal_reason_class": reason_class,
        "terminal_reason": "test stop",
        "episode_id": "EP:1",
        "candidate_id": "CAND:1",
        "signal_id": signal_id,
        "gate_results": [
            {
                "gate": "MARGIN_ELIGIBILITY",
                "status": "PASS",
                "evaluated_at": (NOW + timedelta(milliseconds=50)).isoformat(),
            },
            {
                "gate": "EXECUTION_VALIDATION",
                "status": "PASS",
                "evaluated_at": (NOW + timedelta(milliseconds=125)).isoformat(),
            },
            {
                "gate": terminal_gate,
                "status": "FAIL" if decision != "QUALIFIED" else "PASS",
                "evaluated_at": (NOW + timedelta(milliseconds=350)).isoformat(),
            },
        ],
    }


def _rows(screening, funnel=(), outcome=None, events=()):
    return build_accountability_rows(
        screening_rows=screening,
        funnel_rows=funnel,
        snapshot_rows=[_snapshot()],
        outcome_rows=[outcome or _outcome()],
        intelligence_events=events,
        policy=AccountabilityPolicy(
            production_threshold=80,
            shadow_threshold=70,
            winner_move_pct=2,
        ),
    )


def test_threshold_70_79_winner_is_visible_without_changing_production():
    rows = _rows(
        [
            _screening(
                score_long=75,
                score_short=20,
                outcome="BELOW_THRESHOLD",
                advanced_direction=None,
            )
        ]
    )
    long_row = next(row for row in rows if row["direction"] == "LONG")
    assert long_row["opportunity_classification"] == "THRESHOLD_70_79_MISS_CANDIDATE"
    assert long_row["market_winner"] is True
    assert long_row["executability_status"] == "UNVALIDATED"
    assert long_row["counterfactuals"]["threshold_70_79_shadow"] is True
    assert long_row["affects_ranking"] is False
    assert long_row["affects_trade_authority"] is False


def test_validated_rejection_that_later_wins_is_executable_false_negative():
    rows = _rows([_screening()], [_funnel()])
    long_row = next(row for row in rows if row["direction"] == "LONG")
    assert long_row["opportunity_classification"] == "EXECUTABLE_FALSE_NEGATIVE"
    assert long_row["executable_false_negative"] is True
    assert long_row["estimated_missed_move_pct"] == 6.0
    assert long_row["latency"]["decision_latency_ms"] == 350.0
    assert long_row["latency"]["gate_delta_ms"]["EXECUTION_VALIDATION"] == 75.0


def test_advanced_candidate_dropped_before_funnel_is_ranking_or_cap_miss():
    rows = _rows([_screening()], [])
    long_row = next(row for row in rows if row["direction"] == "LONG")
    assert long_row["opportunity_classification"] == "RANKING_OR_CAP_MISS_CANDIDATE"
    assert long_row["counterfactuals"]["expanded_cap_shadow"] is True
    assert long_row["executable_false_negative"] is False


def test_qualified_signal_paper_outcome_joins_same_accountability_record():
    funnel = _funnel(
        decision="QUALIFIED",
        terminal_gate="FINAL_QUALIFICATION",
        signal_id="SIG:1",
    )
    events = [
        {
            "event_type": "PAPER_ADMISSION",
            "signal_id": "SIG:1",
            "observed_at": (NOW + timedelta(minutes=1)).isoformat(),
            "admitted": True,
            "reason": "ADMITTED",
            "payload": {},
        },
        {
            "event_type": "PAPER_OUTCOME",
            "signal_id": "SIG:1",
            "observed_at": (NOW + timedelta(hours=2)).isoformat(),
            "payload": {"net_pnl": 12.5, "close_profit_ratio": 0.04},
        },
    ]
    rows = _rows([_screening()], [funnel], events=events)
    long_row = next(row for row in rows if row["direction"] == "LONG")
    assert long_row["opportunity_classification"] == "CAPTURED_WINNER"
    assert long_row["paper_admission"]["admitted"] is True
    assert long_row["paper_outcome"]["payload"]["net_pnl"] == 12.5
    summary = build_accountability_summary(rows)
    assert summary["paper"]["outcomes"] == 1
    assert summary["paper"]["wins"] == 1


def test_append_only_state_is_idempotent_and_revises_maturing_outcome(tmp_path):
    ledger = tmp_path / "accountability.jsonl"
    state = tmp_path / "state.sqlite3"
    rows = _rows([_screening()], [_funnel()])
    first = append_accountability_rows(rows, path=ledger, state_path=state)
    second = append_accountability_rows(rows, path=ledger, state_path=state)
    assert len(first) == 2
    assert second == []

    changed = _rows(
        [_screening()],
        [_funnel()],
        outcome=_outcome(mfe=8.0, revision=2),
    )
    third = append_accountability_rows(changed, path=ledger, state_path=state)
    assert len(third) == 2
    assert all(row["revision"] == 2 for row in third)

    summary = build_accountability_summary_from_state(
        ledger_path=ledger,
        state_path=state,
    )
    assert summary["population"]["directional_evaluations"] == 2
    assert summary["population"]["executable_false_negatives"] == 1


def test_dashboard_accountability_rollup_reports_capture_and_latency():
    historical = {
        "available": True,
        "opportunity_accountability_daily": [
            {
                "date": NOW.date().isoformat(),
                "directional_evaluations": 10,
                "completed_forward_outcomes": 8,
                "market_winner_candidates": 4,
                "captured_winners": 3,
                "executable_false_negatives": 1,
                "threshold_70_79_miss_candidates": 2,
                "ranking_or_cap_miss_candidates": 1,
                "operational_executable_misses": 0,
                "estimated_missed_move_pct_sum": 5.2,
                "decision_latency_samples": 3,
                "mean_decision_latency_ms": 420.0,
            }
        ],
    }
    snapshot = _opportunity_accountability_snapshot(historical, "all")
    assert snapshot["opportunity_capture_rate_pct"] == 75.0
    assert snapshot["executable_false_negatives"] == 1
    assert snapshot["mean_decision_latency_ms"] == 420.0


def test_forward_outcomes_include_12h_horizon():
    assert STANDARD_HORIZONS["12h"] == timedelta(hours=12)


def test_new_accountability_layer_has_no_execution_authority_imports():
    root = Path(__file__).resolve().parents[1]
    service = (root / "app" / "services" / "opportunity_accountability.py").read_text(
        encoding="utf-8"
    )
    wrapper = (
        root / "app" / "jobs" / "run_opportunity_intelligence_cycle.py"
    ).read_text(encoding="utf-8")
    combined = service + wrapper
    for forbidden in (
        "app.exchanges",
        "send_trade_plan",
        "trade_action_gate",
        "place_order",
        "AddOrder",
        "telegram_bot_token",
    ):
        assert forbidden not in combined


def test_incremental_join_reads_compressed_archived_screening_and_funnel(tmp_path):
    screening_archive = tmp_path / "screening_archive"
    funnel_archive = tmp_path / "funnel_archive"
    screening_archive.mkdir()
    funnel_archive.mkdir()
    (screening_archive / "screening-0-corrupt.jsonl.gz").write_bytes(
        b"\x1f\x8b\x08\x00truncated"
    )
    with gzip.open(
        screening_archive / "screening-1.jsonl.gz",
        "wt",
        encoding="utf-8",
    ) as handle:
        handle.write(json.dumps(_screening()) + "\n")
    with gzip.open(
        funnel_archive / "funnel-1.jsonl.gz",
        "wt",
        encoding="utf-8",
    ) as handle:
        handle.write(json.dumps(_funnel()) + "\n")

    ledger = tmp_path / "opportunity_accountability.jsonl"
    state = tmp_path / "accountability.sqlite3"
    summary_path = tmp_path / "summary.json"
    summary = build_incremental_from_outcomes(
        [_outcome()],
        screening_path=tmp_path / "screening-hot.jsonl",
        screening_archive=screening_archive,
        funnel_path=tmp_path / "funnel-hot.jsonl",
        funnel_archive=funnel_archive,
        intelligence_event_path=tmp_path / "events.jsonl",
        ledger_path=ledger,
        summary_path=summary_path,
        state_path=state,
        policy=AccountabilityPolicy(
            production_threshold=80,
            shadow_threshold=70,
            winner_move_pct=2,
        ),
    )

    assert summary["population"]["directional_evaluations"] == 2
    assert summary["population"]["executable_false_negatives"] == 1
    assert summary["counterfactual_experiments"]["decay_aware"]["samples"] == 2
    assert ledger.exists()
    assert summary_path.exists()
    persisted = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    long_row = next(row for row in persisted if row["direction"] == "LONG")
    assert long_row["range_consumed_proxy_pct"] == 50.0
    assert long_row["affects_trade_authority"] is False


def test_late_paper_outcome_revises_after_forward_queue_is_complete(tmp_path):
    ledger = tmp_path / "accountability.jsonl"
    state = tmp_path / "accountability.sqlite3"
    events = tmp_path / "events.jsonl"
    summary_path = tmp_path / "summary.json"

    qualified = _rows(
        [_screening()],
        [
            _funnel(
                decision="QUALIFIED",
                terminal_gate="FINAL_QUALIFICATION",
                signal_id="SIG:LATE",
            )
        ],
    )
    append_accountability_rows(
        qualified,
        path=ledger,
        state_path=state,
    )

    events.write_text(
        json.dumps(
            {
                "event_type": "PAPER_OUTCOME",
                "signal_id": "SIG:LATE",
                "observed_at": (NOW + timedelta(hours=72)).isoformat(),
                "payload": {
                    "net_pnl": 21.5,
                    "close_profit_ratio": 0.06,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = build_incremental_from_outcomes(
        [],
        intelligence_event_path=events,
        ledger_path=ledger,
        summary_path=summary_path,
        state_path=state,
    )

    assert summary["paper"]["outcomes"] == 1
    assert summary["paper"]["wins"] == 1
    assert summary["paper"]["net_pnl"] == 21.5
    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    long_rows = [
        row for row in rows
        if row.get("direction") == "LONG"
        and row.get("signal_id") == "SIG:LATE"
    ]
    assert len(long_rows) == 2
    assert long_rows[-1]["revision"] == 2
    assert long_rows[-1]["paper_outcome"]["payload"]["net_pnl"] == 21.5


def test_nonfinite_paper_evidence_skips_only_the_offending_direction():
    rows = build_accountability_rows(
        screening_rows=[_screening()],
        funnel_rows=[
            _funnel(
                decision="QUALIFIED",
                terminal_gate="FINAL_QUALIFICATION",
                signal_id="SIG:NAN",
            )
        ],
        snapshot_rows=[_snapshot()],
        outcome_rows=[_outcome()],
        intelligence_events=[
            {
                "event_type": "PAPER_OUTCOME",
                "signal_id": "SIG:NAN",
                "observed_at": (NOW + timedelta(hours=1)).isoformat(),
                "payload": {"net_pnl": float("nan")},
            }
        ],
        policy=AccountabilityPolicy(
            production_threshold=80,
            shadow_threshold=70,
            winner_move_pct=2,
        ),
    )

    assert [row["direction"] for row in rows] == ["SHORT"]
    assert rows[0]["measurement_only"] is True
