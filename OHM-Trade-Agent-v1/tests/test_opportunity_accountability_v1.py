from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import opportunity_accountability as accountability
from app.services.dashboard_read_model import _opportunity_accountability_snapshot
from app.services.opportunity_accountability import (
    AccountabilityPolicy,
    append_accountability_rows,
    build_accountability_rows,
    build_accountability_summary,
    build_accountability_summary_from_state,
    build_incremental_from_outcomes,
    resolved_accountability_outcomes,
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
    window_complete=True,
):
    return {
        "snapshot_id": snapshot_id,
        "outcome_record_id": f"OUT:{snapshot_id}:{revision}",
        "symbol": symbol,
        "reference_at": NOW.isoformat(),
        "reference_price": 100.0,
        "canonical_episode_id": "EP:1",
        "mfe_pct": mfe,
        "mae_pct": mae,
        "time_to_mfe_seconds": 600,
        "time_to_mae_seconds": 300,
        "horizon_returns_pct": {"15m": 2.5, "60m": 4.0, "12h": 5.0},
        "window_complete": bool(window_complete),
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


def test_gate_deltas_follow_completion_time_not_serialized_gate_order():
    funnel = {
        "decision_at_utc": NOW.isoformat(),
        "gate_results": [
            {
                "gate": "LATER_SERIALIZED_FIRST",
                "evaluated_at": (
                    NOW + timedelta(milliseconds=300)
                ).isoformat(),
            },
            {
                "gate": "EARLIER_SERIALIZED_SECOND",
                "evaluated_at": (
                    NOW + timedelta(milliseconds=100)
                ).isoformat(),
            },
        ],
    }
    latency = accountability._latency_evidence(funnel)
    assert latency["decision_latency_ms"] == 300.0
    assert latency["gate_delta_ms"]["EARLIER_SERIALIZED_SECOND"] == 100.0
    assert latency["gate_delta_ms"]["LATER_SERIALIZED_FIRST"] == 200.0


def test_windowed_archive_reader_uses_manifest_selected_segments(
    monkeypatch,
    tmp_path,
):
    hot = tmp_path / "screening_evaluations.jsonl"
    archive_dir = tmp_path / "screening_evaluations_archive"
    hot.write_text(
        json.dumps({"source": "hot", "observed_at": NOW.isoformat()}) + "\n",
        encoding="utf-8",
    )
    selected_path = archive_dir / "selected.jsonl.gz"
    calls = {}

    class FakeArchive:
        def archive_paths_for_visible_window(
            self,
            *,
            start,
            through,
            max_segments,
        ):
            calls["window"] = (start, through, max_segments)
            return SimpleNamespace(
                paths=(selected_path,),
                complete=True,
                truncated=False,
                warnings=(),
            )

        def iter_archive_rows_from_paths(self, paths, *, strict=False):
            calls["paths"] = tuple(paths)
            calls["strict"] = strict
            yield {"source": "archive", "observed_at": NOW.isoformat()}

    monkeypatch.setattr(
        accountability,
        "screening_evaluations_archive",
        lambda _path: FakeArchive(),
    )

    rows = list(
        accountability._iter_windowed_jsonl_sources(
            hot,
            archive_dir=archive_dir,
            start=NOW - timedelta(minutes=1),
            through=NOW + timedelta(minutes=1),
            kind="screening",
        )
    )
    assert [row["source"] for row in rows] == ["archive", "hot"]
    assert calls["paths"] == (selected_path,)
    assert calls["strict"] is True
    assert (
        calls["window"][2]
        == accountability.ACCOUNTABILITY_MAX_ARCHIVE_SEGMENTS_PER_WINDOW
    )



def test_learning_replica_repairs_incomplete_derived_archive_index(
    monkeypatch,
    tmp_path,
):
    hot = tmp_path / "screening_evaluations.jsonl"
    archive_dir = tmp_path / "screening_evaluations_archive"
    selected_path = archive_dir / "selected.jsonl.gz"
    calls = {"select": 0, "rebuild": 0}

    class ReplicaArchive:
        def archive_paths_for_visible_window(self, **_kwargs):
            calls["select"] += 1
            if calls["select"] == 1:
                return SimpleNamespace(
                    paths=(),
                    complete=False,
                    truncated=False,
                    warnings=("ARCHIVE_WINDOW_INDEX_INCOMPLETE",),
                )
            return SimpleNamespace(
                paths=(selected_path,),
                complete=True,
                truncated=False,
                warnings=(),
            )

        def rebuild_window_index_from_verified_manifest_locked(self):
            calls["rebuild"] += 1
            return True

        def iter_archive_rows_from_paths(self, paths, *, strict=False):
            assert tuple(paths) == (selected_path,)
            assert strict is True
            yield {"source": "archive", "observed_at": NOW.isoformat()}

    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    monkeypatch.setattr(
        accountability,
        "screening_evaluations_archive",
        lambda _path: ReplicaArchive(),
    )

    rows = list(
        accountability._iter_windowed_jsonl_sources(
            hot,
            archive_dir=archive_dir,
            start=NOW - timedelta(minutes=1),
            through=NOW + timedelta(minutes=1),
            kind="screening",
            replica_mode=True,
        )
    )
    assert rows == [{"source": "archive", "observed_at": NOW.isoformat()}]
    assert calls == {"select": 2, "rebuild": 1}


def test_production_path_never_repairs_incomplete_archive_index(
    monkeypatch,
    tmp_path,
):
    hot = tmp_path / "screening_evaluations.jsonl"
    archive_dir = tmp_path / "screening_evaluations_archive"
    calls = {"rebuild": 0}

    class IncompleteArchive:
        def archive_paths_for_visible_window(self, **_kwargs):
            return SimpleNamespace(
                paths=(),
                complete=False,
                truncated=False,
                warnings=("ARCHIVE_WINDOW_INDEX_INCOMPLETE",),
            )

        def rebuild_window_index_from_verified_manifest_locked(self):
            calls["rebuild"] += 1
            return True

    monkeypatch.delenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", raising=False)
    monkeypatch.setattr(
        accountability,
        "screening_evaluations_archive",
        lambda _path: IncompleteArchive(),
    )

    with pytest.raises(RuntimeError, match="ACCOUNTABILITY_ARCHIVE_WINDOW_INCOMPLETE"):
        list(
            accountability._iter_windowed_jsonl_sources(
                hot,
                archive_dir=archive_dir,
                start=NOW - timedelta(minutes=1),
                through=NOW + timedelta(minutes=1),
                kind="screening",
            )
        )
    assert calls["rebuild"] == 0


def test_environment_flag_cannot_enable_repair_without_explicit_replica_mode(
    monkeypatch,
    tmp_path,
):
    hot = tmp_path / "screening_evaluations.jsonl"
    archive_dir = tmp_path / "screening_evaluations_archive"
    calls = {"rebuild": 0}

    class IncompleteArchive:
        def archive_paths_for_visible_window(self, **_kwargs):
            return SimpleNamespace(
                paths=(),
                complete=False,
                truncated=False,
                warnings=("ARCHIVE_WINDOW_INDEX_INCOMPLETE",),
            )

        def rebuild_window_index_from_verified_manifest_locked(self):
            calls["rebuild"] += 1
            return True

    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    monkeypatch.setattr(
        accountability,
        "screening_evaluations_archive",
        lambda _path: IncompleteArchive(),
    )

    with pytest.raises(RuntimeError, match="ACCOUNTABILITY_ARCHIVE_WINDOW_INCOMPLETE"):
        list(
            accountability._iter_windowed_jsonl_sources(
                hot,
                archive_dir=archive_dir,
                start=NOW - timedelta(minutes=1),
                through=NOW + timedelta(minutes=1),
                kind="screening",
                replica_mode=False,
            )
        )
    assert calls["rebuild"] == 0

def test_incomplete_archive_window_is_not_silently_accepted(
    monkeypatch,
    tmp_path,
):
    hot = tmp_path / "funnel_events.jsonl"
    archive_dir = tmp_path / "funnel_events_archive"

    class IncompleteArchive:
        def archive_paths_for_visible_window(self, **_kwargs):
            return SimpleNamespace(
                paths=(),
                complete=False,
                truncated=True,
                warnings=("ARCHIVE_SEGMENT_CEILING_REACHED",),
            )

    monkeypatch.setattr(
        accountability,
        "funnel_events_archive",
        lambda _path: IncompleteArchive(),
    )
    with pytest.raises(RuntimeError, match="ACCOUNTABILITY_ARCHIVE_WINDOW_INCOMPLETE"):
        list(
            accountability._iter_windowed_jsonl_sources(
                hot,
                archive_dir=archive_dir,
                start=NOW - timedelta(minutes=1),
                through=NOW + timedelta(minutes=1),
                kind="funnel",
            )
        )



def test_incremental_join_splits_archive_ceiling_batches(monkeypatch, tmp_path):
    screening_path = tmp_path / "screening.jsonl"
    funnel_path = tmp_path / "funnel.jsonl"
    screening_archive = tmp_path / "screening_archive"
    funnel_archive = tmp_path / "funnel_archive"
    ledger = tmp_path / "accountability.jsonl"
    state = tmp_path / "accountability.sqlite3"
    summary_path = tmp_path / "summary.json"

    later = NOW + timedelta(hours=6)
    screening_rows = [
        _screening(scan_id="SCAN:1"),
        {
            **_screening(scan_id="SCAN:2", symbol="LATERUSD"),
            "observed_at": later.isoformat(),
        },
    ]
    funnel_rows = [
        _funnel(scan_id="SCAN:1"),
        {
            **_funnel(scan_id="SCAN:2", symbol="LATERUSD"),
            "decision_at_utc": later.isoformat(),
        },
    ]
    outcomes = [
        _outcome(snapshot_id="SNAP:1"),
        {
            **_outcome(snapshot_id="SNAP:2", symbol="LATERUSD"),
            "reference_at": later.isoformat(),
        },
    ]
    screening_path.write_text(
        "".join(json.dumps(row) + "\n" for row in screening_rows),
        encoding="utf-8",
    )
    funnel_path.write_text(
        "".join(json.dumps(row) + "\n" for row in funnel_rows),
        encoding="utf-8",
    )


    def bounded_selector(path, *, archive_dir, start, through, kind):
        if through - start > timedelta(hours=2):
            return SimpleNamespace(), SimpleNamespace(
                paths=(),
                complete=False,
                truncated=True,
                warnings=("ARCHIVE_SEGMENT_CEILING_REACHED",),
            )
        # Narrow recursive partitions use the fixture files directly.
        return None

    monkeypatch.setattr(
        accountability,
        "_window_archive_selection",
        bounded_selector,
    )
    summary = build_incremental_from_outcomes(
        outcomes,
        screening_path=screening_path,
        screening_archive=screening_archive,
        funnel_path=funnel_path,
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

    assert summary["batch_disposition"] == {
        "accepted": 2,
        "terminal_rejected": 0,
        "unresolved": 0,
    }
    assert len(
        resolved_accountability_outcomes(
            outcomes,
            ledger_path=ledger,
            state_path=state,
        )
    ) == 2


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


def test_provisional_winner_is_excluded_until_outcome_matures(tmp_path):
    ledger = tmp_path / "accountability.jsonl"
    state = tmp_path / "accountability.sqlite3"

    partial = _rows(
        [_screening()],
        [_funnel()],
        outcome=_outcome(window_complete=False, revision=1),
    )
    append_accountability_rows(partial, path=ledger, state_path=state)
    provisional = build_accountability_summary_from_state(
        ledger_path=ledger,
        state_path=state,
    )
    assert provisional["population"]["completed_forward_outcomes"] == 0
    assert provisional["population"]["market_winner_candidates"] == 0
    assert provisional["population"]["executable_false_negatives"] == 0
    assert provisional["opportunity_capture_rate_pct"] is None

    mature = _rows(
        [_screening()],
        [_funnel()],
        outcome=_outcome(window_complete=True, revision=2),
    )
    append_accountability_rows(mature, path=ledger, state_path=state)
    completed = build_accountability_summary_from_state(
        ledger_path=ledger,
        state_path=state,
    )
    assert completed["population"]["completed_forward_outcomes"] == 2
    assert completed["population"]["market_winner_candidates"] == 1
    assert completed["population"]["executable_false_negatives"] == 1


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


def test_dashboard_all_time_uses_complete_database_aggregate_not_trend_window():
    historical = {
        "available": True,
        "opportunity_accountability_daily": [
            {
                "date": "2026-09-02",
                "directional_evaluations": 10,
                "completed_forward_outcomes": 8,
                "market_winner_candidates": 2,
                "captured_winners": 1,
                "executable_false_negatives": 1,
                "threshold_70_79_miss_candidates": 0,
                "ranking_or_cap_miss_candidates": 0,
                "operational_executable_misses": 0,
                "estimated_missed_move_pct_sum": 2.0,
                "decision_latency_samples": 2,
                "mean_decision_latency_ms": 100.0,
            }
        ],
        "opportunity_accountability_all_time": {
            "directional_evaluations": 1000,
            "completed_forward_outcomes": 800,
            "market_winner_candidates": 200,
            "captured_winners": 150,
            "executable_false_negatives": 50,
            "threshold_70_79_miss_candidates": 20,
            "ranking_or_cap_miss_candidates": 10,
            "operational_executable_misses": 5,
            "estimated_missed_move_pct_sum": 123.4,
            "decision_latency_samples": 400,
            "mean_decision_latency_ms": 250.0,
        },
    }

    snapshot = _opportunity_accountability_snapshot(historical, "all")
    assert snapshot["directional_evaluations"] == 1000
    assert snapshot["captured_winners"] == 150
    assert snapshot["executable_false_negatives"] == 50
    assert snapshot["opportunity_capture_rate_pct"] == 75.0
    assert snapshot["estimated_missed_move_pct_sum"] == 123.4
    assert snapshot["mean_decision_latency_ms"] == 250.0
    assert len(snapshot["trend"]) == 1


def test_dashboard_latency_weights_only_rows_with_latency():
    historical = {
        "available": True,
        "opportunity_accountability_daily": [
            {
                "date": "2026-09-01",
                "directional_evaluations": 1000,
                "completed_forward_outcomes": 100,
                "market_winner_candidates": 0,
                "captured_winners": 0,
                "executable_false_negatives": 0,
                "threshold_70_79_miss_candidates": 0,
                "ranking_or_cap_miss_candidates": 0,
                "operational_executable_misses": 0,
                "estimated_missed_move_pct_sum": 0.0,
                "decision_latency_samples": 1,
                "mean_decision_latency_ms": 100.0,
            },
            {
                "date": "2026-09-02",
                "directional_evaluations": 10,
                "completed_forward_outcomes": 10,
                "market_winner_candidates": 0,
                "captured_winners": 0,
                "executable_false_negatives": 0,
                "threshold_70_79_miss_candidates": 0,
                "ranking_or_cap_miss_candidates": 0,
                "operational_executable_misses": 0,
                "estimated_missed_move_pct_sum": 0.0,
                "decision_latency_samples": 9,
                "mean_decision_latency_ms": 500.0,
            },
        ],
    }
    snapshot = _opportunity_accountability_snapshot(historical, "all")
    assert snapshot["mean_decision_latency_ms"] == 460.0


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


def test_invalid_directional_evidence_gets_durable_terminal_disposition(
    tmp_path,
):
    screening_path = tmp_path / "screening.jsonl"
    screening_archive = tmp_path / "screening_archive"
    funnel_path = tmp_path / "funnel.jsonl"
    funnel_archive = tmp_path / "funnel_archive"
    events = tmp_path / "events.jsonl"
    ledger = tmp_path / "accountability.jsonl"
    state = tmp_path / "accountability.sqlite3"
    summary_path = tmp_path / "summary.json"

    screening_path.write_text(
        json.dumps(_screening()) + "\n",
        encoding="utf-8",
    )
    funnel_path.write_text(
        json.dumps(
            _funnel(
                decision="QUALIFIED",
                terminal_gate="FINAL_QUALIFICATION",
                signal_id="SIG:POISON",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    events.write_text(
        json.dumps(
            {
                "event_type": "PAPER_OUTCOME",
                "signal_id": "SIG:POISON",
                "observed_at": (NOW + timedelta(hours=1)).isoformat(),
                "payload": {"net_pnl": float("nan")},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    outcome = _outcome()

    summary = build_incremental_from_outcomes(
        [outcome],
        screening_path=screening_path,
        screening_archive=screening_archive,
        funnel_path=funnel_path,
        funnel_archive=funnel_archive,
        intelligence_event_path=events,
        ledger_path=ledger,
        summary_path=summary_path,
        state_path=state,
        policy=AccountabilityPolicy(
            production_threshold=80,
            shadow_threshold=70,
            winner_move_pct=2,
        ),
    )

    assert summary["batch_disposition"] == {
        "accepted": 0,
        "terminal_rejected": 1,
        "unresolved": 0,
    }
    resolved = resolved_accountability_outcomes(
        [outcome],
        ledger_path=ledger,
        state_path=state,
    )
    assert [row["outcome_record_id"] for row in resolved] == [
        outcome["outcome_record_id"]
    ]

    # Only the healthy SHORT directional row is persisted; the poisoned LONG
    # row is not silently treated as accepted.
    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["direction"] for row in rows] == ["SHORT"]


def test_valid_incremental_outcome_gets_durable_accepted_disposition(tmp_path):
    screening_path = tmp_path / "screening.jsonl"
    funnel_path = tmp_path / "funnel.jsonl"
    ledger = tmp_path / "accountability.jsonl"
    state = tmp_path / "accountability.sqlite3"
    summary_path = tmp_path / "summary.json"
    outcome = _outcome()

    screening_path.write_text(
        json.dumps(_screening()) + "\n",
        encoding="utf-8",
    )
    funnel_path.write_text(
        json.dumps(_funnel()) + "\n",
        encoding="utf-8",
    )

    summary = build_incremental_from_outcomes(
        [outcome],
        screening_path=screening_path,
        screening_archive=tmp_path / "screening_archive",
        funnel_path=funnel_path,
        funnel_archive=tmp_path / "funnel_archive",
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

    assert summary["batch_disposition"] == {
        "accepted": 1,
        "terminal_rejected": 0,
        "unresolved": 0,
    }
    assert len(
        resolved_accountability_outcomes(
            [outcome],
            ledger_path=ledger,
            state_path=state,
        )
    ) == 1


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
